package com.vdograbber.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.IBinder
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest

/**
 * On-device HLS/DASH (and any yt-dlp-supported site) downloads.
 *
 * Steps 1-3 of docs/plan-android-hls.md:
 *   1. youtubedl-android (library + ffmpeg) pinned to 0.17.3
 *   2. foreground service with progress notification + cancel action
 *   3. wired from MainActivity.tryDownload() for isStream(url) items
 *
 * Publishing the finished file into the media store (MediaStore API) lives in
 * publishFile(); FileLog events mirror the desktop event names.
 */
class StreamDownloadService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var processId: String? = null

    private lateinit var outDir: File

    override fun onCreate() {
        super.onCreate()
        // v1.3.5: yt-dlp now WRITES into an app-private dir - the public
        // Downloads dir is scope-protected on Q+ (targetSdk 30+), where a
        // non-media library cannot create files; the engine "succeeded"
        // (exit 0) but nothing landed on disk and the service reported a
        // null download ("stream download done: null"). The finished file
        // is published into Downloads/VDOGrabber via the MediaStore API
        // (publishFile) exactly as before - that path is unaffected.
        outDir = StreamArgs.engineWorkDir(getExternalFilesDir(null) ?: filesDir).apply { mkdirs() }
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CANCEL) {
            processId?.let {
                try { YoutubeDL.getInstance().destroyProcessById(it) } catch (e: Exception) {
                    FileLog.app("ERROR", "dl", "cancel failed: $e")
                }
            }
            stopSelf()
            return START_NOT_STICKY
        }
        processId = java.util.UUID.randomUUID().toString().substringBefore("-").take(8)
        lastPercent = -1
        isRunning = true

        val url = intent?.getStringExtra(EXTRA_URL).orEmpty()
        val title = intent?.getStringExtra(EXTRA_TITLE).orEmpty()
        if (url.isEmpty()) { stopSelf(); return START_NOT_STICKY }
        // v1.1.6: name the file after the page title (deduped on disk),
        // falling back to yt-dlp's metadata title + id when there is none.
        // v1.1.7: precompute a FREE stem, otherwise yt-dlp skips a re-download
        // with "has already been downloaded" and nothing gets saved.
        val titleBase = freeTitleBase(outDir, StreamArgs.sanitizeFilename(title))

        startForeground(NOTIF_ID, buildNotification(title.ifEmpty { url }, 0))
        FileLog.event("download_start", mapOf("url" to url, "kind" to "stream", "engine" to "ytdl-android"))
        FileLog.app("INFO", "dl", "stream download start: $url")

        scope.launch {
            var ok = false
            var errorMsg = ""
            val pid = processId ?: ""
            try {
                val req = YoutubeDLRequest(url).apply {
                    StreamArgs.optionsArgs(outDir, titleBase).forEach { addOption(it) }
                    addOption("-f", "bv*+ba/b")
                }
                val result = YoutubeDL.getInstance().execute(req, pid) { progress, _eta, line ->
                    updateNotification(title.ifEmpty { url }, progress)
                    // v1.4.0: throttle to whole percents so the event log stays
                    // readable while the notification + main screen tick live
                    val pct = progress.toInt().coerceIn(0, 99)
                    if (pct != lastPercent) {
                        lastPercent = pct
                        FileLog.event("download_progress", mapOf("url" to url.take(200), "percent" to pct))
                    }
                    if (!line.isNullOrBlank()) FileLog.download("[stream] $line")
                }
                ok = result.exitCode == 0
                if (!ok) errorMsg = "yt-dlp exit ${result.exitCode}"
            } catch (e: Exception) {
                errorMsg = e.message ?: e.javaClass.simpleName
                FileLog.app("ERROR", "dl", "stream download error: $e")
            }
            lastPercent = -1 // finished (any outcome) - the UI chip resets

            if (ok) {
                val file = StreamArgs.newestFileIn(outDir)
                if (file == null) {
                    // v1.3.5: the engine exited 0 but produced no file (any
                    // cause) - diagnose from machine facts instead of
                    // reporting a phantom success
                    onNoFile(url, title)
                    finishNotification(false, title.ifEmpty { url })
                    stopSelf()
                    return@launch
                }
                val published = publishFile(file, title)
                FileLog.event("download_done", mapOf("url" to url, "engine" to "ytdl-android", "file" to (published?.first ?: file.name)))
                FileLog.app("INFO", "dl", "stream download done: ${published?.first ?: file.name}")
                finishNotification(
                    true,
                    published?.first ?: file.name,
                    published?.second,
                    published?.let { File(outDir, it.first).absolutePath },
                )
            } else {
                val cause = StreamArgs.ytDlpErrorLine(errorMsg)
                FileLog.event("download_error", mapOf("url" to url, "engine" to "ytdl-android", "error" to errorMsg.take(200)))
                FileLog.app("ERROR", "dl", "stream download failed: $errorMsg")
                finishNotification(false, cause)
                // Unsupported-URL player pages: hand the page to the WebView so
                // the in-page detector can catch the real stream while it plays
                // (desktop parity - plan-android-hls.md / v1.1.4 desktop flow)
                if (errorMsg.contains("Unsupported URL", true)) {
                    val i = Intent("com.vdograbber.app.OPEN_IN_BROWSER")
                        .setPackage(packageName)
                        .putExtra("url", url)
                    sendBroadcast(i)
                }
            }
            // failures already notified with the real yt-dlp cause (v1.3.5)
            stopSelf()
        }
        return START_NOT_STICKY
    }

    /**
     * v1.3.5: engine exited 0 but wrote no file - run a cheap -F probe and
     * log what yt-dlp really sees, so the user is not told a download
     * "succeeded" into nothing. Typical causes: DRM-protected stream,
     * geo-block, or an unknown page layout yt-dlp cannot parse.
     */
    private fun onNoFile(url: String, title: String) {
        var probe = "-"
        try {
            val req = YoutubeDLRequest(url).apply {
                StreamArgs.probeArgs().forEach { addOption(it) }
            }
            YoutubeDL.getInstance().execute(req, null) { _, _, line ->
                if (!line.isNullOrBlank()) probe = (probe + " | " + line).take(400)
            }
            FileLog.app("INFO", "dl", "plan probe: ${probe.take(300)}")
        } catch (e: Exception) {
            probe = StreamArgs.ytDlpErrorLine(e.message ?: e.javaClass.simpleName)
            FileLog.app("INFO", "dl", "plan probe failed: $probe")
        }
        FileLog.event("download_no_file", mapOf("url" to url.take(300), "probe" to probe.take(300)))
        finishNotification(false, probe)
    }

    /**
     * Copy the finished file into the public media store so gallery/apps see it.
     * v1.1.6: renamed to the sanitized page title and de-duplicated
     * (`name (2).mp4`); returns (display name, content URI) for the
     * finished-notification's open action, or null when publish failed.
     */
    private fun publishFile(file: File?, title: String): Pair<String, Uri>? {
        if (file == null || !file.exists()) return null
        val ext = file.extension.ifEmpty { "mp4" }
        val stem = StreamArgs.sanitizeFilename(title)
        // when yt-dlp already named/deduped it after the title, keep that name
        val displayName = when {
            stem.isEmpty() -> file.name
            file.name.startsWith(stem) -> file.name
            else -> StreamArgs.uniqueFileName(outDir, stem, ext)
        }
        try {
            val values = android.content.ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, displayName)
                put(MediaStore.MediaColumns.MIME_TYPE, StreamArgs.mimeOf(ext))
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/VDOGrabber")
                } else {
                    // pre-Q: no RELATIVE_PATH - the absolute path is what makes
                    // it visible in Downloads/VDOGrabber (v1.1.7 fix)
                    put(MediaStore.MediaColumns.DATA, File(outDir, displayName).absolutePath)
                }
            }
            val uri: Uri = contentResolver.insert(
                MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return null
            contentResolver.openOutputStream(uri)?.use { out ->
                file.inputStream().use { it.copyTo(out) }
            }
            file.delete() // avoid a second copy sitting in the public dir
            FileLog.event("download_published", mapOf("file" to displayName, "uri" to uri.toString().take(120)))
            return displayName to uri
        } catch (e: Exception) {
            // worst case: the raw file remains in Downloads/VDOGrabber
            FileLog.app("WARNING", "dl", "publish failed (file kept): $e")
            return null
        }
    }

    /** Return [base], or `base (2)`, `(3)`, ... while files `stem.*` exist. */
    private fun freeTitleBase(dir: File, base: String): String {
        if (base.isEmpty()) return base
        fun taken(stem: String): Boolean =
            dir.listFiles()?.any { it.name.startsWith("$stem.") } == true
        var stem = base
        var n = 2
        while (taken(stem)) { stem = "$base ($n)"; n++ }
        return stem
    }

    // ------------------------------------------------------------ notification
    private fun createChannel() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Downloads", NotificationManager.IMPORTANCE_LOW),
        )
    }

    private fun buildNotification(title: String, progress: Int): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(getString(R.string.stream_dl_title))
            .setContentText(title.take(60))
            // v1.4.0: determinate as soon as yt-dlp reports a percent (only the
            // very start - before the first report - stays indeterminate)
            .setProgress(100, progress, progress <= 0)
            .setOngoing(true)
            .addAction(0, getString(R.string.cancel), cancelPendingIntent())
            .build()

    private fun updateNotification(title: String, progress: Float) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_ID, buildNotification(title, progress.toInt().coerceIn(0, 99)))
    }

    /**
     * Finished-notification (v1.1.6): tapping it (or the เปิด action) opens the
     * published file via its MediaStore URI; failure notifications open the
     * app so the user can re-scan the page. v1.1.7 adds a ลบไฟล์ action that
     * deletes the published file (trampolined through DeleteFileActivity).
     */
    private fun finishNotification(ok: Boolean, text: String, openUri: Uri? = null, rawPath: String? = null) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val b = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(if (ok) android.R.drawable.stat_sys_download_done else android.R.drawable.stat_notify_error)
            .setContentTitle(getString(if (ok) R.string.stream_dl_done else R.string.stream_dl_failed))
            .setContentText(text.take(60))
            .setOngoing(false)
            .setAutoCancel(true)
        val view = openPendingIntent(ok, openUri)
        b.setContentIntent(view)
        if (ok && openUri != null) b.addAction(0, getString(R.string.notif_open), view)
        if (ok) {
            b.addAction(0, getString(R.string.delete),
                DeleteFileActivity.pendingIntent(this, openUri?.toString().orEmpty(), rawPath.orEmpty()))
        }
        nm.notify(NOTIF_DONE_ID, b.build())
    }

    private fun openPendingIntent(ok: Boolean, openUri: Uri?): PendingIntent {
        if (ok && openUri != null) {
            val i = Intent(Intent.ACTION_VIEW)
                .setDataAndType(openUri, "video/*")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            return PendingIntent.getActivity(
                this, RC_OPEN, i,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        }
        return PendingIntent.getActivity(
            this, RC_MAIN, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    private fun cancelPendingIntent(): PendingIntent = PendingIntent.getService(
        this, 0,
        Intent(this, StreamDownloadService::class.java).setAction(ACTION_CANCEL),
        PendingIntent.FLAG_IMMUTABLE,
    )

    override fun onDestroy() {
        isRunning = false
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL_ID = "stream_downloads"
        private const val NOTIF_ID = 41
        private const val NOTIF_DONE_ID = 42
        private const val RC_OPEN = 43
        private const val RC_MAIN = 44
        const val EXTRA_URL = "url"
        const val EXTRA_TITLE = "title"
        const val ACTION_CANCEL = "com.vdograbber.app.CANCEL"

        /** v1.4.0: true from onStartCommand until onDestroy (all stop paths). */
        @Volatile var isRunning = false
            private set

        /** v1.4.0: current download percent 0..99, or -1 when idle. */
        @Volatile var lastPercent = -1
            private set

        fun currentPercent(): Int = lastPercent

        /** Start a stream download (no-op when [url] is blank). */
        fun start(context: Context, url: String, title: String) {
            if (url.isBlank()) return
            val i = Intent(context, StreamDownloadService::class.java)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_TITLE, title)
            context.startForegroundService(i)
        }
    }
}
