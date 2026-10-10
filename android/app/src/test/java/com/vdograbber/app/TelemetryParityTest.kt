package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * v1.9.9 telemetry parity tests (desktop twin in scripts/test_units.py:
 * eventlog_origin_tagging / analyze_events_origin).
 *
 * FileLog.event must stamp origin on every structured event, and
 * ytDlpErrorLine must classify real yt-dlp error shapes into the `reason`
 * field of download_error events (comparable across sessions).
 */
class TelemetryParityTest {

    @Test
    fun `event defaults to origin app`() {
        FileLog.eventOrigin = "app"
        val rec = FileLog.captureEvent("media_found", mapOf("url" to "https://x/a.mp4"))
        assertEquals("app", rec.optString("origin"))
        assertEquals("media_found", rec.optString("event"))
    }

    @Test
    fun `selftest origin is taggable and reversible`() {
        FileLog.eventOrigin = "selftest"
        val tagged = FileLog.captureEvent("download_start", mapOf("url" to "http://127.0.0.1:1/v.mp4"))
        assertEquals("selftest", tagged.optString("origin"))
        FileLog.eventOrigin = "app"
        val back = FileLog.captureEvent("page_loaded")
        assertEquals("app", back.optString("origin"))
    }

    @Test
    fun `explicit origin in data wins over the global tag`() {
        FileLog.eventOrigin = "app"
        val rec = FileLog.captureEvent("download_error", mapOf("origin" to "selftest"))
        assertEquals("selftest", rec.optString("origin"))
    }

    @Test
    fun `reason parsing covers real yt-dlp error shapes`() {
        // plain unsupported URL (the field-log case)
        assertEquals(
            "Unsupported URL: https://site/player.html",
            StreamArgs.ytDlpErrorLine(
                "com.yausername.youtubedl_android.YoutubeDLException: ERROR: Unsupported URL: https://site/player.html",
            ).trim(),
        )
        // blob refusal (v1.3.6 case)
        assertTrue(
            StreamArgs.ytDlpErrorLine("ERROR: [Blob] Only locally in your browser")
                .contains("[Blob]"),
        )
        // HTTP 403 forbidden media
        assertTrue(StreamArgs.ytDlpErrorLine("ERROR: unable to download video data: HTTP Error 403: Forbidden")
            .contains("403"))
        // DRM message keeps its content
        assertTrue(StreamArgs.ytDlpErrorLine("ERROR: This video is DRM protected")
            .contains("DRM"))
        // multi-line stack traces still pick the ERROR line, not the wrapper
        val multi = "Traceback (most recent call last):\n  File \"x\"\nYoutubeDLException: ERROR: Sign in to confirm your age"
        assertTrue(StreamArgs.ytDlpErrorLine(multi).startsWith("Sign in to confirm"))
        // empty/blank degrades to a stable string, never a crash
        assertEquals("unknown error", StreamArgs.ytDlpErrorLine(null))
        assertEquals("unknown error", StreamArgs.ytDlpErrorLine("   "))
    }

    @Test
    fun `reason must not carry the wrapper class name`() {
        val cause = StreamArgs.ytDlpErrorLine(
            "com.yausername.youtubedl_android.YoutubeDLException: ERROR: Unsupported URL: abc",
        )
        assertFalse(cause.contains("YoutubeDLException"))
        assertTrue(cause.startsWith("Unsupported URL"))
    }
}
