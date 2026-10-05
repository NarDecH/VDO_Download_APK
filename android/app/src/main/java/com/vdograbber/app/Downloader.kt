package com.vdograbber.app

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.webkit.CookieManager
import java.io.File
import java.io.FileInputStream

/**
 * Queue direct-file downloads through the platform DownloadManager (writes
 * into Downloads/VDOGrabber and shows a notification with progress).
 * Direct files (mp4/webm/mp3...) download fully; m3u8/mpd manifests go to
 * StreamDownloadService instead (MainActivity.tryDownload routes by isStream).
 *
 * v1.1.7: the name helpers are pure JVM functions (unit-tested like
 * StreamArgs) and files are named after the page title - same parity as the
 * on-device engine and the desktop app.
 *
 * v1.3.4 (".mp4 that is really an HTML page" bug): URL routing is decided by
 * [routeOf] - a URL without a media extension is a PAGE (player/embed), not a
 * file, so it goes to the yt-dlp engine which can open the page and extract
 * the real stream. As a second safety net the finished DownloadManager job is
 * sniffed for HTML magic bytes; a faked .mp4 is deleted and re-routed
 * (event download_not_media).
 */
object Downloader {

    val STREAM_EXT = setOf("m3u8", "mpd")

    private val EXT_RE = Regex(
        """\.(mp4|webm|mkv|m3u8|mpd|flv|mov|avi|mp3|m4a|aac|ts|3gp)([?#].*)?${'$'}""",
        RegexOption.IGNORE_CASE,
    )

    fun extOf(url: String): String =
        EXT_RE.find(url)?.groupValues?.get(1)?.lowercase() ?: ""

    fun isStream(url: String): Boolean = extOf(url) in STREAM_EXT

    /** Where a URL must be downloaded from (v1.3.4). */
    enum class Route { DIRECT_FILE, STREAM, PAGE }

    /**
     * v1.3.4 routing: manifest URLs to the on-device engine, everything with
     * a known media extension straight through DownloadManager, and every
     * other URL (player/embed/watch pages) to the engine as well - those are
     * web pages, not files, and DownloadManager would just save the HTML of
     * the player page as "title.mp4".
     */
    fun routeOf(url: String): Route = when {
        isStream(url) -> Route.STREAM
        extOf(url).isNotEmpty() -> Route.DIRECT_FILE
        else -> Route.PAGE
    }

    /**
     * True when the first bytes look like an HTML/XML page rather than media
     * data (pure JVM, unit-tested): optional UTF-8 BOM, then leading
     * whitespace and HTML comments, then <!doctype html / <html / <?xml.
     * Media files start with binary boxes/sizes, never with a printable tag.
     */
    fun looksLikeHtml(head: ByteArray): Boolean {
        var i = 0
        if (head.size >= 3 && head[0] == 0xEF.toByte() && head[1] == 0xBB.toByte() && head[2] == 0xBF.toByte()) i = 3
        val s = String(head, i, head.size - i, Charsets.US_ASCII).lowercase()
        var p = 0
        while (true) {
            while (p < s.length && (s[p] == ' ' || s[p] == '\t' || s[p] == '\r' || s[p] == '\n')) p++
            if (s.startsWith("<!--", p)) { // some pages ship a comment before the doctype
                val end = s.indexOf("-->", p + 4)
                if (end < 0) return false // comment fills the sniffed window - undecided
                p = end + 3
                continue
            }
            break
        }
        val rest = s.substring(p)
        return rest.startsWith("<!doctype html") || rest.startsWith("<html") || rest.startsWith("<?xml")
    }

    /**
     * v1.3.4: id -> (url, title) of direct downloads, used by the
     * ACTION_DOWNLOAD_COMPLETE receiver to re-route an HTML payload to the
     * yt-dlp engine (in-process only; capped to avoid unbounded growth).
     */
    private val pendingDm = object : LinkedHashMap<Long, Pair<String, String>>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<Long, Pair<String, String>>?): Boolean = size > 32
    }

    fun pendingFor(id: Long): Pair<String, String>? = synchronized(pendingDm) { pendingDm[id] }

    fun forget(id: Long) { synchronized(pendingDm) { pendingDm.remove(id) } }

    /**
     * v1.3.4: check a finished DownloadManager job - when the payload is
     * really an HTML page (the server answered the player/embed page instead
     * of a video), delete the faked file + DM row and report true so the
     * caller can re-route the URL to the yt-dlp engine.
     */
    fun verifyNotHtmlAndClean(ctx: Context, id: Long): Boolean {
        try {
            val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            dm.query(DownloadManager.Query().setFilterById(id))?.use { c ->
                if (!c.moveToFirst()) return false
                if (c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS)) != DownloadManager.STATUS_SUCCESSFUL) return false
                val local = c.getString(c.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_FILENAME)) ?: return false
                val f = File(local)
                if (!f.exists()) return false
                val head = ByteArray(512)
                val n = try { FileInputStream(f).use { it.read(head) } } catch (_: Exception) { return false }
                if (n <= 0 || !looksLikeHtml(head.copyOf(n))) return false
                val url = c.getString(c.getColumnIndexOrThrow(DownloadManager.COLUMN_URI)).orEmpty()
                FileLog.event("download_not_media", mapOf("url" to url.take(300), "file" to f.name.take(120)))
                FileLog.app("WARN", "dl", "not a video (HTML page) - deleted: ${f.name}")
                try { f.delete() } catch (_: Exception) {}
                try { dm.remove(id) } catch (_: Exception) {}
                return true
            }
        } catch (e: Exception) {
            FileLog.app("ERROR", "dl", "html check failed: $e")
        }
        return false
    }

    /** Human-readable size, e.g. 1234567 -> "1.2 MB" (v1.1.8). */
    fun humanSize(b: Long): String = when {
        b >= 1L shl 30 -> String.format(java.util.Locale.US, "%.1f GB", b / 1073741824.0)
        b >= 1L shl 20 -> String.format(java.util.Locale.US, "%.1f MB", b / 1048576.0)
        b >= 1L shl 10 -> String.format(java.util.Locale.US, "%.1f KB", b / 1024.0)
        else -> "$b B"
    }

    /** URL filename without query/fragment ("https://c/v/clip.mp4?x" -> "clip.mp4"). */
    fun urlStem(url: String): String =
        url.substringBefore('?').substringBefore('#').substringAfterLast('/').trim()

    /**
     * Destination file name (v1.1.6 parity): the page title wins - sanitized
     * like the desktop app - otherwise the URL filename; an extension is
     * appended when missing. "video" is the last-resort name.
     */
    fun displayName(url: String, title: String): String {
        val ext = extOf(url).ifEmpty { "mp4" }
        var name = StreamArgs.sanitizeFilename(title.ifEmpty { urlStem(url) })
        name = name.trimEnd('.')
        if (name.isEmpty()) name = "video"
        return if (name.contains('.')) name else "$name.$ext"
    }

    fun enqueue(ctx: Context, url: String, title: String): Long {
        // v1.3.4 guard: a page URL would come back as an HTML player saved as
        // .mp4 - send it to the yt-dlp engine instead (defensive: tryDownload
        // already routes by routeOf, this protects future call sites)
        if (routeOf(url) != Route.DIRECT_FILE) {
            FileLog.event("download_rerouted", mapOf("url" to url.take(300), "route" to routeOf(url).name))
            StreamDownloadService.start(ctx, url, title)
            return -1L
        }
        val fileName = displayName(url, title)

        val req = DownloadManager.Request(Uri.parse(url)).apply {
            setTitle(fileName)
            setDescription("VDO Grabber")
            setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "VDOGrabber/$fileName")
            CookieManager.getInstance().getCookie(url)?.let { addRequestHeader("Cookie", it) }
            addRequestHeader("User-Agent", "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36")
            setAllowedOverMetered(true)
            setAllowedOverRoaming(true)
        }
        val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val id = dm.enqueue(req)
        synchronized(pendingDm) { pendingDm[id] = url to title } // for the HTML-check re-route
        FileLog.download("enqueue #$id url=$url -> Downloads/VDOGrabber/$fileName")
        FileLog.event("download_queued", mapOf("id" to id, "url" to url, "file" to fileName))
        return id
    }
}
