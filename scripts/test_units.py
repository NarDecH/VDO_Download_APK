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
import sys
import tempfile

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
