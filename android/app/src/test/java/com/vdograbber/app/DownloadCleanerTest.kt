package com.vdograbber.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * JVM unit tests for DownloadCleaner's pure logic (v1.1.9). They must not
 * touch android.jar stubs (Uri/Context throw "Stub!" on the JVM), so the
 * uri/id identities are exercised on the emulator in DownloadCleanerE2E -
 * here we cover the raw-file identity and the identity-priority counting.
 */
class DownloadCleanerTest {

    @Test
    fun `identities counts only the identities a candidate carries`() {
        // no Uri here: android.jar Uri is a JVM stub (throws "Stub!") - the
        // uri identity is exercised on the emulator in DownloadCleanerE2E
        val rawOnly = DownloadCleaner.Candidate(-1, null, File("x/a.mp4"), "a.mp4", 1)
        val idOnly = DownloadCleaner.Candidate(7, null, null, "a.mp4", 1)
        assertEquals(1, rawOnly.identities)
        assertEquals(1, idOnly.identities)
        assertEquals(2, DownloadCleaner.Candidate(7, null, rawOnly.raw, "a.mp4", 1).identities)
    }

    @Test
    fun `systemDelete falls through to the raw file identity`() {
        val tmp = File.createTempFile("vclean", ".mp4")
        tmp.writeText("x")

        // ctx = null is fine for a raw-only candidate: the DM/MediaStore
        // identities are skipped and the file itself is deleted
        val ok = DownloadCleaner.systemDelete(
            null,
            DownloadCleaner.Candidate(id = -1, uri = null, raw = tmp, name = tmp.name, size = 1),
        )
        assertTrue("deleting an existing raw file must succeed", ok)
        assertFalse("file must be gone", tmp.exists())

        // deleting a missing file is "nothing to do" -> false (no fake success)
        val missing = File(tmp.parentFile, "vclean-missing.mp4")
        val again = DownloadCleaner.systemDelete(
            null,
            DownloadCleaner.Candidate(-1, null, missing, missing.name, 0),
        )
        assertFalse("deleting a non-existent file must not report success", again)
    }
}
