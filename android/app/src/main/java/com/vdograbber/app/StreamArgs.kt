package com.vdograbber.app

import java.io.File

/**
 * Pure helpers for the on-device yt-dlp engine (StreamDownloadService).
 * Deliberately free of Android types so it is unit-testable on the JVM -
 * mirrors scripts/test_units.py on the desktop side.
 */
object StreamArgs {

    /** yt-dlp options for merging into [dir].
     *
     *  v1.1.6: with a [titleBase] (page title) the file is named after it and
     *  de-duplicated on disk (`name (2).mp4`); without one yt-dlp picks the
     *  metadata title + id, like the desktop fallback template.
     */
    fun optionsArgs(dir: File, titleBase: String = ""): List<String> = listOf(
        "--no-playlist",
        "--no-mtime",
        "-o", outputTemplate(dir, titleBase),
    )

    /** Extra args for a cheap plan-probe run (-F prints the format table).
     *  Used to log WHY a download produced no file (layout / geo / DRM). */
    fun probeArgs(): List<String> = listOf("-F", "--no-playlist")

    /** v1.6.1: args for a --version probe - the engine reports its real
     *  version in one fast line. Needed because YoutubeDL.version() only
     *  returns what updateYoutubeDL() stored in SharedPreferences, i.e. null
     *  for as long as the user never used ตรวจสอบเอนจิน/อัปเดตเอนจิน (field
     *  log 2026-10-07: three sessions of "engine_ready version=null" while
     *  every download worked). Never called while a download is running.
     */
    fun versionProbeArgs(): List<String> = listOf("--version")

    /** v1.6.1: extract "X.Y.Z" from a yt-dlp --version stdout line.
     *  Accepts a bare version, a "2026.10.22" calver and the python wrapper's
     *  "yt-dlp/2026.10.22" prefix form; ignores everything else. */
    fun parseVersionLine(line: String?): String? {
        val m = Regex("(?i)yt-dlp[/ ]?(\\d{4}\\.\\d{2}\\.\\d{2}(?:\\.\\d+)?)").find(line.orEmpty())
        if (m != null) return m.groupValues[1]
        return Regex("(?<!\\d)(\\d+\\.\\d+\\.\\d+(?:\\.\\d+)?)(?!\\d)").find(line.orEmpty())?.groupValues?.get(1)
    }

    /**
     * v1.3.5: app-private dir the engine writes into, under the app's
     * external-files root (Android/data/<pkg>/files/engine-work).
     * publishFile() then moves the finished file into Downloads/VDOGrabber
     * via the MediaStore API. Writing straight into the public Downloads dir
     * is denied on Q+ (scoped storage) - the "stream download done: null"
     * bug from the field log. Pure JVM so tests can pin the layout.
     */
    fun engineWorkDir(externalFilesDir: File): File = File(externalFilesDir, "engine-work")

    /**
     * Pull the first real yt-dlp ERROR (falling back to WARNING) out of a
     * stack-traced YoutubeDLException message, so toasts/logs show the actual
     * cause ("Unsupported URL: ...", "[Blob] You've asked yt-dlp ...")
     * instead of "com.yausername.youtubedl_android.YoutubeDLException: ...".
     */
    fun ytDlpErrorLine(raw: String?): String {
        val s = raw.orEmpty()
        if (s.isBlank()) return "unknown error"
        Regex("ERROR:\\s*(.+)").find(s)?.let { return it.groupValues[1].trim().take(200) }
        Regex("WARNING:\\s*(.+)").find(s)?.let { return it.groupValues[1].trim().take(200) }
        return s.lineSequence().firstOrNull { it.isNotBlank() }?.trim()
            ?.substringAfterLast(": ")?.take(200)
            ?: "unknown error"
    }

    /** Same output naming as the desktop app (app/core/downloader.py).
     *  Literal % is doubled so yt-dlp does not parse it as a template field. */
    fun outputTemplate(dir: File, titleBase: String = ""): String {
        val stem = sanitizeFilename(titleBase)
        return File(dir, if (stem.isNotEmpty()) stem.replace("%", "%%") + ".%(ext)s"
        else "%(title).120B [%(id)s].%(ext)s").absolutePath
    }

    /** Replace Windows/Android-invalid filename characters with spaces. */
    fun sanitizeFilename(name: String, maxLen: Int = 100): String {
        var s = name.replace(Regex("[<>:\"/\\\\|?*\\u0000-\\u001f]"), " ")
            .replace(Regex("\\s+"), " ").trim(' ', '.')
        if (s.length > maxLen) s = s.take(maxLen)
        // v1.3.3: ext4 allows 255 BYTES per filename component - Thai chars
        // take 3 bytes and emoji 4, so the old 100-CHAR cap alone made
        // DownloadManager.enqueue fail with "File name too long" on ad pages
        // with huge Thai titles. Cap the stem at 180 bytes (extension + the
        // " (2)" dedup suffix still fit under the limit).
        s = truncateUtf8Bytes(s, 180)
        return s.trimEnd(' ', '.')
    }

    /** Truncate to at most [maxBytes] UTF-8 bytes without cutting a character
     *  (or an emoji surrogate pair) in half. */
    fun truncateUtf8Bytes(s: String, maxBytes: Int): String {
        if (s.toByteArray(Charsets.UTF_8).size <= maxBytes) return s
        var bytes = 0
        var end = 0
        var i = 0
        while (i < s.length) {
            val c = s[i]
            val w = when {
                c.isHighSurrogate() && i + 1 < s.length && s[i + 1].isLowSurrogate() -> 4
                c.isHighSurrogate() || c.isLowSurrogate() -> 1 // lone half encodes as '?'
                c.code < 0x80 -> 1
                c.code < 0x800 -> 2
                else -> 3 // Thai, CJK, ...
            }
            if (bytes + w > maxBytes) break
            bytes += w
            i += if (w == 4) 2 else 1
            end = i
        }
        return s.substring(0, end)
    }

    /** Free name `stem.ext`; on collision returns `stem (2).ext`, `(3)`, ... */
    fun uniqueFileName(dir: File, stem: String, ext: String): String {
        val e = if (ext.startsWith(".")) ext else "." + ext.ifEmpty { "mp4" }
        if (!File(dir, "$stem$e").exists()) return "$stem$e"
        var n = 2
        while (File(dir, "$stem ($n)$e").exists()) n++
        return "$stem ($n)$e"
    }

    /** Map a media extension to the MIME type used when publishing to MediaStore. */
    fun mimeOf(ext: String): String = when (ext.lowercase()) {
        "mp4", "m3u8", "mpd" -> "video/mp4"
        "webm" -> "video/webm"
        "mkv" -> "video/x-matroska"
        "mov" -> "video/quicktime"
        "avi" -> "video/x-msvideo"
        "3gp" -> "video/3gpp"
        "flv" -> "video/x-flv"
        "ts" -> "video/mp2t"
        "mp3" -> "audio/mpeg"
        "m4a" -> "audio/mp4"
        "aac" -> "audio/aac"
        else -> "application/octet-stream"
    }

    /** Most recently written file in [dir] (the just-finished download). */
    fun newestFileIn(dir: File): File? =
        dir.listFiles()?.filter { it.isFile }?.maxByOrNull { it.lastModified() }

    /** v1.6.1: minimum gap between two rate-limited log writes. */
    const val PROGRESS_LOG_GAP_MS = 1000L

    /** v1.6.1: true for yt-dlp's per-fragment progress chatter ("[download]
     *  12.3% of ~ 17.81MiB ... ETA 00:03 (frag 45/283)") - the only lines
     *  allowed to be rate-limited. Destination/Merger/Deleting lines, the
     *  terminal "100% of" summary and retry notices ("Got error: HTTP 500...")
     *  always pass untouched; the last also survives being appended to a
     *  carriage-returned progress line, as seen in the 2026-10-07 field log. */
    fun isProgressLine(line: String): Boolean =
        Regex("""\[\w+]\s*\d""").containsMatchIn(line) &&
            !line.contains("100% of") && !line.contains("Got error")

    /** v1.6.1: shared rate limiter for noisy progress writes. [lastFireMs]
     *  is the caller's own last-fire timestamp (0 = never); first call always
     *  passes. Desktop keeps verbatim mirrors (rotating 5 MB x 5) but the
     *  Android trio rotates at 2 MB x 3, where one 4-job day flushed all
     *  history - field log 2026-10-07 showed 11k lines / 1.2 MB per session. */
    fun progressLogDue(lastFireMs: Long, nowMs: Long, gapMs: Long = PROGRESS_LOG_GAP_MS): Boolean =
        nowMs - lastFireMs >= gapMs
}
