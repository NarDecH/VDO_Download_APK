package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for the MediaStore LRU behaviour - mirrors
 * scripts/test_units.py on the desktop side (no Android framework needed).
 */
class MediaStoreTest {

    private fun fill(n: Int) {
        for (i in 0 until n) {
            MediaStore.add("http://x/$i.mp4", "mp4", "t", "p", "T")
        }
    }

    @Test
    fun `holds up to limit and evicts oldest`() {
        fill(200)
        MediaStore.add("http://x/200.mp4", "mp4", "t", "p", "T")
        val urls = MediaStore.list().map { it.url }.toSet()
        assertEquals(200, urls.size)
        assertFalse("http://x/0.mp4" in urls)
        assertTrue("http://x/200.mp4" in urls)
    }

    @Test
    fun `re-detecting touches entry - LRU not FIFO`() {
        fill(200)
        // touch the OLDEST entry (0) -> it must survive the next eviction
        assertFalse(MediaStore.add("http://x/0.mp4", "mp4", "t", "p", "T"))
        MediaStore.add("http://x/200.mp4", "mp4", "t", "p", "T")
        val urls = MediaStore.list().map { it.url }.toSet()
        assertEquals(200, urls.size)
        assertTrue("touched entry must survive", "http://x/0.mp4" in urls)
        assertFalse("entry 1 is now the LRU victim", "http://x/1.mp4" in urls)
    }

    @Test
    fun `duplicate add returns false and keeps original item`() {
        assertTrue(MediaStore.add("http://x/a.mp4", "mp4", "first", "p", "T"))
        assertFalse(MediaStore.add("http://x/a.mp4", "mp4", "second", "p", "T"))
        assertEquals(1, MediaStore.list().size)
        assertEquals("first", MediaStore.list()[0].label)
    }

    @Test
    fun `clear empties the store`() {
        fill(10)
        MediaStore.clear()
        assertEquals(0, MediaStore.list().size)
    }
}
