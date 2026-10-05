package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * v1.3.4 tests for the ".mp4 that is really an HTML page" bug: a player/embed
 * URL (no media extension) used to go straight to DownloadManager, which saved
 * the HTML of the player as title.mp4. Coverage: routeOf (page URLs must go to
 * the yt-dlp engine) and looksLikeHtml (the safety-net magic-byte check).
 */
class HtmlDetectorTest {

    // ------------------------------------------------------------- routeOf

    @Test
    fun `page urls without media extension route to the engine`() {
        assertEquals(Downloader.Route.PAGE, Downloader.routeOf("https://site.com/player/123"))
        assertEquals(Downloader.Route.PAGE, Downloader.routeOf("https://site.com/embed?v=abc"))
        assertEquals(Downloader.Route.PAGE, Downloader.routeOf("https://site.com/player.html"))
        // reported bug: the DASH player page whose output.mpd lives on
        // merrylion2.com - the page URL itself has no media extension
        assertEquals(Downloader.Route.PAGE, Downloader.routeOf("https://player.example/e/abc123"))
    }

    @Test
    fun `manifest urls route to the stream engine`() {
        assertEquals(Downloader.Route.STREAM, Downloader.routeOf("https://merrylion2.com/ohudo36m3d/output.mpd"))
        assertEquals(Downloader.Route.STREAM, Downloader.routeOf("https://c/v/index.m3u8?tok=1"))
    }

    @Test
    fun `direct media files keep the DownloadManager route`() {
        assertEquals(Downloader.Route.DIRECT_FILE, Downloader.routeOf("https://c/v/clip.mp4?tok=1"))
        assertEquals(Downloader.Route.DIRECT_FILE, Downloader.routeOf("https://c/v/song.M4A"))
        assertEquals(Downloader.Route.DIRECT_FILE, Downloader.routeOf("https://c/v/movie.mkv"))
    }

    // -------------------------------------------------------- looksLikeHtml

    private fun head(s: String): ByteArray = s.toByteArray(Charsets.UTF_8)

    @Test
    fun `the reported player page is detected as html`() {
        val player = """<!DOCTYPE html>
<html lang="en">
<head><title>Player</title>
<script src="https://cdn.dashjs.org/latest/dash.all.min.js"></script></head>
<body><video id="videoPlayer" controls autoplay></video>
<script>player.initialize(document.querySelector("#videoPlayer"),
"https://merrylion2.com/ohudo36m3d/output.mpd", true);</script></body></html>"""
        assertTrue(Downloader.looksLikeHtml(head(player)))
    }

    @Test
    fun `html variants with bom and leading whitespace are detected`() {
        assertTrue(Downloader.looksLikeHtml(head("<!doctype html><html><body>x</body></html>")))
        assertTrue(Downloader.looksLikeHtml(head("  \r\n\t<!-- comments follow --><!DOCTYPE HTML>")))
        assertTrue(Downloader.looksLikeHtml(byteArrayOf(0xEF.toByte(), 0xBB.toByte(), 0xBF.toByte()) + head("<!DOCTYPE html>")))
        assertTrue(Downloader.looksLikeHtml(head("<?xml version=\"1.0\"?><rss/>")))
        assertTrue(Downloader.looksLikeHtml(head("<HTML>UPPERCASE</HTML>")))
    }

    @Test
    fun `real media magic bytes are not html`() {
        // mp4: ftyp box (size 0x00000018)
        assertFalse(
            Downloader.looksLikeHtml(
                byteArrayOf(0, 0, 0, 0x18, 'f'.code.toByte(), 't'.code.toByte(), 'y'.code.toByte(), 'p'.code.toByte()),
            ),
        )
        // webm/matroska EBML header
        assertFalse(
            Downloader.looksLikeHtml(
                byteArrayOf(0x1A, 0x45, 0xDF.toByte(), 0xA3.toByte()),
            ),
        )
        // mpeg-ts (common for streamed segments saved directly)
        assertFalse(Downloader.looksLikeHtml(byteArrayOf(0x47, 0x40, 0x11, 0x10)))
        // id3/mp3
        assertFalse(Downloader.looksLikeHtml(head("ID3\u0003\u0000")))
        // short/binary garbage must never be mistaken for a page
        assertFalse(Downloader.looksLikeHtml(head("RIFF....")))
        assertFalse(Downloader.looksLikeHtml(ByteArray(0)))
    }
}
