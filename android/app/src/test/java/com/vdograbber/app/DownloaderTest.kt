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
        assertEquals("Live TV.mp4", Downloader.displayName("https://c/v/index.m3u8?tok=1", "Live TV"))
        // invalid chars are sanitized like the desktop app
        assertEquals("bad name .mp4", Downloader.displayName("https://c/v/clip.mp4", "bad:name?"))
    }

    @Test
    fun `displayName falls back to the url file name`() {
        assertEquals("clip.mp4", Downloader.displayName("https://c/v/clip.mp4?x", ""))
        // url filename without extension gets one appended
        assertEquals("clip.mp4", Downloader.displayName("https://c/v/clip?x", ""))
    }

    @Test
    fun `displayName last resort is video with url extension`() {
        assertEquals("video.mp4", Downloader.displayName("https://site.com/watch/1", ""))
        assertEquals("video.m3u8", Downloader.displayName("https://c/v/index.m3u8", ""))
    }
}
