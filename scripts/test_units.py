"""Unit tests for memory-bounded stores - no WebView / GUI required.

Covers the v1.0.1 memory-growth fixes:
  * MediaStore (desktop, app/main.py)  - true LRU eviction on re-detection
  * DownloadManager (app/core/downloader.py) - finished-job history pruning
    that never forgets a Job that is still queued/running/merging
  * detector pair-sync guard (app/core/detector.py) - cheap re-check

Run:  python scripts/test_units.py
The Android MediaStore twin (MediaStore.kt) mirrors the LRU logic 1:1 but
cannot be imported from Python; the desktop side is the reference test.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from core.detector import check_pair_sync  # noqa: E402
from core.downloader import DownloadManager, Job  # noqa: E402

results: dict[str, bool] = {}


# ---------------------------------------------------------------- MediaStore
def test_media_store_lru() -> bool:
    from main import MediaStore

    log = type("L", (), {"log": staticmethod(lambda *a, **k: None)})()
    store = MediaStore(log, limit=3)

    for i in range(3):
        store.add({"url": f"http://x/{i}.mp4", "kind": "mp4", "via": "t"})
    assert len(store.list()) == 3, "store should hold limit items"

    # Re-detecting an OLD url must NOT evict it (true LRU touch)...
    assert store.add({"url": "http://x/0.mp4", "kind": "mp4", "via": "t"}) is False
    store.add({"url": "http://x/3.mp4", "kind": "mp4", "via": "t"})

    urls = {r["url"] for r in store.list()}
    assert len(urls) == 3, f"expected 3 items, got {urls}"
    # ...0 was touched, so 1 is the one that fell out - the old FIFO kept 0.
    assert "http://x/1.mp4" not in urls, f"LRU should evict item 1, got {urls}"
    assert "http://x/0.mp4" in urls, "touched item must survive"

    # A never-seen url returns new=True.
    assert store.add({"url": "http://x/4.mp4", "kind": "mp4", "via": "t"}) is True
    return True


# ------------------------------------------------------ DownloadManager prune
def _manager_with_history(n: int, statuses: list[str]) -> DownloadManager:
    settings = type("S", (), {"get": staticmethod(lambda k: 3)})()
    log = type("L", (), {"log": staticmethod(lambda *a, **k: None)})()
    engines = type("E", (), {})()
    mgr = DownloadManager(settings, engines, log)  # type: ignore[arg-type]
    for i in range(n):
        job = Job(f"http://x/{i}", title=f"j{i}")
        job.status = statuses[i % len(statuses)]
        mgr.jobs[job.id] = job
        mgr.history.append(job.id)
    return mgr


def test_prune_keeps_running_jobs() -> bool:
    # 260 jobs alternating done/running: pruning must cap history while
    # keeping every unfinished Job alive in memory until its _run() finishes.
    mgr = _manager_with_history(260, ["done", "running"])
    unfinished_ids = {j.id for j in mgr.jobs.values()
                      if j.status not in ("done", "error", "canceled")}
    before_ids = set(mgr.jobs)
    with mgr._lock:
        mgr._prune_history_locked(keep=200)
    assert len(mgr.history) == 200, f"history must be capped at 200, got {len(mgr.history)}"
    assert unfinished_ids <= set(mgr.jobs), \
        "unfinished jobs must never be dropped from memory"
    removed = before_ids - set(mgr.jobs)
    assert all(jid not in unfinished_ids for jid in removed), \
        "only finished jobs may be pruned from the jobs dict"
    # Jobs alive in memory but outside the trimmed history are exactly the
    # unfinished ones that fell out of the dropped slice (their _run() will
    # re-append + re-prune when they finish).
    orphans = set(mgr.jobs) - set(mgr.history)
    assert orphans == unfinished_ids & (before_ids - set(mgr.history)), \
        "orphans must be exactly the unfinished jobs cut from history"
    return True


def test_prune_noop_under_limit() -> bool:
    mgr = _manager_with_history(50, ["done"])
    before = len(mgr.jobs)
    with mgr._lock:
        mgr._prune_history_locked(keep=200)
    assert len(mgr.history) == 50 and len(mgr.jobs) == before
    return True


def test_prune_frees_finished_jobs() -> bool:
    mgr = _manager_with_history(300, ["done"])
    with mgr._lock:
        mgr._prune_history_locked(keep=200)
    assert len(mgr.jobs) == 200, f"finished Job objects must be freed, got {len(mgr.jobs)}"
    return True


# ------------------------------------------------- page fallback scanner
def test_page_fallback_scanner() -> bool:
    """Regexes that power the 'Unsupported URL' auto-fallback (v1.1.2).

    Case modeled on a real player page (merrylion2.com/*/player.html):
    a tiny HTML that builds an MPEG-DASH URL in JavaScript - no <video>,
    no iframe. The media regex must fish the .mpd/.m3u8/.mp4 out of the
    script text; the iframe regex handles classic embed wrappers.
    """
    from core.downloader import DownloadManager

    player_html = (
        '<!DOCTYPE html><html><head><title>Player</title></head><body>'
        '<div id="player"></div><script>const u="https://merrylion2.com/f5tzf5shrb/output.mpd";'
        'let p=dashjs.MediaPlayer().create();p.initialize(document.querySelector("#player"),u,true);'
        '</script></body></html>'
    )
    m = DownloadManager._MEDIA_IN_HTML_RE.search(player_html)
    assert m, "media regex must find the .mpd URL in the script"
    assert m.group(0) == "https://merrylion2.com/f5tzf5shrb/output.mpd"

    # media with query string / mixed extensions
    for url in (
        "https://cdn.x.com/v/movie.m3u8?tok=1",
        "https://cdn.x.com/v/movie.webm",
        "http://127.0.0.1:8123/movie.mp4",
    ):
        assert DownloadManager._MEDIA_IN_HTML_RE.search(f"src={url!r}"), url

    # iframe/embed wrapper -> relative src is resolved by urljoin upstream
    iframe_html = '<iframe src="/embed/abc123" allowfullscreen></iframe>'
    m2 = DownloadManager._IFRAME_RE.search(iframe_html)
    assert m2 and m2.group(1) == "/embed/abc123"

    # nothing to find -> both None
    assert not DownloadManager._MEDIA_IN_HTML_RE.search("<p>hello</p>")
    assert not DownloadManager._IFRAME_RE.search("<p>hello</p>")
    return True


def test_page_fallback_obfuscated() -> bool:
    """atob/base64-obfuscated players must still yield a media URL."""
    import base64
    from core.downloader import DownloadManager

    real = "https://cdn.example.com/v/stream.m3u8?tok=9"
    blob = base64.b64encode(real.encode()).decode()
    html = f'<script>var u=atob("{blob}");play(u);</script>'
    assert not DownloadManager._MEDIA_IN_HTML_RE.search(html), "plain scan must miss it"

    # decode step used by _page_fallback
    blob_match = re.search(r'atob\(\s*["\']([A-Za-z0-9+/=]{24,})["\']\s*\)', html)
    assert blob_match, "atob pattern must match"
    decoded = base64.b64decode(blob_match.group(1)).decode()
    m = DownloadManager._MEDIA_IN_HTML_RE.search(decoded)
    assert m and m.group(0) == real
    return True


# --------------------------------------- title-based naming + dedupe (v1.1.6)
def test_sanitize_filename() -> bool:
    from core.downloader import sanitize_filename

    assert sanitize_filename("bad:name?.mp4") == "bad name .mp4"
    # spaces + trailing dots stripped (Windows), interior dots kept
    assert sanitize_filename("  end. ") == "end"
    # collapsed whitespace after replacing invalid chars
    assert sanitize_filename('A<B>C:D"E/F\\G?*|.txt') == "A B C D E F G .txt"
    # Thai + emoji survive
    assert sanitize_filename("  ทดสอบ 🎬 clip  ") == "ทดสอบ 🎬 clip"
    # length cap (100) so `stem (9).ext` still fits MAX_PATH budgets
    assert sanitize_filename("x" * 250) == "x" * 100
    assert sanitize_filename(None) == ""
    return True


def test_unique_stem() -> bool:
    import tempfile
    from core.downloader import unique_stem

    d = tempfile.mkdtemp(prefix="vg-dedupe-")
    a = unique_stem(d, "clip", ".mp4")
    assert os.path.basename(a) == "clip.mp4"
    open(a, "wb").close()
    b = unique_stem(d, "clip", ".mp4")
    assert os.path.basename(b) == "clip (2).mp4", b
    open(b, "wb").close()
    c = unique_stem(d, "clip", ".mp4")
    assert os.path.basename(c) == "clip (3).mp4", c
    # other extensions/stems are unaffected
    assert os.path.basename(unique_stem(d, "clip", ".webm")) == "clip.webm"
    assert os.path.basename(unique_stem(d, "other", ".mp4")) == "other.mp4"
    return True


def test_title_base_policy() -> bool:
    """Page/URL titles name media+manifests; site pages keep yt-dlp's title."""
    from core.downloader import DownloadManager

    d = DownloadManager._decide_title_base
    assert d("http://c/v.mp4", "media", "mp4", "Travel Blog") == "Travel Blog"
    assert d("http://c/v.m3u8?tok=1", "hls", "m3u8", "Live TV") == "Live TV"
    assert d("http://c/v.mpd", "dash", "mpd", "Live TV") == "Live TV"
    # site-page URLs: title is the site name -> keep yt-dlp metadata naming
    assert d("http://site/player.html", "embed", "", "SomeSite") == ""
    assert d("http://site/watch/1", "page", "", "SomeSite") == ""
    # explicit title_base always wins
    assert d("http://c/v.mp4", "media", "mp4", "A", "B") == "B"
    return True


def test_out_template_and_newest_match() -> bool:
    import tempfile
    from core.downloader import DownloadManager, Job

    settings = type("S", (), {"get": staticmethod(lambda k: 3)})()
    log = type("L", (), {"log": staticmethod(lambda *a, **k: None)})()
    mgr = DownloadManager(settings, type("E", (), {})(), log)  # type: ignore[arg-type]
    d = tempfile.mkdtemp(prefix="vg-tmpl-")

    # title already sanitized (the ':' would have become a space anyway)
    job = Job("http://127.0.0.1:1/x.m3u8", kind="hls", out_dir=d,
              title_base="My Clip ตอน 1")
    tmpl = mgr._out_template(job)
    assert os.path.basename(tmpl) == "My Clip ตอน 1.%(ext)s", tmpl

    # literal % must be doubled or yt-dlp eats it as a template field
    job2 = Job("http://127.0.0.1:1/x.mp4", kind="media", out_dir=d, title_base="100% Cool")
    assert os.path.basename(mgr._out_template(job2)) == "100%% Cool.%(ext)s"

    # no title -> legacy metadata template
    job3 = Job("http://site/watch/1", kind="page", out_dir=d)
    assert "[%(id)s]" in mgr._out_template(job3)

    # _newest_match picks the newest stem.ext / stem (n).ext
    f1 = os.path.join(d, "My Clip ตอน 1.mp4")
    open(f1, "wb").close()
    time.sleep(0.02)
    f2 = os.path.join(d, "My Clip ตอน 1 (2).mp4")
    open(f2, "wb").close()
    hit = mgr._newest_match(d, "My Clip ตอน 1")
    assert os.path.basename(hit) == "My Clip ตอน 1 (2).mp4", hit
    assert mgr._newest_match(d, "No Such Stem") == ""
    return True


# ------------------------------------------------- site exclusions (v1.2.0)
def test_exclusion_matcher() -> bool:
    from core.settings import exclusion_re, url_excluded

    cases = [
        # bare domain = domain + subdomains + every path
        ("facebook.com", "https://www.facebook.com/watch/?v=123", True),
        ("facebook.com", "https://facebook.com/", True),
        ("facebook.com", "https://notfacebook.com/video.mp4", False),
        ("facebook.com", "https://mail.google.com/inbox", False),
        # explicit wildcard host (Chrome match-pattern style)
        ("*.tiktok.com/*", "https://www.tiktok.com/@user/video/1", True),
        ("*.tiktok.com/*", "https://tiktok.com/x", True),
        # full match pattern pins the scheme
        ("https://www.facebook.com/*", "https://www.facebook.com/reel/9", True),
        ("https://www.facebook.com/*", "http://www.facebook.com/reel/9", False),
        ("*://*.tiktok.com/*", "http://m.tiktok.com/x", True),
        # host in URLs may carry a port - patterns must still match
        ("127.0.0.1", "http://127.0.0.1:8799/video.mp4", True),
        ("*://127.0.0.1/*", "http://127.0.0.1:8799/video.mp4", True),
        # path scoping
        ("example.com/videos/*", "https://example.com/videos/1.mp4", True),
        ("example.com/videos/*", "https://example.com/watch/1.mp4", False),
        # empty / blank patterns never match; bad ones compile to None
        ("", "https://example.com/v.mp4", False),
        ("   ", "https://facebook.com/", False),
    ]
    for pat, url, want in cases:
        got = url_excluded([pat], url)
        assert got == want, f"pattern {pat!r} vs {url!r}: expected {want}, got {got}"
    assert exclusion_re("bad pattern with spaces/") is None or True  # must not raise
    assert url_excluded([], "https://facebook.com/") is False
    assert url_excluded(None, "https://facebook.com/") is False
    # several patterns: any match wins
    assert url_excluded(["a.com", "b.org"], "https://b.org/x") is True
    # case-insensitive like the JS implementations
    assert url_excluded(["FACEBOOK.COM"], "https://WWW.Facebook.com/v") is True
    return True


# ------------------------------------- exclusion transfer file (v1.2.1)
def test_exclusion_transfer() -> bool:
    """Same JSON file imports on both desktop and the Chrome extension."""
    import json as _json
    from core.settings import merge_exclusion_patterns, norm_exclusion

    # normalization: trim/lowercase + bare www. collapses to the domain
    assert norm_exclusion("  Example.ORG ") == "example.org"
    assert norm_exclusion("www.example.org") == "example.org"
    assert norm_exclusion("https://WWW.x.com/*") == "https://www.x.com/*"  # only bare hosts

    existing = ["facebook.com"]
    merged, added = merge_exclusion_patterns(existing, [
        "TIKTOK.com",              # added (normalized)
        "facebook.com",             # duplicate -> dropped
        "",                         # blank -> dropped
        "  ",                       # whitespace-only -> dropped
        "bad pattern/with spaces",  # whitespace inside -> dropped
        "www.example.org",          # www-collapse -> added once
        "https://a.com/*",          # valid full pattern -> added
        None,                       # non-string -> dropped (norm -> "")
    ])
    assert merged == ["facebook.com", "tiktok.com", "example.org", "https://a.com/*"], merged
    assert added == 3, added
    # original list untouched (pure function)
    assert existing == ["facebook.com"]
    # empty import is a no-op
    m2, a2 = merge_exclusion_patterns(["x.com"], [])
    assert (m2, a2) == (["x.com"], 0)

    # the export envelope the Api produces must round-trip through the parser
    doc = {"app": "VDO Grabber", "kind": "exclusions", "version": 1,
           "exported": "2026-10-01T00:00:00+0700", "patterns": ["a.com", "b.org"]}
    m3, a3 = merge_exclusion_patterns([], doc["patterns"])
    assert m3 == ["a.com", "b.org"] and a3 == 2
    assert _json.loads(_json.dumps(doc))["kind"] == "exclusions"
    return True


# ------------------------------------------------------------- pair-sync
def test_pair_sync_guard() -> bool:
    r = check_pair_sync()
    if r.get("skipped"):  # frozen run without source tree - nothing to assert
        print("  (pair_sync skipped:", r["skipped"], ")")
        return True
    assert r["ok"], f"pair_sync failed: {r.get('error')} / {r.get('checks')}"
    return True


def main() -> int:
    tests = {
        "media_store_lru": test_media_store_lru,
        "prune_keeps_running": test_prune_keeps_running_jobs,
        "prune_noop_under_limit": test_prune_noop_under_limit,
        "prune_frees_finished": test_prune_frees_finished_jobs,
        "page_fallback_scanner": test_page_fallback_scanner,
        "page_fallback_obfuscated": test_page_fallback_obfuscated,
        "sanitize_filename": test_sanitize_filename,
        "unique_stem": test_unique_stem,
        "title_base_policy": test_title_base_policy,
        "out_template_and_newest_match": test_out_template_and_newest_match,
        "exclusion_matcher": test_exclusion_matcher,
        "exclusion_transfer": test_exclusion_transfer,
        "pair_sync_guard": test_pair_sync_guard,
    }
    failed = []
    for name, fn in tests.items():
        try:
            results[name] = bool(fn())
        except AssertionError as e:
            results[name] = False
            failed.append((name, str(e)))
        except Exception as e:  # noqa: BLE001
            results[name] = False
            failed.append((name, repr(e)))
        print(f"{'PASS' if results[name] else 'FAIL'}  {name}")

    if failed:
        print("\nFailures:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        return 1
    print(f"\nALL {len(tests)} UNIT TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
