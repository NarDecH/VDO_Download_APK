package com.vdograbber.app

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.webkit.CookieManager

/**
 * Queue direct-file downloads through the platform DownloadManager (writes
 * into Downloads/VDOGrabber and shows a notification with progress).
 * Direct files (mp4/webm/mp3...) download fully; m3u8/mpd manifests go to
 * StreamDownloadService instead (MainActivity.tryDownload routes by isStream).
 *
 * v1.1.7: the name helpers are pure JVM functions (unit-tested like
 * StreamArgs) and files are named after the page title - same parity as the
 * on-device engine and the desktop app.
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
        FileLog.download("enqueue #$id url=$url -> Downloads/VDOGrabber/$fileName")
        FileLog.event("download_queued", mapOf("id" to id, "url" to url, "file" to fileName))
        return id
    }
}
