package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for the v1.9.3 stuck-download watchdog helpers
 * (field-log lesson: cdend.com direct downloads that were enqueued and
 * then never started - v1.9.2 made them visible, v1.9.3 makes them
 * testable and retryable).
 */
class WatchStaleJobsTest {

    private fun fresh(url: String, queuedAt: Long, id: Long) {
        Downloader.putPending(id, url to "t", queuedAtMs = queuedAt)
    }

    @Test
    fun `staleDirectJobs selects and removes only jobs past the threshold`() {
        val now = 10_000L
        fresh("https://x/a.mp4", queuedAt = now - 130_000, id = 1) // stale (2min)
        fresh("https://x/b.mp4", queuedAt = now - 60_000, id = 2)  // fresh
        fresh("https://x/c.mp4", queuedAt = now - 121_000, id = 3) // stale (barely)

        val stale = Downloader.staleDirectJobs(thresholdMs = 120_000, nowMs = now)
        assertEquals(listOf(1L to "https://x/a.mp4", 3L to "https://x/c.mp4"), stale)

        // selected entries are forgotten: a second pass sees only the fresh one
        val again = Downloader.staleDirectJobs(thresholdMs = 120_000, nowMs = now)
        assertEquals(emptyList<Pair<Long, String>>(), again)
        assertEquals("https://x/b.mp4" to "t", Downloader.pendingFor(2))
    }

    @Test
    fun `staleDirectJobs returns the url so the retry UI can re-enqueue`() {
        val now = 50_000L
        fresh("https://cdend.com/c2FnYW1lc2FnYW1lc2FnYW1lc2FnYW1l/panama888.mp4", now - 180_000, 9)
        val stale = Downloader.staleDirectJobs(thresholdMs = 120_000, nowMs = now)
        assertEquals(1, stale.size)
        assertTrue(stale[0].second.startsWith("https://cdend.com/"))
    }

    @Test
    fun `watchStaleJobs forgets its own checked stamps - idempotent offer per job`() {
        // the offer/retry loop is in MainActivity (needs Context); the unit
        // contract here is that staleDirectJobs CONSUMES the stale entries,
        // so repeated ticker passes never re-offer the same job
        val now = 10_000L
        Downloader.putPending(11, "https://x/stuck.mp4" to "t", queuedAtMs = now - 300_000)
        Downloader.staleDirectJobs(thresholdMs = 120_000, nowMs = now)
        Downloader.staleDirectJobs(thresholdMs = 120_000, nowMs = now)
        assertEquals(emptyList<Pair<Long, String>>(),
            Downloader.staleDirectJobs(thresholdMs = 120_000, nowMs = now))
    }
}
