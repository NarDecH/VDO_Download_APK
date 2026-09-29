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
}
