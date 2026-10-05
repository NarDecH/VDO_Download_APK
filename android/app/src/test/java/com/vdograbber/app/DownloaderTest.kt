package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for Downloader's pure name helpers (v1.1.7) - the direct
 * DownloadManager path. Mirrors the desktop tests in scripts/test_units.py.
 */
class DownloaderTest {

    @Test
    fun `extOf sniffs known media extensions`() {
        assertEquals("mp4", Downloader.extOf("https://c/v/clip.mp4"))
        assertEquals("m3u8", Downloader.extOf("https://c/v/index.m3u8?tok=1"))
        assertEquals("webm", Downloader.extOf("https://c/v/clip.WEBM#t=5"))
        assertEquals("mpd", Downloader.extOf("https://c/v/output.mpd"))
        assertEquals("", Downloader.extOf("https://site.com/watch/123"))
        assertEquals("", Downloader.extOf("https://c/v/clip.mp4x"))
    }

    @Test
    fun `isStream only for m3u8 and mpd`() {
        assertTrue(Downloader.isStream("https://c/v/index.m3u8?tok=1"))
        assertTrue(Downloader.isStream("https://c/v/output.mpd"))
        assertFalse(Downloader.isStream("https://c/v/clip.mp4"))
        assertFalse(Downloader.isStream("https://site.com/player.html"))
    }

    @Test
    fun `urlStem strips query fragment and path`() {
        assertEquals("clip.mp4", Downloader.urlStem("https://c/v/clip.mp4?tok=1#t=3"))
        assertEquals("video.bin", Downloader.urlStem("https://c/v/video.bin"))
        assertEquals("", Downloader.urlStem("https://c/v/"))
    }

    @Test
    fun `displayName prefers the sanitized page title`() {
        assertEquals("Travel Blog.mp4", Downloader.displayName("https://c/v/clip.mp4", "Travel Blog"))
        // the extension follows the URL (m3u8), not a hard-coded mp4
        assertEquals("Live TV.m3u8", Downloader.displayName("https://c/v/index.m3u8?tok=1", "Live TV"))
        // invalid chars are sanitized like the desktop app; trailing spaces
        // are trimmed, so the extension lands right after the stem
        assertEquals("bad name.mp4", Downloader.displayName("https://c/v/clip.mp4", "bad:name?"))
    }

    @Test
    fun `displayName falls back to the url file name`() {
        assertEquals("clip.mp4", Downloader.displayName("https://c/v/clip.mp4?x", ""))
        // url filename without extension gets one appended
        assertEquals("clip.mp4", Downloader.displayName("https://c/v/clip?x", ""))
    }

    @Test
    fun `humanSize formats bytes readably`() {
        assertEquals("0 B", Downloader.humanSize(0))
        assertEquals("512 B", Downloader.humanSize(512))
        assertEquals("1.0 KB", Downloader.humanSize(1024))
        assertEquals("1.5 MB", Downloader.humanSize((1.5 * 1048576).toLong()))
        assertEquals("1.0 GB", Downloader.humanSize(1L shl 30))
    }

    @Test
    fun `displayName caps byte length for huge Thai titles (file name too long fix)`() {
        // v1.3.2 bug: a long Thai page title became a >255-byte filename and
        // DownloadManager.enqueue failed with "File name too long"
        val huge = "หนังเต็มเรื่องดูฟรี".repeat(40)
        val name = Downloader.displayName("https://c/v/clip.mp4", huge)
        assertTrue("ext4 caps a name at 255 bytes",
            name.toByteArray(Charsets.UTF_8).size <= 200)
        assertEquals(".mp4", name.takeLast(4))

        // a long signed URL path segment (used as fallback) is capped too
        val longUrl = "https://c/v/" + "s1-TH-hd-1080p-ก".repeat(30) + ".mp4?tok=1"
        val name2 = Downloader.displayName(longUrl, "")
        assertTrue(name2.toByteArray(Charsets.UTF_8).size <= 200)
    }

    @Test
    fun `displayName url-stem fallback and last resort`() {
        // page-style URL without a media extension: the URL's last segment is
        // used (same rule as the pre-1.1.6 DownloadManager naming)
        assertEquals("1.mp4", Downloader.displayName("https://site.com/watch/1", ""))
        // empty path (no file name at all) falls back to "video"
        assertEquals("video.mp4", Downloader.displayName("https://c/v/", ""))
        // direct manifest URL keeps its file name + media extension
        assertEquals("index.m3u8", Downloader.displayName("https://c/v/index.m3u8", ""))
    }
}
