package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * JVM unit tests for the on-device yt-dlp command helpers used by
 * StreamDownloadService (docs/plan-android-hls.md step 3).
 */
class StreamArgsTest {

    @Test
    fun `output template matches desktop naming scheme`() {
        val t = StreamArgs.outputTemplate(File("/tmp", "x"))
        assertTrue(t.endsWith("%(title).120B [%(id)s].%(ext)s"))
        assertTrue(t.contains(File.separator))
    }

    @Test
    fun `options include no-playlist and output template`() {
        val args = StreamArgs.optionsArgs(File("/tmp"))
        assertTrue("--no-playlist" in args)
        assertTrue("--no-mtime" in args)
        val i = args.indexOf("-o")
        assertEquals("-o", args[i])
        assertTrue("the element after -o must be the output template",
            args[i + 1].endsWith("%(title).120B [%(id)s].%(ext)s"))
        assertTrue("-P" !in args) // output path travels inside the -o template
    }

    @Test
    fun `mime mapping covers video and audio extensions`() {
        assertEquals("video/mp4", StreamArgs.mimeOf("mp4"))
        assertEquals("video/x-matroska", StreamArgs.mimeOf("mkv"))
        assertEquals("audio/mpeg", StreamArgs.mimeOf("mp3"))
        assertEquals("application/octet-stream", StreamArgs.mimeOf("xyz"))
        assertEquals("video/webm", StreamArgs.mimeOf("WEBM")) // case-insensitive
    }

    @Test
    fun `output template with title base names the file after the title`() {
        val t = StreamArgs.outputTemplate(File("/tmp"), "My Clip")
        assertTrue(t.endsWith("My Clip.%(ext)s"))
        // literal % must be doubled for yt-dlp
        val pct = StreamArgs.outputTemplate(File("/tmp"), "100% Cool")
        assertTrue(pct.endsWith("100%% Cool.%(ext)s"))
        // empty title falls back to yt-dlp metadata naming
        val legacy = StreamArgs.outputTemplate(File("/tmp"), "")
        assertTrue(legacy.endsWith("%(title).120B [%(id)s].%(ext)s"))
    }

    @Test
    fun `sanitize filename strips invalid chars and trims`() {
        assertEquals("bad name .mp4", StreamArgs.sanitizeFilename("bad:name?.mp4"))
        assertEquals("end", StreamArgs.sanitizeFilename("  end. "))
        assertEquals("A B C", StreamArgs.sanitizeFilename("A<B>C"))
        assertEquals("ทดสอบ clip", StreamArgs.sanitizeFilename(" ทดสอบ clip "))
        assertEquals("x".repeat(100), StreamArgs.sanitizeFilename("x".repeat(250)))
        assertEquals("", StreamArgs.sanitizeFilename(""))
    }

    @Test
    fun `sanitize filename caps UTF-8 bytes not chars (file name too long fix)`() {
        // 100 Thai chars pass the old 100-char cap but are 300 UTF-8 bytes -
        // beyond the 255-byte ext4 limit -> DownloadManager threw
        // "java.io.IOException: File name too long"
        val capped = StreamArgs.sanitizeFilename("ก".repeat(100))
        assertTrue("Thai chars are 3 bytes each - the byte cap must bite",
            capped.toByteArray(Charsets.UTF_8).size <= 180)
        assertTrue(capped.length < 100)
        // no partial characters: the result is stable under re-truncation
        assertEquals(capped, StreamArgs.truncateUtf8Bytes(capped, 180))

        // emoji = 4 bytes per surrogate pair - never cut in half
        val cappedEmoji = StreamArgs.sanitizeFilename("👍".repeat(80))
        assertTrue(cappedEmoji.toByteArray(Charsets.UTF_8).size <= 180)
        assertTrue(cappedEmoji.length % 2 == 0) // whole pairs only
        assertEquals("👍", cappedEmoji.takeLast(2)) // a full pair, not a split half

        // short names are untouched
        assertEquals("clip", StreamArgs.sanitizeFilename("clip"))
    }

    @Test
    fun `truncateUtf8Bytes never splits characters`() {
        assertEquals("aก", StreamArgs.truncateUtf8Bytes("aกา", 4)) // a=1 + ก=3; า does not fit
        assertEquals("", StreamArgs.truncateUtf8Bytes("ก", 2))
        assertEquals("ก", StreamArgs.truncateUtf8Bytes("กา", 3))
        assertEquals("a👍", StreamArgs.truncateUtf8Bytes("a👍ก", 5)) // 1 + 4; ก does not fit
        assertEquals("abcdef", StreamArgs.truncateUtf8Bytes("abcdef", 100))
    }

    @Test
    fun `unique file name dedupes like a browser`() {
        val dir = java.nio.file.Files.createTempDirectory("vg-dedupe").toFile()
        try {
            assertEquals("clip.mp4", StreamArgs.uniqueFileName(dir, "clip", "mp4"))
            File(dir, "clip.mp4").createNewFile()
            assertEquals("clip (2).mp4", StreamArgs.uniqueFileName(dir, "clip", "mp4"))
            File(dir, "clip (2).mp4").createNewFile()
            assertEquals("clip (3).mp4", StreamArgs.uniqueFileName(dir, "clip", "mp4"))
            // other stems/extensions unaffected
            assertEquals("clip.webm", StreamArgs.uniqueFileName(dir, "clip", "webm"))
            assertEquals("other.mp4", StreamArgs.uniqueFileName(dir, "other", "mp4"))
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `newest file wins`() {
        val dir = java.nio.file.Files.createTempDirectory("vg").toFile()
        val old = File(dir, "old.mp4").apply { writeText("a"); setLastModified(1_000_000) }
        val new = File(dir, "new.mp4").apply { writeText("bb"); setLastModified(9_000_000_000) }
        assertEquals(new, StreamArgs.newestFileIn(dir))
        old.delete(); new.delete(); dir.delete()
    }

    // ---- v1.6.1: engine version probe (field log: engine_ready v=null) ----

    @Test
    fun `parseVersionLine reads bare and wrapper forms`() {
        assertEquals("2026.10.22", StreamArgs.parseVersionLine("2026.10.22"))
        assertEquals("2026.10.22", StreamArgs.parseVersionLine("yt-dlp/2026.10.22"))
        assertEquals("2026.09.14", StreamArgs.parseVersionLine("yt-dlp 2026.09.14"))
        assertEquals("1.2.3", StreamArgs.parseVersionLine("1.2.3"))
        assertEquals("2026.10.22.1", StreamArgs.parseVersionLine("yt-dlp/2026.10.22.1"))
        assertEquals(null, StreamArgs.parseVersionLine(""))
        assertEquals(null, StreamArgs.parseVersionLine("no digits here"))
        assertEquals(null, StreamArgs.parseVersionLine(null))
    }

    @Test
    fun `version probe args ask the engine itself`() {
        assertEquals(listOf("--version"), StreamArgs.versionProbeArgs())
    }

    // ---- v1.6.1: progress log throttle (field log: 11k lines / 1.2 MB per day) ----

    @Test
    fun `only per-fragment chatter is a progress line`() {
        assertTrue(StreamArgs.isProgressLine(
            "[download]   12.3% of ~  17.81MiB at  376.70KiB/s ETA 00:03 (frag 45/283)"))
        // retry notices are NOT chatter - they pass verbatim for diagnosis,
        // even when glued to a carriage-returned progress line
        assertTrue(!StreamArgs.isProgressLine(
            "[download] Got error: HTTP Error 500: Internal Server Error. Retrying (1/10)..."))
        assertTrue(!StreamArgs.isProgressLine(
            "[download]  73.9% of ~  17.81MiB at  376.70KiB/s ETA 00:03 (frag 210/283)  [download] Got error: HTTP Error 500: Internal Server Error. Retrying (1/10)..."))
        // terminal summary is NEVER rate-limited
        assertTrue(!StreamArgs.isProgressLine(
            "[download] 100% of  239.80MiB in 00:02:44 at 1.46MiB/s"))
        // structural lines are never rate-limited
        assertTrue(!StreamArgs.isProgressLine(
            "[download] Destination: /data/engine-work/clip.f0.mp4"))
        assertTrue(!StreamArgs.isProgressLine(
            "[Merger] Merging formats into \"/data/engine-work/clip.mp4\""))
        assertTrue(!StreamArgs.isProgressLine(
            "Deleting original file /data/engine-work/clip.f0.mp4 (pass -k to keep)"))
    }

    @Test
    fun `progress log limiter keeps a 1s gap and never blocks the first write`() {
        val gap = StreamArgs.PROGRESS_LOG_GAP_MS
        assertTrue("first write always passes", StreamArgs.progressLogDue(0L, 10_000L))
        assertTrue("within the gap is blocked", !StreamArgs.progressLogDue(10_000L, 10_000L + gap - 1))
        assertTrue("after the gap passes", StreamArgs.progressLogDue(10_000L, 10_000L + gap))
        // custom gap also starts open
        assertTrue(StreamArgs.progressLogDue(0L, 5L, gapMs = 3L))
        assertTrue(!StreamArgs.progressLogDue(5L, 6L, gapMs = 3L))
    }
}
