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
import urllib.parse
import urllib.request
import uuid

from .settings import Settings
from .ytdlp_mgr import EngineManager

PROGRESS_TEMPLATE = (
    "VGPROG|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s"
    "|%(progress.downloaded_bytes)s|%(progress.total_bytes_estimate)s"
    "|%(progress.total_bytes)s|%(progress.fragment_index)s|%(progress.fragment_count)s"
)

MEDIA_EXT_RE = re.compile(r"\.(mp4|webm|mkv|m3u8|mpd|flv|mov|avi|mp3|m4a|aac|ts|3gp)([?#].*)?$", re.I)

# v1.3.6 (Android parity): a blob: URL exists only inside the browser page -
# neither yt-dlp nor a direct fetch can ever reach it (field log: the engine
# answered "ERROR: [Blob] ... only locally in your browser").
def is_usable_download_url(url: str) -> bool:
    return not str(url or "").lower().startswith("blob:")

# Windows-invalid filename characters (plus control chars) -> replaced by spaces
_INVALID_FS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name, max_len: int = 100) -> str:
    """Make a page title / URL stem safe to use as a filename on Windows.

    Invalid chars become spaces, whitespace collapses, and the result is
    stripped of trailing dots/spaces (Windows hates those) and capped in
    length so `stem + ' (9)' + '.ext'` still fits MAX_PATH budgets.
    """
    name = _INVALID_FS_RE.sub(" ", str(name or ""))
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name


def unique_stem(directory: str, stem: str, ext: str) -> str:
    """Full path `stem.ext` that does not collide on disk - duplicates become
    `stem (2).ext`, `stem (3).ext`, ... (the classic browser-download rule)."""
    first = os.path.join(directory, stem + ext)
    if not os.path.exists(first):
        return first
    n = 2
    while os.path.exists(os.path.join(directory, "%s (%d)%s" % (stem, n, ext))):
        n += 1
    return os.path.join(directory, "%s (%d)%s" % (stem, n, ext))


class Job:
    _attrs = ("url", "title", "kind", "format_id", "page_url", "referrer",
              "ext", "title_base", "page_title")

    def __init__(self, url: str, title: str = "", kind: str = "media", format_id: str = "",
                 page_url: str = "", referrer: str = "", out_dir: str = "", ext: str = "",
                 title_base: str = "", page_title: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.title = title or ""
        self.kind = kind              # media | page | hls | dash
        self.format_id = format_id
        self.page_url = page_url
        self.referrer = referrer
        self.out_dir = out_dir
        self.ext = ext or ""              # source extension sniffed from the URL
        self.title_base = title_base or ""  # filename stem decided up-front
        self.page_title = page_title or ""
        self.status = "queued"        # queued running merging done error canceled paused
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
            "ext": self.ext, "title_base": self.title_base,
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
    @staticmethod
    def _decide_title_base(url: str, kind: str, ext: str, title: str, title_base: str = "") -> str:
        """Filename stem policy (v1.1.6): page/URL titles name raw media and
        manifest (hls/dash) URLs; site-page URLs ("page"/"embed") keep
        yt-dlp's own metadata title because their `title` is the site name."""
        if title_base:
            return title_base
        if ext or kind in ("hls", "dash"):
            return title
        return ""

    def start(self, url: str, **kw) -> Job:
        # Sniff the source extension, then decide the filename stem (above).
        if not kw.get("ext"):
            m = MEDIA_EXT_RE.search(url)
            kw["ext"] = m.group(1).lower() if m else ""
        kw["title_base"] = self._decide_title_base(
            url, str(kw.get("kind") or "media"), str(kw.get("ext") or ""),
            str(kw.get("title") or ""), str(kw.get("title_base") or ""))
        job = Job(url, out_dir=kw.pop("out_dir", "") or self.settings.get("download_dir"), **kw)
        os.makedirs(job.out_dir, exist_ok=True)
        with self._lock:
            self.jobs[job.id] = job
        self.log.log("download queued: %s (kind=%s fmt=%s)" % (url, job.kind, job.format_id or "auto"),
                     event="download_queued", id=job.id, url=url, kind=job.kind, format_id=job.format_id,
                     title=job.title, page_title=job.page_title, title_base=job.title_base)
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

    # -------------------------------------------------- v1.8.0 pause/resume
    def pause(self, job_id: str) -> dict:
        """หยุดพัก: stop the engine but KEEP its .part/.part-FragN files in
        out_dir (yt-dlp always resumes them when the same output template is
        re-run) and remember the job for ดาวน์โหลดต่อ. The worker thread
        observes status "paused" exactly like "canceled" and finishes
        quietly - no error event, no no-file probe."""
        job = self.jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "no such job"}
        if job.status == "paused":
            return {"ok": False, "error": "already paused"}
        if job.status in ("queued", "running", "merging"):
            job.status = "paused"
            try:
                if job.proc and job.proc.poll() is None:
                    job.proc.terminate()
            except OSError:
                pass
            self.log.log("download paused: %s" % job_id,
                         event="download_paused", id=job_id, url=job.url,
                         title_base=job.title_base)
            self.push_ui("download_update", job.public())
            return {"ok": True, "job": job.public()}
        # done/error/canceled jobs have nothing left to pause
        return {"ok": False, "error": "job is %s" % job.status}

    def resume(self, job_id: str) -> dict:
        """ดาวน์โหลดต่อ: re-queue a paused job with the SAME title_base (its
        stored stem) so the new yt-dlp run picks up the kept .part fragments
        and continues instead of starting over under `stem (2)`."""
        job = self.jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "no such job"}
        if job.status != "paused":
            return {"ok": False, "error": "job is not paused"}
        job.status = "queued"
        job.error = ""
        self.log.log("download resumed: %s" % job_id,
                     event="download_resumed", id=job_id, url=job.url)
        self.push_ui("download_update", job.public())
        threading.Thread(target=self._run, args=(job,), name=f"dl-{job.id}", daemon=True).start()
        return {"ok": True, "job": job.public()}

    def clear_list(self) -> dict:
        """Forget every finished job (UI "clear list"); running ones keep going
        and re-appear in the list when they finish."""
        with self._lock:
            finished = [jid for jid in self.history
                        if self.jobs.get(jid) and self.jobs[jid].status in ("done", "error", "canceled")]
            done = set(finished)
            self.history = [jid for jid in self.history if jid not in done]
            for jid in finished:
                self.jobs.pop(jid, None)
        self.log.log("download list cleared: %d finished jobs" % len(finished),
                     event="downloads_cleared", removed=len(finished))
        return {"ok": True, "removed": len(finished)}

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

    # ------------------------------------------------- unsupported-page fallback
    _MEDIA_IN_HTML_RE = re.compile(
        r"https?://[^\s\"'<>]+?\.(?:m3u8|mpd|mp4|webm)(?:\?[^\s\"'<>]*)?", re.I)
    _IFRAME_RE = re.compile(
        r"<(?:iframe|embed)[^>]+(?:src|data)=[\"']([^\"']+)[\"']", re.I)

    def _page_fallback(self, job: Job) -> str | None:
        """Scan an HTML page for a downloadable candidate (yt-dlp gave up).

        Player pages (player.html etc.) hide the real stream behind JS or an
        iframe. Prefer a direct media URL found in the page source (including
        base64/atob-obfuscated ones); otherwise return the first iframe/embed
        URL so yt-dlp can try its site extractor.
        """
        try:
            ua = str(self.settings.get("user_agent") or "").strip() or \
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            req = urllib.request.Request(job.url, headers={"User-Agent": ua, "Referer": job.url})
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", "replace")
        except Exception as e:
            self.log.log("page fallback fetch failed: %r" % e, level="debug",
                         event="fallback_fetch_error", url=job.url, error=repr(e))
            return None

        m = self._MEDIA_IN_HTML_RE.search(html)
        if m:
            return m.group(0)

        # obfuscated players: atob("aHR0cHM6...") / base64 blobs holding a URL
        for blob in re.findall(r"atob\(\s*[\"']([A-Za-z0-9+/=]{24,})[\"']\s*\)", html):
            try:
                import base64
                decoded = base64.b64decode(blob).decode("utf-8", "replace")
            except Exception:
                continue
            m = self._MEDIA_IN_HTML_RE.search(decoded)
            if m:
                self.log.log("decoded atob URL in %s" % job.url[:120], level="debug",
                             event="fallback_atob", url=job.url)
                return m.group(0)

        m = self._IFRAME_RE.search(html)
        if m:
            return urllib.parse.urljoin(job.url, m.group(1).strip())
        return None

    # ------------------------------------------------------- output naming
    def _out_template(self, job: Job) -> str:
        """yt-dlp output template for a job (v1.1.6 title-based naming).

        Known page/URL title (toolbar sends document.title) wins so files stop
        landing as `output [output].mp4`; without one, fall back to yt-dlp's
        metadata title + id. Literal `%` in the stem must be doubled or
        yt-dlp would parse it as a template field.
        """
        stem = sanitize_filename(job.title_base)
        if stem:
            return os.path.join(job.out_dir, stem.replace("%", "%%") + ".%(ext)s")
        return os.path.join(job.out_dir, "%(title).120B [%(id)s].%(ext)s")

    @staticmethod
    def _newest_match(directory: str, stem: str) -> str:
        """Newest existing `stem.ext` / `stem (n).ext` file in directory."""
        try:
            pat = re.compile(re.escape(stem) + r"(?: \(\d+\))?\.[A-Za-z0-9]{1,5}$")
            cands = [f for f in os.listdir(directory or ".") if pat.fullmatch(f)]
        except OSError:
            return ""
        if not cands:
            return ""
        best = max(cands, key=lambda f: os.path.getmtime(os.path.join(directory, f)))
        return os.path.join(directory, best)

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

    def _execute(self, job: Job, allow_fallback: bool = True) -> None:
        ytdlp = self.engines.ensure_ytdlp()
        if not ytdlp:
            job.status, job.error = "error", "yt-dlp not available"
            return

        # v1.3.6 (Android parity): never hand a blob: URL to the engine
        if not is_usable_download_url(job.url):
            job.status = "error"
            job.error = ("blob: URL มีอยู่แค่ในหน้าเว็บ — เอนจินเข้าถึงไม่ได้ "
                         "ให้เลือกลิงก์ไฟล์/ลิงก์สตรีมจากรายการ 🎬 แทน")
            self.log.log("download rejected (blob URL) #%s: %s" % (job.id, job.url[:120]),
                         level="warning", event="download_skipped_blob", id=job.id, url=job.url)
            return

        # HLS/DASH or split A/V formats need ffmpeg for demux/merge -> get it once.
        if job.kind in ("hls", "dash") or ".m3u8" in job.url or ".mpd" in job.url or job.format_id in ("bv*", "bv+ba"):
            ff = self.engines.ensure_ffmpeg(push=lambda stage, pct, msg: self.push_ui(
                "engine_progress", {"engine": "ffmpeg", "stage": stage, "percent": pct, "message": msg}))
            if not ff:
                self.log.log("ffmpeg unavailable - falling back to single-file best format",
                             level="warning", event="download_no_ffmpeg", id=job.id)
                job.format_id = job.format_id if job.format_id and "*" not in job.format_id and "+" not in job.format_id else ""

        out_tmpl = self._out_template(job)
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
        elif job.status == "paused":
            # v1.8.0: the user pressed หยุดพัก - .part fragments stay in
            # out_dir and resume() re-runs the same template. Keep the job in
            # the list (no error event, no no-file probe, no fallback retry).
            self.log.log("download paused #%s" % job.id, event="download_paused", id=job.id, url=job.url)
        elif rc == 0:
            job.status = "done"
            job.finished = time.time()
            job.filepath = self._guess_output_file(job)
            job.percent = 100.0
            # fragment/manifest downloads often never report totals - fill
            # them from the finished file so the UI shows real size + 100%
            if not job.filepath or not os.path.exists(job.filepath):
                # v1.3.6 (field-log parity with Android): exit 0 without a file
                # must not be reported as a floating success - probe -F and log
                # the real reason (DRM / geo / unknown structure)
                job.status = "error"
                job.error = ("yt-dlp finished (exit 0) but no file appeared in the "
                             "download folder - see the no-file probe in the log")
                self.log.log("download done but no file #%s" % job.id,
                             level="error", event="download_no_file", id=job.id, url=job.url)
                self._probe_no_file(job, ytdlp)
                self.push_ui("download_update", job.public())
                return
            try:
                size = os.path.getsize(job.filepath)
                job.total = job.downloaded = size
            except OSError:
                pass
            # v1.3.6 (Android parity safety net): a "video" that is really an
            # HTML player page gets flagged + kept as .html evidence, and the
            # page fallback gets one retry (it often finds the real manifest)
            if self._looks_like_html(job.filepath):
                self._handle_html_payload(job, allow_fallback)
                return
            self.log.log("download done #%s -> %s" % (job.id, job.filepath or "(file name unknown)"),
                         event="download_done", id=job.id, url=job.url, filepath=job.filepath,
                         seconds=round(job.finished - job.created, 1), bytes=job.total)
        else:
            job.status = "error"
            job.error = job.error or "yt-dlp exited with code %s" % rc
            self.log.log("download error #%s: %s" % (job.id, job.error),
                         level="error", event="download_error", id=job.id, url=job.url, error=job.error)
            # "Unsupported URL" on a player page: scan the HTML for the real
            # stream / embed and retry once with the candidate we find.
            retriable = allow_fallback and (
                "Unsupported URL" in job.error or "Postprocessing:" in job.error)
            if retriable:
                cand = self._page_fallback(job)
                if cand and cand != job.url:
                    self.log.log("unsupported URL #%s -> retrying with %s" % (job.id, cand[:120]),
                                 event="download_fallback", id=job.id, from_url=job.url, to_url=cand)
                    # v1.9.7 UX: while the fallback retry is in flight, the job
                    # leaves "error" - the user never sees a dead-looking red
                    # badge for a download that is about to succeed
                    # (field log: both real sessions showed error, then done).
                    job.status = "fallback"
                    job.error = ""
                    self.push_ui("download_update", job.public())
                    job.url = cand
                    # the fallback stream is a bare manifest - name it after
                    # the page the user actually wanted (v1.1.2 case)
                    if not job.title_base:
                        job.title_base = job.title
                    job.status = "queued"
                    return self._execute(job, allow_fallback=False)
        self.push_ui("download_update", job.public())

    # ------------------------------------------------- v1.3.6 payload checks
    def _looks_like_html(self, path: str) -> bool:
        """True when the first bytes of `path` look like an HTML/XML page
        rather than media data (desktop twin of the Android
        Downloader.looksLikeHtml sniff, unit-tested): optional UTF-8 BOM,
        leading whitespace and HTML comments, then <!doctype html / <html /
        <?xml. Media files start with binary boxes/sizes, never a tag."""
        try:
            with open(path, "rb") as fh:
                head = fh.read(512)
        except OSError:
            return False
        if head[:3] == b"\xef\xbb\xbf":
            head = head[3:]
        low = head.decode("ascii", "replace").lstrip().lower()
        while True:
            if low.startswith("<!--"):  # some pages ship a comment before the doctype
                end = low.find("-->", 4)
                if end < 0:
                    return False  # comment fills the sniffed window - undecided
                low = low[end + 3:].lstrip()
                continue
            break
        return low.startswith("<!doctype html") or low.startswith("<html") or low.startswith("<?xml")

    def _probe_no_file(self, job: Job, ytdlp: str) -> None:
        """Exit 0 without a produced file: ask yt-dlp what it actually sees
        (-F) and mirror the answer into the logs - the same diagnosis the
        Android service runs (event download_no_file_probe)."""
        try:
            probe = subprocess.run(
                [ytdlp, "--no-playlist", "--no-warnings", "-F", job.url],
                capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            tail = [ln for ln in (probe.stdout or "").strip().splitlines() if ln][-12:]
            for ln in tail:
                self.log.dl("[%s][probe] %s" % (job.id, ln))
            self.log.log("no-file probe #%s tail: %s" % (job.id, " | ".join(tail)[-400:]),
                         event="download_no_file_probe", id=job.id, url=job.url)
        except Exception as e:  # noqa: BLE001
            self.log.log("no-file probe failed #%s: %r" % (job.id, e),
                         level="warning", event="download_no_file_probe_error",
                         id=job.id, error=repr(e))

    def _handle_html_payload(self, job: Job, allow_fallback: bool) -> None:
        """The finished job wrote an HTML player page instead of media: flag
        it (event download_not_media), keep the page as *.html evidence, and
        retry once through the page-fallback scanner."""
        self.log.log("saved file is HTML, not media #%s: %s" % (job.id, job.filepath),
                     level="error", event="download_not_media", id=job.id, url=job.url,
                     filepath=job.filepath)
        try:
            evidence = job.filepath + ".html"
            os.replace(job.filepath, evidence)
            job.filepath = evidence
        except OSError:
            pass
        if allow_fallback:
            cand = self._page_fallback(job)
            if cand and cand != job.url:
                self.log.log("not-media payload #%s -> retrying with %s" % (job.id, cand[:120]),
                             event="download_fallback", id=job.id, from_url=job.url, to_url=cand)
                # v1.9.7 UX: same as the "Unsupported URL" path - show the
                # user "กำลังลองแหล่งอื่น" instead of an error right before
                # the retry that usually succeeds
                job.status = "fallback"
                job.error = ""
                self.push_ui("download_update", job.public())
                job.url = cand
                job.status = "queued"
                if not job.title_base:
                    job.title_base = job.title
                return self._execute(job, allow_fallback=False)
        job.status = "error"
        job.error = ("the saved file is an HTML page, not a video (the site served the "
                     "player page) - try a stream link (.m3u8/.mpd) from the list")
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
        # 1) known stem: our title-based template may have produced
        #    `stem.ext` / `stem (2).ext` without yt-dlp ever printing the path
        stem = sanitize_filename(job.title_base)
        if stem:
            hit = self._newest_match(job.out_dir, stem)
            if hit:
                return hit
        # 2) ask yt-dlp what it would have written
        try:
            out = subprocess.run([self.engines.ytdlp_path or "yt-dlp", "--no-playlist", "--print", "filename",
                                  "-o", self._out_template(job), "--skip-download", job.url],
                                 capture_output=True, text=True, timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            p = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
            return p if p and os.path.exists(p) else job.filepath
        except Exception:
            return job.filepath
