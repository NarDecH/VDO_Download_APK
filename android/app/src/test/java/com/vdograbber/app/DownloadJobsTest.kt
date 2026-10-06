package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * v1.5.0: JVM unit tests for the active-stream-jobs registry that backs the
 * media sheet's "กำลังดาวน์โหลด" tab and the chip ticker. Pure JVM state -
 * no Android framework types involved.
 */
class DownloadJobsTest {

    @Before
    fun reset() = DownloadJobs.clear()

    @Test
    fun `register adds a job and active returns it oldest-first`() {
        val a = DownloadJobs.register("aaa", "http://x/a.m3u8", "A")
        Thread.sleep(2) // startedAt resolution
        val b = DownloadJobs.register("bbb", "http://x/b.m3u8", "B")
        assertEquals(2, DownloadJobs.count())
        assertEquals(listOf("aaa", "bbb"), DownloadJobs.active().map { it.pid })
        assertEquals("A", a.title)
        assertEquals("B", b.title)
    }

    @Test
    fun `percent is per job and currentPercent takes the max`() {
        DownloadJobs.register("aaa", "http://x/a.m3u8", "A")
        DownloadJobs.register("bbb", "http://x/b.m3u8", "B")
        assertEquals(-1, DownloadJobs.currentPercent()) // no report yet
        DownloadJobs.update("bbb", 42)
        assertEquals(42, DownloadJobs.get("bbb")?.percent)
        assertEquals(-1, DownloadJobs.get("aaa")?.percent) // untouched
        DownloadJobs.update("aaa", 10)
        assertEquals(42, DownloadJobs.currentPercent())
        DownloadJobs.update("bbb", 99)
        assertEquals(99, DownloadJobs.currentPercent())
    }

    @Test
    fun `remove drops only the finished job`() {
        DownloadJobs.register("aaa", "http://x/a.m3u8", "A")
        DownloadJobs.register("bbb", "http://x/b.m3u8", "B")
        DownloadJobs.remove("aaa")
        assertEquals(listOf("bbb"), DownloadJobs.active().map { it.pid })
        DownloadJobs.remove("missing") // no-op
        assertEquals(1, DownloadJobs.count())
    }

    @Test
    fun `markCancelled hides the job once and arms the one-shot flag`() {
        DownloadJobs.register("aaa", "http://x/a.m3u8", "A")
        assertTrue(DownloadJobs.markCancelled("aaa"))
        assertEquals(0, DownloadJobs.count()) // immediately gone from the tab
        assertTrue(DownloadJobs.consumeCancelled("aaa"))
        assertFalse("flag is one-shot", DownloadJobs.consumeCancelled("aaa"))
        // unknown / already-finished pid: nothing to cancel
        assertFalse(DownloadJobs.markCancelled("aaa"))
        assertFalse(DownloadJobs.consumeCancelled("nope"))
    }

    @Test
    fun `clear empties jobs and cancel flags (service destroyed)`() {
        DownloadJobs.register("aaa", "http://x/a.m3u8", "A")
        DownloadJobs.markCancelled("aaa")
        DownloadJobs.register("bbb", "http://x/b.m3u8", "B")
        DownloadJobs.clear()
        assertEquals(0, DownloadJobs.count())
        assertEquals(-1, DownloadJobs.currentPercent())
        assertFalse(DownloadJobs.consumeCancelled("aaa")) // flags wiped too
    }
}
