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
import android.webkit.CookieManager
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
 *
 * v1.5.0: per-job tracking via [DownloadJobs] - a new onStartCommand while a
 * download is running now starts a SECOND concurrent job, each with its own
 * pid/percent (the old single lastPercent static mixed their progress), and
 * a cancel targets one job instead of stopping the whole service.
 */
class StreamDownloadService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

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
        PausedJobs.init(filesDir) // v1.7.0: pause may arrive with no Activity alive
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_CANCEL) {
            // v1.5.0: a pid extra cancels exactly that job (the sheet's row
            // ยกเลิก button); without one (the notification action) every
            // running job is cancelled as before.
            val single = intent.getStringExtra(EXTRA_PID)
            val pids = if (single.isNullOrEmpty()) DownloadJobs.active().map { it.pid }
                       else listOf(single)
            for (p in pids) {
                if (!DownloadJobs.markCancelled(p)) continue // already finished
                try { YoutubeDL.getInstance().destroyProcessById(p) } catch (e: Exception) {
                    FileLog.app("ERROR", "dl", "cancel failed: $e")
                }
            }
            if (DownloadJobs.count() == 0) {
                cancelProgressNotification()
                stopSelfResult(startId)
            }
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_PAUSE) {
            // v1.7.0: หยุดพัก - kill the engine but KEEP its .part/.part-FragN
            // files in engine-work and file the job in PausedJobs, so a later
            // ดาวน์โหลดต่อ restarts the same output template and yt-dlp
            // resumes from the last fragment. Same pid semantics as cancel.
            val single = intent.getStringExtra(EXTRA_PID)
            val pids = if (single.isNullOrEmpty()) DownloadJobs.active().map { it.pid }
                       else listOf(single)
            for (p in pids) {
                val j = DownloadJobs.get(p) ?: continue
                if (!DownloadJobs.markPaused(p)) continue // already finished
                try { PausedJobs.add(j.url, j.title, j.titleBase) } catch (e: Exception) {
                    FileLog.app("ERROR", "dl", "pause persist failed: $e")
                }
                try { YoutubeDL.getInstance().destroyProcessById(p) } catch (e: Exception) {
                    FileLog.app("ERROR", "dl", "pause kill failed: $e")
                }
                FileLog.event("download_paused", mapOf("url" to j.url.take(200), "pid" to p))
                FileLog.app("INFO", "dl", "stream download paused: ${j.url}")
            }
            if (DownloadJobs.count() == 0) {
                cancelProgressNotification()
                stopSelfResult(startId)
            }
            return START_NOT_STICKY
        }
        isRunning = true

        val url = intent?.getStringExtra(EXTRA_URL).orEmpty()
        val title = intent?.getStringExtra(EXTRA_TITLE).orEmpty()
        // v1.9.26: the page the media was found on - its host owns the WebView
        // session cookies that yt-dlp needs for login-gated sites (X/Twitter)
        val pageUrl = intent?.getStringExtra(EXTRA_PAGE_URL).orEmpty().ifEmpty { url }
        if (url.isEmpty()) { stopSelfResult(startId); return START_NOT_STICKY }

        // v1.5.0: process id is per-job (a second download may already be
        // running) and stays alive in DownloadJobs for the UI + cancel
        val pid = java.util.UUID.randomUUID().toString().substringBefore("-").take(8)

        // v1.8.0: EVERYTHING this job writes (.part fragments, ytdl state,
        // the merged mp4) goes into its own engine-work/<pid> dir - during
        // parallel downloads A's publish must never see (or delete) B's
        // partially written data (field log 2026-10-07 22:11 crash).
        val jobDir = StreamArgs.jobWorkDir(outDir, pid).apply { mkdirs() }

        // v1.1.6: name the file after the page title (deduped on disk),
        // falling back to yt-dlp's metadata title + id when there is none.
        // v1.1.7: precompute a FREE stem, otherwise yt-dlp skips a re-download
        // with "has already been downloaded" and nothing gets saved.
        // v1.7.0: a resumed job carries its EXACT paused titleBase - recomputing
        // freeTitleBase here would dedupe against the kept .part files and the
        // resume would silently start from zero under a different name.
        // v1.8.0: the paused stem now lives inside THIS job's dir, so the
        // dedupe check reads the per-job dir too (and a resumed titleBase is
        // never "taken" by any other concurrent job).
        val titleBase = intent?.getStringExtra(EXTRA_TITLE_BASE)
            ?.takeIf { it.isNotBlank() }
            ?: freeTitleBase(jobDir, StreamArgs.sanitizeFilename(title))

        DownloadJobs.register(pid, url, title, titleBase)

        startForeground(NOTIF_ID, buildNotification(title.ifEmpty { url }, 0))
        FileLog.event("download_start", mapOf("url" to url, "kind" to "stream", "engine" to "ytdl-android"))
        FileLog.app("INFO", "dl", "stream download start: $url (work ${jobDir.name})")

        scope.launch {
            var ok = false
            var errorMsg = ""
            var loggedPct = -1
            // v1.6.1: rate-limit the two progress log sinks. The field log
            // (2026-10-07) showed ~11k downloads.log lines and ~330
            // download_progress events for ONE job - every line hit the log
            // while the UI already had its own 500 ms ticker.
            var lastProgressLogMs = 0L
            var lastLineLogMs = 0L
            var cookiePath = ""
            try {
                cookiePath = writeCookieFile(jobDir, pageUrl)
                val req = YoutubeDLRequest(url).apply {
                    StreamArgs.optionsArgs(jobDir, titleBase).forEach { addOption(it) }
                    StreamArgs.cookieArgs(cookiePath).forEach { addOption(it) }
                    addOption("-f", "bv*+ba/b")
                }
                val result = YoutubeDL.getInstance().execute(req, pid) { progress, _eta, line ->
                    updateNotification(title.ifEmpty { url }, progress)
                    // v1.4.0: throttle to whole percents so the event log stays
                    // readable while the notification + main screen tick live.
                    // v1.5.0: percent is per-JOB (DownloadJobs) - overlapping
                    // downloads no longer overwrite each other's progress
                    val pct = progress.toInt().coerceIn(0, 99)
                    DownloadJobs.update(pid, pct)
                    // v1.9.22: one download_progress per 2s / 5% step, never
                    // backwards (v1.6.1's 1s limiter still logged ~550 events
                    // for one long HLS job)
                    if (StreamArgs.shouldLogProgress(loggedPct, lastProgressLogMs, pct, System.currentTimeMillis())) {
                        loggedPct = pct
                        lastProgressLogMs = System.currentTimeMillis()
                        FileLog.event("download_progress", mapOf("url" to url.take(200), "percent" to pct, "pid" to pid))
                    }
                    // v1.6.1: keep the verbatim mirror for everything except
                    // per-fragment progress chatter (rate-limited to 1/s);
                    // Destination/Merger/Deleting and the 100% summary pass as-is
                    if (!line.isNullOrBlank()) {
                        if (!StreamArgs.isProgressLine(line) || StreamArgs.progressLogDue(lastLineLogMs, System.currentTimeMillis())) {
                            lastLineLogMs = System.currentTimeMillis()
                            FileLog.download("[stream] $line")
                        }
                    }
                }
                ok = result.exitCode == 0
                if (!ok) errorMsg = "yt-dlp exit ${result.exitCode}"
            } catch (e: Exception) {
                errorMsg = e.message ?: e.javaClass.simpleName
                // v1.8.0: a CanceledException here is normal when THIS pid was
                // just paused/cancelled (destroyProcessById); do not scream to
                // app.log - the consumePaused/consumeCancelled branch below
                // turns it into the right outcome (field log 2026-10-07:
                // "download_error CanceledException" noise)
                if (e !is com.yausername.youtubedl_android.YoutubeDL.CanceledException) {
                    FileLog.app("ERROR", "dl", "stream download error: $e")
                }
            }
            // v1.9.26: the exported cookie jar is session material - never
            // leave it behind in engine-work after the job ends
            if (cookiePath.isNotEmpty()) {
                try { File(cookiePath).delete() } catch (e: Exception) {
                    FileLog.app("WARNING", "dl", "cookie cleanup failed: $e")
                }
            }
            DownloadJobs.remove(pid) // gone from the sheet/chip whatever the outcome

            if (DownloadJobs.consumeCancelled(pid)) {
                // user pressed ยกเลิก: the process died on purpose - report it
                // as cancelled, not as a failure (no error notification and no
                // Unsupported-URL re-route broadcast for a killed process)
                FileLog.event("download_cancel", mapOf("url" to url.take(200), "pid" to pid))
                FileLog.app("INFO", "dl", "stream download cancelled: $url")
            } else if (DownloadJobs.consumePaused(pid)) {
                // v1.7.0: user pressed พัก - the job is already in PausedJobs
                // with its .part files kept; the pause action logged the event,
                // here we only avoid reporting a fake "yt-dlp exit" failure
                FileLog.app("INFO", "dl", "stream download paused (worker exit): $url")
            } else if (ok) {
                // v1.8.0: publish from THIS job's own dir only - the old shared
                // newestFileIn(engine-work) could pick ANOTHER running job's
                // actively written .part file, copy it into Downloads and
                // delete it under the running job (field log Errno 2).
                // publishableFile takes the biggest real file: a merged mp4
                // always dwarfs yt-dlp's state bookkeeping.
                val file = StreamArgs.publishableFile(jobDir)
                if (file == null) {
                    // v1.3.5: the engine exited 0 but produced no file (any
                    // cause) - diagnose from machine facts instead of
                    // reporting a phantom success
                    onNoFile(url, title)
                    finishNotification(false, title.ifEmpty { url })
                    notifyMainActivity(false, "")
                    stopSelfResult(startId)
                    return@launch
                }
                // v1.9.28: exit 0 is not proof of a playable file - check the
                // final container before publishing it as a success
                val verdict = verifyMedia(file)
                if (verdict.isNotEmpty()) {
                    FileLog.event("download_verify_failed", mapOf(
                        "url" to url.take(200), "file" to file.name,
                        "bytes" to file.length(), "error" to verdict.take(200)))
                    FileLog.app("ERROR", "dl", "stream download file is incomplete: $verdict")
                    finishNotification(false, verdict)
                    notifyMainActivity(false, "")
                    stopSelfResult(startId)
                    return@launch
                }
                FileLog.event("download_verify", mapOf(
                    "url" to url.take(200), "file" to file.name, "bytes" to file.length()))
                val published = publishFile(file)
                FileLog.event("download_done", mapOf("url" to url, "engine" to "ytdl-android", "file" to (published?.first ?: file.name)))
                FileLog.app("INFO", "dl", "stream download done: ${published?.first ?: file.name}")
                finishNotification(
                    true,
                    published?.first ?: file.name,
                    published?.second,
                    published?.let { File(file.parent, it.first).absolutePath },
                )
                // v1.6.0: surface the outcome inside the app too (Snackbar + chip)
                notifyMainActivity(true, published?.first ?: file.name)
            } else {
                val cause = StreamArgs.ytDlpErrorLine(errorMsg)
                // v1.9.9 (desktop parity): keep a parsed reason alongside the
                // raw error - comparable across sessions (app/core/
                // downloader.py download_error_line reason field)
                FileLog.event("download_error", mapOf(
                    "url" to url, "engine" to "ytdl-android",
                    "error" to errorMsg.take(200), "reason" to cause))
                FileLog.app("ERROR", "dl", "stream download failed: $errorMsg")
                // v1.9.26: an account/permission wall is not a broken site -
                // log it explicitly and tell the user what to do next
                if (StreamArgs.isLoginRequired(errorMsg)) {
                    FileLog.event("download_login_required", mapOf(
                        "url" to url.take(200), "reason" to cause,
                        "cookie" to if (cookiePath.isEmpty()) "none" else "webview"))
                    FileLog.app("WARNING", "dl", "stream download needs a login: $url")
                }
                finishNotification(false, cause)
                notifyMainActivity(
                    false, "",
                    if (StreamArgs.isLoginRequired(errorMsg)) getString(R.string.login_required_hint) else "",
                )
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
            // stop only once every delivered start request was handled - with
            // a concurrent download still running the service must stay alive
            stopSelfResult(startId)
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
    private fun publishFile(file: File?): Pair<String, Uri>? {
        if (file == null || !file.exists()) return null
        val ext = file.extension.ifEmpty { "mp4" }
        // v1.9.28: every job writes into its own fresh engine dir, so its name
        // can still collide with an earlier download that is already visible
        // in Downloads/VDOGrabber - dedupe in the PUBLISHING namespace.
        val displayName = freePublishedName(file.name)
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

    /**
     * v1.9.28: first free name in Downloads/VDOGrabber for a finished file -
     * `name.mp4`, then `name (2).mp4`, `name (3).mp4`, ... MediaStore has no
     * uniqueness constraint, so without this check a second download of the
     * same title shows up twice under the same name.
     */
    private fun freePublishedName(name: String): String {
        val dot = name.lastIndexOf('.')
        val stem = if (dot > 0) name.substring(0, dot) else name
        val ext = if (dot > 0) name.substring(dot) else ""
        fun taken(candidate: String): Boolean = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val selection = "${MediaStore.MediaColumns.DISPLAY_NAME} = ? AND " +
                    "${MediaStore.MediaColumns.RELATIVE_PATH} = ?"
                val args = arrayOf(
                    candidate,
                    Environment.DIRECTORY_DOWNLOADS + "/VDOGrabber/",
                )
                contentResolver.query(
                    MediaStore.Downloads.EXTERNAL_CONTENT_URI,
                    arrayOf(MediaStore.MediaColumns._ID), selection, args, null,
                )?.use { it.count > 0 } ?: false
            } else {
                val dir = File(
                    Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                    "VDOGrabber",
                )
                File(dir, candidate).exists()
            }
        } catch (e: Exception) {
            false // a failed check must never block publishing
        }
        if (!taken(name)) return name
        var n = 2
        while (taken("$stem ($n)$ext")) n++
        return "$stem ($n)$ext"
    }

    /**
     * v1.9.28: container-level integrity check for a finished HLS/DASH job.
     * Desktop parity: fail only when the container cannot be opened (the
     * ffprobe non-zero-exit equivalent) or holds no audio/video stream -
     * an unusual duration is informational, never an error.
     * Returns "" when the file looks complete, otherwise the reason in Thai.
     */
    private fun verifyMedia(file: File): String {
        if (!file.exists() || file.length() <= 0L) return "ไฟล์ว่าง (0 ไบต์)"
        val mmr = android.media.MediaMetadataRetriever()
        return try {
            mmr.setDataSource(file.absolutePath)
            val hasVideo = mmr.extractMetadata(
                android.media.MediaMetadataRetriever.METADATA_KEY_HAS_VIDEO) == "yes"
            val hasAudio = mmr.extractMetadata(
                android.media.MediaMetadataRetriever.METADATA_KEY_HAS_AUDIO) == "yes"
            if (!hasVideo && !hasAudio) "ไม่พบสตรีมวิดีโอ/เสียงในไฟล์" else ""
        } catch (e: Exception) {
            "เปิดไฟล์ไม่ได้: ${e.javaClass.simpleName}"
        } finally {
            try { mmr.release() } catch (_: Exception) {}
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

    /**
     * v1.6.0: tell the (possibly dead) MainActivity a download finished or
     * failed - a broadcast it registers with RECEIVER_NOT_EXPORTED, so only
     * this app receives it. Never throws: a dead/missing receiver must not
     * take the download worker down.
     */
    private fun notifyMainActivity(ok: Boolean, file: String, hint: String = "") {
        try {
            sendBroadcast(
                Intent(ACTION_DL_STATE)
                    .setPackage(packageName)
                    .putExtra(EXTRA_DL_OK, ok)
                    .putExtra(EXTRA_DL_FILE, file.take(120))
                    .putExtra(EXTRA_DL_HINT, hint.take(200)),
            )
        } catch (e: Exception) {
            FileLog.app("WARNING", "dl", "notify main activity failed: $e")
        }
    }

    /**
     * v1.9.26: export the WebView's cookies for the page host into the
     * job dir as a Netscape cookies.txt, so yt-dlp can download login-gated
     * media (X/Twitter, Instagram, members-only). Returns "" when there is
     * nothing to write; values never reach the logs.
     */
    private fun writeCookieFile(jobDir: File, pageUrl: String): String {
        return try {
            val host = Uri.parse(pageUrl).host.orEmpty()
            val text = StreamArgs.netscapeCookieLines(CookieManager.getInstance().getCookie(pageUrl), host)
            if (text.isEmpty()) "" else {
                val f = File(jobDir, "cookies.txt")
                f.writeText(text)
                val n = text.lineSequence().count { it.isNotBlank() } - 1
                FileLog.event("cookie_source", mapOf("mode" to "webview", "host" to host, "cookies" to n))
                f.absolutePath
            }
        } catch (e: Exception) {
            FileLog.app("WARNING", "dl", "cookie export failed: $e")
            ""
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
            // v1.4.0: determinate as soon as yt-dlp reports a percent (only the
            // very start - before the first report - stays indeterminate)
            .setProgress(100, progress, progress <= 0)
            .setOngoing(true)
            .addAction(0, getString(R.string.pause), pausePendingIntent())
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

    private fun pausePendingIntent(): PendingIntent = PendingIntent.getService(
        this, 1,
        Intent(this, StreamDownloadService::class.java).setAction(ACTION_PAUSE),
        PendingIntent.FLAG_IMMUTABLE,
    )

    /** The progress notification is shared by every running job - remove it
     *  only when the LAST job goes away (cancel/pause of a single job among
     *  several leaves it up for the survivor). */
    private fun cancelProgressNotification() {
        try {
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .cancel(NOTIF_ID)
        } catch (e: Exception) {
            FileLog.app("WARNING", "dl", "progress notification cancel failed: $e")
        }
    }

    override fun onDestroy() {
        isRunning = false
        DownloadJobs.clear() // service dead -> nothing is running anymore
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
        /** v1.9.26: page whose WebView cookies are exported for yt-dlp. */
        const val EXTRA_PAGE_URL = "pageUrl"
        /** v1.5.0: per-job cancel - absent means "cancel every running job". */
        const val EXTRA_PID = "pid"
        const val ACTION_CANCEL = "com.vdograbber.app.CANCEL"

        /** v1.7.0: หยุดพัก - same pid semantics as [ACTION_CANCEL] but keeps
         *  the partial files; [EXTRA_TITLE_BASE] feeds a resumed job back its
         *  exact paused stem so yt-dlp resumes instead of restarting. */
        const val ACTION_PAUSE = "com.vdograbber.app.PAUSE"
        const val EXTRA_TITLE_BASE = "titleBase"

        /** v1.6.0: in-app download outcome notice (MainActivity shows a
         *  Snackbar + a temporary status chip). */
        const val ACTION_DL_STATE = "com.vdograbber.app.DL_STATE"
        const val EXTRA_DL_OK = "ok"
        const val EXTRA_DL_FILE = "file"
        /** v1.9.26: Thai guidance shown when a download needs a login. */
        const val EXTRA_DL_HINT = "hint"

        /** v1.4.0: true from onStartCommand until onDestroy (all stop paths). */
        @Volatile var isRunning = false
            private set

        /** v1.4.0: current download percent 0..99, or -1 when idle. Backed by
         *  the per-job registry since v1.5.0. */
        fun currentPercent(): Int = DownloadJobs.currentPercent()

        /** v1.5.0: jobs currently downloading (media sheet "กำลังดาวน์โหลด" tab). */
        fun activeJobs(): List<DownloadJobs.Job> = DownloadJobs.active()

        /** v1.5.0: cancel ONE job by pid (no-op when it already finished). */
        fun cancel(context: Context, pid: String) {
            if (pid.isBlank()) return
            val i = Intent(context, StreamDownloadService::class.java)
                .setAction(ACTION_CANCEL)
                .putExtra(EXTRA_PID, pid)
            try {
                context.startService(i)
            } catch (e: Exception) {
                FileLog.app("ERROR", "dl", "cancel service start failed: $e")
            }
        }

        /** v1.7.0: pause ONE job by pid (no pid = pause every running job,
         *  same shape as [cancel]) - partial files are kept for a resume. */
        fun pause(context: Context, pid: String) {
            val i = Intent(context, StreamDownloadService::class.java)
                .setAction(ACTION_PAUSE)
            if (pid.isNotBlank()) i.putExtra(EXTRA_PID, pid)
            try {
                context.startService(i)
            } catch (e: Exception) {
                FileLog.app("ERROR", "dl", "pause service start failed: $e")
            }
        }

        /** v1.7.0: ดาวน์โหลดต่อ a paused job - drop it from the paused
         *  registry and restart the engine with the SAME titleBase so yt-dlp
         *  finds its .part files and resumes instead of starting over. */
        fun resume(context: Context, entry: PausedJobs.Entry) {
            if (entry.url.isBlank()) return
            try { PausedJobs.remove(entry.titleBase) } catch (e: Exception) {
                FileLog.app("ERROR", "dl", "resume unbook failed: $e")
            }
            FileLog.event("download_resumed", mapOf("url" to entry.url.take(200)))
            FileLog.app("INFO", "dl", "stream download resumed: ${entry.url}")
            start(context, entry.url, entry.title, entry.titleBase)
        }

        /** Start a stream download (no-op when [url] is blank). */
        fun start(context: Context, url: String, title: String, titleBase: String = "", pageUrl: String = "") {
            if (url.isBlank()) return
            val i = Intent(context, StreamDownloadService::class.java)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_TITLE, title)
            if (pageUrl.isNotBlank()) i.putExtra(EXTRA_PAGE_URL, pageUrl)
            if (titleBase.isNotBlank()) i.putExtra(EXTRA_TITLE_BASE, titleBase)
            context.startForegroundService(i)
        }
    }
}
