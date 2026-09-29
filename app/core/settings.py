"""Persistent app settings (JSON in the data dir)."""

from __future__ import annotations

import json
import os
import threading

DEFAULTS = {
    "download_dir": os.path.join(os.path.expanduser("~"), "Downloads", "VDOGrabber"),
    "max_concurrent": 3,
    "log_level": "DEBUG",
    "user_agent": "",           # empty = engine default
    "embed_thumbnail": False,
    "auto_detect": True,        # run page scan on every navigation
    "notify_done": True,
    "start_page": "https://www.google.com",
    "theme": "dark",
}


class Settings:
    def __init__(self, data_dir: str, log=None):
        self.path = os.path.join(data_dir, "settings.json")
        self._lock = threading.Lock()
        self._log = log
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                self.data.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except Exception as e:
            if self._log:
                self._log.log("settings load failed: %r" % e, level="warning", event="settings_load_error", error=repr(e))

    def save(self) -> None:
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
            except OSError as e:
                if self._log:
                    self._log.log("settings save failed: %r" % e, level="error")

    def get(self, key: str):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if key in DEFAULTS:
            self.data[key] = value
            self.save()
