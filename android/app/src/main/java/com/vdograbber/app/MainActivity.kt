package com.vdograbber.app

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
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
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
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
        FileLog.app("INFO", "app", "VDO Grabber 1.3.1 starting (Android ${Build.VERSION.RELEASE}, ${Build.MODEL})")
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

        // yt-dlp + ffmpeg init for on-device stream downloads (async, engine
        // is installed into files/ on first launch - docs/plan-android-hls.md)
        thread(name = "engine-init") {
            try {
                com.yausername.youtubedl_android.YoutubeDL.getInstance().init(applicationContext)
                com.yausername.ffmpeg.FFmpeg.getInstance().init(applicationContext)
                val v = com.yausername.youtubedl_android.YoutubeDL.getInstance().version(applicationContext)
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
                try {
                    val url = request.url.toString()
                    val ext = Downloader.extOf(url)
                    if (ext.isNotEmpty() && MediaStore.add(url, ext, "network request", url.substringBeforeLast('/'), web.title ?: "")) {
                        FileLog.event("media_found", mapOf("url" to url, "kind" to ext, "via" to "shouldInterceptRequest"))
                        runOnUiThread { updateChip() }
                    }
                } catch (t: Throwable) {
                    FileLog.app("ERROR", "sniff", "intercept failed: $t")
                    FileLog.event("intercept_error", mapOf("error" to t.toString().take(300)))
                }
                return null
            }

            override fun onPageFinished(view: WebView, url: String) {
                renderGoneCount = 0 // successful load -> crash-loop guard reset
                FileLog.event("page_loaded", mapOf("url" to url))
                FileLog.app("INFO", "nav", "page loaded: $url")
                view.evaluateJavascript(Detector.INJECT_JS, null)
                urlBox.setText(url)
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

    private fun showMediaSheet() {
        val items = MediaStore.list()
        val dls = DownloadCleaner.systemList(this)
        if (items.isEmpty() && dls.isEmpty()) {
            Toast.makeText(this, R.string.no_media, Toast.LENGTH_SHORT).show()
            return
        }
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 32, 48, 32)
        }
        val dialog = AlertDialog.Builder(this)
            .setTitle(
                if (items.isNotEmpty()) getString(R.string.found_title, items.size)
                else getString(R.string.downloads_title, dls.size)
            )
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
        for (m in items.take(15)) {
            val row = layoutInflater.inflate(R.layout.item_media, container, false)
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
                dialog.dismiss()
            }
            container.addView(row)
        }

        // v1.1.8: finished downloads from Downloads/VDOGrabber, each with a
        // ลบไฟล์ button (file + list entry disappear)
        if (dls.isNotEmpty()) {
            val head = TextView(this).apply {
                text = getString(R.string.downloads_section)
                setTextColor(0xFF93A4C3.toInt())
                textSize = 13f
                setPadding(0, 24, 0, 4)
            }
            container.addView(head)
            for (d in dls.take(15)) {
                val row = layoutInflater.inflate(R.layout.item_media, container, false)
                row.findViewById<TextView>(R.id.mLabel).text = d.name
                val info = row.findViewById<TextView>(R.id.mUrl)
                info.text = Downloader.humanSize(d.size)
                info.visibility = if (d.size > 0) View.VISIBLE else View.GONE
                row.findViewById<TextView>(R.id.mDl).visibility = View.GONE
                row.findViewById<TextView>(R.id.mCopy).visibility = View.GONE
                row.findViewById<TextView>(R.id.mOpen).visibility = View.GONE
                val del = row.findViewById<TextView>(R.id.mDel)
                del.visibility = View.VISIBLE
                del.setOnClickListener { deleteDownload(d) { row.visibility = View.GONE } }
                container.addView(row)
            }
        }
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
        FileLog.event("download_click", mapOf("url" to url))
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P) {
            requestPermissions(arrayOf(android.Manifest.permission.WRITE_EXTERNAL_STORAGE), 42)
        }
        try {
            if (Downloader.isStream(url)) {
                // HLS/DASH or site pages: on-device yt-dlp engine (plan-android-hls.md)
                StreamDownloadService.start(this, url, title)
            } else {
                Downloader.enqueue(this, url, title)
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
        FileLog.event("app_exit")
        web.destroy()
        super.onDestroy()
    }

    companion object {
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
