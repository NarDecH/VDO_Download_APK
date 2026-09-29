package com.vdograbber.app

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Detailed logging system - mirrors the desktop app trio (see app/core/logger.py):
 *   app.log        rotating text log, everything
 *   downloads.log  download actions and results
 *   events.jsonl   structured one-JSON-per-line events
 * Files live in <filesDir>/logs and are viewable in LogsActivity.
 */
object FileLog {
    private const val MAX_BYTES = 2L * 1024 * 1024
    private const val KEEP = 3
    private lateinit var dir: File
    private val ts = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)

    fun init(ctx: Context) {
        dir = File(ctx.filesDir, "logs").apply { mkdirs() }
    }

    val logsDir: File get() = dir

    fun app(level: String, tag: String, msg: String) {
        write("app.log", "${ts.format(Date())} [$level] [$tag]: $msg")
    }

    fun download(msg: String) {
        write("downloads.log", "${ts.format(Date())}: $msg")
    }

    fun event(type: String, data: Map<String, Any?> = emptyMap()) {
        val rec = JSONObject()
        rec.put("ts", System.currentTimeMillis() / 1000.0)
        rec.put("event", type)
        for ((k, v) in data) rec.put(k, v ?: JSONObject.NULL)
        write("events.jsonl", rec.toString(), rotate = false)
    }

    fun tail(name: String, lines: Int = 300): String = try {
        val f = File(dir, name)
        if (!f.exists()) "(empty)"
        else f.readLines().takeLast(lines).joinToString("\n")
    } catch (e: Exception) {
        "(cannot read: $e)"
    }

    private fun write(name: String, line: String, rotate: Boolean = true) {
        try {
            val f = File(dir, name)
            if (rotate && f.exists() && f.length() > MAX_BYTES) {
                for (i in KEEP downTo 1) {
                    val from = File(dir, "$name.$i")
                    val to = File(dir, "$name.${i + 1}")
                    if (from.exists()) from.renameTo(to)
                }
                f.renameTo(File(dir, "$name.1"))
            }
            f.appendText(line + "\n")
        } catch (_: Exception) {
        }
    }
}
