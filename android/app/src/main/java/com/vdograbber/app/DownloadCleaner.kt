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

    /**
     * DownloadManager jobs that finished successfully (direct files).
     *
     * v1.9.1 (field-log fix): the old loop read COLUMN_LOCAL_FILENAME, which
     * Android throws a SecurityException for on every row when targeting
     * N+ ("deprecated; use ContentResolver.openFileDescriptor"). Worse, the
     * throw escaped the per-row loop and killed the WHOLE query, so the media
     * sheet's "finished files" tab was always empty and app.log filled up
     * with hundreds of "list download jobs failed" lines.
     *
     * Read COLUMN_LOCAL_URI instead (a file:// URI permitted to the enqueuing
     * app) and wrap the per-row access so one bad row cannot sink the rest:
     * a job that still has no reachable identity is skipped, and the systemList
     * merge catches the file again through MediaStore or the raw directory.
     */
    fun dmSuccessful(ctx: Context): List<Candidate> {
        val out = mutableListOf<Candidate>()
        try {
            val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val q = DownloadManager.Query().setFilterByStatus(DownloadManager.STATUS_SUCCESSFUL)
            dm.query(q)?.use { c ->
                while (c.moveToNext()) {
                    try {
                        val id = c.getLong(c.getColumnIndexOrThrow(DownloadManager.COLUMN_ID))
                        val localUri = c.getString(c.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_URI))
                        val local = localUri?.let { Uri.parse(it).path }.orEmpty()
                        if (!local.contains("VDOGrabber")) continue
                        val f = File(local)
                        if (!f.exists()) continue
                        val title = c.getString(c.getColumnIndexOrThrow(DownloadManager.COLUMN_TITLE)).orEmpty()
                        // no content uri here: getUriForDownloadedFile returns
                        // the downloads-provider URI, which we cannot delete
                        // from - the MediaStore entry (when present) provides
                        // the deletable content URI
                        out.add(Candidate(id, null, f, title.ifEmpty { f.name }, f.length()))
                    } catch (e: Exception) { // one bad row must not sink the list
                        FileLog.app("WARN", "dl", "skip download job row: $e")
                    }
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
     * Everything deletable in Downloads/VDOGrabber, MERGED by file name - the
     * same finished file is typically a DM job AND a MediaStore row AND a
     * path on disk, and deleting it for real needs those identities combined
     * (a ghost DM row alone must not make the MediaStore URI unreachable).
     */
    fun systemList(ctx: Context): List<Candidate> {
        val byName = linkedMapOf<String, Candidate>()
        fun add(c: Candidate) {
            val prev = byName[c.name]
            byName[c.name] = if (prev == null) c else Candidate(
                id = if (prev.id >= 0) prev.id else c.id,
                uri = prev.uri ?: c.uri,
                raw = prev.raw ?: c.raw,
                name = prev.name,
                size = maxOf(prev.size, c.size),
            )
        }
        dmSuccessful(ctx).forEach(::add)
        mediaStore(ctx).forEach(::add)
        try {
            val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "VDOGrabber")
            dir.listFiles()?.filter { it.isFile }?.forEach { f ->
                add(Candidate(-1, null, f, f.name, f.length()))
            }
        } catch (_: Exception) {}
        return byName.values.sortedByDescending { it.raw?.lastModified() ?: 0L }
    }

    /**
     * v1.6.0: delete EVERY candidate in [list], one delete attempt per
     * candidate (single confirm dialog in the UI covers the whole tab).
     * Returns (deleted, total) so the caller can report an honest "ลบแล้ว 5/7
     * ไฟล์" toast when a ghost row refuses to die - candidates that carry no
     * identity left (already gone) count as deleted, matching systemDelete's
     * fall-through semantics. Pure logic over systemDelete (logging is the
     * caller's job, like every other cleaner entry point) -> JVM-testable.
     */
    fun deleteAll(ctx: Context?, list: List<Candidate>): Pair<Int, Int> {
        var deleted = 0
        for (c in list) {
            // nothing left to remove (no id, no uri, no file) - treat as gone
            if (c.identities == 0 || systemDelete(ctx, c)) deleted++
        }
        return deleted to list.size
    }

    /**
     * Delete a candidate: try the MediaStore row first (points at the real
     * file), then the DM row (its remove() is documented to delete the file,
     * but fresh emulators can keep ghost rows that delete nothing), then the
     * raw file itself. Success means at least one identity actually removed
     * something - not just that a call "didn't throw". [ctx] is only needed
     * for the DM/MediaStore identities; a raw-file candidate deletes without
     * one (this is what makes the JVM unit test possible).
     */
    fun systemDelete(ctx: Context?, c: Candidate): Boolean {
        var ok = false
        try {
            if (c.uri != null && ctx != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ok = try { ctx.contentResolver.delete(c.uri, null, null) > 0 } catch (_: Exception) { false }
            }
            if (!ok && c.id >= 0 && ctx != null) {
                try {
                    val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
                    dm.remove(c.id) // deletes the completed file with the row
                } catch (_: Exception) {}
                ok = c.raw != null && !c.raw.exists()
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
