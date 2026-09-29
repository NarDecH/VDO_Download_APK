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
        outDir = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            "VDOGrabber",
        ).apply { mkdirs() }
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

        val url = intent?.getStringExtra(EXTRA_URL).orEmpty()
        val title = intent?.getStringExtra(EXTRA_TITLE).orEmpty()
        if (url.isEmpty()) { stopSelf(); return START_NOT_STICKY }

        startForeground(NOTIF_ID, buildNotification(title.ifEmpty { url }, 0))
        FileLog.event("download_start", mapOf("url" to url, "kind" to "stream", "engine" to "ytdl-android"))
        FileLog.app("INFO", "dl", "stream download start: $url")

        scope.launch {
            var ok = false
            var errorMsg = ""
            val pid = processId ?: ""
            try {
                val req = YoutubeDLRequest(url).apply {
                    StreamArgs.optionsArgs(outDir).forEach { addOption(it) }
                    addOption("-f", "bv*+ba/b")
                }
                val result = YoutubeDL.getInstance().execute(req, pid) { progress, _eta, line ->
                    updateNotification(title.ifEmpty { url }, progress)
                    if (!line.isNullOrBlank()) FileLog.download("[stream] $line")
                }
                ok = result.exitCode == 0
                if (!ok) errorMsg = "yt-dlp exit ${result.exitCode}"
            } catch (e: Exception) {
                errorMsg = e.message ?: e.javaClass.simpleName
                FileLog.app("ERROR", "dl", "stream download error: $e")
            }

            if (ok) {
                val file = StreamArgs.newestFileIn(outDir)
                publishFile(file)
                FileLog.event("download_done", mapOf("url" to url, "engine" to "ytdl-android"))
                FileLog.app("INFO", "dl", "stream download done: ${file?.name}")
            } else {
                FileLog.event("download_error", mapOf("url" to url, "engine" to "ytdl-android", "error" to errorMsg.take(200)))
                FileLog.app("ERROR", "dl", "stream download failed: $errorMsg")
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
            finishNotification(ok, title.ifEmpty { url })
            stopSelf()
        }
        return START_NOT_STICKY
    }

    /** Copy the finished file into the public media store so gallery/apps see it. */
    private fun publishFile(file: File?) {
        if (file == null || !file.exists()) return
        val ext = file.extension.ifEmpty { "mp4" }
        try {
            val values = android.content.ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, file.name)
                put(MediaStore.MediaColumns.MIME_TYPE, StreamArgs.mimeOf(ext))
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/VDOGrabber")
                }
            }
            val uri: Uri = contentResolver.insert(
                MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: return
            contentResolver.openOutputStream(uri)?.use { out ->
                file.inputStream().use { it.copyTo(out) }
            }
            file.delete() // avoid a second copy sitting in the public dir
            FileLog.event("download_published", mapOf("file" to file.name, "uri" to uri.toString().take(120)))
        } catch (e: Exception) {
            // worst case: the raw file remains in Downloads/VDOGrabber
            FileLog.app("WARNING", "dl", "publish failed (file kept): $e")
        }
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
            .setProgress(100, progress, progress == 0)
            .setOngoing(true)
            .addAction(0, getString(R.string.cancel), cancelPendingIntent())
            .build()

    private fun updateNotification(title: String, progress: Float) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_ID, buildNotification(title, progress.toInt().coerceIn(0, 99)))
    }

    private fun finishNotification(ok: Boolean, title: String) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val n = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(if (ok) android.R.drawable.stat_sys_download_done else android.R.drawable.stat_notify_error)
            .setContentTitle(getString(if (ok) R.string.stream_dl_done else R.string.stream_dl_failed))
            .setContentText(title.take(60))
            .setOngoing(false)
            .build()
        nm.notify(NOTIF_DONE_ID, n)
    }

    private fun cancelPendingIntent(): PendingIntent = PendingIntent.getService(
        this, 0,
        Intent(this, StreamDownloadService::class.java).setAction(ACTION_CANCEL),
        PendingIntent.FLAG_IMMUTABLE,
    )

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL_ID = "stream_downloads"
        private const val NOTIF_ID = 41
        private const val NOTIF_DONE_ID = 42
        const val EXTRA_URL = "url"
        const val EXTRA_TITLE = "title"
        const val ACTION_CANCEL = "com.vdograbber.app.CANCEL"

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
