"""Favorite websites (v1.9.21) - one-click access to the sites the user
returns to.

A separate JSON file (``favorites.json``) next to settings.json in the data
dir keeps the list independent of the user-tweakable settings (deleting
settings.json on move/reset must not lose the bookmarks). Each entry:
{"url": ..., "title": ..., "added": iso-timestamp}.

Ordering is newest-first for display, but removal is by exact URL so a
duplicate add can never create two rows (latest title wins instead).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime

MAX_FAVORITES = 300


def norm_url(raw: str) -> str:
    """Canonical form used both for dedupe and as the removal key."""
    return str(raw or "").strip()


class FavoritesStore:
    """Load/save a favorites.json file; thread-safe."""

    def __init__(self, data_dir: str, log=None):
        self.path = os.path.join(data_dir, "favorites.json")
        self._lock = threading.Lock()
        self._log = log
        self.items: list[dict] = []
        self.load()

    # ------------------------------------------------------------ io
    def load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    self.items = [e for e in raw
                                  if isinstance(e, dict) and e.get("url")]
        except Exception as e:
            if self._log:
                self._log.log("favorites load failed: %r" % e,
                              level="warning", event="favorites_load_error",
                              error=repr(e))
            self.items = []

    def _save(self) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            if self._log:
                self._log.log("favorites save failed: %r" % e,
                              level="warning", event="favorites_save_error",
                              error=repr(e))

    # ------------------------------------------------------- operations
    def list(self) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self.items]

    def add(self, url: str, title: str = "") -> dict:
        """Insert or refresh one bookmark. Returns the stored entry."""
        u = norm_url(url)
        if not u:
            return {"ok": False, "error": "empty url"}
        with self._lock:
            entry = None
            for e in self.items:
                if norm_url(e.get("url")) == u:
                    entry = e
                    break
            if entry:
                # refresh title, move to top (most recently used first)
                if title:
                    entry["title"] = title.strip()[:200]
                entry["added"] = datetime.now().isoformat(timespec="seconds")
                self.items.remove(entry)
                self.items.insert(0, entry)
                added = False
            else:
                entry = {"url": u, "title": (title or "").strip()[:200],
                         "added": datetime.now().isoformat(timespec="seconds")}
                self.items.insert(0, entry)
                del self.items[MAX_FAVORITES:]
                added = True
            self._save()
        if self._log:
            self._log.log("favorite %s: %s" % ("added" if added else "moved to top", u),
                          event="favorite_added" if added else "favorite_used",
                          url=u, title=entry.get("title") or "", total=len(self.items))
        return {"ok": True, "added": added, "entry": dict(entry),
                "total": len(self.items)}

    def remove(self, url: str) -> dict:
        u = norm_url(url)
        with self._lock:
            before = len(self.items)
            self.items = [e for e in self.items if norm_url(e.get("url")) != u]
            self._save()
            removed = before - len(self.items)
        if self._log:
            self._log.log("favorite removed: %s" % u, event="favorite_removed",
                          url=u, total=len(self.items))
        return {"ok": True, "removed": removed, "total": len(self.items)}
