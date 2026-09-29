"""Download manager: runs yt-dlp as a subprocess, parses live progress,
queues concurrent jobs, supports cancel, and logs everything verbosely.

Design notes
------------
* One daemon thread per job, capped by a semaphore (settings.max_concurrent).
* yt-dlp progress is read line-by-line via --newline + --progress-template so
  we get machine-parsable fields instead of ANSI control sequences.
* Every yt-dlp output line is mirrored to downloads.log verbatim.
* Events are pushed to the UI through a callback (main.push_ui).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid

from .settings import Settings
from .ytdlp_mgr import EngineManager

PROGRESS_TEMPLATE = (
    "VGPROG|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s"
    "|%(progress.downloaded_bytes)s|%(progress.total_bytes_estimate)s"
    "|%(progress.total_bytes)s|%(progress.fragment_index)s|%(progress.fragment_count)s"
)

MEDIA_EXT_RE = re.compile(r"\.(mp4|webm|mkv|m3u8|mpd|flv|mov|avi|mp3|m4a|aac|ts|3gp)([?#].*)?$", re.I)


class Job:
    _attrs = ("url", "title", "kind", "format_id", "page_url", "referrer")

    def __init__(self, url: str, title: str = "", kind: str = "media", format_id: str = "",
                 page_url: str = "", referrer: str = "", out_dir: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.title = title or ""
        self.kind = kind              # media | page | hls | dash
        self.format_id = format_id
        self.page_url = page_url
        self.referrer = referrer
        self.out_dir = out_dir
        self.status = "queued"        # queued running merging done error canceled
        self.percent = 0.0
        self.speed = ""
        self.eta = ""
        self.downloaded = 0
        self.total = 0
        self.filepath = ""
        self.error = ""
        self.created = time.time()
        self.finished = 0.0
        self.proc: subprocess.Popen | None = None

    def public(self) -> dict:
        return {
            "id": self.id, "url": self.url, "title": self.title, "kind": self.kind,
            "format_id": self.format_id, "status": self.status, "percent": self.percent,
            "speed": self.speed, "eta": self.eta, "downloaded": self.downloaded,
            "total": self.total, "filepath": self.filepath, "error": self.error,
            "created": self.created, "finished": self.finished, "out_dir": self.out_dir,
        }


class DownloadManager:
    def __init__(self, settings: Settings, engines: EngineManager, log, push_ui=None):
        self.settings = settings
        self.engines = engines
        self.log = log
        self.push_ui = push_ui or (lambda *a, **k: None)
        self.jobs: dict[str, Job] = {}
        self.history: list[str] = []
        self._sem = threading.Semaphore(int(settings.get("max_concurrent") or 3))
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ api
    def start(self, url: str, **kw) -> Job:
        job = Job(url, out_dir=kw.pop("out_dir", "") or self.settings.get("download_dir"), **kw)
        os.makedirs(job.out_dir, exist_ok=True)
        with self._lock:
            self.jobs[job.id] = job
        self.log.log("download queued: %s (kind=%s fmt=%s)" % (url, job.kind, job.format_id or "auto"),
                     event="download_queued", id=job.id, url=url, kind=job.kind, format_id=job.format_id)
        threading.Thread(target=self._run, args=(job,), name=f"dl-{job.id}", daemon=True).start()
        return job

    def cancel(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "no such job"}
        if job.status in ("queued", "running", "merging"):
            job.status = "canceled"
            try:
                if job.proc and job.proc.poll() is None:
                    job.proc.terminate()
            except OSError:
                pass
            self.log.log("download canceled: %s" % job_id, event="download_canceled", id=job_id, url=job.url)
        self.push_ui("download_update", job.public())
        return {"ok": True, "job": job.public()}

    def get_formats(self, url: str, referrer: str = "") -> dict:
        """Probe a URL with yt-dlp -J and return a compact format list."""
        ytdlp = self.engines.ensure_ytdlp()
        cmd = [ytdlp, "--no-playlist", "--no-warnings", "-J", url]
        if referrer:
            cmd += ["--referer", referrer]
        ua = self.settings.get("user_agent")
        if ua:
            cmd += ["--user-agent", ua]
        self.log.log("probing formats: %s" % url, event="probe_start", url=url)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                 encoding="utf-8", errors="replace",
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            info = json.loads(out.stdout)
        except Exception as e:
            self.log.log("probe failed: %r" % e, level="error", event="probe_error", url=url, error=repr(e))
            return {"ok": False, "error": repr(e), "formats": []}

        formats = []
        for f in info.get("formats") or []:
            note = f.get("format_note") or ""
            formats.append({
                "id": f.get("format_id"),
                "ext": f.get("ext"),
                "res": f.get("resolution") or "",
                "fps": f.get("fps"),
                "tbr": f.get("tbr"),
                "size": f.get("filesize") or f.get("filesize_approx"),
                "vcodec": f.get("vcodec"), "acodec": f.get("acodec"),
                "note": note, "proto": f.get("protocol"),
            })
        result = {
            "ok": True,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "uploader": info.get("uploader"),
            "is_hls": bool(info.get("is_live")) or "m3u8" in (info.get("manifest_url") or ""),
            "formats": formats,
        }
        self.log.log("probe ok: %r -> %d formats" % (info.get("title"), len(formats)),
                     event="probe_done", url=url, title=info.get("title"), n_formats=len(formats))
        return result

    def list(self) -> list[dict]:
        with self._lock:
            return [self.jobs[j].public() for j in self.history[-50:]] + \
                   [j.public() for j in self.jobs.values() if j.id not in self.history]

    # ----------------------------------------------------------------- core
    def _run(self, job: Job) -> None:
        with self._sem:
            if job.status == "canceled":
                return
            self._execute(job)
        with self._lock:
            self.history.append(job.id)
            self._prune_history_locked()
        self.push_ui("download_update", job.public())

    def _prune_history_locked(self, keep: int = 200) -> None:
        """Cap in-memory history: forget oldest *finished* jobs (and their Job
        objects) so a long session does not grow without bound. Requires
        self._lock to be held."""
        if len(self.history) <= keep:
            return
        drop, self.history = self.history[:-keep], self.history[-keep:]
        for jid in drop:
            job = self.jobs.get(jid)
            if job and job.status in ("done", "error", "canceled"):
                del self.jobs[jid]

    def _execute(self, job: Job) -> None:
        ytdlp = self.engines.ensure_ytdlp()
        if not ytdlp:
            job.status, job.error = "error", "yt-dlp not available"
            return

        # HLS/DASH or split A/V formats need ffmpeg for demux/merge -> get it once.
        if job.kind in ("hls", "dash") or ".m3u8" in job.url or ".mpd" in job.url or job.format_id in ("bv*", "bv+ba"):
            ff = self.engines.ensure_ffmpeg(push=lambda stage, pct, msg: self.push_ui(
                "engine_progress", {"engine": "ffmpeg", "stage": stage, "percent": pct, "message": msg}))
            if not ff:
                self.log.log("ffmpeg unavailable - falling back to single-file best format",
                             level="warning", event="download_no_ffmpeg", id=job.id)
                job.format_id = job.format_id if job.format_id and "*" not in job.format_id and "+" not in job.format_id else ""

        out_tmpl = os.path.join(job.out_dir, "%(title).120B [%(id)s].%(ext)s")
        cmd = [ytdlp, "--no-playlist", "--no-warnings", "--newline", "--progress-template", PROGRESS_TEMPLATE,
               "--no-mtime", "-o", out_tmpl]
        if job.format_id:
            cmd += ["-f", job.format_id]
        if job.referrer:
            cmd += ["--referer", job.referrer]
        ua = self.settings.get("user_agent")
        if ua:
            cmd += ["--user-agent", ua]
        ff = self.engines.ffmpeg_path()
        if ff:
            cmd += ["--ffmpeg-location", ff]
        if self.settings.get("embed_thumbnail"):
            cmd += ["--embed-thumbnail"]
        if job.kind in ("hls", "dash"):
            cmd += ["--concurrent-fragment-downloads", "4"]
        cmd.append(job.url)

        self.log.dl("=" * 70)
        self.log.dl("[%s] START %s" % (job.id, job.url))
        self.log.dl("[%s] CMD  %s" % (job.id, " ".join(cmd)))
        self.log.log("download start #%s: %s" % (job.id, job.url),
                     event="download_start", id=job.id, url=job.url, kind=job.kind,
                     format_id=job.format_id, out_dir=job.out_dir)
        job.status = "running"
        self.push_ui("download_update", job.public())

        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace", creationflags=creationflags,
                                        bufsize=1)
            assert job.proc.stdout
            for line in job.proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                self.log.dl("[%s] %s" % (job.id, line))
                if job.status == "canceled":
                    continue
                self._parse_line(job, line)
            rc = job.proc.wait()
        except Exception as e:
            self.log.exception("download job", e)
            rc, job.error = -1, repr(e)

        if job.status == "canceled":
            self.log.log("download canceled #%s" % job.id, event="download_canceled", id=job.id, url=job.url)
        elif rc == 0:
            job.status = "done"
            job.finished = time.time()
            job.filepath = self._guess_output_file(job)
            self.log.log("download done #%s -> %s" % (job.id, job.filepath or "(file name unknown)"),
                         event="download_done", id=job.id, url=job.url, filepath=job.filepath,
                         seconds=round(job.finished - job.created, 1), bytes=job.total)
        else:
            job.status = "error"
            job.error = job.error or "yt-dlp exited with code %s" % rc
            self.log.log("download error #%s: %s" % (job.id, job.error),
                         level="error", event="download_error", id=job.id, url=job.url, error=job.error)
        self.push_ui("download_update", job.public())

    def _parse_line(self, job: Job, line: str) -> None:
        if line.startswith("VGPROG|"):
            parts = line.split("|")

            def num(idx: int) -> float:
                v = parts[idx].strip().rstrip("%") if idx < len(parts) else ""
                try:
                    return float(v)
                except ValueError:
                    return float("nan")

            try:
                pct = num(1)
                if pct == pct:  # NaN check (yt-dlp prints NA for unknown)
                    job.percent = pct
                speed = parts[2].strip() if len(parts) > 2 else ""
                if speed not in ("NA", "Unknown"):
                    job.speed = speed
                eta = parts[3].strip() if len(parts) > 3 else ""
                if eta not in ("NA", "Unknown"):
                    job.eta = eta
                dl = num(4)
                job.downloaded = int(dl) if dl == dl else job.downloaded
                tot = num(5) if num(5) == num(5) else num(6)
                job.total = int(tot) if tot == tot else job.total
                if len(parts) > 8 and parts[7].strip().isdigit() and parts[8].strip().isdigit():
                    fi, fc = int(parts[7]), int(parts[8])
                    if fc and fi:
                        job.status, job.percent = "running", min(99.9, fi * 100 / fc)
                self.push_ui("download_update", job.public())
            except (ValueError, IndexError) as e:
                self.log.log("progress parse issue %r for line %r" % (e, line), level="debug")
            return
        m = re.search(r'\[Merger\] Destination: (.+)', line)
        if m:
            job.status, job.filepath = "merging", m.group(1).strip()
            self.push_ui("download_update", job.public())
            return
        m = re.search(r"(?:has already been downloaded|Destination): (.+)", line)
        if m:
            job.filepath = m.group(1).strip()
            return
        if line.startswith("ERROR:"):
            job.error = line
            self.log.log("yt-dlp error line: %s" % line, level="error", event="download_error_line", id=job.id)
        elif "[download] 100%" in line and job.total == 0:
            job.percent = 100.0

    def _guess_output_file(self, job: Job) -> str:
        if job.filepath and os.path.exists(job.filepath):
            return job.filepath
        try:
            out = subprocess.run([self.engines.ytdlp_path or "yt-dlp", "--no-playlist", "--print", "filename",
                                  "-o", os.path.join(job.out_dir, "%(title).120B [%(id)s].%(ext)s"), "--skip-download", job.url],
                                 capture_output=True, text=True, timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            p = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
            return p if p and os.path.exists(p) else job.filepath
        except Exception:
            return job.filepath
