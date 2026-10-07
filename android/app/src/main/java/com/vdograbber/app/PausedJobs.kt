package com.vdograbber.app

import java.io.File
import java.net.URLDecoder
import java.net.URLEncoder

/**
 * v1.7.0: registry of PAUSED stream downloads (หยุดพัก), persisted to
 * <filesDir>/paused_jobs.tsv so a pause survives process death and the user
 * can ดาวน์โหลดต่อ from the media sheet later.
 *
 * Resume works by re-running the SAME yt-dlp command (same titleBase ->
 * same output template) into the same engine-work dir: yt-dlp then finds its
 * own .part / .part-FragN files and continues from the last fragment. That
 * is why the stored titleBase must be the EXACT free stem the paused run
 * used - recomputing it (freeTitleBase) would see the partial files as
 * "taken", pick `stem (2)` and silently start over.
 *
 * Pure JVM on purpose (no android.*, no org.json - the FileLog lesson from
 * the JVM unit tests): each line is `titleBase \t url \t title \t savedAt`
 * with every field URLEncoder-escaped, so tabs/newlines/Thai inside a title
 * cannot corrupt the format and the round-trip is unit-testable.
 */
object PausedJobs {
    const val FILE_NAME = "paused_jobs.tsv"

    class Entry(
        val titleBase: String,
        val url: String,
        val title: String,
        val savedAt: Long,
    )

    @Volatile private var dir: File? = null
    private val lock = Any()

    /** Point the registry at its directory (MainActivity + service onCreate). */
    fun init(d: File) {
        dir = d
    }

    private fun file(): File =
        File(dir ?: throw IllegalStateException("PausedJobs.init not called"), FILE_NAME)

    /** Every paused job, oldest first (stable order in the sheet). */
    fun all(): List<Entry> = synchronized(lock) {
        read().sortedBy { it.savedAt }
    }

    /** Add a paused job - deduped by titleBase, the newest record wins. */
    fun add(url: String, title: String, titleBase: String) = synchronized(lock) {
        if (titleBase.isBlank()) throw IllegalArgumentException("blank titleBase")
        val rest = read().filter { it.titleBase != titleBase }
        write(rest + Entry(titleBase, url, title, System.currentTimeMillis()))
    }

    /** Forget a paused job (resumed elsewhere, or the user deleted it). */
    fun remove(titleBase: String) = synchronized(lock) {
        write(read().filter { it.titleBase != titleBase })
    }

    // ------------------------------------------------- persistence (TSV lines)

    private fun read(): List<Entry> {
        val f = file()
        if (!f.exists()) return emptyList()
        return try {
            // a corrupt line (half-written file, future format) is skipped -
            // one bad row must not hide the other paused downloads
            parse(f.readText())
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun write(entries: List<Entry>) {
        val f = file()
        if (entries.isEmpty()) {
            f.delete()
            return
        }
        f.parentFile?.mkdirs()
        f.writeText(serialize(entries))
    }

    fun serialize(entries: List<Entry>): String = entries.joinToString("\n") { e ->
        listOf(e.titleBase, e.url, e.title, e.savedAt.toString())
            .joinToString("\t") { URLEncoder.encode(it, "UTF-8") }
    }

    fun parse(text: String): List<Entry> = text.lineSequence()
        .filter { it.isNotBlank() }
        .mapNotNull { line ->
            val p = line.split("\t")
            if (p.size < 4) return@mapNotNull null
            val savedAt = p[3].toLongOrNull() ?: return@mapNotNull null
            Entry(dec(p[0]), dec(p[1]), dec(p[2]), savedAt)
        }
        .toList()

    private fun dec(s: String): String =
        try { URLDecoder.decode(s, "UTF-8") } catch (_: Exception) { s }
}
