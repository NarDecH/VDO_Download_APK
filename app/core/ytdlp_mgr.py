"""Locate / prepare the download engines (yt-dlp.exe, ffmpeg.exe).

Priority for yt-dlp:
  1. %APPDATA%/VDOGrabber/bin/yt-dlp.exe  (writable copy -> supports self-update)
  2. bundled copy inside the frozen exe / repo  (app/bin/yt-dlp.exe)

ffmpeg is NOT bundled (keeps the exe small); it is downloaded once, on first
need, into the same bin folder, with UI progress events.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-lgpl.zip"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"


def app_bin_dir(data_dir: str) -> str:
    d = os.path.join(data_dir, "bin")
    os.makedirs(d, exist_ok=True)
    return d


def bundled_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "bin")  # noqa: SLF001 (PyInstaller bundle root)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")


class EngineManager:
    def __init__(self, data_dir: str, log):
        self.data_dir = data_dir
        self.log = log
        self._lock = threading.Lock()
        self.ytdlp_path: str | None = None
        self.ytdlp_version: str = "?"

    # ---------------------------------------------------------------- yt-dlp
    def ensure_ytdlp(self) -> str:
        """Return a working yt-dlp.exe path; install the bundled copy into the
        writable bin dir on first run (so -U self-update works later)."""
        with self._lock:
            if self.ytdlp_path and os.path.exists(self.ytdlp_path):
                return self.ytdlp_path

            bin_dir = app_bin_dir(self.data_dir)
            target = os.path.join(bin_dir, "yt-dlp.exe")
            bundled = os.path.join(bundled_dir(), "yt-dlp.exe")

            if not os.path.exists(target):
                if os.path.exists(bundled):
                    shutil.copy2(bundled, target)
                    self.log.log("installed bundled yt-dlp -> %s" % target, event="engine_install", engine="yt-dlp", path=target)
                else:
                    self._download_file(YTDLP_URL, target, "yt-dlp")
            self.ytdlp_path = target
            self.ytdlp_version = self._version(target)
            self.log.log("yt-dlp ready: %s (v%s)" % (target, self.ytdlp_version),
                         event="engine_ready", engine="yt-dlp", version=self.ytdlp_version, path=target)
            return target

    def _version(self, path: str) -> str:
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return out.stdout.strip() or "?"
        except Exception as e:
            self.log.log("yt-dlp --version failed: %r" % e, level="warning")
            return "?"

    def update_ytdlp(self) -> dict:
        path = self.ensure_ytdlp()
        self.log.log("updating yt-dlp...", event="engine_update_start", engine="yt-dlp")
        try:
            out = subprocess.run([path, "-U"], capture_output=True, text=True, timeout=300,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            txt = (out.stdout + out.stderr).strip()[-2000:]
            self.log.dl("yt-dlp -U output:\n%s" % txt)
            self.ytdlp_version = self._version(path)
            ok = "is up to date" in txt.lower() or "updated" in txt.lower() or out.returncode == 0
            self.log.log("yt-dlp update finished (v%s)" % self.ytdlp_version,
                         event="engine_update_done", engine="yt-dlp", version=self.ytdlp_version, ok=bool(ok))
            return {"ok": True, "message": txt[-500:], "version": self.ytdlp_version}
        except Exception as e:
            self.log.exception("engine update", e)
            return {"ok": False, "message": repr(e)}

    # ---------------------------------------------------------------- ffmpeg
    def ffmpeg_path(self) -> str | None:
        p = os.path.join(app_bin_dir(self.data_dir), "ffmpeg.exe")
        return p if os.path.exists(p) else None

    def ensure_ffmpeg(self, push=None) -> str | None:
        """Download ffmpeg.exe once (needed for HLS/DASH demux + A/V merge).
        push(stage, pct, msg) reports progress to the UI."""
        p = self.ffmpeg_path()
        if p:
            return p
        with self._lock:
            p = self.ffmpeg_path()
            if p:
                return p
            bin_dir = app_bin_dir(self.data_dir)
            zpath = os.path.join(bin_dir, "ffmpeg.zip")
            try:
                self._download_file(FFMPEG_URL, zpath, "ffmpeg", push=push)
                with zipfile.ZipFile(zpath) as z:
                    member = next(n for n in z.namelist() if n.endswith("/ffmpeg.exe"))
                    with z.open(member) as src, open(os.path.join(bin_dir, "ffmpeg.exe"), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                os.remove(zpath)
                self.log.log("ffmpeg installed -> %s" % os.path.join(bin_dir, "ffmpeg.exe"),
                             event="engine_install", engine="ffmpeg", path=os.path.join(bin_dir, "ffmpeg.exe"))
                return os.path.join(bin_dir, "ffmpeg.exe")
            except Exception as e:
                self.log.exception("ffmpeg install", e)
                for f in (zpath,):
                    if os.path.exists(f):
                        try: os.remove(f)
                        except OSError: pass
                return None

    # ---------------------------------------------------------------- shared
    def _download_file(self, url: str, dest: str, engine: str, push=None) -> None:
        self.log.log("downloading %s engine: %s" % (engine, url), event="engine_download_start", engine=engine, url=url)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 VDOGrabber/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if push:
                    push("download", round(done * 100 / total) if total else 0, "%s (%.1f MB)" % (engine, done / 1048576))
        self.log.log("%s engine downloaded: %.1f MB -> %s" % (engine, os.path.getsize(dest) / 1048576, dest),
                     event="engine_download_done", engine=engine, bytes=os.path.getsize(dest))
