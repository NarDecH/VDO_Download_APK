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

    // ---------------------------------- v1.6.0 deleteAll (clear-all button)

    @Test
    fun `deleteAll removes every raw file and reports honest counts`() {
        val a = File.createTempFile("vclean-a", ".mp4")
        val b = File.createTempFile("vclean-b", ".mp4")
        a.writeText("a"); b.writeText("b")

        val (deleted, total) = DownloadCleaner.deleteAll(
            null,
            listOf(
                DownloadCleaner.Candidate(-1, null, a, a.name, 1),
                DownloadCleaner.Candidate(-1, null, b, b.name, 1),
            ),
        )
        assertEquals(2, total)
        assertEquals(2, deleted)
        assertFalse(a.exists()); assertFalse(b.exists())
    }

    @Test
    fun `deleteAll keeps going after failures and counts identity-less as gone`() {
        val gone = File.createTempFile("vclean-ok", ".mp4")
        gone.writeText("a")
        // NOTE: no injected "delete fails" case here - making File.delete()
        // fail is OS-dependent (a read-only FILE blocks deletion on Windows
        // but not on Linux, where only a read-only DIRECTORY does), so the
        // deterministic non-deleted cases are a missing file (systemDelete
        // reports false) and a candidate with no identity at all
        val missing = File(gone.parentFile, "vclean-never.mp4")
        val (deleted, total) = DownloadCleaner.deleteAll(
            null,
            listOf(
                DownloadCleaner.Candidate(-1, null, gone, gone.name, 1),
                DownloadCleaner.Candidate(-1, null, missing, missing.name, 0),
                // no identity at all: nothing to remove -> counted as gone
                DownloadCleaner.Candidate(-1, null, null, "ghost", 0),
            ),
        )
        assertEquals(3, total)
        assertEquals("1 file deleted + 1 identity-less candidate", 2, deleted)
        assertFalse(gone.exists())
    }
}
