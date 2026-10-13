"""Desktop self-update (v1.9.12).

Flow: check GitHub releases/latest for a tag newer than APP_VERSION ->
download the matching VDOGrabber-<ver>-windows-x64.exe asset + its .sha256 ->
verify the hash -> write an update launcher .bat into the data dir and (when
the user confirms) spawn it, then exit the app. The .bat waits for this
process to die and replaces the exe.

Notes:
 - checks are throttled (default once every 12 h, setting
   `update_check_interval_hours`) so the app does not hammer the GitHub API
 - the check touches only the public GitHub API; no user data is sent
 - all steps log structured events: update_check, update_available,
   update_downloading, update_downloaded, update_error, update_apply
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.request

from core.logger import APP_VERSION  # noqa: E402 - core is on sys.path in every entry point

REPO = "NarDecH/VDO_Download_APK"
RELEASES_API = "https://api.github.com/repos/%s/releases/latest" % REPO
ASSET_BASE = "https://github.com/%s/releases/download/v%%s/" % REPO


def parse_version(v: str) -> tuple[int, ...]:
    """'1.9.12' / 'v1.9.12' / '1.9.12.0' -> (1, 9, 12) for stable compare."""
    parts = [p for p in str(v).strip().lstrip("vV").split(".") if p.isdigit()]
    try:
        return tuple(int(p) for p in parts) or (0,)
    except ValueError:
        return (0,)


def compare_versions(local: str, remote: str) -> int:
    """-1 when remote is newer, 0 equal, 1 local newer. Component-wise."""
    a, b = parse_version(local), parse_version(remote)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return (a > b) - (a < b)


class Updater:
    def __init__(self, data_dir: str, settings, log, push_ui=lambda *a, **k: None):
        self.data_dir = data_dir
        self.settings = settings
        self.log = log
        self.push_ui = push_ui
        self._lock = threading.Lock()
        self.status = "idle"          # idle|checking|available|downloading|ready|none
        self.remote_version = ""
        self.remote_notes = ""
        self.local = self._exe_path()

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _exe_path() -> str:
        if getattr(sys, "frozen", False):
            return os.path.abspath(sys.executable)
        return os.path.abspath(sys.argv[0])

    def _updates_dir(self) -> str:
        d = os.path.join(self.data_dir, "updates")
        os.makedirs(d, exist_ok=True)
        return d

    def _cache_file(self) -> str:
        return os.path.join(self._updates_dir(), "last_update_check.json")

    def _download(self, url: str, dest: str, progress_key: int = 4 << 20) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "VDOGrabber-updater"})
        with urllib.request.urlopen(req, timeout=180) as resp, open(dest + ".part", "wb") as fh:
            total = int(resp.headers.get("content-length") or 0)
            seen, last = 0, 0.0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                seen += len(chunk)
                if total and seen - last >= progress_key:
                    last = seen
                    self.push_ui("update_progress", {
                        "percent": round(seen / total * 100, 1),
                        "downloaded": seen, "total": total})
        os.replace(dest + ".part", dest)

    # ------------------------------------------------------------------ check
    def check(self, force: bool = False) -> dict:
        with self._lock:
            if self.status == "checking":
                return {"ok": False, "error": "check already running"}
            try:
                last = json.load(open(self._cache_file(), encoding="utf-8")).get("ts", 0)
            except Exception:
                last = 0
            hours = float(self.settings.get("update_check_interval_hours") or 12)
            if not force and (time.time() - last) < hours * 3600:
                return {"ok": True, "checked": False, "reason": "recently checked",
                        "update_available": self.status != "none" and self.status != "idle",
                        "version": self.remote_version}
            if self.status not in ("ready", "available"):
                self.status = "checking"

        self.log.log("checking for updates: %s" % RELEASES_API, event="update_check")
        try:
            req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                meta = json.load(resp)
        except Exception as e:
            self.status = "idle"
            self.log.log("update check failed: %r" % e, level="warning",
                         event="update_error", stage="check")
            return {"ok": False, "error": repr(e)}

        tag = str(meta.get("tag_name") or "").strip()
        self.remote_version = tag.lstrip("vV")
        self.remote_notes = str(meta.get("body") or "")[:2000]
        try:
            json.dump({"ts": time.time(), "tag": tag},
                      open(self._cache_file(), "w", encoding="utf-8"))
        except Exception:
            pass

        newer = compare_versions(APP_VERSION, self.remote_version) < 0
        if not newer:
            self.status = "none"
            self.log.log("no update: local %s vs remote %s" % (APP_VERSION, self.remote_version),
                         event="update_check_done", version=self.remote_version)
            return {"ok": True, "update_available": False,
                    "version": self.remote_version, "checked": True}

        self.status = "available"
        self.log.log("update available: %s -> %s" % (APP_VERSION, self.remote_version),
                     event="update_available", local=APP_VERSION,
                     remote=self.remote_version)
        return {"ok": True, "update_available": True, "version": self.remote_version,
                "notes": self.remote_notes, "checked": True}

    # --------------------------------------------------------------- download
    def download(self) -> dict:
        if not self.remote_version or self.status not in ("available", "downloading"):
            return {"ok": False, "error": "no update pending (run check first)"}
        with self._lock:
            if self.status == "downloading":
                return {"ok": False, "error": "download already running"}
            self.status = "downloading"
        name = "VDOGrabber-%s-windows-x64.exe" % self.remote_version
        base = ASSET_BASE % self.remote_version
        exe_path = os.path.join(self._updates_dir(), name)
        sha_path = exe_path + ".sha256"
        self.log.log("downloading update %s" % name,
                     event="update_downloading", version=self.remote_version)
        try:
            self._download(base + name, exe_path, progress_key=2 << 20)
            self._download(base + name + ".sha256", sha_path)
        except Exception as e:
            self.status = "available"
            self.log.log("update download failed: %r" % e, level="error",
                         event="update_error", stage="download")
            return {"ok": False, "error": repr(e)}

        want = ""
        try:
            line = open(sha_path, encoding="utf-8", errors="replace").readline().strip()
            want = line.split()[0].lower() if line else ""
        except Exception:
            pass
        got = self._file_sha256(exe_path)
        if want and got != want:
            self.status = "available"
            self.log.log("update hash mismatch: want %s got %s" % (want, got),
                         level="error", event="update_error", stage="verify")
            return {"ok": False, "error": "sha256 mismatch - update discarded"}
        if not want:
            self.log.log("update sha256 file empty - trusting GitHub transport only",
                         level="warning", event="update_error", stage="verify")

        self.status = "ready"
        size_mb = os.path.getsize(exe_path) // (1 << 20)
        self.log.log("update staged: %s (%d MB)" % (exe_path, size_mb),
                     event="update_downloaded", version=self.remote_version,
                     path=exe_path, sha256=got, verified=bool(want))
        return {"ok": True, "version": self.remote_version, "path": exe_path,
                "sha256": got, "verified": bool(want), "size_mb": size_mb}

    @staticmethod
    def _file_sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------------ apply
    def apply(self) -> dict:
        if self.status != "ready":
            return {"ok": False, "error": "nothing staged (run download first)"}
        name = "VDOGrabber-%s-windows-x64.exe" % self.remote_version
        exe_path = os.path.join(self._updates_dir(), name)
        if not os.path.exists(exe_path):
            self.status = "available"
            return {"ok": False, "error": "staged file missing: %s" % exe_path}
        try:
            self.log.log("applying update: %s -> %s" % (exe_path, self.local),
                         event="update_apply", version=self.remote_version,
                         target=self.local)
            os.startfile(self._spawn_swap(exe_path))  # noqa: S606 - controlled .bat
        except Exception as e:
            self.log.log("update apply failed: %r" % e, level="error",
                         event="update_error", stage="apply")
            return {"ok": False, "error": repr(e)}
        return {"ok": True, "version": self.remote_version}

    def _spawn_swap(self, exe_path: str) -> str:
        """Write the .bat that waits for us to exit and swaps the exe
        (Windows cannot overwrite a running exe)."""
        bat = os.path.join(self._updates_dir(), "apply_update.bat")
        me = self._exe_path()
        with open(bat, "w", encoding="ascii", errors="replace") as fh:
            fh.write("@echo off\r\n")
            fh.write("ping -n 3 127.0.0.1 >nul\r\n")   # wait ~2s for exit
            fh.write('copy /y "%s" "%s" >nul\r\n' % (exe_path, me))
            fh.write('if errorlevel 1 exit /b 1\r\n')
            fh.write('start "" "%s"\r\n' % me)
        self.log.log("update swap script: %s" % bat, event="update_apply_script", path=bat)
        return bat


def spawn_startup_check(app) -> None:
    """Fire the throttled background check once the GUI loop is alive."""
    def run():
        try:
            time.sleep(20)  # let the engines/UI settle first
            r = app.updater.check(force=False)
            if r.get("update_available"):
                app.push_control("update_available", {"version": app.updater.remote_version,
                                                      "notes": app.updater.remote_notes})
        except Exception as e:
            try:
                app.logm.log("startup update check failed: %r" % e, level="debug",
                             event="update_error", stage="check")
            except Exception:
                pass
    threading.Thread(target=run, name="update-check", daemon=True).start()
