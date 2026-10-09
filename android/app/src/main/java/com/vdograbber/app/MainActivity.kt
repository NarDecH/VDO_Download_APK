package com.vdograbber.app

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.snackbar.Snackbar
import kotlin.concurrent.thread

/**
 * VDO Grabber for Android: a WebView browser that opens any site, detects the
 * video currently displayed (DOM scan + fetch/XHR hooks + native request
 * sniffing) and offers a Download button for it. All activity is logged in
 * detail (FileLog) exactly like the desktop build.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var urlBox: EditText
    private lateinit var chipMedia: TextView
    private lateinit var progressBar: View

    private val bridge = Bridge()

    /** renderer deaths since the last successful page load (crash-loop guard) */
    private var renderGoneCount = 0

    /** v1.4.0: ticks the 🎬 chip with the live stream-download percent - the
     *  notification alone was easy to miss (hidden by some launchers/DMI). */
    private var progressTicker: java.util.Timer? = null

    // v1.5.0: the open media sheet's state - the same 500ms ticker re-renders
    // its active tab so downloading rows show live progress without any extra
    // timer (views are dropped on dismiss to avoid leaks)
    private var sheetDialog: AlertDialog? = null
    private var sheetContainer: LinearLayout? = null
    private var sheetButtons: Map<Int, TextView> = emptyMap()
    private var sheetTab = TAB_FOUND
    private var sheetFoundCount = 0
    private var sheetLastActive = 0
    private var sheetShown: MutableMap<String, View> = mutableMapOf()
    private var sheetShownPids: List<String> = emptyList()

    // v1.6.0: last in-app download notice (debounce) - the sheet tab refresh
    // is only re-armed after this window so a fast burst of events does not
    // fight the user's taps (renderSheet rebuilds views)
    private var lastDlNoticeMs = 0L
    private var lastDlNoticeOk = false
    // v1.9.3: one-tap retry offer for stuck direct downloads (debounce)
    private var lastStuckOfferMs = 0L
    private var lastStuckOfferUrl = ""

    inner class Bridge {
        @JavascriptInterface
        fun reportMedia(json: String) {
            // an uncaught exception on the WebView JS-bridge thread closes the app
            try {
                val o = Detector.parse(json) ?: return
                val url = o.optString("url")
                val added = MediaStore.add(
                    url = url,
                    kind = o.optString("kind", "media"),
                    label = o.optString("label", ""),
                    page = o.optString("page", ""),
                    title = o.optString("title", ""),
                )
                if (added) {
                    FileLog.event("media_found", mapOf("url" to url, "kind" to o.optString("kind"), "via" to "js"))
                    FileLog.app("DEBUG", "detect", "media found: $url")
                    runOnUiThread { updateChip() }
                }
            } catch (t: Throwable) {
                FileLog.app("ERROR", "detect", "reportMedia failed: $t")
                FileLog.event("bridge_error", mapOf("error" to t.toString().take(300)))
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        FileLog.init(applicationContext)
        PausedJobs.init(filesDir) // v1.7.0: หยุดพักไว้ registry (paused_jobs.tsv)
        FileLog.app("INFO", "app", "VDO Grabber 1.9.5 starting (Android ${Build.VERSION.RELEASE}, ${Build.MODEL})")
        FileLog.event("app_start", mapOf("device" to Build.MODEL, "api" to Build.VERSION.SDK_INT))
        setContentView(R.layout.activity_main)

        // Stream service reports "Unsupported URL" pages so the user can
        // open them in this WebView and let the detector catch the stream
        registerReceiver(object : android.content.BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: android.content.Intent?) {
                val u = intent?.getStringExtra("url") ?: return
                FileLog.app("INFO", "dl", "unsupported URL -> opening in browser: $u")
                Toast.makeText(this@MainActivity, R.string.open_in_browser_hint, Toast.LENGTH_LONG).show()
                navigate(u)
            }
        }, android.content.IntentFilter("com.vdograbber.app.OPEN_IN_BROWSER"),
            Context.RECEIVER_NOT_EXPORTED)

        // v1.6.0: the stream service announces finished/failed downloads so
        // the user sees the outcome in the app (Snackbar + temporary chip),
        // not only in the notification drawer
        registerReceiver(object : android.content.BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: android.content.Intent?) {
                if (intent?.action != StreamDownloadService.ACTION_DL_STATE) return
                val ok = intent.getBooleanExtra(StreamDownloadService.EXTRA_DL_OK, false)
                val file = intent.getStringExtra(StreamDownloadService.EXTRA_DL_FILE).orEmpty()
                onDlState(ok, file)
            }
        }, android.content.IntentFilter(StreamDownloadService.ACTION_DL_STATE),
            Context.RECEIVER_NOT_EXPORTED)

        // v1.3.4: a finished direct download whose payload is really an HTML
        // page (the server answered with the player page) is deleted and the
        // URL is re-routed to the yt-dlp engine automatically
        registerReceiver(object : android.content.BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: android.content.Intent?) {
                val id = intent?.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1) ?: return
                val pending = Downloader.pendingFor(id) ?: return
                try {
                    if (Downloader.verifyNotHtmlAndClean(this@MainActivity, id)) {
                        FileLog.app("INFO", "dl", "re-routing page URL to the yt-dlp engine: $pending")
                        Toast.makeText(
                            this@MainActivity,
                            R.string.not_media_rerouted,
                            Toast.LENGTH_LONG,
                        ).show()
                        Downloader.forget(id)
                        StreamDownloadService.start(this@MainActivity, pending.first, pending.second)
                    }
                } catch (t: Throwable) {
                    FileLog.app("ERROR", "dl", "download-complete check failed: $t")
                }
            }
        }, android.content.IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE),
            Context.RECEIVER_NOT_EXPORTED)

        // yt-dlp + ffmpeg init for on-device stream downloads (async, engine
        // is installed into files/ on first launch - docs/plan-android-hls.md)
        thread(name = "engine-init") {
            try {
                com.yausername.youtubedl_android.YoutubeDL.getInstance().init(applicationContext)
                com.yausername.ffmpeg.FFmpeg.getInstance().init(applicationContext)
                // v1.6.1: YoutubeDL.version() reads a SharedPreferences value
                // that only updateYoutubeDL() writes - bundled installs report
                // null forever (field log 2026-10-07). Ask the engine itself;
                // the stored value is kept only as the fallback.
                val v = com.yausername.youtubedl_android.YoutubeDL.getInstance()
                    .version(applicationContext) ?: runEngineVersionProbe()
                FileLog.event("engine_ready", mapOf("engine" to "yt-dlp", "version" to v))
                FileLog.app("INFO", "engine", "yt-dlp ready (v$v)")
            } catch (e: Exception) {
                FileLog.app("ERROR", "engine", "init failed: $e")
                FileLog.event("engine_init_error", mapOf("error" to (e.message ?: "unknown")))
            }
        }

        web = findViewById(R.id.web)
        urlBox = findViewById(R.id.urlBox)
        chipMedia = findViewById(R.id.chipMedia)
        progressBar = findViewById(R.id.progress)

        setupWebView()
        findViewById<ImageButton>(R.id.btnBack).setOnClickListener { if (web.canGoBack()) web.goBack() }
        findViewById<ImageButton>(R.id.btnFwd).setOnClickListener { if (web.canGoForward()) web.goForward() }
        findViewById<ImageButton>(R.id.btnReload).setOnClickListener { web.reload() }
        findViewById<ImageButton>(R.id.btnLogs).setOnClickListener { startActivity(Intent(this, LogsActivity::class.java)) }
        startProgressTicker() // v1.4.0: live download percent on the chip
        findViewById<ImageButton>(R.id.btnPaste).setOnClickListener { openFromClipboard() }
        chipMedia.setOnClickListener { showMediaSheet() }
        urlBox.setOnEditorActionListener { _, _, event ->
            if (event == null || event.action == KeyEvent.ACTION_DOWN) {
                navigate(urlBox.text.toString()); true
            } else false
        }

        val start = intent?.dataString
            ?: "https://duckduckgo.com/?q=sample+video+mp4"
        navigate(start)
    }

    /** v1.6.1: run `yt-dlp --version` through the same env execute() uses and
     *  return the printed version, or null when anything goes wrong. Called
     *  once on the engine-init thread only - never while a download runs. */
    private fun runEngineVersionProbe(): String? = try {
        val req = com.yausername.youtubedl_android.YoutubeDLRequest(emptyList<String>())
            .apply { addCommands(StreamArgs.versionProbeArgs()) }
        val res = com.yausername.youtubedl_android.YoutubeDL.getInstance().execute(req)
        StreamArgs.parseVersionLine(res.out.lineSequence().firstOrNull { it.isNotBlank() })
    } catch (t: Throwable) {
        android.util.Log.w("engine", "version probe failed", t)
        null
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            userAgentString = userAgentString.replace("; wv", "")
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)

        web.addJavascriptInterface(bridge, "AndroidBridge")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                return when (url.scheme) {
                    "http", "https" -> false // keep navigation in-app
                    // ad overlay buttons often link with javascript: - run in-page
                    "javascript" -> false
                    else -> {
                        // intent://, market://, tel:, ... -> hand to an external app
                        FileLog.event("external_link", mapOf("scheme" to (url.scheme ?: "?"), "url" to url.toString().take(300)))
                        try { startActivity(Intent(Intent.ACTION_VIEW, url)) }
                        catch (t: Throwable) { FileLog.app("INFO", "nav", "no external app for '${url.scheme}': $t") }
                        true
                    }
                }
            }

            // v1.3.2: without this override a dead WebView render process (heavy
            // ad pages are the classic trigger) terminates the whole app process -
            // the user saw the app close itself when tapping an ad button.
            // Rebuild the WebView instead and keep the app alive.
            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                val why = if (detail.didCrash()) "crash" else "killed"
                val lastUrl = web.url ?: ""
                FileLog.event("render_gone", mapOf("reason" to why, "url" to lastUrl.take(300)))
                FileLog.app("ERROR", "webview", "render process gone ($why): $lastUrl")
                renderGoneCount++
                rebuildWebView(lastUrl)
                return true // we handled it - do not terminate the process
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    FileLog.event("page_error", mapOf("url" to request.url.toString().take(300), "error" to error.description?.toString()))
                    FileLog.app("WARN", "nav", "page error: ${error.description} (${request.url})")
                }
            }

            // Native network sniffing (the extension's webRequest role):
            // every request that looks like media surfaces as a candidate.
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): android.webkit.WebResourceResponse? {
                // never throw out of this callback - on ad-heavy pages an
                // exception here takes the whole app down
                val url = request.url.toString()
                val ext = try { Downloader.extOf(url) } catch (_: Throwable) { "" }
                if (ext.isEmpty()) return null
                // v1.3.5: WebView getters (web.url/web.title) must run on the
                // main thread - this callback fires on a WebView thread-pool
                // thread ("A WebView method was called on thread
                // 'ThreadPoolForeg'" spam killed every sniff in the field).
                // Read the page facts there, then record like the JS bridge.
                runOnUiThread {
                    try {
                        val page = web.url ?: ""
                        val title = web.title ?: ""
                        if (MediaStore.add(url, ext, "network request", page.substringBeforeLast('/'), title)) {
                            FileLog.event("media_found", mapOf("url" to url, "kind" to ext, "via" to "shouldInterceptRequest"))
                            updateChip()
                        }
                    } catch (t: Throwable) {
                        FileLog.app("ERROR", "sniff", "intercept (main) failed: $t")
                        FileLog.event("intercept_error", mapOf("error" to t.toString().take(300)))
                    }
                }
                return null
            }

            override fun onPageFinished(view: WebView, url: String) {
                renderGoneCount = 0 // successful load -> crash-loop guard reset
                FileLog.event("page_loaded", mapOf("url" to url))
                FileLog.app("INFO", "nav", "page loaded: $url")
                view.evaluateJavascript(Detector.INJECT_JS, null)
                urlBox.setText(url)
                // v1.4.0: the injected detector re-scans the NEW page here -
                // clear the list first when the main frame changed origin so
                // stale entries (and re-reports of the previous page) vanish
                val removed = MediaStore.onMainFrameNavigate(url)
                if (removed > 0) {
                    FileLog.event("media_cleared", mapOf("reason" to "origin_change", "url" to url.take(300), "removed" to removed))
                    FileLog.app("INFO", "detect", "media list reset on new origin ($removed items)")
                }
                updateChip()
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                progressBar.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
            }
        }
    }

    /** Replace the WebView whose renderer died with a fresh one (same
     * settings/clients), then reload the page - or show a notice when the
     * same page kills the renderer over and over (crash-loop guard). */
    private fun rebuildWebView(lastUrl: String) {
        val parent = web.parent as? ViewGroup
        val stale = web
        if (parent != null) parent.removeView(stale)
        try { stale.stopLoading(); stale.destroy() } catch (_: Exception) {}
        web = WebView(this).apply { id = R.id.web }
        parent?.addView(
            web,
            FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT),
        )
        setupWebView()
        if (renderGoneCount <= 1 && lastUrl.startsWith("http")) {
            navigate(lastUrl) // usually a transient renderer OOM - just reload
        } else {
            web.loadDataWithBaseURL(null, ERROR_PAGE_HTML, "text/html", "utf-8", null)
            Toast.makeText(this, R.string.render_gone, Toast.LENGTH_LONG).show()
        }
    }

    private fun navigate(raw: String) {
        var url = raw.trim()
        if (url.isEmpty()) return
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = if (url.contains('.') && !url.contains(' ')) "https://$url"
            else "https://duckduckgo.com/?q=" + Uri.encode(url)
        }
        FileLog.app("INFO", "nav", "navigate -> $url")
        FileLog.event("navigate", mapOf("url" to url))
        // v1.4.0: reset the detected list when leaving the current origin -
        // do it at navigate() time so the chip updates instantly (failures
        // keep stale media visible instead of silently wiping it)
        val removed = MediaStore.onMainFrameNavigate(url)
        if (removed > 0) {
            FileLog.event("media_cleared", mapOf("reason" to "origin_change_navigate", "url" to url.take(300), "removed" to removed))
            FileLog.app("INFO", "nav", "media list reset on new origin ($removed items)")
            updateChip()
        }
        web.loadUrl(url)
    }

    /** Open the URL currently in the clipboard (same rules as the desktop
     * Api.open_clipboard: take the first URL-looking line, scheme kept,
     * bare domains get https:// prefixed, the rest is rejected with a toast). */
    private fun openFromClipboard() {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = cm.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString()?.trim().orEmpty()
        val url = text.lines()
            .map { it.trim().trim('\'', '"') }
            .firstOrNull { line ->
                line.isNotEmpty() && (
                    line.startsWith("http://") || line.startsWith("https://") ||
                        (!line.contains('@') && !line.contains(' ') && line.contains('.'))
                    )
            }
            ?.let { if (it.startsWith("http://") || it.startsWith("https://")) it else "https://$it" }
        if (url == null) {
            val reason = if (text.isEmpty()) "empty" else "no url"
            FileLog.event("clipboard_open_error", mapOf("reason" to reason))
            FileLog.app("INFO", "ui", "clipboard open skipped: $reason")
            Toast.makeText(this, if (text.isEmpty()) R.string.clipboard_empty else R.string.clipboard_no_url, Toast.LENGTH_SHORT).show()
            return
        }
        FileLog.event("clipboard_open", mapOf("url" to url.take(200)))
        FileLog.app("INFO", "nav", "open URL from clipboard: $url")
        Toast.makeText(this, R.string.clipboard_opened, Toast.LENGTH_SHORT).show()
        navigate(url)
    }

    private fun updateChip() {
        val n = MediaStore.list().size
        chipMedia.text = getString(R.string.found_chip, n)
        chipMedia.visibility = if (n > 0) View.VISIBLE else View.GONE
    }

    /** v1.4.0: 500ms ticker - while the stream engine reports a percent the
     *  chip shows "⬇ กำลังดาวน์โหลด 42%"; otherwise it falls back to updateChip. */
    private fun startProgressTicker() {
        progressTicker?.cancel()
        progressTicker = java.util.Timer("dl-progress-ui").apply {
            schedule(object : java.util.TimerTask() {
                override fun run() {
                    runOnUiThread {
                        val p = StreamDownloadService.currentPercent()
                        if (p in 0..99) {
                            chipMedia.text = getString(R.string.downloading_chip, p)
                            chipMedia.visibility = View.VISIBLE
                        } else {
                            updateChip()
                        }
                        // v1.9.2: direct-download jobs that were enqueued but never
                        // started/completed (the field-log "queued and vanished" fd)
                        // are named in the logs once, then forgotten...
                        val stuck = Downloader.watchStaleJobs()
                        // v1.9.3: ...and the UI offers a one-tap retry per stuck job
                        for (job in stuck) offerStuckRetry(job.second)
                        updateOpenSheet() // v1.5.0: live rows inside the open sheet
                    }
                }
            }, 500, 500)
        }
    }

    /**
     * v1.9.3: one-tap retry for a direct download that was enqueued but never
     * started (v1.9.2 watchdog). Shows the Snackbar with a ลองใหม่ action that
     * re-enqueues through the same guarded path as a normal tap
     * (tryDownload -> Downloader.enqueue), so a retry respects every routing
     * rule. Debounced per URL so a Toast burst does not spam the user.
     */
    private fun offerStuckRetry(url: String) {
        val now = System.currentTimeMillis()
        if (now - lastStuckOfferMs < 5_000 && url == lastStuckOfferUrl) return
        lastStuckOfferMs = now
        lastStuckOfferUrl = url
        FileLog.event("download_retry_offer", mapOf("url" to url.take(300)))
        val root = window.decorView as? ViewGroup ?: return
        Snackbar.make(
            root,
            getString(R.string.stuck_retry_prompt, Downloader.urlStem(url).take(28)),
            Snackbar.LENGTH_LONG,
        )
            .setAction(R.string.stuck_retry_action) {
                FileLog.event("download_retry", mapOf("url" to url.take(300)))
                tryDownload(url, "")
                Toast.makeText(this, R.string.stuck_requeued, Toast.LENGTH_SHORT).show()
            }
            .show()
    }

    // ------------------------------------------------------------------ media sheet
    // v1.5.0: tabbed sheet (user request) - the detected media, the ACTIVE
    // stream jobs and the finished files no longer share one mixed list.
    private fun showMediaSheet() {
        val items = MediaStore.list()
        val dls = DownloadCleaner.systemList(this)
        val active = StreamDownloadService.activeJobs()
        if (items.isEmpty() && dls.isEmpty() && active.isEmpty()) {
            Toast.makeText(this, R.string.no_media, Toast.LENGTH_SHORT).show()
            return
        }
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 32, 48, 32)
        }
        val tabsRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER
        }
        val tabButtons = linkedMapOf<Int, TextView>()
        for (tab in intArrayOf(TAB_FOUND, TAB_DL, TAB_DONE)) {
            val b = TextView(this).apply {
                textSize = 13f
                setPadding(26, 12, 26, 12)
                setOnClickListener {
                    sheetTab = tab
                    renderSheet()
                }
            }
            tabsRow.addView(b)
            tabButtons[tab] = b
        }
        container.addView(tabsRow)
        val sheet = AlertDialog.Builder(this)
            .setTitle(R.string.sheet_title)
            .setView(container)
            .setNegativeButton(android.R.string.cancel, null)
            .setNeutralButton(R.string.clear_list) { _, _ ->
                MediaStore.clear()
                updateChip()
                FileLog.event("media_cleared", mapOf("removed" to items.size))
                FileLog.app("INFO", "ui", "media list cleared (${items.size} items)")
                Toast.makeText(this, R.string.cleared, Toast.LENGTH_SHORT).show()
            }
            .show()
        sheetDialog = sheet
        sheetContainer = container
        sheetButtons = tabButtons
        // land on the downloading tab while a job runs (the usual "where is my
        // download?" case), else on พบวิดีโอ, else on เสร็จแล้ว
        sheetTab = when {
            active.isNotEmpty() -> TAB_DL
            items.isNotEmpty() -> TAB_FOUND
            else -> TAB_DONE
        }
        renderSheet()
        // the main 500ms ticker keeps the open sheet live; drop the views on dismiss
        sheet.setOnDismissListener {
            sheetDialog = null
            sheetContainer = null
            sheetButtons = emptyMap()
            sheetShown.clear()
            sheetShownPids = emptyList()
        }
    }

    /** Rebuild the open sheet's current tab (also refreshes the tab counts). */
    private fun renderSheet() {
        val container = sheetContainer ?: return
        while (container.childCount > 1) container.removeViewAt(1) // keep the tab strip
        val items = MediaStore.list()
        val dls = DownloadCleaner.systemList(this)
        val active = StreamDownloadService.activeJobs()
        val paused = PausedJobs.all() // v1.7.0: หยุดพักไว้ (persisted)
        sheetFoundCount = items.size
        sheetLastActive = active.size
        sheetShown.clear()
        sheetShownPids = emptyList()
        fun tabLabel(tab: Int, n: Int): String = getString(
            when (tab) {
                TAB_FOUND -> R.string.tab_found
                TAB_DL -> R.string.tab_downloading
                else -> R.string.tab_done
            },
            n,
        )
        sheetButtons.forEach { (tab, b) ->
            val n = when (tab) {
                TAB_FOUND -> items.size
                TAB_DL -> active.size + paused.size
                else -> dls.size
            }
            b.text = tabLabel(tab, n)
            val selected = tab == sheetTab
            b.background = if (selected) ContextCompat.getDrawable(this@MainActivity, R.drawable.chip_bg) else null
            b.setTextColor(if (selected) 0xFF111629.toInt() else 0xFF93A4C3.toInt())
            b.setTypeface(null, if (selected) Typeface.BOLD else Typeface.NORMAL)
        }
        // v1.6.0: one-shot ล้างทั้งหมด chip on the finished tab (kept out of
        // item rows so it cannot scroll away with the list)
        container.findViewWithTag<View>(TAG_CLEAR_ALL)?.let { container.removeView(it) }
        if (sheetTab == TAB_DONE && dls.isNotEmpty()) {
            container.addView(TextView(this).apply {
                tag = TAG_CLEAR_ALL
                text = getString(R.string.clear_all)
                background = ContextCompat.getDrawable(this@MainActivity, R.drawable.chip_bg)
                setTextColor(0xFF111629.toInt())
                textSize = 13f
                setPadding(26, 12, 26, 12)
                setOnClickListener { clearAllDownloads() }
            })
        }
        when (sheetTab) {
            TAB_FOUND -> {
                if (items.isEmpty()) container.addView(sheetEmptyBox(getString(R.string.empty_found)))
                for (m in items.take(15)) container.addView(bindMediaRow(m))
            }
            TAB_DL -> {
                sheetShownPids = active.take(15).map { it.pid }
                if (active.isEmpty() && paused.isEmpty())
                    container.addView(sheetEmptyBox(getString(R.string.empty_downloading)))
                for (j in active.take(15)) {
                    val row = bindDownloadingRow(j)
                    sheetShown[j.pid] = row
                    container.addView(row)
                }
                // v1.7.0: หยุดพักไว้ - kept .part files, ดาวน์โหลดต่อ resumes
                for (p in paused.take(10)) container.addView(bindPausedRow(p))
            }
            else -> {
                if (dls.isEmpty()) container.addView(sheetEmptyBox(getString(R.string.empty_done)))
                for (d in dls.take(15)) container.addView(bindDoneRow(d))
            }
        }
    }

    /** v1.5.0: one row of the กำลังดาวน์โหลด tab - live percent, determinate
     *  bar and a per-download ยกเลิก button (EXTRA_PID -> engine process kill). */
    private fun bindDownloadingRow(j: DownloadJobs.Job): View {
        val row = layoutInflater.inflate(R.layout.item_download, sheetContainer, false)
        val label = row.findViewById<TextView>(R.id.dLabel)
        val pct = row.findViewById<TextView>(R.id.dPct)
        val bar = row.findViewById<ProgressBar>(R.id.dBar)
        fun paint() {
            label.text = j.title.ifEmpty { Downloader.urlStem(j.url) }
            if (j.percent >= 0) {
                pct.text = "$j.percent%"
                bar.isIndeterminate = false
                bar.progress = j.percent
            } else {
                pct.text = "…"
                bar.isIndeterminate = true
            }
        }
        paint()
        // v1.7.0: หยุดพัก - keep the .part files, resume later from the sheet
        row.findViewById<TextView>(R.id.dPause).setOnClickListener {
            FileLog.event("download_pause_click", mapOf("url" to j.url.take(200), "pid" to j.pid))
            StreamDownloadService.pause(this@MainActivity, j.pid)
            Toast.makeText(this, R.string.download_paused_toast, Toast.LENGTH_SHORT).show()
            renderSheet()
        }
        row.findViewById<TextView>(R.id.dCancel).setOnClickListener {
            FileLog.event("download_cancel_click", mapOf("url" to j.url.take(200), "pid" to j.pid))
            FileLog.app("INFO", "dl", "cancel requested: ${j.url}")
            StreamDownloadService.cancel(this@MainActivity, j.pid)
            Toast.makeText(this, R.string.download_cancelled, Toast.LENGTH_SHORT).show()
            renderSheet()
        }
        return row
    }

    /** v1.7.0: one row of the หยุดพักไว้ section - ดาวน์โหลดต่อ re-runs the
     *  SAME yt-dlp command (engine resumes its kept .part files) or ลบ drops
     *  the entry together with the partial artifacts. */
    private fun bindPausedRow(p: PausedJobs.Entry): View {
        val row = layoutInflater.inflate(R.layout.item_paused, sheetContainer, false)
        row.findViewById<TextView>(R.id.pLabel).text = p.title.ifEmpty { p.titleBase }
        val workDir = StreamArgs.engineWorkDir(getExternalFilesDir(null) ?: filesDir)
        val partials = StreamArgs.partialFilesFor(workDir, p.titleBase)
        val ago = android.text.format.DateUtils.getRelativeTimeSpanString(
            p.savedAt, System.currentTimeMillis(), android.text.format.DateUtils.MINUTE_IN_MILLIS)
        row.findViewById<TextView>(R.id.pMeta).text =
            getString(R.string.paused_meta, ago.toString(), partials.size)
        row.findViewById<TextView>(R.id.pResume).setOnClickListener {
            FileLog.event("download_resume_click", mapOf("url" to p.url.take(200)))
            StreamDownloadService.resume(this@MainActivity, p)
            Toast.makeText(this, R.string.download_resumed_toast, Toast.LENGTH_SHORT).show()
            renderSheet()
        }
        row.findViewById<TextView>(R.id.pDelete).setOnClickListener { deletePaused(p) }
        return row
    }

    /** v1.7.0: forget a paused job and remove its partial artifacts - the
     *  finished file is never matched (partialFilesFor takes only
     *  .part/.part-FragN/.ytdl names). */
    private fun deletePaused(p: PausedJobs.Entry) {
        val workDir = StreamArgs.engineWorkDir(getExternalFilesDir(null) ?: filesDir)
        var n = 0
        for (f in StreamArgs.partialFilesFor(workDir, p.titleBase)) if (f.delete()) n++
        try {
            PausedJobs.remove(p.titleBase)
        } catch (e: Exception) {
            FileLog.app("ERROR", "dl", "paused unbook failed: $e")
        }
        FileLog.event("paused_deleted", mapOf("title_base" to p.titleBase.take(120), "files" to n))
        FileLog.app("INFO", "dl", "paused download deleted: ${p.titleBase} ($n partial files)")
        Toast.makeText(this, getString(R.string.paused_deleted_toast, n), Toast.LENGTH_SHORT).show()
        renderSheet()
    }

    /** One row of the พบวิดีโอ tab (same behaviour as pre-v1.5.0). */
    private fun bindMediaRow(m: MediaStore.Item): View {
        val row = layoutInflater.inflate(R.layout.item_media, sheetContainer, false)
        row.findViewById<TextView>(R.id.mLabel).text =
            "${m.label.ifEmpty { m.kind }}  ·  ${m.kind}"
        row.findViewById<TextView>(R.id.mUrl).text = m.url
        row.findViewById<TextView>(R.id.mDl).setOnClickListener { tryDownload(m.url, m.title) }
        row.findViewById<TextView>(R.id.mCopy).setOnClickListener {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("url", m.url))
            Toast.makeText(this, R.string.copied, Toast.LENGTH_SHORT).show()
        }
        // embed players: opening the embed page as the top page lets the
        // detector find the real video URLs inside it (same idea as the
        // desktop toolbar's "เปิดหน้า embed" button)
        row.findViewById<TextView>(R.id.mOpen).visibility =
            if (m.kind == "embed") View.VISIBLE else View.GONE
        row.findViewById<TextView>(R.id.mOpen).setOnClickListener {
            FileLog.event("embed_open", mapOf("url" to m.url))
            FileLog.app("INFO", "nav", "open embed page: ${m.url}")
            navigate(m.url)
            sheetDialog?.dismiss()
        }
        return row
    }

    /** One row of the ดาวน์โหลดเสร็จแล้ว tab: file + ลบไฟล์ (v1.1.8 behaviour). */
    private fun bindDoneRow(d: DownloadCleaner.Candidate): View {
        val row = layoutInflater.inflate(R.layout.item_media, sheetContainer, false)
        row.findViewById<TextView>(R.id.mLabel).text = d.name
        val info = row.findViewById<TextView>(R.id.mUrl)
        info.text = Downloader.humanSize(d.size)
        info.visibility = if (d.size > 0) View.VISIBLE else View.GONE
        row.findViewById<TextView>(R.id.mDl).visibility = View.GONE
        row.findViewById<TextView>(R.id.mCopy).visibility = View.GONE
        row.findViewById<TextView>(R.id.mOpen).visibility = View.GONE
        val del = row.findViewById<TextView>(R.id.mDel)
        del.visibility = View.VISIBLE
        del.setOnClickListener { deleteDownload(d) { renderSheet() } }
        return row
    }

    /** v1.6.0: confirm, then delete EVERY finished download (single dialog
     *  covers the whole tab; honest ลบแล้ว N/M toast for ghost rows). */
    private fun clearAllDownloads() {
        val dls = DownloadCleaner.systemList(this)
        if (dls.isEmpty()) return
        AlertDialog.Builder(this)
            .setMessage(getString(R.string.clear_all_confirm, dls.size))
            .setPositiveButton(R.string.delete) { _, _ ->
                val (deleted, total) = DownloadCleaner.deleteAll(this@MainActivity, dls)
                Toast.makeText(
                    this,
                    getString(R.string.clear_all_done, deleted, total),
                    Toast.LENGTH_SHORT,
                ).show()
                renderSheet()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    /** v1.6.0: a stream download finished (or failed) - surface it in-app:
     *  Snackbar with a เปิด action that opens the media sheet, plus a
     *  temporary status chip for when the toolbar chip is hidden. The open
     *  sheet refreshes too (debounced so taps are not stolen mid-burst). */
    private fun onDlState(ok: Boolean, file: String) {
        val now = System.currentTimeMillis()
        val repeated = now - lastDlNoticeMs < 800 && ok == lastDlNoticeOk
        lastDlNoticeMs = now
        lastDlNoticeOk = ok
        val text = if (ok) getString(R.string.dl_done_inapp, file.ifEmpty { "-" })
                   else getString(R.string.dl_failed_inapp)
        val root = window.decorView as? ViewGroup
        if (root != null) {
            Snackbar.make(root, text, Snackbar.LENGTH_LONG)
                .setAction(R.string.notif_open) { showMediaSheet() }
                .show()
        }
        chipMedia.text = if (ok) text else getString(R.string.dl_failed_inapp)
        chipMedia.visibility = View.VISIBLE
        if (sheetDialog?.isShowing == true && !repeated) renderSheet()
        if (ok) {
            // restore the normal chip (count or live percent) after a beat
            java.util.Timer("dl-notice-reset").schedule(object : java.util.TimerTask() {
                override fun run() {
                    runOnUiThread { updateChip() }
                }
            }, 8000)
        }
    }

    private fun sheetEmptyBox(msg: String): View = TextView(this).apply {
        text = msg
        setTextColor(0xFF93A4C3.toInt())
        textSize = 13f
        setPadding(0, 24, 0, 8)
    }

    /** Refresh the open sheet only when something visible changed. On the
     *  downloading tab the percents update IN PLACE while the job set is
     *  unchanged - re-rendering every tick would steal button presses. */
    private fun updateOpenSheet() {
        val d = sheetDialog ?: return
        if (!d.isShowing) return
        if (sheetTab == TAB_DL) {
            val active = StreamDownloadService.activeJobs()
            if (active.map { it.pid } == sheetShownPids) {
                if (active.isEmpty()) return
                for (j in active) {
                    val row = sheetShown[j.pid] ?: continue
                    if (j.percent >= 0) {
                        row.findViewById<TextView>(R.id.dPct).text = "$j.percent%"
                        val bar = row.findViewById<ProgressBar>(R.id.dBar)
                        bar.isIndeterminate = false
                        bar.progress = j.percent
                    }
                }
                return
            }
            renderSheet() // a job started/finished - rebuild the tab once
            return
        }
        if (StreamDownloadService.activeJobs().size != sheetLastActive ||
            MediaStore.list().size != sheetFoundCount
        ) renderSheet()
    }

    /** Confirm, then delete one finished download (file + list entry). */
    private fun deleteDownload(d: DownloadCleaner.Candidate, onDeleted: () -> Unit) {
        AlertDialog.Builder(this)
            .setMessage(getString(R.string.delete_confirm, d.name))
            .setPositiveButton(R.string.delete) { _, _ ->
                val ok = DownloadCleaner.systemDelete(this@MainActivity, d)
                FileLog.event(
                    if (ok) "download_deleted" else "download_delete_error",
                    mapOf("file" to d.name.take(120), "dm_id" to d.id),
                )
                FileLog.app(if (ok) "INFO" else "ERROR", "dl", "delete ${d.name}: ok=$ok")
                Toast.makeText(this, if (ok) R.string.deleted else R.string.delete_failed, Toast.LENGTH_SHORT).show()
                onDeleted()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun tryDownload(url: String, title: String) {
        // v1.3.5: blob: URLs exist only inside the page (MSE players) - yt-dlp
        // cannot reach them ("[Blob] A blob URL exists only locally in your
        // browser"); the usable sources are the m3u8/mpd/direct URLs the
        // detector also lists for the same page.
        if (url.startsWith("blob:")) {
            FileLog.event("download_skipped_blob", mapOf("url" to url.take(120)))
            FileLog.app("INFO", "dl", "skip blob URL (browser-local only): $url")
            Toast.makeText(this, R.string.blob_unsupported, Toast.LENGTH_SHORT).show()
            return
        }
        FileLog.event("download_click", mapOf("url" to url))
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P) {
            requestPermissions(arrayOf(android.Manifest.permission.WRITE_EXTERNAL_STORAGE), 42)
        }
        try {
            // v1.3.4: PAGE urls (player/embed pages, no media extension in the
            // path) go to the yt-dlp engine too - DownloadManager would just
            // save the HTML of the player page as "title.mp4" (the reported
            // ".mp4 that is really HTML" bug).
            when (Downloader.routeOf(url)) {
                Downloader.Route.DIRECT_FILE -> Downloader.enqueue(this, url, title)
                Downloader.Route.STREAM, Downloader.Route.PAGE -> StreamDownloadService.start(this, url, title)
                null -> FileLog.app("INFO", "dl", "unusable URL - not downloaded: $url")
            }
            Toast.makeText(this, R.string.download_started, Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            FileLog.app("ERROR", "dl", "download failed: $e")
            Toast.makeText(this, getString(R.string.download_failed, e.message), Toast.LENGTH_LONG).show()
        }
    }

    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    override fun onDestroy() {
        progressTicker?.cancel()
        FileLog.event("app_exit")
        web.destroy()
        super.onDestroy()
    }

    companion object {
        // v1.5.0: media sheet tabs
        private const val TAB_FOUND = 0
        private const val TAB_DL = 1
        private const val TAB_DONE = 2

        /** v1.6.0: tag of the ล้างทั้งหมด chip inside the sheet container. */
        private const val TAG_CLEAR_ALL = "clear_all_chip"

        /** Shown after repeated renderer deaths on the same page. */
        private const val ERROR_PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
body{background:#0B1020;color:#E8ECF4;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
div{text-align:center;max-width:32em;padding:24px;line-height:1.6}
</style></head><body><div>
<h2>⚠️ แสดงผลหน้านี้ไม่สำเร็จ</h2>
<p>หน้าเว็บนี้ทำให้ตัวแสดงผลของแอปล่ม (มักเกิดกับหน้าที่มีโฆษณาหนัก ๆ)<br>ป้อน URL อื่นในช่องด้านล่างได้เลย</p>
</div></body></html>"""
    }
}
