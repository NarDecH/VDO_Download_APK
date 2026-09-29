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
    fun `newest file wins`() {
        val dir = java.nio.file.Files.createTempDirectory("vg").toFile()
        val old = File(dir, "old.mp4").apply { writeText("a"); setLastModified(1_000_000) }
        val new = File(dir, "new.mp4").apply { writeText("bb"); setLastModified(9_000_000_000) }
        assertEquals(new, StreamArgs.newestFileIn(dir))
        old.delete(); new.delete(); dir.delete()
    }
}
