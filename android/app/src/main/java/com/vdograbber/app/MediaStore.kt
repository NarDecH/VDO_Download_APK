package com.vdograbber.app

import java.util.concurrent.ConcurrentHashMap

/** Deduplicated store of videos detected in this session (all tabs/pages). */
object MediaStore {
    class Item(val url: String, val kind: String, val label: String, val page: String, val title: String) {
        override fun toString(): String = label.ifEmpty { kind }
    }

    private val items = LinkedHashMap<String, Item>()

    @Synchronized
    fun add(url: String, kind: String, label: String, page: String, title: String): Boolean {
        if (items.containsKey(url)) {
            // touch -> re-insert at the end so eviction is true LRU
            val existing = items.remove(url) ?: return false
            items[url] = existing
            return false
        }
        items[url] = Item(url, kind, label, page, title)
        if (items.size > 200) {
            val it = items.entries.iterator()
            it.next(); it.remove()
        }
        return true
    }

    @Synchronized
    fun list(): List<Item> = items.values.toList().reversed()

    @Synchronized
    fun clear() = items.clear()
}
