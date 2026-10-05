package com.vdograbber.app

import java.util.concurrent.ConcurrentHashMap

/**
 * Deduplicated store of videos detected in this session (all tabs/pages).
 *
 * v1.4.0 (user request): the 🎬 list follows the page - a MAIN-FRAME
 * navigation to a different ORIGIN (scheme+host+port) clears it, same-origin
 * navigations keep detections (multi-page players/series sites). Duplicate
 * media of the previous page then stops re-filling the list.
 */
object MediaStore {
    class Item(val url: String, val kind: String, val label: String, val page: String, val title: String) {
        override fun toString(): String = label.ifEmpty { kind }
    }

    private val items = LinkedHashMap<String, Item>()
    private var origin = ""

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

    /**
     * v1.4.0: track page changes. A main-frame navigation whose origin
     * differs from the stored one clears the list and re-arms the origin;
     * same-origin navigations (and repeated loads of the same page) keep it.
     * Returns the removed count (0 when kept) - logged by the caller.
     */
    @Synchronized
    fun onMainFrameNavigate(url: String): Int {
        val next = originOf(url)
        // non-http frames (about:blank, error pages) are not real page
        // changes - they must not silently wipe the detections
        if (next.isEmpty() || next == origin) return 0
        val n = items.size
        items.clear()
        origin = next
        return n
    }

    /**
     * scheme://host[:port] of [url] (host kept lowercase, default ports
     * normalized away) - "" for non-http(s) input. Pure JVM, unit-tested.
     */
    fun originOf(url: String): String {
        val m = Regex("^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)").find(url) ?: return ""
        val auth = m.groupValues[1].lowercase().substringBefore('@')
        val host = auth.substringBefore(':')
        val port = auth.substringAfter(':', "")
        val scheme = url.substringBefore("://").lowercase()
        val default = (scheme == "https" && port == "443") || (scheme == "http" && port == "80")
        return if (port.isEmpty() || default) "$scheme://$host" else "$scheme://$host:$port"
    }
}
