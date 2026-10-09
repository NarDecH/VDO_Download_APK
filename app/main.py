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

import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

# Make core importable both as `python app/main.py` and frozen.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402

from core.detector import DETECT_JS, TOOLBAR_JS, build_report, check_pair_sync  # noqa: E402
from core.downloader import DownloadManager, is_usable_download_url  # noqa: E402
from core.logger import APP_NAME, APP_VERSION, LogManager, default_data_dir  # noqa: E402
from core.settings import SECRET_KEYS, Settings, merge_exclusion_patterns, norm_exclusion, url_excluded  # noqa: E402
from core.sieve import DEVICE_CLIENT_NAME, SieveError, SieveManager, device_login  # noqa: E402
from core.ytdlp_mgr import EngineManager  # noqa: E402


def public_settings() -> dict:
    """settings.json with credentials replaced by presence flags, safe to hand
    to the Control Center UI (the raw values never leave the process)."""
    data = {k: v for k, v in _APP.settings.data.items() if k not in SECRET_KEYS}
    data["has_github_pat"] = bool(_APP.settings.get("github_pat"))
    data["has_sieve_key"] = _APP.sieve.configured if _APP else False
    return data


def read_clipboard_text() -> str:
    """Current Windows clipboard text (CF_UNICODETEXT) via ctypes - no extra
    dependency and it works in the frozen exe. Returns '' when the clipboard
    is empty, holds no text, or is owned by another process right now."""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        CF_UNICODETEXT = 13
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


def clipboard_url(raw: str) -> str:
    """Turn clipboard text into a loadable http(s) URL ('' when it does not
    look like one) - same rules as the Android app's openFromClipboard():
    scan lines and take the first URL-looking one (scheme kept as-is,
    bare domains get https:// prefixed)."""
    for raw_line in (raw or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip().strip("'\"")
        if not line:
            continue
        if line.lower().startswith(("http://", "https://")):
            return line
        if "@" in line or " " in line or "." not in line:
            continue
        return "https://" + line
    return ""


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
            self.items.pop(key, None)  # re-insert = touch -> true LRU order
            self.items[key] = report
            if len(self.items) > self.limit:
                for k in list(self.items)[: len(self.items) - self.limit]:
                    del self.items[k]
        return new

    def list(self) -> list[dict]:
        with self._lock:
            return list(self.items.values())[-self.limit :]

    def clear(self) -> None:
        with self._lock:
            self.items.clear()


class Api:
    """Bridge exposed to JS as window.pywebview.api (both windows).

    IMPORTANT: keep this object method-only. pywebview 6 walks the api
    object's attribute graph to build its JS bridge; storing a back-reference
    to App (which holds webview Windows) causes infinite recursion.
    The app singleton is read via the module-level `_APP` instead.
    """

    # ------------------------------------------------------------ navigation
    def navigate(self, url: str) -> None:
        # Defer the actual load: pywebview delivers the JS return-value callback
        # right after this method returns, and a synchronous load_url would wipe
        # the page (and the callback) first -> "returnValuesCallbacks ... is not
        # a function" spam. A short timer lets the callback win the race.
        _APP.logm.log("api.navigate called (deferred): %s" % url, level="debug", event="api_navigate", url=url)
        threading.Timer(0.08, _APP.navigate, args=(url,)).start()

    def nav_back(self) -> None:
        _APP.browser.evaluate_js("history.back()")

    def nav_fwd(self) -> None:
        _APP.browser.evaluate_js("history.forward()")

    def nav_reload(self) -> None:
        _APP.browser.evaluate_js("location.reload()")

    def detect_now(self) -> None:
        _APP.logm.log("manual detection requested", event="detect_manual")
        _APP.browser.evaluate_js("window.__vgDetect && window.__vgDetect.scan('manual')")

    def open_clipboard(self, mode: str = "go") -> dict:
        """Read a URL from the Windows clipboard. Clipboard is read on the
        Python side because WebView2 only grants navigator.clipboard.readText()
        after a permission prompt that does not reliably appear in the Control
        Center window.

        v1.9.4: mode="go" (default) navigates the browser window to the URL,
        mode="peek" only returns it so the in-page toolbar can put it in the
        urlbox and navigate itself (in-page location.href - never races the
        pywebview return-value callback)."""
        raw = read_clipboard_text()
        url = clipboard_url(raw)
        if not url:
            reason = "empty" if not raw.strip() else "no url"
            _APP.logm.log("open from clipboard: %s" % reason, level="debug",
                          event="clipboard_open", ok=False, reason=reason)
            return {"ok": False, "error": "คลิปบอร์ดว่าง" if reason == "empty" else "ในคลิปบอร์ดไม่มี URL"}
        if mode == "peek":
            # no app-level navigation, no clipboard_open event dup - the
            # caller (toolbar) logs its own clipboard_paste_to_urlbox
            return {"ok": True, "url": url}
        _APP.logm.log("open URL from clipboard: %s" % url[:120], event="clipboard_open", url=url)
        # Deferred like navigate(): let the JS return-value callback win first.
        threading.Timer(0.08, _APP.navigate, args=(url,)).start()
        return {"ok": True, "url": url}

    # -------------------------------------------------------- media reporting
    def report_media(self, payload_json: str) -> None:
        try:
            page_url = _APP.browser.get_current_url() or ""
        except Exception:
            page_url = ""
        rep = build_report(payload_json, page_url)
        if not rep:
            return
        # v1.2.0: belt-and-suspenders - never accept media from an excluded page
        if page_url and url_excluded(_APP.settings.get("exclusions") or [], page_url):
            _APP.logm.log("media ignored (page excluded): %s" % page_url[:120],
                          level="debug", event="media_ignored_excluded", page=page_url[:200])
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
        # v1.3.6 (Android parity): blob: URLs live only inside the page - the
        # engine would just answer "ERROR: [Blob] ... only locally in your browser"
        if not is_usable_download_url(url):
            _APP.logm.log("download rejected (blob URL): %s" % url[:120],
                          level="warning", event="download_skipped_blob", url=url)
            return {"ok": False, "error": "blob: URL มีอยู่แค่ในหน้าเว็บ — เลือกลิงก์ไฟล์/ลิงก์สตรีมจากรายการ 🎬 แทน"}
        job = _APP.downloads.start(
            url,
            kind=str(spec.get("kind") or "media"),
            format_id=str(spec.get("format_id") or ""),
            title=str(spec.get("title") or ""),
            page_url=str(spec.get("page_url") or ""),
            referrer=str(spec.get("referrer") or ""),
            page_title=str(spec.get("page_title") or ""),
        )
        _APP.push_control("download_update", job.public())
        return {"ok": True, "job": job.public()}

    def download_cancel(self, job_id: str) -> dict:
        return _APP.downloads.cancel(job_id)

    def download_pause(self, job_id: str) -> dict:
        """v1.8.0: หยุดพัก - keep yt-dlp's .part files, resume later."""
        return _APP.downloads.pause(job_id)

    def download_resume(self, job_id: str) -> dict:
        """v1.8.0: ดาวน์โหลดต่อ - re-run the stored template over the kept
        .part fragments."""
        return _APP.downloads.resume(job_id)

    def downloads_list(self) -> list[dict]:
        return _APP.downloads.list()

    def downloads_clear(self) -> dict:
        """Clear finished downloads from the Control Center list."""
        return _APP.downloads.clear_list()

    def media_clear(self) -> dict:
        """Clear the detected-videos list (both windows use the same store)."""
        n = len(_APP.media.items)
        _APP.media.clear()
        _APP.logm.log("media list cleared: %d items" % n, event="media_cleared", removed=n)
        return {"ok": True, "removed": n}

    def download_open_folder(self, job_id: str = "") -> None:
        d = _APP.downloads.jobs[job_id].out_dir if job_id in _APP.downloads.jobs else _APP.settings.get("download_dir")
        os.makedirs(d, exist_ok=True)
        _APP.logm.log("open folder: %s" % d, event="open_folder", path=d)
        os.startfile(d)  # noqa: S606

    def download_open_file(self, job_id: str) -> None:
        job = _APP.downloads.jobs.get(job_id)
        if job and job.filepath and os.path.exists(job.filepath):
            os.startfile(job.filepath)  # noqa: S606

    def download_delete_file(self, job_id: str) -> dict:
        """Delete the finished file from disk AND drop its card from the list."""
        job = _APP.downloads.jobs.get(job_id)
        if not job or not job.filepath:
            return {"ok": False, "error": "file not found"}
        path = job.filepath
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as e:
            _APP.logm.log("delete file failed: %s (%r)" % (path, e), level="error",
                          event="download_delete_error", id=job_id, path=path, error=repr(e))
            return {"ok": False, "error": repr(e)}
        _APP.logm.log("deleted file: %s" % path, event="download_deleted", id=job_id, path=path)
        result = _APP.downloads.clear_list()  # drop finished cards, this one included
        _APP.push_control("downloads_cleared", {"removed": result.get("removed", 0)})
        return {"ok": True, "path": path}

    def media_list(self) -> list[dict]:
        return _APP.media.list()

    # ----------------------------------------------------------- control UI
    def show_control(self) -> None:
        _APP.show_control()

    def get_state(self) -> dict:
        return {
            "version": APP_VERSION,
            "app_name": APP_NAME,
            "settings": public_settings(),
            "ytdlp_version": _APP.engines.ytdlp_version,
            "has_ffmpeg": bool(_APP.engines.ffmpeg_path()),
            "downloads": _APP.downloads.list(),
            "media": _APP.media.list(),
            "platform": sys.platform,
            "frozen": bool(getattr(sys, "frozen", False)),
        }

    def set_setting(self, key: str, value) -> dict:
        _APP.settings.set(key, value)
        if key in SECRET_KEYS:
            # never log a credential value; keep the live client in sync
            _APP.logm.log("setting changed: %s = [redacted]" % key, event="setting_changed", key=key)
            if key == "sieve_api_key":
                _APP.sieve.client.api_key = str(value or "")
        else:
            _APP.logm.log("setting changed: %s = %r" % (key, value), event="setting_changed", key=key, value=value)
        if key == "log_level":
            _APP.logm.set_level(str(value))
        if key == "max_concurrent":
            _APP.downloads._sem = threading.Semaphore(int(value) or 3)
        return {"ok": True}

    # -------------------------------------------- sieve scrape API (v1.3.0)
    # Optional: every method is a no-op when SIEVE_API_KEY is not configured.
    def sieve_status(self) -> dict:
        """Non-secret status for the UI. Never returns the key."""
        return {"ok": True, "configured": _APP.sieve.configured, "runs": _APP.sieve.list_runs()}

    def sieve_login_start(self) -> dict:
        """Begin the device login; the returned link/code are for the USER to
        open and approve themselves - the app never approves on their behalf."""
        return _APP.sieve.login_start()

    def sieve_login_cancel(self) -> dict:
        return _APP.sieve.login_cancel()

    def sieve_set_key(self, key: str) -> dict:
        """Store a user-supplied key (Settings -> API keys alternative)."""
        return _APP.sieve.set_api_key(key)

    def sieve_run_start(self, spec: dict) -> dict:
        spec = spec or {}
        urls = spec.get("target_urls")
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.replace("\n", ",").split(",") if u.strip()]
        opts = {
            "target_urls": urls or None,
            "fields": spec.get("fields") or None,
            "schema": spec.get("schema") or None,
            "output_schema": spec.get("output_schema") or None,
            "table_shape": spec.get("table_shape") or None,
            "compliance_mode": str(spec.get("compliance_mode") or "regular"),
        }
        return _APP.sieve.start(str(spec.get("instruction") or ""), **opts)

    def sieve_run_list(self) -> dict:
        return {"ok": True, "runs": _APP.sieve.list_runs()}

    def sieve_followup(self, run_id: str, instruction: str) -> dict:
        return _APP.sieve.followup(run_id, instruction)

    def sieve_download_files(self, run_id: str) -> dict:
        return _APP.sieve.download_files(run_id)

    def sieve_credits(self) -> dict:
        return _APP.sieve.credits()

    # --------------------------------------------- site exclusions (v1.2.0)
    @staticmethod
    def _norm_exclusion(pattern: str) -> str:
        """See core.settings.norm_exclusion (kept as an Api alias)."""
        return norm_exclusion(pattern)

    def exclusion_list(self) -> list:
        return list(_APP.settings.get("exclusions") or [])

    def exclusion_add(self, pattern: str) -> dict:
        p = self._norm_exclusion(pattern)
        if not p:
            return {"ok": False, "error": "empty pattern", "patterns": self.exclusion_list()}
        lst = _APP.settings.get("exclusions") or []
        if p not in lst:
            lst.append(p)
            _APP.settings.set("exclusions", lst)
        _APP.logm.log("exclusion added: %s (%d total)" % (p, len(lst)),
                      event="exclusion_added", pattern=p, total=len(lst))
        return {"ok": True, "patterns": list(lst)}

    def exclusion_remove(self, pattern: str) -> dict:
        p = self._norm_exclusion(pattern)
        lst = [x for x in (_APP.settings.get("exclusions") or []) if x != p]
        _APP.settings.set("exclusions", lst)
        _APP.logm.log("exclusion removed: %s (%d total)" % (p, len(lst)),
                      event="exclusion_removed", pattern=p, total=len(lst))
        return {"ok": True, "patterns": list(lst)}

    # ---------------------------------------- exclusion cloud sync (v1.2.2)
    # One secret GitHub gist carries vdograbber-exclusions.json so the desktop
    # app and the Chrome extension (and other machines) share one list. The
    # token lives in local settings only - never logged, never exported.
    GIST_FILENAME = "vdograbber-exclusions.json"

    def _github_api(self, method: str, path: str, token: str, payload=None):
        req = urllib.request.Request(
            "https://api.github.com" + path,
            method=method,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "User-Agent": APP_NAME,
                "Content-Type": "application/json",
            },
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    @staticmethod
    def _gist_doc_text(doc) -> str:
        """Extract the transfer-file text from a gist payload."""
        try:
            return str((doc.get("files") or {}).get(Api.GIST_FILENAME, {}).get("content") or "")
        except Exception:
            return ""

    def exclusion_cloud_push(self) -> dict:
        """Upload the local exclusion list into the gist (create it if needed).
        A gist carries the filename+content we set; both sides merge on pull,
        so push order does not lose data as long as devices pull-then-push."""
        token = str(_APP.settings.get("github_pat") or "")
        if not token:
            return {"ok": False, "error": "no token - set github_pat in settings first"}
        doc = json.loads(self.exclusion_export()["json"])
        gist_id = str(_APP.settings.get("gist_id") or "")
        body = {"description": "VDO Grabber exclusion list (automatic sync)",
                "files": {self.GIST_FILENAME: {"content": json.dumps(doc, ensure_ascii=False, indent=2)}}}
        try:
            if gist_id:
                out = self._github_api("PATCH", "/gists/" + gist_id, token, body)
            else:
                out = self._github_api("POST", "/gists", token, {**body, "public": False})
                _APP.settings.set("gist_id", out.get("id") or "")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("message", "")
            except Exception:
                pass
            _APP.logm.log("gist push failed: %s %s" % (e.code, detail), level="error",
                          event="exclusion_cloud_error", op="push", status=e.code)
            return {"ok": False, "error": "github %s %s" % (e.code, detail)}
        except Exception as e:
            _APP.logm.exception("gist push", e)
            return {"ok": False, "error": repr(e)}
        lst = doc["patterns"]
        _APP.logm.log("exclusions pushed to gist (%d patterns)" % len(lst),
                      event="exclusion_cloud_pushed", total=len(lst))
        return {"ok": True, "total": len(lst), "gist_id": _APP.settings.get("gist_id")}

    def exclusion_cloud_pull(self) -> dict:
        """Fetch the gist's document and merge it into the local list
        (same rules as exclusion_import)."""
        token = str(_APP.settings.get("github_pat") or "")
        gist_id = str(_APP.settings.get("gist_id") or "")
        if not token or not gist_id:
            return {"ok": False, "error": "no token or gist_id - push once first"}
        try:
            out = self._github_api("GET", "/gists/" + gist_id, token)
        except urllib.error.HTTPError as e:
            _APP.logm.log("gist pull failed: %s" % e.code, level="error",
                          event="exclusion_cloud_error", op="pull", status=e.code)
            return {"ok": False, "error": "github %s" % e.code}
        except Exception as e:
            _APP.logm.exception("gist pull", e)
            return {"ok": False, "error": repr(e)}
        text = self._gist_doc_text(out)
        try:
            doc = json.loads(text or "{}")
        except ValueError:
            return {"ok": False, "error": "gist holds an invalid document"}
        patterns = doc.get("patterns") if isinstance(doc, dict) else None
        if not isinstance(patterns, list):
            return {"ok": False, "error": "gist document has no patterns array"}
        lst, added = merge_exclusion_patterns(_APP.settings.get("exclusions"), patterns)
        _APP.settings.set("exclusions", lst)
        _APP.logm.log("exclusions pulled from gist: +%d (%d total)" % (added, len(lst)),
                      event="exclusion_cloud_pulled", added=added, total=len(lst))
        return {"ok": True, "added": added, "total": len(lst), "patterns": list(lst)}

    def exclusion_cloud_status(self) -> dict:
        """Non-secret status for the settings UI (never returns the token)."""
        token = str(_APP.settings.get("github_pat") or "")
        gist_id = str(_APP.settings.get("gist_id") or "")
        return {"ok": True, "has_token": bool(token), "gist_id": gist_id,
                "total": len(_APP.settings.get("exclusions") or [])}

    def exclusion_export(self) -> dict:
        """Serialize the exclusion list as JSON for backup / cross-device sync.
        The format is shared with the Chrome extension (vg:exclusion:export in
        extension/background.js) so a file from one side imports on the other."""
        lst = list(_APP.settings.get("exclusions") or [])
        doc = {"app": APP_NAME, "kind": "exclusions", "version": 1,
               "exported": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "patterns": lst}
        _APP.logm.log("exclusions exported: %d patterns" % len(lst), event="exclusion_exported", total=len(lst))
        return {"ok": True, "json": json.dumps(doc, ensure_ascii=False, indent=2), "count": len(lst)}

    def exclusion_import(self, json_text: str) -> dict:
        """Merge patterns from an exclusion export file (same format on both
        platforms). Duplicates, blanks, whitespace typos and matcher-
        incompatible entries are dropped (core.settings.merge_exclusion_patterns)."""
        try:
            doc = json.loads(str(json_text or ""))
        except ValueError as e:
            return {"ok": False, "error": "invalid json: %s" % e}
        patterns = doc.get("patterns") if isinstance(doc, dict) else None
        if not isinstance(patterns, list):
            return {"ok": False, "error": "no patterns array"}
        lst, added = merge_exclusion_patterns(_APP.settings.get("exclusions"), patterns)
        _APP.settings.set("exclusions", lst)
        _APP.logm.log("exclusions imported: +%d (%d total)" % (added, len(lst)),
                      event="exclusion_imported", added=added, total=len(lst))
        return {"ok": True, "added": added, "total": len(lst), "patterns": list(lst)}

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
        self.sieve = SieveManager(self.settings, self.logm, self.data_dir, push_ui=self._push)
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
        threading.Thread(target=self._resume_sieve, name="sieve-resume", daemon=True).start()
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()

    def _resume_sieve(self):
        """Resume polling of runs persisted before a crash/restart. No-op when
        Sieve is not configured."""
        try:
            self.sieve.resume_pending()
        except Exception as e:
            self.logm.exception("sieve resume", e)

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
                        # v1.2.0: respect exclusions even when the watchdog races a load
                        if self._page_excluded(self.browser.get_current_url() or ""):
                            continue
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

    def _page_excluded(self, url: str) -> bool:
        """v1.2.0: does the current page match a user exclusion pattern?
        Excluded pages get no detection and no toolbar - same semantics as
        the Chrome extension (core.settings.url_excluded)."""
        try:
            return url_excluded(self.settings.get("exclusions") or [], url)
        except Exception:
            return False

    def _on_loaded(self):
        """Every completed page load: (re)inject hooks + toolbar, log it."""
        try:
            url = self.browser.get_current_url() or "?"
            self.logm.log("page loaded: %s" % url, event="page_loaded", url=url)
            if self._page_excluded(url):
                self.logm.log("page excluded by user settings - no detection, no toolbar",
                              level="debug", event="page_excluded", url=url[:200])
                self.push_control("page_loaded", {"url": url})
                return
            self.browser.evaluate_js(DETECT_JS)
            self.browser.evaluate_js(TOOLBAR_JS)
            self.push_control("page_loaded", {"url": url})
        except Exception as e:
            self.logm.log("on_loaded injection failed: %r" % e, level="warning", event="inject_error", error=repr(e))

    def show_control(self):
        _APP.logm.log("control center shown", event="control_shown")
        # show + restore so an already-open (but hidden behind the browser)
        # window actually comes to the front instead of silently staying put
        _APP.control.restore()
        _APP.control.show()
        try:
            _APP.control.evaluate_js("window.__onEvent && window.__onEvent('state_changed', %s); 1;" % json.dumps(_APP.api.get_state(), ensure_ascii=False, default=str))
        except Exception:
            pass


def selftest() -> int:
    """Headless smoke test: detection JS + real download via yt-dlp + delete-file API."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    app = App()
    results = {}

    def push_stub(event, payload=None):
        results.setdefault("events", []).append((event, payload))

    app.downloads.push_ui = push_stub

    # --- tiny local media server (two ports = two origins for iframe tests) -
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

    def start_server(port_hint=0):
        s = ThreadingHTTPServer(("127.0.0.1", port_hint), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        return s

    srv = start_server()
    port = srv.server_address[1]
    srv2 = start_server()
    port2 = srv2.server_address[1]
    app.logm.log("selftest: local media servers on ports %d, %d" % (port, port2),
                 event="selftest_server", port=port, port2=port2)

    # --- 1) engines --------------------------------------------------------
    try:
        ytdlp = app.engines.ensure_ytdlp()
        results["ytdlp"] = {"ok": os.path.exists(ytdlp), "version": app.engines.ytdlp_version}
    except Exception as e:
        results["ytdlp"] = {"ok": False, "error": repr(e)}

    # --- 2) detection JS in a real webview ---------------------------------
    src_page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "testpage.html")
    with open(src_page, "r", encoding="utf-8") as f:
        html = f.read().replace("__PORT__", str(port)).replace("__PORT2__", str(port2))
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
        items = app.media.list()
        kinds = {r["kind"] for r in items}
        urls = [r["url"] for r in items]
        has_embed = "embed" in kinds and any("/embed.html" in u for u in urls)
        has_iframe_video = any("iframe-video.mp4" in u for u in urls)
        has_dom_video = any("dom-video.mp4" in u for u in urls)
        has_hooks = "m3u8" in kinds and "mpd" in kinds
        results["detection"] = {
            "ok": has_hooks and has_dom_video and has_embed and has_iframe_video,
            "found": [{"kind": r["kind"], "via": r["via"], "url": r["url"][:80]} for r in items],
            "checks": {"dom": has_dom_video, "hooks": has_hooks, "embed_iframe": has_embed, "srcdoc_iframe": has_iframe_video},
        }
    except Exception as e:
        results["detection"] = {"ok": False, "error": repr(e)}
        app.logm.exception("selftest detection", e)

    # --- 2.5) detector pair sync (desktop DETECT_JS <-> android Detector.kt) -
    try:
        results["pair_sync"] = check_pair_sync()
    except Exception as e:
        results["pair_sync"] = {"ok": False, "error": repr(e)}
        app.logm.exception("selftest pair sync", e)

    # --- 3) real download via yt-dlp ---------------------------------------
    try:
        job = app.downloads.start(f"http://127.0.0.1:{port}/video.mp4", title="selftest", kind="media")
        for _ in range(120):
            time.sleep(0.5)
            if job.status in ("done", "error", "canceled"):
                break
        ok_file = job.status == "done" and job.filepath and os.path.exists(job.filepath) and os.path.getsize(job.filepath) >= len(mp4) - 1024
        results["download"] = {"ok": bool(ok_file), "status": job.status, "file": job.filepath, "error": job.error}
    except Exception as e:
        results["download"] = {"ok": False, "error": repr(e)}

    # --- 3.5) delete a finished file via Api.download_delete_file ----------
    # v1.1.8: exercises the same path the UI "ลบไฟล์" button uses - the file
    # must disappear from disk and the card must drop from the list.
    try:
        jid = next((j["id"] for j in app.downloads.list()
                    if j.get("status") == "done" and j.get("filepath") and os.path.exists(j["filepath"])), None)
        if not jid:
            raise AssertionError("no finished job with a file on disk to delete")
        path = app.downloads.jobs[jid].filepath
        res = app.api.download_delete_file(jid)
        gone = res.get("ok") and not os.path.exists(path)
        cleared = all(j["id"] != jid for j in app.downloads.list())
        results["delete_file"] = {
            "ok": bool(gone and cleared),
            "api_ok": bool(res.get("ok")), "file_gone": not os.path.exists(path),
            "card_cleared": bool(cleared), "path": path,
        }
        app.logm.log("selftest delete_file: ok=%s gone=%s cleared=%s" % (res.get("ok"), not os.path.exists(path), cleared),
                     event="selftest_delete_file", ok=bool(gone and cleared))
    except Exception as e:
        results["delete_file"] = {"ok": False, "error": repr(e)}
        app.logm.exception("selftest delete_file", e)

    # --- 3.6) site exclusions (v1.2.0): matcher + Api + injection gate -------
    try:
        cases = [
            # (pattern, url, expected)
            ("facebook.com", "https://www.facebook.com/watch/?v=123", True),
            ("facebook.com", "https://facebook.com/", True),
            ("facebook.com", "https://notfacebook.com/video.mp4", False),
            ("facebook.com", "https://mail.google.com/inbox", False),
            ("*.tiktok.com/*", "https://www.tiktok.com/@user/video/1", True),
            ("https://www.facebook.com/*", "https://www.facebook.com/reel/9", True),
            ("https://www.facebook.com/*", "http://www.facebook.com/reel/9", False),
            ("*://*.tiktok.com/*", "http://m.tiktok.com/x", True),
            ("127.0.0.1", "http://127.0.0.1:8799/video.mp4", True),   # ports ignored
            ("*://127.0.0.1/*", "http://127.0.0.1:8799/video.mp4", True),
            ("", "https://example.com/v.mp4", False),                  # empty pattern
        ]
        from core.settings import exclusion_re
        matcher_ok = all(bool(exclusion_re(p)) == bool(p) and url_excluded([p], u) == want
                         for p, u, want in cases)
        # full Api round-trip through real Settings (in the app's data dir)
        r1 = app.api.exclusion_add("Example.org")
        r2 = app.api.exclusion_add("www.example.org")     # dedupe via www-strip
        gate_before = app._page_excluded("https://cdn.example.org/v.mp4")
        r3 = app.api.exclusion_remove("example.org")
        gate_after = app._page_excluded("https://cdn.example.org/v.mp4")
        # v1.2.1: export -> import round-trip of the transfer file (sharing
        # the format with the Chrome extension); re-importing our own export
        # must be a no-op, junk patterns/json must never pollute the list
        exp = app.api.exclusion_export()
        imp = app.api.exclusion_import(exp["json"])          # all duplicates
        bad = app.api.exclusion_import('{"patterns":["bad pattern x"]}')
        notjson = app.api.exclusion_import("not json at all")
        transfer_ok = bool(exp.get("ok") and exp.get("count") == 0
                           and imp.get("ok") and imp.get("added") == 0
                           and bad.get("ok") and bad.get("added") == 0
                           and not notjson.get("ok"))
        results["exclusions"] = {
            "ok": bool(matcher_ok and r1["ok"] and r2["ok"] and gate_before
                       and r3["ok"] and not gate_after and transfer_ok
                       and app.api.exclusion_list() == []),
            "matcher": matcher_ok, "add_ok": bool(r1["ok"] and r2["ok"]),
            "gate_on": bool(gate_before), "remove_ok": bool(r3["ok"]), "gate_off": not gate_after,
            "transfer_ok": transfer_ok,
            "list": app.api.exclusion_list(),
        }
    except Exception as e:
        results["exclusions"] = {"ok": False, "error": repr(e)}
        app.logm.exception("selftest exclusions", e)

    srv.shutdown()
    srv2.shutdown()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    passed = (results["ytdlp"]["ok"] and results["detection"]["ok"]
              and results["pair_sync"]["ok"] and results["download"]["ok"]
              and results["delete_file"]["ok"] and results["exclusions"]["ok"])
    app.logm.log("SELFTEST %s" % ("PASS" if passed else "FAIL"), event="selftest", passed=passed,
                 results={k: v.get("ok") for k, v in results.items() if isinstance(v, dict)})
    return 0 if passed else 1


def sieve_login_cli() -> int:
    """Interactive device login (`python app/main.py --sieve-login`).

    The user opens the printed link and approves the code themselves; the app
    only polls. The key is written to the local config store and never printed.
    """
    data_dir = default_data_dir()
    logm = LogManager(data_dir)
    settings = Settings(data_dir, logm)
    mgr = SieveManager(settings, logm, data_dir)
    if mgr.configured:
        print("Sieve is already configured. Remove sieve_api_key from settings.json to replace it.")
        return 0

    def on_prompt(info: dict) -> None:
        print("")
        print("Open this link in a browser and sign in:")
        print("  " + str(info.get("verification_uri_complete") or info.get("verification_uri") or ""))
        print("Confirm the code matches:  " + str(info.get("user_code") or ""))
        print("")
        print("Notes:")
        print("  - The tool name shown on the page is self-reported (supplied by this app).")
        print("  - Approve ONLY a code you started yourself.")
        print("  - The code expires in %d minutes and works once." % (int(info.get("expires_in") or 600) // 60))
        print("Waiting for approval...")

    try:
        key = device_login(mgr.client, on_prompt=on_prompt, sleep=time.sleep)
    except SieveError as e:
        print("Login failed: %s" % e)
        return 1
    mgr.set_api_key(key)
    print("Done. The key is stored locally and never printed.")
    return 0


def sieve_run_cli(instruction: str, urls=None) -> int:
    """Headless one-shot run (`python app/main.py --sieve-run \"...\" --url U`).
    Spends credits; only run it after the user agrees."""
    data_dir = default_data_dir()
    logm = LogManager(data_dir)
    settings = Settings(data_dir, logm)
    mgr = SieveManager(settings, logm, data_dir, push_ui=lambda *a, **k: None)
    res = mgr.start(instruction, target_urls=urls or None)
    if not res.get("ok"):
        print("start failed: %s" % res.get("error"))
        return 1
    run_id = res["run"]["id"]
    print("queued session_id=%s" % res["run"]["session_id"])
    print("polling (this spends credits)...")
    deadline = time.time() + 3600
    while time.time() < deadline:
        time.sleep(3)
        if (mgr.get_run(run_id) or {}).get("status") in ("done", "refused", "error"):
            break
    run = mgr.get_run(run_id) or {}
    print("status=%s" % run.get("status"))
    if run.get("status") == "done":
        print("files=%d schema_conformance=%s" % (len(run.get("files") or []), run.get("schema_conformance")))
        dl = mgr.download_files(run_id)
        print("downloaded: %s" % (dl.get("files") or dl.get("error")))
        return 0
    print("run ended: %s" % run.get("error"))
    return 1


def main() -> int:
    if "--sieve-login" in sys.argv:
        return sieve_login_cli()
    if "--sieve-run" in sys.argv:
        i = sys.argv.index("--sieve-run")
        instruction = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        urls = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--url=")]
        return sieve_run_cli(instruction, urls)
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
