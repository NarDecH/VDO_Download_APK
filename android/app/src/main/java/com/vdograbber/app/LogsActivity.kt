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
        val tabApp: Button = findViewById(R.id.tabApp)
        val tabDl: Button = findViewById(R.id.tabDl)
        val tabEv: Button = findViewById(R.id.tabEv)
        val tabs = mapOf<Button, String>(
            tabApp to "app.log",
            tabDl to "downloads.log",
            tabEv to "events.jsonl",
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
        refresh("app.log")
    }
}
