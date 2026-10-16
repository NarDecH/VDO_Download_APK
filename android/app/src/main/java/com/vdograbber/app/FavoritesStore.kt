package com.vdograbber.app

import java.io.File
import java.net.URLDecoder
import java.net.URLEncoder

/**
 * v1.9.21: favorite websites (เว็บโปรด) - one-click bookmarks of the pages
 * the user keeps coming back to, persisted to <filesDir>/favorites.tsv.
 * Android twin of app/core/favorites.py (same semantics: dedupe by exact
 * URL - a repeat save refreshes the title and moves the row to the top,
 * newest-first display order, MAX 300 rows).
 *
 * Pure JVM on purpose (no android.*, no org.json - the PausedJobs lesson
 * from the JVM unit tests): each line is `url \t title \t savedAt` with
 * every field URLEncoder-escaped, so tabs/newlines/Thai inside a title
 * cannot corrupt the format and the round-trip is unit-testable.
 */
object FavoritesStore {
    const val FILE_NAME = "favorites.tsv"
    const val MAX_FAVORITES = 300

    class Entry(
        val url: String,
        val title: String,
        val savedAt: Long,
    )

    @Volatile private var dir: File? = null
    private val lock = Any()

    /** Point the store at its directory (MainActivity onCreate). */
    fun init(d: File) {
        dir = d
    }

    private fun file(): File =
        File(dir ?: throw IllegalStateException("FavoritesStore.init not called"), FILE_NAME)

    /** Every favorite, NEWEST first (top of the sheet tab). */
    fun all(): List<Entry> = synchronized(lock) {
        read().sortedByDescending { it.savedAt }
    }

    /** Add (or refresh) one bookmark. Returns true only for a NEW row. */
    fun add(url: String, title: String): Boolean = synchronized(lock) {
        if (url.isBlank()) throw IllegalArgumentException("blank url")
        val rest = read().filter { it.url != url }
        val isNew = rest.size == read().size
        val e = Entry(url.trim(), title.trim().take(200), System.currentTimeMillis())
        // dedupe changed the list only when the url was unknown; either way
        // the new entry goes to the top (most recently used first)
        write(listOf(e) + rest.filter { it.url != e.url }.take(MAX_FAVORITES - 1))
        isNew
    }

    /** Forget one bookmark by exact URL (false when it was not saved). */
    fun remove(url: String): Boolean = synchronized(lock) {
        val rest = read().filter { it.url != url.trim() }
        val removed = rest.size < read().size
        if (removed) write(rest)
        removed
    }

    fun count(): Int = synchronized(lock) { read().size }

    // ------------------------------------------------- persistence (TSV lines)

    private fun read(): List<Entry> {
        val f = file()
        if (!f.exists()) return emptyList()
        return try {
            // a corrupt line (half-written file, future format) is skipped -
            // one bad row must not hide the other favorites
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
        listOf(e.url, e.title, e.savedAt.toString())
            .joinToString("\t") { URLEncoder.encode(it, "UTF-8") }
    }

    fun parse(text: String): List<Entry> = text.lineSequence()
        .filter { it.isNotBlank() }
        .mapNotNull { line ->
            val p = line.split("\t")
            if (p.size < 3) return@mapNotNull null
            val savedAt = p[2].toLongOrNull() ?: return@mapNotNull null
            Entry(dec(p[0]), dec(p[1]), savedAt)
        }
        .toList()

    private fun dec(s: String): String =
        try { URLDecoder.decode(s, "UTF-8") } catch (_: Exception) { s }
}
