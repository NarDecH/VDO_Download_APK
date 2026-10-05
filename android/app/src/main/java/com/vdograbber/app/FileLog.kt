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
 *   crash.log      uncaught exception stack traces (installed crash handler)
 * Files live in <filesDir>/logs and are viewable in LogsActivity.
 */
object FileLog {
    private const val MAX_BYTES = 2L * 1024 * 1024
    private const val KEEP = 3
    private lateinit var dir: File
    private val ts = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)

    private var crashHandlerInstalled = false

    fun init(ctx: Context) {
        dir = File(ctx.filesDir, "logs").apply { mkdirs() }
        installCrashHandler()
    }

    /** v1.3.2: an uncaught exception used to close the app leaving no trace on
     * disk (the desktop build already keeps crash.log). Persist the stack trace
     * + an app_crash event, then hand over to the system handler as usual. */
    private fun installCrashHandler() {
        if (crashHandlerInstalled) return
        crashHandlerInstalled = true
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                val sw = java.io.StringWriter()
                throwable.printStackTrace(java.io.PrintWriter(sw))
                write(
                    "crash.log",
                    "${ts.format(Date())} CRASH on thread \"${thread.name}\"\n" +
                        "device: ${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL} " +
                        "(Android ${android.os.Build.VERSION.RELEASE}, API ${android.os.Build.VERSION.SDK_INT})\n" +
                        sw.toString(),
                )
                event("app_crash", mapOf("thread" to thread.name, "error" to throwable.toString().take(300)))
            } catch (_: Exception) {
            }
            previous?.uncaughtException(thread, throwable)
        }
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
