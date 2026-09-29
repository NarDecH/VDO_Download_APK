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
        if (s.length > maxLen) s = s.take(maxLen).trimEnd(' ', '.')
        return s
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
}
