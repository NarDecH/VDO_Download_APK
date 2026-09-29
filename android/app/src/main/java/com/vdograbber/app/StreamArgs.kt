package com.vdograbber.app

import java.io.File

/**
 * Pure helpers for the on-device yt-dlp engine (StreamDownloadService).
 * Deliberately free of Android types so it is unit-testable on the JVM -
 * mirrors scripts/test_units.py on the desktop side.
 */
object StreamArgs {

    /** yt-dlp options for merging into [dir] with the desktop naming scheme. */
    fun optionsArgs(dir: File): List<String> = listOf(
        "--no-playlist",
        "--no-mtime",
        "-o", outputTemplate(dir),
    )

    /** Same output naming as the desktop app (app/core/downloader.py). */
    fun outputTemplate(dir: File): String =
        File(dir, "%(title).120B [%(id)s].%(ext)s").absolutePath

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
