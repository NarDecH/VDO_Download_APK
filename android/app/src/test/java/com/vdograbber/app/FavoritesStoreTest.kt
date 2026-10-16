package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * v1.9.21: JVM unit tests for the favorite-websites store backing the
 * เว็บโปรด tab and the ★ toolbar button. Pure JVM: the TSV round-trip
 * (URLEncoder escaping) and the file load/save both run without Android.
 */
class FavoritesStoreTest {

    private fun tmpDir(): File = java.nio.file.Files.createTempDirectory("vg-fav").toFile()

    @Test
    fun `serialize parse round-trips thai titles with tabs and newlines`() {
        val e = FavoritesStore.Entry(
            url = "https://xn--l3caaa1a0c3czc8gzc.com/คลิปหลุด/เย็ดสด-หลั/",
            title = "ชื่อมี\tแท็บ และ\nขึ้นบรรทัด 100%",
            savedAt = 1_792_129_375_000,
        )
        val back = FavoritesStore.parse(FavoritesStore.serialize(listOf(e)))
        assertEquals(1, back.size)
        assertEquals(e.url, back[0].url)
        assertEquals(e.title, back[0].title)
        assertEquals(e.savedAt, back[0].savedAt)
    }

    @Test
    fun `parse skips malformed lines instead of dying`() {
        val good = FavoritesStore.Entry("https://x/page", "P", 42L)
        val text = FavoritesStore.serialize(listOf(good)) +
            "\nonly-two\tfields" +
            "\nbad-timestamp\ttitle\tnot-a-number" +
            "\n\n"
        val back = FavoritesStore.parse(text)
        assertEquals(1, back.size)
        assertEquals("https://x/page", back[0].url)
    }

    @Test
    fun `file round-trip via init and add remove - newest first`() {
        val dir = tmpDir()
        try {
            FavoritesStore.init(dir)
            assertTrue(FavoritesStore.add("https://x/a", "A"))
            Thread.sleep(2)
            assertTrue(FavoritesStore.add("https://x/b", "B"))
            assertEquals(listOf("https://x/b", "https://x/a"), FavoritesStore.all().map { it.url })

            assertFalse(FavoritesStore.remove("https://x/nope"))
            assertTrue(FavoritesStore.remove("https://x/a"))
            assertEquals(listOf("https://x/b"), FavoritesStore.all().map { it.url })

            // remove-to-empty deletes the file (no stale store after cleanup)
            assertTrue(FavoritesStore.remove("https://x/b"))
            assertTrue(!File(dir, FavoritesStore.FILE_NAME).exists())
            assertEquals(0, FavoritesStore.all().size)
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `add dedupes by url - newest title wins and row moves to top`() {
        val dir = tmpDir()
        try {
            FavoritesStore.init(dir)
            FavoritesStore.add("https://x/a", "old title")
            Thread.sleep(2)
            FavoritesStore.add("https://x/b", "B")
            assertFalse(FavoritesStore.add("https://x/a", "new title"))
            val all = FavoritesStore.all()
            assertEquals(2, all.size)
            assertEquals("https://x/a", all[0].url)
            assertEquals("new title", all[0].title)
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test(expected = IllegalArgumentException::class)
    fun `add rejects a blank url`() {
        val dir = tmpDir()
        try {
            FavoritesStore.init(dir)
            FavoritesStore.add("  ", "A")
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `read survives a corrupt store file`() {
        val dir = tmpDir()
        try {
            FavoritesStore.init(dir)
            File(dir, FavoritesStore.FILE_NAME).writeText("garbage without tabs")
            assertEquals(0, FavoritesStore.all().size)
            // and the next add rewrites a healthy file
            FavoritesStore.add("https://x/a", "A")
            assertEquals(listOf("https://x/a"), FavoritesStore.all().map { it.url })
        } finally {
            dir.deleteRecursively()
        }
    }
}
