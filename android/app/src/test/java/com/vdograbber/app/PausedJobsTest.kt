package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * v1.7.0: JVM unit tests for the persisted paused-jobs registry backing the
 * หยุดพักไว้ section and the ดาวน์โหลดต่อ flow. Pure JVM: the TSV round-trip
 * (URLEncoder escaping) and the file load/save both run without Android.
 */
class PausedJobsTest {

    private fun tmpDir(): File = java.nio.file.Files.createTempDirectory("vg-paused").toFile()

    @Test
    fun `serialize parse round-trips thai titles with tabs and newlines`() {
        val e = PausedJobs.Entry(
            titleBase = "คลิปหลุด ไทย (2)",
            url = "https://merrylion2.com/abc/output.mpd",
            title = "ชื่อมี\tแท็บ และ\nขึ้นบรรทัด 100%",
            savedAt = 1_791_263_659_734,
        )
        val back = PausedJobs.parse(PausedJobs.serialize(listOf(e)))
        assertEquals(1, back.size)
        assertEquals(e.titleBase, back[0].titleBase)
        assertEquals(e.url, back[0].url)
        assertEquals(e.title, back[0].title)
        assertEquals(e.savedAt, back[0].savedAt)
    }

    @Test
    fun `parse skips malformed lines instead of dying`() {
        val good = PausedJobs.Entry("stem", "https://x/v.mpd", "V", 42L)
        val text = PausedJobs.serialize(listOf(good)) +
            "\nonly-two\tfields" +
            "\nbad-timestamp\turl\ttitle\tnot-a-number" +
            "\n\n"
        val back = PausedJobs.parse(text)
        assertEquals(1, back.size)
        assertEquals("stem", back[0].titleBase)
    }

    @Test
    fun `file round-trip via init and add remove`() {
        val dir = tmpDir()
        try {
            PausedJobs.init(dir)
            PausedJobs.add("https://x/a.mpd", "A", "stem-a")
            Thread.sleep(2)
            PausedJobs.add("https://x/b.mpd", "B", "stem-b")
            assertEquals(listOf("stem-a", "stem-b"), PausedJobs.all().map { it.titleBase })

            // a second process would read the same file (no in-memory cache)
            assertEquals(2, PausedJobs.all().size)
            PausedJobs.remove("stem-a")
            assertEquals(listOf("stem-b"), PausedJobs.all().map { it.titleBase })

            // remove-to-empty deletes the file (no stale registry after cleanup)
            PausedJobs.remove("stem-b")
            assertTrue(!File(dir, PausedJobs.FILE_NAME).exists())
            assertEquals(0, PausedJobs.all().size)
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `add dedupes by titleBase - newest record wins`() {
        val dir = tmpDir()
        try {
            PausedJobs.init(dir)
            PausedJobs.add("https://x/a.mpd", "old title", "stem")
            PausedJobs.add("https://x/a.mpd", "new title", "stem")
            val all = PausedJobs.all()
            assertEquals(1, all.size)
            assertEquals("new title", all[0].title)
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test(expected = IllegalArgumentException::class)
    fun `add rejects a blank titleBase (resume would restart from zero)`() {
        val dir = tmpDir()
        try {
            PausedJobs.init(dir)
            PausedJobs.add("https://x/a.mpd", "A", "  ")
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `read survives a corrupt registry file`() {
        val dir = tmpDir()
        try {
            PausedJobs.init(dir)
            File(dir, PausedJobs.FILE_NAME).writeText("garbage without tabs")
            assertEquals(0, PausedJobs.all().size)
            // and the next add rewrites a healthy file
            PausedJobs.add("https://x/a.mpd", "A", "stem")
            assertEquals(listOf("stem"), PausedJobs.all().map { it.titleBase })
        } finally {
            dir.deleteRecursively()
        }
    }
}
