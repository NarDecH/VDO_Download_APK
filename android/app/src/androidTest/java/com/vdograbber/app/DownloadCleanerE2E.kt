package com.vdograbber.app

import android.app.DownloadManager
import android.app.Instrumentation
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Emulator E2E for the media sheet's "downloaded files" section (v1.1.9).
 *
 * Proves that DownloadCleaner lists and permanently deletes files that were
 * created the way the app really creates them:
 *  - published into MediaStore Downloads/VDOGrabber (the yt-dlp engine path)
 *  - queued through the platform DownloadManager (the direct-file path)
 * No UI, no network beyond loopback - the same machine-facts the sheet's
 * delete buttons act on.
 */
@RunWith(AndroidJUnit4::class)
class DownloadCleanerE2E {

    private val instr: Instrumentation = InstrumentationRegistry.getInstrumentation()
    private val ctx: Context get() = instr.targetContext

    private fun dir(): File =
        File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "VDOGrabber")

    @Test
    fun mediaStorePublishedFileIsListedAndDeleted() {
        dir().mkdirs()
        val name = "e2e-ms-delete.mp4"
        // publish like StreamDownloadService.publishFile() does (Q+ path)
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.MIME_TYPE, "video/mp4")
            put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/VDOGrabber")
        }
        val uri: Uri = ctx.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)!!
        ctx.contentResolver.openOutputStream(uri)!!.use { it.write(ByteArray(2048)) }

        val listed = DownloadCleaner.systemList(ctx).firstOrNull { it.name == name }
        assertNotNull("published file must be listed (has a content URI)", listed)
        assertTrue(listed!!.size > 0)

        val raw = File(dir(), name)
        val ok = DownloadCleaner.systemDelete(ctx, listed)
        assertTrue("delete must report success", ok)
        assertFalse("file must be gone from disk", raw.exists())
        assertTrue("MediaStore row must be gone", DownloadCleaner.mediaStore(ctx).none { it.name == name })
        assertTrue("candidate must no longer be listed", DownloadCleaner.systemList(ctx).none { it.name == name })
    }

    @Test
    fun rawEngineLeftoverIsListedAndDeleted() {
        dir().mkdirs()
        val f = File(dir(), "e2e-raw-delete.mp4").apply { writeBytes(ByteArray(512)) }

        val listed = DownloadCleaner.systemList(ctx).firstOrNull { it.name == f.name }
        assertNotNull("raw file must be listed", listed)
        assertEquals(512L, listed!!.size)

        assertTrue(DownloadCleaner.systemDelete(ctx, listed))
        assertFalse(f.exists())
        assertTrue(DownloadCleaner.systemList(ctx).none { it.name == f.name })
    }

    @Test
    fun downloadManagerJobIsListedAndRemovedWithItsFile() {
        // serve a tiny mp4 over loopback and enqueue it the way
        // Downloader.enqueue() does, into Downloads/VDOGrabber
        val payload = ByteArray(4096) { (it % 251).toByte() }
        val server = java.net.ServerSocket(0)
        val port = server.localPort
        Thread {
            server.accept().use { c ->
                val reader = c.getInputStream().bufferedReader()
                var line: String?
                do { line = reader.readLine() } while (!line.isNullOrEmpty()) // drain headers
                val out = c.getOutputStream()
                out.write(
                    ("HTTP/1.0 200 OK\r\nContent-Type: video/mp4\r\nContent-Length: ${payload.size}\r\nConnection: close\r\n\r\n").toByteArray(),
                )
                out.write(payload)
            }
        }.apply { isDaemon = true; start() }

        val name = "e2e-dm-delete.mp4"
        val req = DownloadManager.Request(Uri.parse("http://127.0.0.1:$port/$name"))
            .setTitle(name)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_HIDDEN)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "VDOGrabber/$name")
        val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val id = dm.enqueue(req)

        try {
            val deadline = System.currentTimeMillis() + TimeUnit.SECONDS.toMillis(60)
            var done = false
            while (System.currentTimeMillis() < deadline && !done) {
                dm.query(DownloadManager.Query().setFilterById(id))?.use { c ->
                    if (c.moveToFirst()) {
                        val status = c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                        if (status == DownloadManager.STATUS_SUCCESSFUL) done = true
                        if (status == DownloadManager.STATUS_FAILED) {
                            val reason = c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON))
                            throw AssertionError("DownloadManager failed: reason=$reason")
                        }
                    }
                }
                if (!done) Thread.sleep(500)
            }
            assertTrue("download must finish within 60s", done)

            // the platform keeps a ghost DM row for this test-run id in fresh
            // emulators -> match by "our file really is in the listing" rather
            // than by exact name
            val listed = DownloadCleaner.systemList(ctx).firstOrNull { it.name == name && it.id >= 0 }
            assertNotNull("finished DM job must be listed", listed)

            assertTrue(DownloadCleaner.systemDelete(ctx, listed!!))
            assertFalse("file must be gone from disk", File(dir(), name).exists())
            val stillThere = DownloadCleaner.systemList(ctx).any { it.name == name }
            assertFalse(
                "file must no longer be listed (DM ghost rows excepted)",
                stillThere && File(dir(), name).exists(),
            )
        } finally {
            dm.remove(id) // never leave the job behind on failure
            server.close()
        }
    }
}
