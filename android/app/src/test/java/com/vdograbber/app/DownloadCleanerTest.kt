package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * JVM unit tests for DownloadCleaner's pure logic (v1.1.9) - the system
 * touch points ([DownloadCleaner.systemList] / [DownloadCleaner.systemDelete])
 * are covered by DownloadCleanerE2E on the emulator.
 */
class DownloadCleanerTest {

    @Test
    fun `identities counts only the identities a candidate carries`() {
        val rawOnly = DownloadCleaner.Candidate(-1, null, File("x/a.mp4"), "a.mp4", 1)
        val uriOnly = DownloadCleaner.Candidate(-1, android.net.Uri.parse("content://x/1"), null, "a.mp4", 1)
        val idOnly = DownloadCleaner.Candidate(7, null, null, "a.mp4", 1)
        assertEquals(1, rawOnly.identities)
        assertEquals(1, uriOnly.identities)
        assertEquals(1, idOnly.identities)
        assertEquals(2, DownloadCleaner.Candidate(7, uriOnly.uri, null, "a.mp4", 1).identities)
        assertEquals(3, DownloadCleaner.Candidate(7, uriOnly.uri, rawOnly.raw, "a.mp4", 1).identities)
    }

    @Test
    fun `systemDelete falls through identities until one succeeds`() {
        val tmp = File.createTempFile("vclean", ".mp4")
        tmp.writeText("x")
        val missingFile = File(tmp.parentFile, "vclean-missing.mp4")

        // a fake "DM row" whose remove() leaves the file in place -> must
        // fall through to the raw File.delete() identity and succeed
        // (ctx = null is fine: a raw-file candidate never touches the system)
        val ok = DownloadCleaner.systemDelete(
            null,
            DownloadCleaner.Candidate(id = -1, uri = null, raw = tmp, name = tmp.name, size = 1),
        )
        assertTrue(ok)
        assertFalse(tmp.exists())

        // nothing real to delete -> false
        val fail = DownloadCleaner.systemDelete(
            null,
            DownloadCleaner.Candidate(-1, null, missingFile, missingFile.name, 0),
        )
        assertFalse(fail)
    }
}
