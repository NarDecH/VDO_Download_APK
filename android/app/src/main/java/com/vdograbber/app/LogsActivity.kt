package com.vdograbber.app

import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.yausername.youtubedl_android.YoutubeDL
import kotlin.concurrent.thread

/** In-app log viewer: app.log / downloads.log / events.jsonl / crash.log tails + engine maintenance. */
class LogsActivity : AppCompatActivity() {

    private lateinit var box: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_logs)
        FileLog.init(applicationContext)

        box = findViewById(R.id.logBox)
        val tabApp: Button = findViewById(R.id.tabApp)
        val tabDl: Button = findViewById(R.id.tabDl)
        val tabEv: Button = findViewById(R.id.tabEv)
        val tabCrash: Button = findViewById(R.id.tabCrash)
        val tabs = mapOf<Button, String>(
            tabApp to "app.log",
            tabDl to "downloads.log",
            tabEv to "events.jsonl",
            tabCrash to "crash.log",
        )
        val refresh = { name: String ->
            box.text = FileLog.tail(name, 400)
        }
        tabs.forEach { (btn: Button, name: String) ->
            btn.setOnClickListener {
                refresh(name)
                tabs.keys.forEach { b: Button -> b.alpha = 0.55f }
                btn.alpha = 1f
            }
        }
        val btnRefresh: Button = findViewById(R.id.btnRefresh)
        btnRefresh.setOnClickListener {
            refresh(tabs.values.first())
        }

        // ---- engine maintenance (docs/plan-android-hls.md step 6) ----------
        findViewById<Button>(R.id.btnEngine).setOnClickListener { showEngineDialog() }

        refresh("app.log")
    }

    private fun showEngineDialog() {
        AlertDialog.Builder(this)
            .setTitle(R.string.engine_title)
            .setItems(arrayOf(
                getString(R.string.engine_check),
                getString(R.string.engine_update),
            )) { _, which ->
                if (which == 0) checkEngine() else updateEngine()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun checkEngine() {
        thread(name = "engine-check") {
            try {
                val v = YoutubeDL.getInstance().version(this)
                FileLog.event("engine_check", mapOf("engine" to "yt-dlp", "version" to v))
                runOnUiThread {
                    box.text = getString(R.string.engine_version, v)
                }
            } catch (e: Exception) {
                FileLog.app("ERROR", "engine", "version check failed: $e")
                runOnUiThread { showEngineError(e) }
            }
        }
    }

    private fun updateEngine() {
        Toast.makeText(this, R.string.engine_update, Toast.LENGTH_SHORT).show()
        thread(name = "engine-update") {
            try {
                // swaps the bundled yt-dlp binary for the latest stable release
                val status = YoutubeDL.getInstance().updateYoutubeDL(this, YoutubeDL.UpdateChannel._STABLE)
                val v = YoutubeDL.getInstance().version(this)
                val msg = if (status == YoutubeDL.UpdateStatus.DONE) {
                    FileLog.event("engine_update_done", mapOf("engine" to "yt-dlp", "version" to v, "ok" to true))
                    getString(R.string.engine_updated, v)
                } else {
                    FileLog.event("engine_update_done", mapOf("engine" to "yt-dlp", "version" to v, "ok" to true, "already_latest" to true))
                    getString(R.string.engine_uptodate, v)
                }
                runOnUiThread { box.text = msg }
            } catch (e: Exception) {
                FileLog.app("ERROR", "engine", "update failed: $e")
                FileLog.event("engine_update_done", mapOf("engine" to "yt-dlp", "ok" to false, "error" to e.message))
                runOnUiThread { showEngineError(e) }
            }
        }
    }

    private fun showEngineError(e: Exception) {
        Toast.makeText(this, getString(R.string.engine_error, e.message ?: "unknown"), Toast.LENGTH_LONG).show()
    }
}
