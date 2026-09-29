package com.vdograbber.app

import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

/** In-app log viewer: app.log / downloads.log / events.jsonl tails. */
class LogsActivity : AppCompatActivity() {

    private lateinit var box: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_logs)
        FileLog.init(applicationContext)

        box = findViewById(R.id.logBox)
        val tabs = mapOf<Button, String>(
            findViewById(R.id.tabApp) to "app.log",
            findViewById(R.id.tabDl) to "downloads.log",
            findViewById(R.id.tabEv) to "events.jsonl",
        )
        val refresh = { name: String ->
            box.text = FileLog.tail(name, 400)
        }
        tabs.forEach { (btn, name) ->
            btn.setOnClickListener {
                refresh(name)
                tabs.keys.forEach { it.alpha = 0.55f }
                btn.alpha = 1f
            }
        }
        findViewById<Button>(R.id.btnRefresh).setOnClickListener {
            refresh(tabs.values.first())
        }
        refresh("app.log")
    }
}
