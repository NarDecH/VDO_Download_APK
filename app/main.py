"""VDO Grabber - desktop app entry point.

A browser window (Edge WebView2) that opens any website the user wants.
Video detection is injected into every page (DOM scan + network hooks) and a
floating toolbar offers a Download button for the currently displayed video.
Downloads run through yt-dlp; everything is logged in fine detail.

Usage:
  python main.py [start-url]        normal GUI run
  python main.py --selftest         headless smoke test (CI / packaging check)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser

# Make core importable both as `python app/main.py` and frozen.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402

from core.detector import DETECT_JS, TOOLBAR_JS, build_report  # noqa: E402
from core.downloader import DownloadManager  # noqa: E402
from core.logger import APP_NAME, APP_VERSION, LogManager, default_data_dir  # noqa: E402
from core.settings import Settings  # noqa: E402
from core.ytdlp_mgr import EngineManager  # noqa: E402


class MediaStore:
    """Deduplicated list of videos detected across pages."""

    def __init__(self, log, limit: int = 300):
        self.log = log
        self.limit = limit
        self.items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def add(self, report: dict) -> bool:
        key = report["url"][:400]
        with self._lock:
            new = key not in self.items
            self.items[key] = report
            if len(self.items) > self.limit:
                for k in list(self.items)[: len(self.items) - self.limit]:
                    del self.items[k]
        return new

    def list(self) -> list[dict]:
        with self._lock:
            return list(self.items.values())[-self.limit :]


class Api:
    """Bridge exposed to JS as window.pywebview.api (both windows).

    IMPORTANT: keep this object method-only. pywebview 6 walks the api
    object's attribute graph to build its JS bridge; storing a back-reference
    to App (which holds webview Windows) causes infinite recursion.
    The app singleton is read via the module-level `_APP` instead.
    """

    # ------------------------------------------------------------ navigation
    def navigate(self, url: str) -> None:
        _APP.navigate(url)

    def nav_back(self) -> None:
        _APP.browser.evaluate_js("history.back()")

    def nav_fwd(self) -> None:
        _APP.browser.evaluate_js("history.forward()")

    def nav_reload(self) -> None:
        _APP.browser.evaluate_js("location.reload()")

    def detect_now(self) -> None:
        _APP.logm.log("manual detection requested", event="detect_manual")
        _APP.browser.evaluate_js("window.__vgDetect && window.__vgDetect.scan('manual')")

    # -------------------------------------------------------- media reporting
    def report_media(self, payload_json: str) -> None:
        try:
            page_url = _APP.browser.get_current_url() or ""
        except Exception:
            page_url = ""
        rep = build_report(payload_json, page_url)
        if not rep:
            return
        rep["first_seen"] = time.time()
        if _APP.media.add(rep):
            _APP.logm.log("media found: %s (%s) via %s" % (rep["url"][:120], rep["kind"], rep["via"]),
                          event="media_found", url=rep["url"], kind=rep["kind"], via=rep["via"],
                          page=rep["page_url"][:200])
            _APP.push_browser("media_found", rep)
            _APP.push_control("media_found", rep)

    # ------------------------------------------------------------- downloads
    def get_formats(self, url: str, referrer: str = "") -> dict:
        return _APP.downloads.get_formats(url, referrer)

    def download_start(self, spec: dict) -> dict:
        url = str(spec.get("url") or "").strip()
        if not url:
            return {"ok": False, "error": "empty url"}
        job = _APP.downloads.start(
            url,
            kind=str(spec.get("kind") or "media"),
            format_id=str(spec.get("format_id") or ""),
            title=str(spec.get("title") or ""),
            page_url=str(spec.get("page_url") or ""),
            referrer=str(spec.get("referrer") or ""),
        )
        _APP.push_control("download_update", job.public())
        return {"ok": True, "job": job.public()}

    def download_cancel(self, job_id: str) -> dict:
        return _APP.downloads.cancel(job_id)

    def downloads_list(self) -> list[dict]:
        return _APP.downloads.list()

    def download_open_folder(self, job_id: str = "") -> None:
        d = _APP.downloads.jobs[job_id].out_dir if job_id in _APP.downloads.jobs else _APP.settings.get("download_dir")
        os.makedirs(d, exist_ok=True)
        _APP.logm.log("open folder: %s" % d, event="open_folder", path=d)
        os.startfile(d)  # noqa: S606

    def download_open_file(self, job_id: str) -> None:
        job = _APP.downloads.jobs.get(job_id)
        if job and job.filepath and os.path.exists(job.filepath):
            os.startfile(job.filepath)  # noqa: S606

    def media_list(self) -> list[dict]:
        return _APP.media.list()

    # ----------------------------------------------------------- control UI
    def show_control(self) -> None:
        _APP.show_control()

    def get_state(self) -> dict:
        return {
            "version": APP_VERSION,
            "app_name": APP_NAME,
            "settings": _APP.settings.data,
            "ytdlp_version": _APP.engines.ytdlp_version,
            "has_ffmpeg": bool(_APP.engines.ffmpeg_path()),
            "downloads": _APP.downloads.list(),
            "media": _APP.media.list(),
            "platform": sys.platform,
            "frozen": bool(getattr(sys, "frozen", False)),
        }

    def set_setting(self, key: str, value) -> dict:
        _APP.settings.set(key, value)
        _APP.logm.log("setting changed: %s = %r" % (key, value), event="setting_changed", key=key, value=value)
        if key == "log_level":
            _APP.logm.set_level(str(value))
        if key == "max_concurrent":
            _APP.downloads._sem = threading.Semaphore(int(value) or 3)
        return {"ok": True}

    def choose_download_dir(self) -> dict:
        """Native folder picker on the browser window."""
        result = {"ok": False, "path": ""}
        try:
            files = _APP.browser.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)
            if files:
                result = {"ok": True, "path": files[0] if isinstance(files, (list, tuple)) else files}
                _APP.settings.set("download_dir", result["path"])
        except Exception as e:
            _APP.logm.exception("folder picker", e)
            result = {"ok": False, "error": repr(e)}
        return result

    # ----------------------------------------------------------------- logs
    def get_log_tail(self, name: str, lines: int = 200) -> str:
        return _APP.logm.tail(name, int(lines))

    def open_logs_folder(self) -> None:
        _APP.logm.open_logs_folder()

    def export_diagnostics(self) -> dict:
        try:
            path = _APP.logm.export_diagnostics()
            return {"ok": True, "path": path}
        except Exception as e:
            _APP.logm.exception("diagnostics export", e)
            return {"ok": False, "error": repr(e)}

    def update_engines(self) -> dict:
        return _APP.engines.update_ytdlp()

    def open_external(self, url: str) -> None:
        webbrowser.open(url)

    def log_line(self, msg: str) -> None:
        """JS console bridge (from control window)."""
        _APP.logm.log("[js] %s" % msg, level="debug")


_APP: "App | None" = None


class App:
    def __init__(self, start_url: str | None = None):
        self.data_dir = default_data_dir()
        self.logm = LogManager(self.data_dir)
        self.settings = Settings(self.data_dir, self.logm)
        self.logm.set_level(self.settings.get("log_level"))
        self.engines = EngineManager(self.data_dir, self.logm)
        self.media = MediaStore(self.logm)
        self.downloads = DownloadManager(self.settings, self.engines, self.logm, push_ui=self._push)
        self.api = Api()
        self.browser: webview.Window | None = None
        self.control: webview.Window | None = None
        self.ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
        self.start_url = start_url or self.settings.get("start_page")
        self._watch_stop = False
        global _APP
        _APP = self

        self.logm.log(
            "%s v%s starting | data=%s | frozen=%s | python=%s" % (APP_NAME, APP_VERSION, self.data_dir, getattr(sys, "frozen", False), sys.version.split()[0]),
            event="app_start", version=APP_VERSION, data_dir=self.data_dir, frozen=bool(getattr(sys, "frozen", False)),
        )

    # ------------------------------------------------------------------ push
    def _push(self, event: str, payload) -> None:
        self.push_control(event, payload)

    def push_control(self, event: str, payload) -> None:
        if not self.control:
            return
        try:
            self.control.evaluate_js("window.__onEvent && window.__onEvent(%s, %s); 1;"
                                     % (json.dumps(event), json.dumps(payload, ensure_ascii=False, default=str)))
        except Exception as e:
            self.logm.log("push_control failed: %r" % e, level="debug")

    def push_browser(self, event: str, payload) -> None:
        if not self.browser:
            return
        try:
            if event == "media_found":
                self.browser.evaluate_js("window.__vgToolbar && window.__vgToolbar.media(%s); 1;"
                                         % json.dumps(payload, ensure_ascii=False, default=str))
        except Exception as e:
            self.logm.log("push_browser failed: %r" % e, level="debug")

    # ------------------------------------------------------------- lifecycle
    def start(self):
        self.browser = webview.create_window(
            "%s — เปิดเว็บ แล้วกดดาวน์โหลดวิดีโอ" % APP_NAME,
            url=self.start_url,
            js_api=self.api,
            width=1360, height=880, min_size=(900, 600),
            background_color="#0f1729",
        )
        self.browser.events.loaded += self._on_loaded

        self.control = webview.create_window(
            "%s — Control Center" % APP_NAME,
            url=os.path.join(self.ui_dir, "index.html"),
            js_api=self.api,
            width=980, height=720, min_size=(760, 560),
            hidden=True, background_color="#0b1020",
        )
        webview.start(func=self._post_start, gui="edgechromium", debug="--debug" in sys.argv)

    def _post_start(self):
        """Runs once the GUI loop is alive: engine warm-up + watchdog."""
        def warm():
            try:
                self.engines.ensure_ytdlp()
                self.push_control("state_changed", self.api.get_state())
            except Exception as e:
                self.logm.exception("engine warm-up", e)
        threading.Thread(target=warm, name="warmup", daemon=True).start()
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()

    def _watchdog(self):
        """Re-inject detection/toolbar after full navigations that race the
        loaded event, and keep the URL box in sync."""
        inject = "window.__vgDetect || (%s); window.__vgToolbar || (%s); 'ok'" % (DETECT_JS, TOOLBAR_JS)
        sync = "window.__vgToolbar && window.__vgToolbar.sync(); (window.__vgDetect && window.__vgDetect.scan('watchdog')) || 0"
        while not self._watch_stop:
            time.sleep(3)
            try:
                if self.browser:
                    alive = self.browser.evaluate_js("!!(window.__vgDetect && window.__vgToolbar)")
                    if not alive:
                        self.logm.log("watchdog re-injecting page hooks", level="debug", event="page_reinject")
                        self.browser.evaluate_js(DETECT_JS)
                        self.browser.evaluate_js(TOOLBAR_JS)
                    else:
                        self.browser.evaluate_js(sync)
            except Exception:
                pass  # window busy or closing

    def navigate(self, url: str) -> None:
        if not url:
            return
        if not url.startswith(("http://", "https://", "file://")):
            if url.startswith("localhost") or url.startswith("127."):
                url = "http://" + url
            elif "." in url and " " not in url:
                url = "https://" + url
            else:
                url = "https://duckduckgo.com/?q=" + url  # search fallback
        self.logm.log("navigate -> %s" % url, event="navigate", url=url)
        try:
            self.browser.load_url(url)
        except Exception as e:
            self.logm.exception("navigate", e)

    def _on_loaded(self):
        """Every completed page load: (re)inject hooks + toolbar, log it."""
        try:
            url = self.browser.get_current_url() or "?"
            self.logm.log("page loaded: %s" % url, event="page_loaded", url=url)
            self.browser.evaluate_js(DETECT_JS)
            self.browser.evaluate_js(TOOLBAR_JS)
            self.push_control("page_loaded", {"url": url})
        except Exception as e:
            self.logm.log("on_loaded injection failed: %r" % e, level="warning", event="inject_error", error=repr(e))

    def show_control(self):
        self.logm.log("control center shown", event="control_shown")
        self.control.show()
        try:
            self.control.evaluate_js("window.__onEvent && window.__onEvent('state_changed', %s); 1;" % json.dumps(self.api.get_state(), ensure_ascii=False, default=str))
        except Exception:
            pass


def selftest() -> int:
    """Headless smoke test: detection JS + real download through yt-dlp."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    app = App()
    results = {}

    def push_stub(event, payload=None):
        results.setdefault("events", []).append((event, payload))

    app.downloads.push_ui = push_stub

    # --- tiny local media server -----------------------------------------
    mp4 = (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * (256 * 1024 - 24))
    m3u8 = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body, ctype = (m3u8, "application/vnd.apple.mpegurl") if self.path.endswith(".m3u8") else (mp4, "video/mp4")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    app.logm.log("selftest: local media server on port %d" % port, event="selftest_server", port=port)

    # --- 1) engines --------------------------------------------------------
    try:
        ytdlp = app.engines.ensure_ytdlp()
        results["ytdlp"] = {"ok": os.path.exists(ytdlp), "version": app.engines.ytdlp_version}
    except Exception as e:
        results["ytdlp"] = {"ok": False, "error": repr(e)}

    # --- 2) detection JS in a real webview ---------------------------------
    src_page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "testpage.html")
    with open(src_page, "r", encoding="utf-8") as f:
        html = f.read().replace("__PORT__", str(port))
    tmp_page = os.path.join(app.data_dir, "selftest-page.html")
    with open(tmp_page, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        w = webview.create_window("selftest", url="file:///" + tmp_page.replace("\\", "/"),
                                  js_api=app.api, width=640, height=480, hidden=True)
        app.browser = w

        def on_loaded():
            try:
                time.sleep(1.5)                       # page + pywebview bridge ready
                w.evaluate_js(DETECT_JS)              # arm detector (same as _on_loaded)
                time.sleep(4)                         # DOM scan + fetch hook + periodic scans
            except Exception as e:
                app.logm.exception("selftest injection", e)
            try:
                w.destroy()
            except Exception:
                pass

        webview.start(func=on_loaded, gui="edgechromium")
        kinds = {r["kind"] for r in app.media.list()}
        results["detection"] = {
            "ok": ("mp4" in kinds or "media" in kinds) and "m3u8" in kinds,
            "found": [{"kind": r["kind"], "via": r["via"], "url": r["url"][:80]} for r in app.media.list()],
        }
    except Exception as e:
        results["detection"] = {"ok": False, "error": repr(e)}
        app.logm.exception("selftest detection", e)

    # --- 3) real download via yt-dlp ---------------------------------------
    try:
        job = app.downloads.start(f"http://127.0.0.1:{port}/video.mp4", title="selftest", kind="media")
        for _ in range(120):
            time.sleep(0.5)
            if job.status in ("done", "error", "canceled"):
                break
        ok_file = job.status == "done" and job.filepath and os.path.exists(job.filepath) and os.path.getsize(job.filepath) >= len(mp4) - 1024
        results["download"] = {"ok": bool(ok_file), "status": job.status, "file": job.filepath, "error": job.error}
        if ok_file:
            os.remove(job.filepath)
    except Exception as e:
        results["download"] = {"ok": False, "error": repr(e)}

    srv.shutdown()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    passed = results["ytdlp"]["ok"] and results["detection"]["ok"] and results["download"]["ok"]
    app.logm.log("SELFTEST %s" % ("PASS" if passed else "FAIL"), event="selftest", passed=passed,
                 results={k: v.get("ok") for k, v in results.items() if isinstance(v, dict)})
    return 0 if passed else 1


def main() -> int:
    if "--selftest" in sys.argv:
        try:
            return selftest()
        except SystemExit:
            raise
        except BaseException:
            import traceback
            from core.logger import default_data_dir as _ddd
            os.makedirs(os.path.join(_ddd(), "logs"), exist_ok=True)
            with open(os.path.join(_ddd(), "logs", "crash.log"), "a", encoding="utf-8") as f:
                f.write("\n=== selftest crash ===\n" + traceback.format_exc())
            return 1
    args = [a for a in sys.argv[1:] if not a.startswith("-") and a not in ("--debug",)]
    app = App(start_url=args[0] if args else None)
    try:
        app.start()
    except Exception as e:
        app.logm.exception("app run", e)
        raise
    finally:
        app.logm.log("app exit", event="app_exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
