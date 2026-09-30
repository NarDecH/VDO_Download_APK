package com.vdograbber.app

import android.app.DownloadManager
import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore as AndroidMediaStore
import java.io.File

/**
 * v1.1.9: the delete + list logic of the media sheet's "downloaded files"
 * section, extracted from MainActivity so it is JVM-testable like
 * [Downloader] / [StreamArgs] - only two touch points hit the system
 * ([systemList] and [systemDelete], covered by the emulator E2E).
 *
 * A finished download is one of:
 *  - DownloadManager job (direct files)      -> DownloadManager.remove()
 *  - published MediaStore entry (Q+)         -> contentResolver.delete()
 *  - raw file in Downloads/VDOGrabber        -> File.delete()
 * A [Candidate] can carry more than one identity (the same finished file is
 * typically a DM job AND a MediaStore row); [delete] removes each identity
 * until one reports success.
 */
object DownloadCleaner {

    /** One deletable download row: DownloadManager job, MediaStore item, or raw file. */
    class Candidate(
        val id: Long,           // DownloadManager row id, or -1
        val uri: Uri?,          // MediaStore content URI, or null
        val raw: File?,         // absolute path, or null
        val name: String,
        val size: Long,
    ) {
        val identities: Int get() = listOfNotNull(if (id >= 0) id else null, uri, raw).size
    }

    /** DownloadManager jobs that finished successfully (direct files). */
    fun dmSuccessful(ctx: Context): List<Candidate> {
        val out = mutableListOf<Candidate>()
        try {
            val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val q = DownloadManager.Query().setFilterByStatus(DownloadManager.STATUS_SUCCESSFUL)
            dm.query(q)?.use { c ->
                while (c.moveToNext()) {
                    val id = c.getLong(c.getColumnIndexOrThrow(DownloadManager.COLUMN_ID))
                    val local = c.getString(c.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_FILENAME)) ?: continue
                    if (!local.contains("VDOGrabber")) continue
                    val f = File(local)
                    if (!f.exists()) continue
                    val title = c.getString(c.getColumnIndexOrThrow(DownloadManager.COLUMN_TITLE)).orEmpty()
                    val uri = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) dm.getUriForDownloadedFile(id) else null
                    out.add(Candidate(id, uri, f, title.ifEmpty { f.name }, f.length()))
                }
            }
        } catch (e: Exception) {
            FileLog.app("ERROR", "dl", "list download jobs failed: $e")
        }
        return out
    }

    /** Published MediaStore entries under Downloads/VDOGrabber (Q+ only). */
    fun mediaStore(ctx: Context): List<Candidate> {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return emptyList()
        val out = mutableListOf<Candidate>()
        try {
            ctx.contentResolver.query(
                AndroidMediaStore.Downloads.EXTERNAL_CONTENT_URI,
                arrayOf(
                    AndroidMediaStore.MediaColumns._ID,
                    AndroidMediaStore.MediaColumns.DISPLAY_NAME,
                    AndroidMediaStore.MediaColumns.SIZE,
                ),
                "${AndroidMediaStore.MediaColumns.RELATIVE_PATH}=?",
                arrayOf(Environment.DIRECTORY_DOWNLOADS + "/VDOGrabber/"), null,
            )?.use { c ->
                while (c.moveToNext()) {
                    val name = c.getString(1) ?: continue
                    val uri = ContentUris.withAppendedId(AndroidMediaStore.Downloads.EXTERNAL_CONTENT_URI, c.getLong(0))
                    out.add(Candidate(-1, uri, null, name, c.getLong(2)))
                }
            }
        } catch (e: Exception) {
            FileLog.app("ERROR", "dl", "list media store failed: $e")
        }
        return out
    }

    /**
     * Everything deletable in Downloads/VDOGrabber, deduped by file name
     * (a finished direct download shows up in DM, MediaStore and on disk).
     */
    fun systemList(ctx: Context): List<Candidate> {
        val out = mutableListOf<Candidate>()
        val seen = mutableSetOf<String>()
        for (c in dmSuccessful(ctx)) if (seen.add(c.name)) out.add(c)
        for (c in mediaStore(ctx)) if (seen.add(c.name)) out.add(c)
        try {
            val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "VDOGrabber")
            dir.listFiles()?.filter { it.isFile }?.forEach { f ->
                if (seen.add(f.name)) out.add(Candidate(-1, null, f, f.name, f.length()))
            }
        } catch (_: Exception) {}
        return out.sortedByDescending { it.raw?.lastModified() ?: 0L }
    }

    /**
     * Delete a candidate: try DownloadManager.remove(), then the MediaStore
     * URI, then the raw file - success when at least one identity is gone
     * (and, when known, the file itself no longer exists). [ctx] is only
     * needed for the DM/MediaStore identities; a raw-file candidate deletes
     * without one (this is what makes the JVM unit test possible).
     */
    fun systemDelete(ctx: Context?, c: Candidate): Boolean {
        var ok = false
        try {
            if (c.id >= 0 && ctx != null) {
                val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
                dm.remove(c.id) // deletes the completed file with the row
                ok = c.raw == null || !c.raw.exists()
            }
            if (!ok && c.uri != null && ctx != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ok = try { ctx.contentResolver.delete(c.uri, null, null) > 0 } catch (_: Exception) { false }
            }
            if (!ok && c.raw != null) {
                // the real File.delete() result: false when the file is not
                // there (nothing to clean), true when we removed it
                ok = try { c.raw.delete() } catch (_: Exception) { false }
            }
        } catch (_: Exception) {}
        return ok
    }
}
