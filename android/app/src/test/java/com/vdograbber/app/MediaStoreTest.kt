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

    // -------------------------------------- v1.4.0 origin reset (user request)

    @Test
    fun `originOf parses scheme host and port`() {
        assertEquals("https://x.com", MediaStore.originOf("https://x.com/a/b?c#d"))
        assertEquals("https://x.com:8443", MediaStore.originOf("HTTPS://X.com:8443/a"))
        assertEquals("http://x.com", MediaStore.originOf("http://x.com:80/a"))
        assertEquals("http://x.com:8080", MediaStore.originOf("http://x.com:8080"))
        assertEquals("", MediaStore.originOf("blob:https://x.com/1"))
        assertEquals("", MediaStore.originOf("about:blank"))
    }

    @Test
    fun `main-frame navigation across origins clears the list`() {
        MediaStore.clear()
        MediaStore.onMainFrameNavigate("https://site-a.example/page1")
        fill(3)
        assertEquals(3, MediaStore.list().size)
        // same-origin navigation keeps the detections (multi-page players)
        assertEquals(0, MediaStore.onMainFrameNavigate("https://site-a.example/page2"))
        assertEquals(3, MediaStore.list().size)
        // crossing to another origin wipes them and returns the count
        assertEquals(3, MediaStore.onMainFrameNavigate("https://site-b.example/"))
        assertEquals(0, MediaStore.list().size)
        // repeated loads of the same new page stay armed and keep it empty
        assertEquals(0, MediaStore.onMainFrameNavigate("https://site-b.example/"))
        MediaStore.clear()
    }

    @Test
    fun `non-http main frame keeps the current list`() {
        MediaStore.clear()
        MediaStore.onMainFrameNavigate("https://site-a.example/")
        fill(2)
        // error pages / about:blank must not silently wipe detections
        assertEquals(0, MediaStore.onMainFrameNavigate("about:blank"))
        assertEquals(2, MediaStore.list().size)
        MediaStore.clear()
    }
}
