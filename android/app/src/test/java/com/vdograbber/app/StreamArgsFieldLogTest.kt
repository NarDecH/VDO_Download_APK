package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * v1.3.5 regression tests from the field log (2026-10-05, Android 16,
 * 23113RKC6G):
 *  1. "stream download done: null" - the engine wrote into the public
 *     Downloads dir, which scoped storage denies on Q+; the unit args must
 *     now point at an app-private dir (StreamDownloadService.onCreate).
 *  2. blob: URLs offered to the yt-dlp engine ("[Blob] ... only locally in
 *     your browser") - they must be filtered before any engine call.
 *  3. raw stack-traced YoutubeDLException strings shown to the user instead
 *     of the real cause ("Unsupported URL: ...").
 */
class StreamArgsFieldLogTest {

    // ------------------- 1. engine writes into an app-private dir -------------------

    @Test
    fun `engine output dir is app-private and the service uses it`() {
        val files = java.io.File("data/user/0/com.vdograbber.app/files")
        val dir = StreamArgs.engineWorkDir(files)
        assertEquals("files", dir.parentFile?.name)
        assertEquals("engine-work", dir.name)
        // app-private storage: Android/data/<pkg>/files/engine-work - never
        // the shared public Downloads dir (scoped-storage "done: null" bug)
        assertTrue("engine work dir must NOT be the public Downloads dir",
            !dir.name.contains("Download", ignoreCase = true))
    }

    @Test
    fun `service output template targets the engine work dir`() {
        val dir = StreamArgs.engineWorkDir(java.io.File("data/data/x/files"))
        val t = StreamArgs.outputTemplate(dir, "คลิปทดสอบ")
        assertTrue(t.startsWith(dir.absolutePath))
        assertTrue(t.endsWith("คลิปทดสอบ.%(ext)s"))
    }

    // ------------------------- 2. blob: URLs never reach yt-dlp ----------------------

    @Test
    fun `blob urls are rejected before routing`() {
        val blob = "blob:https://merrylion2.com/d9f19fc9-b55c-423b-8ec7-b7e51d641866"
        assertFalse(Downloader.isUsableDownloadUrl(blob))
        assertTrue(Downloader.isUsableDownloadUrl("https://merrylion2.com/ohudo36m3d/output.mpd"))
        assertTrue(Downloader.isUsableDownloadUrl("https://c/v/clip.mp4"))
    }

    @Test
    fun `blob urls get no route at all`() {
        val blob = "blob:https://x/y"
        // must not be classified as STREAM/DIRECT_FILE/PAGE - callers bail out
        assertTrue(Downloader.routeOf(blob) != Downloader.Route.STREAM)
        assertTrue(Downloader.routeOf(blob) != Downloader.Route.DIRECT_FILE)
        assertTrue(Downloader.routeOf(blob) != Downloader.Route.PAGE)
    }

    // ----------------------- 3. real cause lines from yt-dlp ------------------------

    @Test
    fun `error line is extracted from the field-log exception`() {
        val raw = "com.yausername.youtubedl_android.YoutubeDLException: " +
            "WARNING: [generic] Falling back on generic information extractor\n" +
            "ERROR: Unsupported URL: https://merrylion2.com/ohudo36m3d/player.html\n"
        assertEquals(
            "Unsupported URL: https://merrylion2.com/ohudo36m3d/player.html",
            StreamArgs.ytDlpErrorLine(raw),
        )
    }

    @Test
    fun `blob error is extracted in full`() {
        val raw = "com.yausername.youtubedl_android.YoutubeDLException: ERROR: [Blob] " +
            "You've asked yt-dlp to download a blob URL. A blob URL exists only locally in your browser. " +
            "It is not possible for yt-dlp to access it."
        val line = StreamArgs.ytDlpErrorLine(raw)
        assertTrue(line.startsWith("[Blob]"))
        assertTrue(line.contains("only locally in your browser"))
    }

    @Test
    fun `warning is used when there is no error line`() {
        assertEquals(
            "[generic] Falling back on generic information extractor",
            StreamArgs.ytDlpErrorLine(
                "YoutubeDLException: WARNING: [generic] Falling back on generic information extractor",
            ),
        )
    }

    @Test
    fun `plain exception falls back to the last cause segment`() {
        assertEquals("Connection refused", StreamArgs.ytDlpErrorLine("java.net.ConnectException: Connection refused"))
        assertEquals("unknown error", StreamArgs.ytDlpErrorLine(null))
        assertEquals("unknown error", StreamArgs.ytDlpErrorLine(""))
        assertEquals("unknown error", StreamArgs.ytDlpErrorLine("   "))
    }

    @Test
    fun `probe args are cheap and playlist-free`() {
        val args = StreamArgs.probeArgs()
        assertTrue("-F" in args)
        assertTrue("--no-playlist" in args)
    }
}
