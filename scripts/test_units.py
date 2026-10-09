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


# ------------------------------------------- v1.8.0 pause / resume (desktop)
def test_pause_resume_lifecycle() -> bool:
    """หยุดพัก keeps the job in the list with status 'paused' and its
    title_base untouched (resume re-runs the SAME template over the kept
    .part fragments); ดาวน์โหลดต่อ flips it back to queued and re-spawns the
    worker. Cancel and paused never leak into each other."""
    mgr = _manager_with_history(2, ["running"])
    jobs = list(mgr.jobs.values())
    a, b = jobs[0], jobs[1]

    pa = mgr.pause(a.id)
    assert pa["ok"] and pa["job"]["status"] == "paused", pa
    assert a.status == "paused"
    # paused jobs stay in the list/history (UI must keep showing them)
    assert a.id in mgr.jobs and a.id in mgr.history

    # pausing again / resuming a running job is a clean no
    assert not mgr.pause(a.id)["ok"], "already paused"
    assert not mgr.resume(b.id)["ok"], "running job is not resumable"

    # cancel and paused are distinct states - cancel must not revive it
    r = mgr.resume(a.id)
    assert r["ok"] and a.status == "queued" and not a.error, r
    # unknown ids fail cleanly
    assert not mgr.pause("nope")["ok"] and not mgr.resume("nope")["ok"]
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


# ------------------------------------------- sieve scrape API (v1.3.0)
def _sieve_fake_transport(script):
    """Record requests; return (calls, transport). `script` entries are either
    (status, headers, body) tuples or an Exception to raise, consumed in order.
    A 400 body must be bytes; HTTP is the only faked boundary."""
    calls = []

    def transport(method, url, headers, data, timeout):
        calls.append({"method": method, "url": url, "headers": dict(headers), "data": data, "timeout": timeout})
        item = script[min(len(calls) - 1, len(script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    return calls, transport


def _sieve_settings(**over):
    d = {"sieve_api_key": "dc_sk_test", "download_dir": tempfile.mkdtemp(prefix="vg-sieve-")}
    d.update(over)
    return type("S", (), {"get": staticmethod(lambda k, d=d: d.get(k)),
                          "set": staticmethod(lambda k, v, d=d: d.__setitem__(k, v))})()


def _sieve_log():
    events = type("L", (), {"log": staticmethod(lambda *a, **k: None),
                            "exception": staticmethod(lambda *a, **k: None)})()
    return events


def test_sieve_request_building() -> bool:
    import json
    from core.sieve import SIEVE_BASE_URL, SieveClient

    calls, transport = _sieve_fake_transport([(202, {}, json.dumps(
        {"status": "queued", "session_id": "sc_1", "poll": "/api/scrapes/sc_1"}).encode())])
    c = SieveClient(api_key="dc_sk_test", transport=transport)
    out = c.start_scrape("Extract the text and author of each quote",
                         target_urls=["https://quotes.toscrape.com"], fields=["text", "author"],
                         output_schema={"type": "object"}, table_shape="long")
    assert out["session_id"] == "sc_1"
    req = calls[0]
    assert req["method"] == "POST" and req["url"] == SIEVE_BASE_URL + "/api/scrapes", req["url"]
    assert req["headers"]["Authorization"] == "Bearer dc_sk_test"
    assert req["headers"]["Content-Type"] == "application/json"
    body = json.loads(req["data"].decode())
    assert body["instruction"].startswith("Extract")
    assert body["compliance_mode"] == "regular"          # default unless user chooses
    assert body["target_urls"] == ["https://quotes.toscrape.com"]
    assert body["output_schema"] == {"type": "object"} and body["table_shape"] == "long"
    # relative file links get the base URL prefixed
    assert c.url_for("/api/x") == SIEVE_BASE_URL + "/api/x"
    assert c.url_for("http://other/y") == "http://other/y"
    return True


def test_sieve_status_handling() -> bool:
    from core.sieve import SieveError, followup_ready, parse_scrape_status, turn_count

    assert parse_scrape_status({"status": "running"}) == "running"
    assert parse_scrape_status({"status": "done"}) == "done"
    assert parse_scrape_status({"status": "refused"}) == "refused"
    for bad in ({}, {"status": "finished"}, {"status": None}):
        try:
            parse_scrape_status(bad)
            raise AssertionError("unknown status must raise")
        except SieveError as e:
            assert e.kind == "unknown_status"
    # follow-up turn check: done AND turns advanced, else the previous answer
    assert turn_count({"turns": [1, 2]}) == 2
    assert turn_count({"turn_count": 4}) == 4
    assert followup_ready({"status": "done", "turns": [1, 2, 3]}, 2) is True
    assert followup_ready({"status": "done", "turns": [1, 2]}, 2) is False
    assert followup_ready({"status": "running", "turns": [1, 2, 3]}, 2) is False
    return True


def test_sieve_error_mapping() -> bool:
    from core.sieve import map_http_error

    cases = {
        400: ("client", False), 401: ("auth", False), 402: ("credits", False),
        404: ("not_found", False), 409: ("in_flight", True), 429: ("rate_limit", True),
        500: ("server", True), 503: ("server", True),
    }
    for status, (kind, retryable) in cases.items():
        e = map_http_error(status, '{"error":"boom"}')
        assert (e.kind, e.retryable) == (kind, retryable), (status, e.kind, e.retryable)
    # the human detail survives mapping
    assert "boom" in map_http_error(400, '{"error":"boom"}').message
    assert map_http_error(401, "").kind == "auth"
    return True


def test_sieve_secret_never_reaches_logs() -> bool:
    import json
    from core.logger import EventLog

    path = os.path.join(tempfile.mkdtemp(prefix="vg-events-"), "events.jsonl")
    ev = EventLog(path)
    ev.write("credential", sieve_api_key="dc_sk_secret", api_key="dc_sk_secret",
             token="ghp_secret", authorization="Bearer dc_sk_secret", note="ok")
    with open(path, "r", encoding="utf-8") as f:
        line = f.read()
    assert "dc_sk_secret" not in line, "the sieve key must never reach events.jsonl"
    assert "ghp_secret" not in line
    assert json.loads(line)["note"] == "ok"
    assert line.count("[redacted]") >= 4
    return True


def test_sieve_retry_after() -> bool:
    from core.sieve import SieveClient, SieveError, retry_after_seconds

    assert retry_after_seconds({"Retry-After": "12"}) == 12.0
    assert retry_after_seconds({}) is None
    # a 429 must carry the server's Retry-After so the poll loop waits it out
    _, transport = _sieve_fake_transport([(429, {"Retry-After": "7"}, b'{"error":"slow down"}')])
    c = SieveClient(api_key="dc_sk_test", transport=transport)
    try:
        c.get_scrape("sc_1")
        raise AssertionError("429 must raise")
    except SieveError as e:
        assert e.kind == "rate_limit" and e.retryable is True and e.retry_after == 7.0
    return True


def test_sieve_never_retries_post_on_timeout() -> bool:
    from core.sieve import SieveClient, SieveError

    calls, transport = _sieve_fake_transport([SieveError("timed out", kind="timeout", retryable=True)])
    c = SieveClient(api_key="dc_sk_test", transport=transport)
    try:
        c.start_scrape("anything")
        raise AssertionError("timeout must surface")
    except SieveError as e:
        assert e.kind == "timeout"
    assert len(calls) == 1, "POST /api/scrapes must be sent exactly once (no auto-retry)"
    return True


def test_sieve_device_token_mapping() -> bool:
    import json
    from core.sieve import SieveClient

    def body(payload):
        return (400, {}, json.dumps(payload).encode())

    for code, want in (("authorization_pending", "pending"), ("slow_down", "slow_down"),
                       ("access_denied", "denied"), ("expired_token", "expired")):
        _, transport = _sieve_fake_transport([body({"error": code})])
        c = SieveClient(transport=transport)
        assert c.device_token("d1")["status"] == want, code
    _, transport = _sieve_fake_transport([(200, {}, json.dumps(
        {"api_key": "dc_sk_new", "token_type": "Bearer", "key_name": "laptop"}).encode())])
    c = SieveClient(transport=transport)
    out = c.device_token("d1")
    assert out["status"] == "ok" and out["api_key"] == "dc_sk_new"
    return True


def test_sieve_unconfigured_is_noop() -> bool:
    from core.sieve import SieveClient, SieveError, SieveManager

    calls, transport = _sieve_fake_transport([(202, {}, b'{"session_id":"x"}')])
    client = SieveClient(api_key="", transport=transport)
    mgr = SieveManager(_sieve_settings(sieve_api_key=""), _sieve_log(),
                       tempfile.mkdtemp(prefix="vg-sieve-"), client=client)
    assert mgr.configured is False
    res = mgr.start("do something")
    assert res["ok"] is False and not len(calls), "unconfigured app must not call the API"
    assert mgr.download_files("nope")["ok"] is False
    assert mgr.credits()["ok"] is False
    try:
        client.start_scrape("x")
        raise AssertionError("no key must raise auth")
    except SieveError as e:
        assert e.kind == "auth"
    return True


def test_sieve_persists_before_polling() -> bool:
    import json
    from core.sieve import SieveClient, SieveManager

    _, transport = _sieve_fake_transport([(202, {}, json.dumps(
        {"status": "queued", "session_id": "sc_persist", "poll": "/api/scrapes/sc_persist"}).encode())])
    client = SieveClient(api_key="dc_sk_test", transport=transport)
    data_dir = tempfile.mkdtemp(prefix="vg-sieve-")
    mgr = SieveManager(_sieve_settings(), _sieve_log(), data_dir, client=client)
    seen = {}

    def fake_poll(run_id):
        # at this point the run MUST already be durable (crash-safe resume)
        with open(os.path.join(data_dir, "sieve_runs.json"), "r", encoding="utf-8") as f:
            seen["store"] = json.load(f)

    mgr._start_poll = fake_poll
    res = mgr.start("Extract quotes", target_urls=["https://quotes.toscrape.com"])
    assert res["ok"]
    sid = res["run"]["session_id"]
    run_id = res["run"]["id"]
    assert seen["store"][run_id]["session_id"] == sid == "sc_persist"
    # a restart loads the pending run back
    mgr2 = SieveManager(_sieve_settings(), _sieve_log(), data_dir, client=client)
    assert mgr2.get_run(run_id)["session_id"] == sid
    return True


def test_sieve_file_download_uses_bearer_and_relative_url() -> bool:
    from core.sieve import SIEVE_BASE_URL, SieveClient

    calls, transport = _sieve_fake_transport([(200, {}, b"col1,col2\n1,2\n")])
    c = SieveClient(api_key="dc_sk_test", transport=transport)
    data = c.download_file("/api/scrapes/sc_1/files/quotes.csv")
    assert data == b"col1,col2\n1,2\n"
    assert calls[0]["url"] == SIEVE_BASE_URL + "/api/scrapes/sc_1/files/quotes.csv"
    assert calls[0]["headers"]["Authorization"] == "Bearer dc_sk_test"
    return True


def test_sieve_poll_backoff() -> bool:
    from core.sieve import POLL_MAX, POLL_START, next_poll_delay

    assert next_poll_delay(0) == POLL_START          # first poll: 5s
    assert next_poll_delay(POLL_START) > POLL_START  # then back off
    assert next_poll_delay(1000) == POLL_MAX         # capped at ~30s, never shorter
    return True


# --------------------------------------------------- events.jsonl analyzer
def test_analyze_events_parsing_and_diagnosis() -> bool:
    import analyze_events

    sample = [
        '{"ts":1759120327.1,"event":"page_loaded","url":"https://example.com/v"}',
        '{"ts":1759120328.0,"event":"media_found","url":"https://cdn.example.com/a.mp4"}',
        'not json at all',
        '{"ts":1759120330.0,"event":"download_skipped_blob","url":"blob:https://x/1"}',
        '{"ts":1759120331.0,"event":"download_no_file","url":"https://x/v"}',
        '[1,2,3]',
    ]
    records, bad = analyze_events.parse_events(sample)
    assert len(records) == 4, "4 valid records expected"
    assert bad == 2, "junk lines must be counted, got %d" % bad

    s = analyze_events.summarize(records)
    assert s["total"] == 4 and s["counts"]["media_found"] == 1
    assert s["blob_urls"] == ["blob:https://x/1"], "blob URLs collected"
    assert s["first_ts"] == 1759120327.1 and s["last_ts"] == 1759120331.0
    assert s["engine_ready_seen"] is False

    findings = analyze_events.diagnose(s, records)
    text = "\n".join(findings)
    assert "blob" in text and "exit 0" in text, "blob + no-file findings present"
    return True


def test_analyze_events_clean_log_has_no_known_problem() -> bool:
    import analyze_events

    records, bad = analyze_events.parse_events(
        ['{"ts":1,"event":"app_start"}', '{"ts":2,"event":"download_done","url":"https://x/a.mp4"}'])
    assert bad == 0
    s = analyze_events.summarize(records)
    findings = analyze_events.diagnose(s, records)
    assert len(findings) == 1 and "ไม่พบสัญญาณปัญหา" in findings[0]
    out = analyze_events.render_text(s, records, findings, records)
    assert "download_done" in out and "app_start" in out, "funnel table lists both"
    return True


def test_analyze_events_cli_missing_file_and_filter() -> bool:
    import contextlib
    import io
    import os
    import tempfile

    import analyze_events

    # stdout must be captured: findings are Thai and a cp1252 console would
    # raise UnicodeEncodeError inside the CLI print (Windows runner lesson)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = analyze_events.main(["Z:/definitely/missing/events.jsonl"])
    assert rc == 2, "missing log exits 2"

    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write('{"ts":1,"event":"download_queued","url":"https://x/a.mp4"}\n')
        fh.write('{"ts":2,"event":"download_done","url":"https://x/a.mp4"}\n')
        fh.write('{"ts":3,"event":"download_no_file","url":"https://x/b.mpd"}\n')
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = analyze_events.main([path, "--event", "download_no_file", "--json"])
        assert rc == 0
        out = buf.getvalue()
        assert "download_no_file" in out and "download_queued" not in out, \
            "--event filter keeps only matching events"
    finally:
        os.remove(path)
    return True


def test_analyze_events_zip_input() -> bool:
    """v1.9.2: the "share logs"/FileLog diagnostics ZIP (events.jsonl +
    app.log + downloads.log as the real user attachment on 2026-10-09)
    must load straight into the analyzer - no manual unzip step."""
    import contextlib
    import io
    import os
    import tempfile
    import zipfile as zf

    import analyze_events

    fd, jsonl_path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write('{"ts":1,"event":"download_queued","url":"https://x/a.mp4"}\n')
        fh.write('{"ts":2,"event":"download_stuck","url":"https://x/a.mp4","age_s":125}\n')
    fdz, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fdz)
    try:
        with zf.ZipFile(zip_path, "w") as z:
            z.write(jsonl_path, "events.jsonl")
            z.writestr("app.log", "x\n" * 10)
            z.writestr("downloads.log", "y\n" * 20)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = analyze_events.main([zip_path])
        assert rc == 0, "zip input must analyze cleanly"
        out = buf.getvalue()
        assert "download_stuck" in out, "stuck event must appear in the funnel table"
        assert "app.log: 10 lines" in out and "downloads.log: 20 lines" in out, \
            "sibling log line counts reported"
        assert "crash.log: (missing)" in out
        # findings: the stuck-event lesson must surface
        assert "download_stuck" in "\n".join(analyze_events.diagnose(
            analyze_events.summarize(analyze_events.parse_events(
                open(jsonl_path, encoding="utf-8").readlines())[0]), []))
    finally:
        os.remove(jsonl_path)
        os.remove(zip_path)
    return True


def test_analyze_events_zip_without_events() -> bool:
    import contextlib
    import io
    import zipfile as zf
    import tempfile
    import os

    import analyze_events

    fdz, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fdz)
    try:
        with zf.ZipFile(zip_path, "w") as z:
            z.writestr("app.log", "x\n")
        buf_err = io.StringIO()
        with contextlib.redirect_stdout(buf_err), contextlib.redirect_stderr(buf_err):
            rc = analyze_events.main([zip_path])
        assert rc == 2, "a zip without events.jsonl must exit 2 with a clear reason"
        assert "no events.jsonl" in buf_err.getvalue()
    finally:
        os.remove(zip_path)
    return True


# --------------------------------------------------------- clipboard helpers
def test_clipboard_url_rules() -> bool:
    """clipboard_url() turns clipboard text into a loadable http(s) URL -
    scheme kept as-is, bare domain gets https://, non-URL text rejected.
    (v1.9.4: the toolbar 📋 button reuses this same function for mode=peek.)"""
    sys.path.insert(0, os.path.join(ROOT, "app"))
    import main as app_main

    assert app_main.clipboard_url("https://merrylion2.com/ou2myn7f92/output.mpd") == \
        "https://merrylion2.com/ou2myn7f92/output.mpd"
    assert app_main.clipboard_url("http://insecure.example") == "http://insecure.example"
    assert app_main.clipboard_url("merrylion2.com") == "https://merrylion2.com"
    # multi-line clipboard: first URL-looking line wins
    assert app_main.clipboard_url("title line\nmerrylion2.com/abc") == "https://merrylion2.com/abc"
    # non-URL content rejected
    assert app_main.clipboard_url("") == ""
    assert app_main.clipboard_url("hello world") == ""
    assert app_main.clipboard_url("mail@example.com") == ""
    return True


def test_toolbar_has_clipboard_button_parity() -> bool:
    """v1.9.4: the in-page toolbar gets a 📋 button (data-act="clip") that
    calls open_clipboard('peek') and puts the URL in the urlbox - never
    navigating from the callback (same race rule as back/fwd/reload)."""
    sys.path.insert(0, os.path.join(ROOT, "app", "core"))
    import detector
    assert 'data-act="clip"' in detector.TOOLBAR_JS, "clipboard button rendered"
    assert "open_clipboard('peek')" in detector.TOOLBAR_JS, "calls peek mode"
    assert "urlbox.value = r.url" in detector.TOOLBAR_JS, "fills the urlbox"
    # navigation happens only through the existing urlbox Enter path
    assert "location.href" in detector.TOOLBAR_JS
    # open_clipboard('go') still navigates for the Control Center
    import main as app_main
    import inspect
    src = inspect.getsource(app_main.Api.open_clipboard)
    assert 'threading.Timer(0.08, _APP.navigate' in src, "go mode navigates"
    assert 'if mode == "peek"' in src, "peek mode returns without navigating"
    return True


def main() -> int:
    tests = {
        "sieve_request_building": test_sieve_request_building,
        "sieve_status_handling": test_sieve_status_handling,
        "sieve_error_mapping": test_sieve_error_mapping,
        "sieve_retry_after": test_sieve_retry_after,
        "sieve_secret_never_reaches_logs": test_sieve_secret_never_reaches_logs,
        "sieve_no_retry_post_on_timeout": test_sieve_never_retries_post_on_timeout,
        "sieve_device_token_mapping": test_sieve_device_token_mapping,
        "sieve_unconfigured_noop": test_sieve_unconfigured_is_noop,
        "sieve_persist_before_poll": test_sieve_persists_before_polling,
        "sieve_file_download": test_sieve_file_download_uses_bearer_and_relative_url,
        "sieve_poll_backoff": test_sieve_poll_backoff,
        "media_store_lru": test_media_store_lru,
        "prune_keeps_running": test_prune_keeps_running_jobs,
        "prune_noop_under_limit": test_prune_noop_under_limit,
        "prune_frees_finished": test_prune_frees_finished_jobs,
        "pause_resume_lifecycle": test_pause_resume_lifecycle,
        "page_fallback_scanner": test_page_fallback_scanner,
        "page_fallback_obfuscated": test_page_fallback_obfuscated,
        "sanitize_filename": test_sanitize_filename,
        "unique_stem": test_unique_stem,
        "title_base_policy": test_title_base_policy,
        "out_template_and_newest_match": test_out_template_and_newest_match,
        "exclusion_matcher": test_exclusion_matcher,
        "exclusion_transfer": test_exclusion_transfer,
        "pair_sync_guard": test_pair_sync_guard,
        "analyze_events_parse_and_diagnose": test_analyze_events_parsing_and_diagnosis,
        "analyze_events_clean_log": test_analyze_events_clean_log_has_no_known_problem,
        "analyze_events_cli": test_analyze_events_cli_missing_file_and_filter,
        "analyze_events_zip_input": test_analyze_events_zip_input,
        "analyze_events_zip_without_events": test_analyze_events_zip_without_events,
        "clipboard_url_rules": test_clipboard_url_rules,
        "toolbar_clip_button": test_toolbar_has_clipboard_button_parity,
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
