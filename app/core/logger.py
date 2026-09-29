"""VDO Grabber - detailed logging system.

Three log streams, all under <data_dir>/logs:
  app.log        rotating text log, DEBUG level, human readable  (5 MB x 5)
  downloads.log  every yt-dlp / network download line, verbatim  (5 MB x 5)
  events.jsonl   structured machine-readable events, one JSON per line

The same trio is mirrored 1:1 by the Android app (see android/.../FileLog.kt)
so both platforms produce comparable diagnostics.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler

APP_NAME = "VDOGrabber"
APP_VERSION = "1.1.0"

_levels = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


class EventLog:
    """Structured JSONL event stream (events.jsonl)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._seq = 0
        self.session_id = f"s-{int(time.time())}-{os.getpid()}"

    def write(self, event_type: str, **data) -> None:
        self._seq += 1
        now = time.time()
        rec = {
            "ts": round(now, 3),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "seq": self._seq,
            "session": self.session_id,
            "event": event_type,
        }
        rec.update(data)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError:
            pass


class LogManager:
    """Central logging hub. One instance per process."""

    def __init__(self, data_dir: str, level: str = "DEBUG"):
        self.data_dir = data_dir
        self.logs_dir = os.path.join(data_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

        self.level = _levels.get(level.upper(), logging.DEBUG)
        self._lock = threading.Lock()

        fmt = logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)-7s] [%(threadName)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        self.app_log = logging.getLogger("vdo.app")
        self.app_log.setLevel(logging.DEBUG)
        self.app_log.handlers.clear()
        self.app_log.propagate = False
        h1 = RotatingFileHandler(os.path.join(self.logs_dir, "app.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        h1.setFormatter(fmt)
        h1.setLevel(self.level)
        self.app_log.addHandler(h1)

        self.dl_log = logging.getLogger("vdo.download")
        self.dl_log.setLevel(logging.DEBUG)
        self.dl_log.handlers.clear()
        self.dl_log.propagate = False
        h2 = RotatingFileHandler(os.path.join(self.logs_dir, "downloads.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        h2.setFormatter(fmt)
        self.dl_log.addHandler(h2)

        self.events = EventLog(os.path.join(self.logs_dir, "events.jsonl"))

    # ------------------------------------------------------------------
    def log(self, msg: str, *args, level: str = "info", event: str | None = None, **event_data) -> None:
        """Log to app.log and (optionally) emit a structured event."""
        getattr(self.app_log, level, self.app_log.info)(msg, *args)
        if event:
            self.events.write(event, **event_data)

    def dl(self, msg: str, *args, level: str = "info") -> None:
        getattr(self.dl_log, level, self.dl_log.info)(msg, *args)

    def exception(self, where: str, exc: BaseException) -> None:
        self.app_log.error("EXCEPTION in %s: %r\n%s", where, exc, traceback.format_exc())
        self.events.write("exception", where=where, error=repr(exc), trace=traceback.format_exc(limit=8))

    # ------------------------------------------------------------------
    def tail(self, name: str, lines: int = 200) -> str:
        """Return the last N lines of a log file (for the in-app log viewer)."""
        path = {"app": "app.log", "downloads": "downloads.log", "events": "events.jsonl"}.get(name)
        if not path:
            return f"unknown log: {name}"
        full = os.path.join(self.logs_dir, path)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-lines:])
        except OSError as e:
            return f"(cannot read {path}: {e})"

    def open_logs_folder(self) -> None:
        os.startfile(self.logs_dir)  # noqa: S606  (Windows-only app)

    def export_diagnostics(self, dest_dir: str | None = None) -> str:
        """Zip all logs + settings into a single diagnostics bundle."""
        import zipfile

        dest_dir = dest_dir or os.path.join(os.path.expanduser("~"), "Desktop")
        os.makedirs(dest_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = os.path.join(dest_dir, f"{APP_NAME}-diagnostics-{stamp}.zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for fn in ("app.log", "downloads.log", "events.jsonl"):
                p = os.path.join(self.logs_dir, fn)
                if os.path.exists(p):
                    z.write(p, fn)
            sp = os.path.join(self.data_dir, "settings.json")
            if os.path.exists(sp):
                z.write(sp, "settings.json")
        self.log("diagnostics exported -> %s" % out, event="diagnostics_export", path=out)
        return out

    # ------------------------------------------------------------------
    def set_level(self, level: str) -> None:
        lv = _levels.get(level.upper(), logging.DEBUG)
        self.level = lv
        for h in self.app_log.handlers:
            h.setLevel(lv)


def default_data_dir() -> str:
    """%APPDATA%/VDOGrabber, or a portable/ folder next to the exe when portable.flag exists."""
    exe_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    if os.path.exists(os.path.join(exe_dir, "portable.flag")) or os.path.exists(os.path.join(os.path.dirname(exe_dir), "portable.flag")):
        base = os.path.dirname(exe_dir) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base, "portable-data")
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)
