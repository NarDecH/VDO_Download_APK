"""Persistent app settings (JSON in the data dir).

Also hosts the per-site exclusion matcher (v1.2.0) - a Python mirror of the
regex logic used by the Chrome extension (extension/content.js) so both
platforms interpret the same patterns the same way:
  - full match patterns: "https://www.facebook.com/*", "*://*.tiktok.com/*"
  - bare domains: "facebook.com" = the domain + all subdomains, any path
  - ports are ignored when matching (host in URLs may carry :port)
"""

from __future__ import annotations

import json
import os
import re
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
    "exclusions": [],           # v1.2.0: sites with no detection/toolbar (list of patterns)
    "github_pat": "",           # v1.2.2: GitHub token (gist scope) for cloud sync - stored locally only
    "gist_id": "",              # secret gist that carries vdograbber-exclusions.json (empty = create on first sync)
}


# ------------------------------------------------------------ exclusion matcher
def exclusion_re(pattern: str):
    """Compile a user exclusion pattern into an anchored regex (None = bad).
    Python mirror of vgExclusionRe() in extension/content.js + background.js.
    Keep the three implementations in sync when changing the semantics."""
    p = str(pattern or "").strip().lower()
    if not p:
        return None
    if not re.match(r"^[a-z*]+://", p):
        p = "*://" + p                      # bare "host/path" -> any scheme
    m = re.match(r"^([a-z*]+)://([^/]*)(.*)$", p)
    if not m:
        return None
    scheme, host, path = m.group(1), m.group(2), m.group(3)
    if not path or path == "/":
        path = "/*"
    scheme_re = "https?" if scheme == "*" else re.escape(scheme)
    # "*.host.tld" matches subdomains AND the bare host (Chrome patterns
    # only cover subdomains - matching the apex too is friendlier); a bare
    # host covers its subdomains too
    if host.startswith("*."):
        host_re = "(?:[^/]+\\.)?" + re.escape(host[2:]).replace(r"\*", "[^/]*")
    elif "*" in host:
        host_re = re.escape(host).replace(r"\*", "[^/]*")
    else:
        host_re = "(?:[^/]+\\.)?" + re.escape(host)
    path_re = re.escape(path).replace(r"\*", ".*")
    try:
        return re.compile("^" + scheme_re + "://" + host_re + "(?::\\d+)?" + path_re + "$")
    except re.error:
        return None


def url_excluded(patterns, url: str) -> bool:
    """True when `url` matches any exclusion pattern (case-insensitive)."""
    u = str(url or "").lower()
    return any((r and r.search(u)) for r in (exclusion_re(p) for p in (patterns or [])))


def norm_exclusion(pattern: str) -> str:
    """Normalize a user-typed exclusion pattern (lowercase, trimmed; a bare
    "www." host collapses to the registrable domain so it covers the whole
    site, mirroring what a user means). Python mirror of the pattern cleanup
    in extension/background.js."""
    p = str(pattern or "").strip().lower()
    if p and "://" not in p and p.startswith("www."):
        p = p[4:]
    return p


def merge_exclusion_patterns(existing, patterns):
    """Merge imported patterns onto `existing` for the transfer file
    (v1.2.1 - same JSON document on desktop and the Chrome extension):
    blanks, whitespace typos, duplicates and matcher-incompatible entries
    are dropped. Returns (new_list, added_count)."""
    out = list(existing or [])
    added = 0
    for raw in patterns or []:
        p = norm_exclusion(raw)
        if p and not any(c.isspace() for c in p) and p not in out and exclusion_re(p) is not None:
            out.append(p)
            added += 1
    return out, added


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
