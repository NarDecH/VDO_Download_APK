"""E2E test for the VDO Grabber Chrome extension.

Launches a real Chrome with --load-extension, serves the blob test page
(same mechanism as player2u.com: fetch -> Blob -> URL.createObjectURL ->
video.src = "blob:..."), then drives the page over CDP:

  1. the content script UI must mount and detect the blob + direct link + m3u8
  2. clicking the blob item's Download button must produce a file whose bytes
     are identical to the source sample.mp4 (proves the blob pipeline works)

Usage: python scripts/test_extension.py [--keep]
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websocket  # websocket-client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "extension")
TESTS = os.path.join(EXT, "tests")
TMP_DL = os.path.join(ROOT, "tmp_dl")
TMP_PROFILE = os.path.join(ROOT, "tmp_chrome")
PORT = 8799
DEBUG_PORT = 9333
CHROME_CANDIDATES = [
    os.path.join(ROOT, "tools", "chrome-win64", "chrome.exe"),      # Chrome for Testing (allows --load-extension)
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))


# ------------------------------------------------------------------ server
def serve():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0].lstrip("/") or "blobtest.html"
            full = os.path.join(TESTS, path)
            if not os.path.exists(full):
                self.send_error(404)
                return
            ctype = "video/mp4" if path.endswith((".mp4", ".m4s")) else (
                "application/vnd.apple.mpegurl" if path.endswith(".m3u8") else
                "application/dash+xml" if path.endswith(".mpd") else
                "video/mp2t" if path.endswith(".ts") else "text/html")
            body = open(full, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ------------------------------------------------------------------ CDP
class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.mid = 0
        self.sessions = {}

    def send(self, method, params=None, session=None):
        self.mid += 1
        msg = {"id": self.mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        deadline = time.time() + 30
        while time.time() < deadline:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == self.mid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})
            # ignore events
        raise TimeoutError(method)

    def wait_event(self, method, timeout=30):
        deadline = time.time() + timeout
        self.ws.settimeout(max(1, int(deadline - time.time())))
        try:
            while time.time() < deadline:
                data = json.loads(self.ws.recv())
                if data.get("method") == method:
                    return data
        except websocket.WebSocketTimeoutException:
            pass
        return None


def http_json(path):
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}{path}", timeout=10) as r:
        return json.loads(r.read())


def js(cdp, session, expr, timeout=15000):
    r = cdp.send("Runtime.evaluate", {
        "expression": expr,
        "returnByValue": True,
        "awaitPromise": True,
        "timeout": timeout,
    }, session=session)
    return r.get("result", {}).get("value")


# ------------------------------------------------------------------ main
def main():
    global TMP_PROFILE
    # Thai check names/details are normal output - a cp1252 Windows console
    # would raise UnicodeEncodeError inside check()'s print (same fix as
    # release.py / analyze_events)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    keep = "--keep" in sys.argv
    sample = os.path.join(TESTS, "sample.mp4")
    sample_hash = hashlib.sha256(open(sample, "rb").read()).hexdigest()
    fmp4_expected = open(os.path.join(TESTS, "fmp4_expected.sha")).read().strip()

    srv = serve()
    shutil.rmtree(TMP_DL, ignore_errors=True)
    os.makedirs(TMP_DL, exist_ok=True)
    # unique profile per run: never collide with other instances or the user
    TMP_PROFILE = os.path.join(ROOT, f"tmp_chrome-{os.getpid()}")
    if not keep:
        pass  # cleaned up in finally

    chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
    if not chrome:
        print("NO CHROME/EDGE FOUND")
        return 2

    proc = subprocess.Popen([
        chrome,
        f"--user-data-dir={TMP_PROFILE}",
        "--headless=new",
        "--no-first-run", "--no-default-browser-check",
        "--disable-features=DisableLoadExtensionCommandLineSwitch",
        f"--load-extension={EXT}",
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-allow-origins=*",
        "--window-size=1280,900",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # wait for the debug endpoint
        for _ in range(40):
            try:
                http_json("/json/version")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("chrome debug port never came up")

        ver = http_json("/json/version")
        browser_ws = ver["webSocketDebuggerUrl"]
        cdp = CDP(browser_ws)
        check("chrome+CDP connected", True, ver.get("Browser", "?")[:40])

        # downloads go to our test dir
        cdp.send("Browser.setDownloadBehavior",
                 {"behavior": "allow", "downloadPath": TMP_DL, "eventsEnabled": False})

        target = cdp.send("Target.createTarget", {"url": "about:blank"})
        tid = target["targetId"]
        attached = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = attached["sessionId"]
        cdp.send("Page.enable", session=sid)
        cdp.send("Runtime.enable", session=sid)

        cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{PORT}/blobtest.html"}, session=sid)
        time.sleep(3)

        # the extension can lag the first page load on a cold profile - poll and
        # reload until the content script shows up (up to ~40 s)
        mounted = None
        for attempt in range(4):
            for _ in range(16):
                mounted = js(cdp, sid, "!!document.querySelector('#vg-content-host')")
                if mounted:
                    break
                time.sleep(1)
            if mounted:
                break
            print(f"   (extension not mounted on attempt {attempt + 1} - reloading)")
            js(cdp, sid, "location.reload()")
            time.sleep(3)
        check("content script UI mounted", bool(mounted))

        video_src = js(cdp, sid, "document.querySelector('#v').src") or ""
        check("test page created blob: video src", video_src.startswith("blob:"),
              video_src[:60])
        expected_blob_prefix = f"blob:http://127.0.0.1:{PORT}/"
        check("blob URL origin matches page origin", video_src.startswith(expected_blob_prefix))

        kinds_present = []
        for _ in range(24):
            kinds_present = js(cdp, sid,
                               "[...document.querySelector('#vg-content-host').shadowRoot.querySelectorAll('.dl')]"
                               ".map(b => b.getAttribute('data-k'))") or []
            if "blob" in kinds_present and "m3u8" in kinds_present:
                break
            time.sleep(0.5)
        check("extension detected media (blob + direct + m3u8 across frames)",
              "blob" in kinds_present and "m3u8" in kinds_present, str(kinds_present))

        kinds = js(cdp, sid,
                   "[...document.querySelector('#vg-content-host').shadowRoot.querySelectorAll('.dl')]"
                   ".map(b => b.getAttribute('data-k'))") or []
        check("blob item present in panel", "blob" in kinds, str(kinds))
        check("m3u8 item detected via hooks", "m3u8" in kinds, str(kinds))
        check("iframe embed src offered as candidate", "embed" in kinds, str(kinds))
        open_btn = js(cdp, sid,
                      "(() => { const b = document.querySelector('#vg-content-host').shadowRoot"
                      ".querySelector('.dl[data-k=\"embed\"]'); return b ? !!b.closest('.item').querySelector('[data-open]') : false; })()")
        check("embed item has 'open page' button", bool(open_btn))

        # ==== embed item downloads the VIDEO, not the player HTML (v1.1.6) ===
        # The iframe src (/embed.html) is only a player page. The SW must
        # resolve it to the media that page plays (embed-video.mp4) before
        # downloading - saving the page itself yielded a useless .html file.
        before_embed_dl = set(os.listdir(TMP_DL))
        dl_click = js(cdp, sid,
                      "(() => { const b = document.querySelector('#vg-content-host').shadowRoot"
                      ".querySelector('.dl[data-k=\"embed\"]'); if (!b) return false; b.click(); return true; })()")
        check("embed download click dispatched", bool(dl_click))
        embed_file = wait_for_new_file(TMP_DL, before_embed_dl, 60)
        embed_ok = False
        if embed_file:
            head = open(embed_file, "rb").read(12)
            embed_ok = head[4:8] == b"ftyp"
        check("embed item download produced a video (mp4), not the player HTML", embed_ok,
              (os.path.basename(embed_file) + " header=" + repr(head[:8])) if embed_file else "no file within 60s")

        # ---- download the blob item -------------------------------------
        blob_url = js(cdp, sid, "document.querySelector('#v').src") or ""
        clicked = click_when_ready(cdp, sid, "blob", 15, url_part=blob_url)
        check("blob item button present", clicked)
        blob_file = wait_for_hash(TMP_DL, sample_hash, 40)
        check("blob download produced the video (byte-identical)", bool(blob_file), blob_file or "timeout")

        # ---- direct http download --------------------------------------
        click_when_ready(cdp, sid, "mp4", 15, url_part="sample.mp4")
        direct_file = wait_for_hash(TMP_DL, sample_hash, 30)
        check("direct http download produced the video (byte-identical)", bool(direct_file), direct_file or "timeout")

        # ================= fallback phase (offscreen assembler) ===========
        # Simulate what happens on picky sites: the direct blob:/http routes
        # are unavailable, so bytes must be read inside the page and streamed
        # through the offscreen assembler. Force it via the debug bridge.
        flag = js(cdp, sid, f"({ASK})('debugSetFlag', true)")
        check("forceFallback flag set (debug bridge)", bool(flag and flag.get("ok")), json.dumps(flag or {}))

        # blob item through the offscreen assembler
        blob_url2 = js(cdp, sid, "document.querySelector('#v').src") or ""
        click_when_ready(cdp, sid, "blob", 15, url_part=blob_url2)
        fb_file = wait_for_hash(TMP_DL, sample_hash, 60)
        check("fallback (page blob read) produced the video (byte-identical)", bool(fb_file), fb_file or "timeout")
        if not fb_file:
            logs = js(cdp, sid, f"({ASK})('debugLogs')")
            print("--- SW LOGS (fallback failure) ---")
            for line in (logs or {}).get("logs", [])[-14:]:
                print("   ", line)
            print("--- page-side network probe ---")
            print("fetch:", js(cdp, sid,
                "fetch('/sample.mp4').then(r => ({t: r.type, s: r.status, ok: r.ok})).catch(e => 'FETCHFAIL ' + e)"))
            print("vgFetchOrig === fetch:", js(cdp, sid, "String(window.__vgFetchOrig === window.fetch)"))
            print("hooked fetch:", js(cdp, sid,
                "window.fetch('/sample.mp4').then(r => ({t: r.type, s: r.status})).catch(e => 'HOOKFAIL ' + e)"))
            print("readBlob direct probe:", js(cdp, sid,
                "fetch(document.querySelector('#v').src).then(r => ({t: r.type, s: r.status})).catch(e => 'BLOBFAIL ' + e)"))
        if fb_file:
            got = hashlib.sha256(open(fb_file, "rb").read()).hexdigest()
            check("fallback blob bytes == source video bytes", got == sample_hash,
                  f"{got[:16]} vs {sample_hash[:16]}")

        # direct http item through the page-fetch fallback
        click_when_ready(cdp, sid, "mp4", 15, url_part="sample.mp4")
        fb2 = wait_for_hash(TMP_DL, sample_hash, 60)
        check("fallback (page fetch) produced the video (byte-identical)", bool(fb2), fb2 or "timeout")
        if not fb2:
            logs = js(cdp, sid, f"({ASK})('debugLogs')")
            print("--- SW LOGS (page-fetch failure) ---")
            for line in (logs or {}).get("logs", [])[-8:]:
                print("   ", line)
        if fb2:
            got = hashlib.sha256(open(fb2, "rb").read()).hexdigest()
            check("fallback http bytes == source video bytes", got == sample_hash)

        js(cdp, sid, f"({ASK})('debugSetFlag', false)")

        # ================= MSE capture phase (player2u-style) ==============
        # The video source is a MediaSource-backed blob: URL - the direct blob
        # download cannot work, so the extension must capture appendBuffer()
        # chunks and reassemble init+segment through the offscreen assembler.
        cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{PORT}/msetest.html"}, session=sid)
        time.sleep(4)
        mse_url = js(cdp, sid, "document.querySelector('#v').src") or ""
        clicked = click_when_ready(cdp, sid, "blob", 25, url_part=mse_url)
        check("MSE page: blob item offered for download", clicked)
        fb3 = wait_for_hash(TMP_DL, fmp4_expected, 90)
        check("MSE capture produced the reassembled stream (byte-identical)", bool(fb3), fb3 or "timeout")
        if not fb3:
            logs = js(cdp, sid, f"({ASK})('debugLogs')")
            for line in (logs or {}).get("logs", [])[-12:]:
                print("   ", line)

        # ================= site exclusions (v1.1.3) ========================
        # Drive the SAME messages the popup sends (vg:exclusion:add / remove)
        # through the debug bridge, then reload and assert the panel mounts.
        cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{PORT}/blobtest.html"}, session=sid)
        time.sleep(3)
        check("exclusion phase: panel mounts without rules",
              bool(js(cdp, sid, "!!document.querySelector('#vg-content-host')")))

        add_r = js(cdp, sid, f"({ASK})('debugAddExclusion', 'http://127.0.0.1:8799/*')")
        check("exclusion added (vg:exclusion:add path)", bool(add_r and add_r.get("ok")), json.dumps(add_r or {}))
        if add_r and not add_r.get("ok"):
            swlogs = js(cdp, sid, f"({ASK})('debugLogs')")
            for line in (swlogs or {}).get("logs", [])[-10:]:
                print("   [sw]", line)

        js(cdp, sid, "location.reload()")
        ex_mounted = None
        for _ in range(12):
            time.sleep(1)
            ex_mounted = js(cdp, sid, "!!document.querySelector('#vg-content-host')")
        check("excluded page: panel does NOT mount", ex_mounted is False, f"mounted={ex_mounted}")

        rm_r = js(cdp, sid, f"({ASK})('debugRemoveExclusion', 'http://127.0.0.1:8799/*')")
        check("exclusion removed", bool(rm_r and rm_r.get("ok")), json.dumps(rm_r or {}))

        js(cdp, sid, "location.reload()")
        remounted = False
        for _ in range(16):
            time.sleep(1)
            remounted = js(cdp, sid, "!!document.querySelector('#vg-content-host')")
            if remounted:
                break
        check("exclusion removed: panel mounts again", bool(remounted))

        # ============ in-panel "exclude this site" button (v1.1.4) ==========
        # The floating panel offers a two-step exclude button: click 1 arms
        # it (\"ยืนยัน?\"), click 2 confirms and adds the CURRENT host as a
        # bare-domain pattern, then the panel must vanish immediately.
        exbtn_ok = None
        for _ in range(10):
            exbtn_ok = js(cdp, sid,
                          "(() => { const r = document.querySelector('#vg-content-host') && document.querySelector('#vg-content-host').shadowRoot; return !!(r && r.getElementById('vg-exbtn')); })()")
            if exbtn_ok:
                break
            time.sleep(0.5)
        check("in-panel exclude button present", bool(exbtn_ok))

        js(cdp, sid, "document.querySelector('#vg-content-host').shadowRoot.getElementById('vg-pill').click()")
        time.sleep(0.3)
        js(cdp, sid, "document.querySelector('#vg-content-host').shadowRoot.getElementById('vg-exbtn').click()")
        time.sleep(0.3)
        armed = js(cdp, sid,
                   "(document.querySelector('#vg-content-host').shadowRoot.getElementById('vg-exbtn') || {}).textContent || ''")
        check("exclude button arms on first click (two-step confirm)", "ยืนยัน" in str(armed), str(armed)[:48])

        js(cdp, sid, "document.querySelector('#vg-content-host').shadowRoot.getElementById('vg-exbtn').click()")
        gone = None
        for _ in range(10):
            time.sleep(0.5)
            gone = js(cdp, sid, "!document.querySelector('#vg-content-host')")
            if gone:
                break
        check("in-panel exclude: panel removed immediately", bool(gone))

        # assert the stored pattern via the exclusion bridge: adding the same
        # pattern again is a no-op, so the response doubles as a list read
        stored = js(cdp, sid, f"({ASK})('debugAddExclusion', '127.0.0.1')")
        check("in-panel exclude: host pattern '127.0.0.1' stored (hostname, no port)",
              bool(stored and stored.get("ok")) and "127.0.0.1" in (stored or {}).get("patterns", [])
              and "127.0.0.1:8799" not in (stored or {}).get("patterns", []),
              json.dumps(stored or {}))

        js(cdp, sid, "location.reload()")
        still_gone = None
        for _ in range(8):
            time.sleep(1)
            still_gone = js(cdp, sid, "!document.querySelector('#vg-content-host')")
        check("in-panel exclusion persists after reload", still_gone is True, f"mounted={not still_gone}")

        # clean up both forms (pre-1.1.4 patterns may carry a port)
        rm2 = js(cdp, sid, f"({ASK})('debugRemoveExclusion', '127.0.0.1')")
        rm3 = js(cdp, sid, f"({ASK})('debugRemoveExclusion', '127.0.0.1:8799')")
        check("in-panel exclusion removed via bridge",
              bool(rm2 and rm2.get("ok")) and not (rm2 or {}).get("patterns"), json.dumps(rm2 or {}))
        remounted2 = False
        for _ in range(16):
            time.sleep(1)
            remounted2 = js(cdp, sid, "!!document.querySelector('#vg-content-host')")
            if remounted2:
                break
        check("in-panel exclusion removed: panel returns", bool(remounted2))

        # ========= exclusion import/export bridge (v1.2.1) ==================
        # Same transfer file as the desktop app: export through the SW,
        # wipe the list, import the document back (idempotent re-add), and
        # make sure malformed input is rejected without polluting the list.
        exs = js(cdp, sid, f"({ASK})('debugExportExclusions', '')")
        check("bridge export returns a transfer document",
              bool(exs and exs.get("ok")) and (exs or {}).get("count", -1) == 0
              and '"patterns": []' in str((exs or {}).get("json", "")),
              json.dumps(exs or {})[:120])

        seed = js(cdp, sid, f"({ASK})('debugAddExclusion', '127.0.0.1')")
        check("bridge: seed one pattern for the round-trip",
              bool(seed and seed.get("ok")) and (seed or {}).get("patterns") == ["127.0.0.1"],
              json.dumps(seed or {}))

        doc = js(cdp, sid, f"({ASK})('debugExportExclusions', '')")
        doc_str = json.dumps((doc or {}).get("json", ""))
        check("bridge export carries the seeded pattern",
              bool(doc and doc.get("ok")) and (doc or {}).get("count") == 1
              and "127.0.0.1" in doc_str, doc_str[:120])

        wiped = js(cdp, sid, f"({ASK})('debugRemoveExclusion', '127.0.0.1')")
        check("bridge: list wiped before import", bool(wiped and wiped.get("ok"))
              and not (wiped or {}).get("patterns"), json.dumps(wiped or {}))

        imported = js(cdp, sid, f"({ASK})('debugImportExclusions', {json.dumps(doc.get('json'))})")
        check("bridge import restores the exported list",
              bool(imported and imported.get("ok")) and (imported or {}).get("added") == 1
              and (imported or {}).get("patterns") == ["127.0.0.1"],
              json.dumps(imported or {}))

        again = js(cdp, sid, f"({ASK})('debugImportExclusions', {json.dumps(doc.get('json'))})")
        check("bridge re-import is idempotent (no duplicates)",
              bool(again and again.get("ok")) and (again or {}).get("added") == 0
              and (again or {}).get("patterns") == ["127.0.0.1"],
              json.dumps(again or {}))

        junk = js(cdp, sid, f"({ASK})('debugImportExclusions', {json.dumps('{\"patterns\":[\"bad pattern x\"]}')})")
        check("bridge import drops whitespace typos",
              bool(junk and junk.get("ok")) and (junk or {}).get("added") == 0
              and (junk or {}).get("patterns") == ["127.0.0.1"],
              json.dumps(junk or {}))

        notjson = js(cdp, sid, f"({ASK})('debugImportExclusions', 'not json at all')")
        check("bridge import rejects non-JSON",
              bool(notjson is not None) and not (notjson or {}).get("ok"),
              json.dumps(notjson or {}))

        cleaned = js(cdp, sid, f"({ASK})('debugRemoveExclusion', '127.0.0.1')")
        check("bridge round-trip cleaned up",
              bool(cleaned and cleaned.get("ok")) and not (cleaned or {}).get("patterns"),
              json.dumps(cleaned or {}))

        # ==== SW-side player-page refusal (v1.1.6) ==========================
        # Asking the SW to download a plain HTML player page must NEVER start
        # a download of that page (the old .html-file bug). Run this on a
        # media-free page: the tab's store is empty and the live content
        # script finds nothing -> the SW must refuse deterministically.
        cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{PORT}/nomedia.html"}, session=sid)
        refuse_mounted = False
        for _ in range(20):
            time.sleep(1)
            refuse_mounted = bool(js(cdp, sid, "!!document.querySelector('#vg-content-host')"))
            if refuse_mounted:
                break
        check("nomedia.html: content script mounted", refuse_mounted)
        time.sleep(1)
        before_refuse = set(os.listdir(TMP_DL))
        refuse = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/nomedia.html', 'kind': 'embed', 'name': 'e2e-refuse'}))})")
        check("SW refuses a player page with no in-page media match",
              bool(refuse is not None) and not (refuse or {}).get("ok") and (refuse or {}).get("page") is True,
              json.dumps(refuse or {}))
        time.sleep(2)
        check("refused download wrote no file", set(os.listdir(TMP_DL)) - before_refuse == set(),
              str(set(os.listdir(TMP_DL)) - before_refuse))

        # ...and the happy path: a player page whose <video> plays a real
        # mp4. Navigate to it, let detection register the media, then ask
        # the SW to download the PAGE candidate - it must resolve to the mp4.
        cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{PORT}/player.html"}, session=sid)
        page_mounted = False
        for _ in range(20):
            time.sleep(1)
            page_mounted = bool(js(cdp, sid, "!!document.querySelector('#vg-content-host')"))
            if page_mounted:
                break
        check("player.html: content script mounted", page_mounted)
        time.sleep(2)  # let the DOM scan register the media item
        before_res = set(os.listdir(TMP_DL))
        happy = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/player.html', 'kind': 'page', 'name': 'e2e-resolve'}))})")
        check("SW resolves a page candidate to the playing video",
              bool(happy and happy.get("ok")) and bool((happy or {}).get("resolved")),
              json.dumps(happy or {}))
        res_file = wait_for_new_file(TMP_DL, before_res, 60)
        res_ok = False
        head2 = b""
        if res_file:
            head2 = open(res_file, "rb").read(12)
            res_ok = head2[4:8] == b"ftyp"
        check("resolved page download produced a video (mp4)", res_ok,
              (os.path.basename(res_file) + " header=" + repr(head2[:8])) if res_file else "no file within 60s")

        # ==== strict iframe resolve - the "wrong file" bug (v1.1.8) ==========
        # A page plays an unrelated clip (the decoy, like an ad) AND embeds
        # an iframe player. The old resolver's final catch-all ("any pickable
        # media in the tab") downloaded the DECOY when the embed item was
        # clicked. The strict resolver accepts only media the player page
        # itself loaded (sniffer page attribution) - here clip2.mp4.
        cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{PORT}/wrongfile.html"}, session=sid)
        wf_mounted = False
        for _ in range(20):
            time.sleep(1)
            wf_mounted = bool(js(cdp, sid, "!!document.querySelector('#vg-content-host')"))
            if wf_mounted:
                break
        check("wrongfile.html: content script mounted", wf_mounted)
        time.sleep(4)  # both videos + the webRequest sniffer must register
        before_wf = set(os.listdir(TMP_DL))
        wf = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/frameplayer.html', 'kind': 'embed', 'name': 'e2e-wrongfile'}))})")
        check("embed item on a decoy page resolves to the PLAYER's media",
              bool(wf and wf.get("ok")), json.dumps(wf or {}))
        wf_file = wait_for_new_file(TMP_DL, before_wf, 60)
        wf_hash = ""
        if wf_file:
            wf_hash = hashlib.sha256(open(wf_file, "rb").read()).hexdigest()
        decoy_hash = hashlib.sha256(open(os.path.join(TESTS, "sample.mp4"), "rb").read()).hexdigest()
        clip2_hash = hashlib.sha256(open(os.path.join(TESTS, "video", "clip2.mp4"), "rb").read()).hexdigest()
        clip3_hash = hashlib.sha256(open(os.path.join(TESTS, "video", "clip3.mp4"), "rb").read()).hexdigest()
        check("wrong-file regression: got the player's clip2, never the decoy",
              bool(wf_file) and open(wf_file, "rb").read(12)[4:8] == b"ftyp"
              and wf_hash == clip2_hash and wf_hash != decoy_hash,
              (os.path.basename(wf_file or "")) + " " + wf_hash[:12])

        # ==== SW html-scan fallback - obfuscated player (v1.1.8) =============
        # Mirrors the desktop app's _page_fallback: player-obf.html has NO
        # video element and NO plain media URL - the stream lives inside
        # atob("..."). It was never opened, so the tab store has nothing for
        # it and the live content script must stay silent (notHere) -> the
        # SW fetches the page HTML itself and decodes the URL.
        obf = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/player-obf.html', 'kind': 'embed', 'name': 'e2e-obf'}))})")
        check("SW html-scan decodes an atob-obfuscated player URL",
              bool(obf and obf.get("ok")), json.dumps(obf or {}))
        before_o = set(os.listdir(TMP_DL)) if not obf or not obf.get("ok") else set()
        # the direct download starts inside the SW call above - look for the
        # clip3 file either way (hash check proves WHICH url was decoded)
        obf_deadline = time.time() + 60
        obf_file = None
        while time.time() < obf_deadline:
            for f in os.listdir(TMP_DL):
                if f.endswith((".crdownload", ".tmp")):
                    continue
                p = os.path.join(TMP_DL, f)
                try:
                    if os.path.getsize(p) > 0 and hashlib.sha256(open(p, "rb").read()).hexdigest() == clip3_hash:
                        obf_file = p
                        break
                except OSError:
                    pass
            if obf_file:
                break
            time.sleep(0.5)
        check("html-scan downloaded exactly the decoded stream (clip3)", bool(obf_file),
              os.path.basename(obf_file or ""))
        if before_o:
            time.sleep(1)
            check("refused-when-no-html-match left no junk file",
                  set(os.listdir(TMP_DL)) - before_o == set(),
                  str(set(os.listdir(TMP_DL)) - before_o))

        # ==== JS-created <video> still resolves via detection (v1.1.8) =======
        # player-hidden.html creates its <video> from an inline script; the
        # DOM scanner must pick it up and the page candidate must resolve to
        # that clip (detection path stays intact next to the new scan).
        cdp.send("Page.navigate", {"url": f"http://127.0.0.1:{PORT}/player-hidden.html"}, session=sid)
        hidden_mounted = False
        for _ in range(20):
            time.sleep(1)
            hidden_mounted = bool(js(cdp, sid, "!!document.querySelector('#vg-content-host')"))
            if hidden_mounted:
                break
        check("player-hidden.html: content script mounted", hidden_mounted)
        time.sleep(3)  # the script-created <video> must be detected
        before_h = set(os.listdir(TMP_DL))
        hid = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/player-hidden.html', 'kind': 'page', 'name': 'e2e-hidden'}))})")
        check("page candidate resolves to a script-created <video> source",
              bool(hid and hid.get("ok")) and bool((hid or {}).get("resolved")),
              json.dumps(hid or {}))
        hid_file = wait_for_hash(TMP_DL, clip2_hash, 60)
        check("script-created video download produced clip2 (byte-identical)", bool(hid_file), hid_file or "timeout")

        # ==== HLS assembly inside the extension (v1.1.9) =====================
        # Real-world case (merrylion player.html): the stream is an m3u8
        # playlist - Chrome cannot save it and the extension used to bounce
        # the user to the desktop app. Now the SW fetches manifest + segments
        # itself and assembles one .ts through the offscreen assembler.
        hls_expected = hashlib.sha256(
            open(os.path.join(TESTS, "hls", "seg1.ts"), "rb").read() +
            open(os.path.join(TESTS, "hls", "seg2.ts"), "rb").read()).hexdigest()
        hlsr = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/hls/stream.m3u8', 'kind': 'm3u8', 'name': 'e2e-hls'}))})")
        check("direct m3u8 item assembles an HLS stream in the extension",
              bool(hlsr and hlsr.get("ok") and hlsr.get("hls")), json.dumps(hlsr or {}))
        hls_file = wait_for_hash(TMP_DL, hls_expected, 60)
        check("assembled HLS file is seg1+seg2 concatenated (byte-identical)", bool(hls_file), hls_file or "timeout")

        # the player page path: embed candidate -> html-scan finds the m3u8 ->
        # resolved kind=m3u8 -> same assembly (the full merrylion chain)
        before_hls2 = set(os.listdir(TMP_DL))
        hlsr2 = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/hlsplayer.html', 'kind': 'embed', 'name': 'e2e-hls2'}))})")
        check("embed candidate resolves to m3u8 and assembles it too",
              bool(hlsr2 and hlsr2.get("ok") and hlsr2.get("hls")), json.dumps(hlsr2 or {}))
        hls2_file = wait_for_new_file(TMP_DL, before_hls2, 60)
        hls2_ok = False
        if hls2_file:
            hls2_ok = hashlib.sha256(open(hls2_file, "rb").read()).hexdigest() == hls_expected
        check("player-page HLS download matches the stream bytes", hls2_ok,
              os.path.basename(hls2_file or ""))

        # ==== DASH assembly inside the extension (v1.1.10) ===================
        # Same story as HLS: an mpd manifest used to be refused with
        # "HLS/DASH stream - use the VDOGrabber desktop app (yt-dlp)". The SW
        # now parses the MPD, picks the highest-bandwidth Representation and
        # concatenates init + media segments into one .mp4 through the
        # offscreen assembler.
        dash_expected = hashlib.sha256(
            open(os.path.join(TESTS, "dash", "init.mp4"), "rb").read() +
            open(os.path.join(TESTS, "dash", "seg1.m4s"), "rb").read() +
            open(os.path.join(TESTS, "dash", "seg2.m4s"), "rb").read()).hexdigest()
        dashr = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/dash/stream.mpd', 'kind': 'mpd', 'name': 'e2e-dash'}))})")
        check("direct mpd item assembles a DASH stream in the extension",
              bool(dashr and dashr.get("ok") and dashr.get("dash")), json.dumps(dashr or {}))
        dash_file = wait_for_hash(TMP_DL, dash_expected, 60)
        check("assembled DASH file is init+seg1+seg2 concatenated (byte-identical)", bool(dash_file), dash_file or "timeout")

        # the player page path: embed candidate -> html-scan finds the mpd ->
        # resolved kind=mpd -> same assembly (mirrors the HLS embed chain)
        before_d = set(os.listdir(TMP_DL))
        dashr2 = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/dashplayer.html', 'kind': 'embed', 'name': 'e2e-dash2'}))})")
        check("embed candidate resolves to mpd and assembles it too",
              bool(dashr2 and dashr2.get("ok") and dashr2.get("dash")), json.dumps(dashr2 or {}))
        dash2_file = wait_for_new_file(TMP_DL, before_d, 60)
        dash2_ok = False
        if dash2_file:
            dash2_ok = hashlib.sha256(open(dash2_file, "rb").read()).hexdigest() == dash_expected
        check("player-page DASH download matches the stream bytes", dash2_ok,
              os.path.basename(dash2_file or ""))

        # ==== DASH SegmentTimeline + SegmentBase single-file (v1.1.10) ========
        # Real-world case (merrylion2.com player.html): SegmentTemplate without
        # @duration - the segment list lives in <SegmentTimeline><S t d r/>
        # (used to be refused as "unsupported DASH layout (SegmentBase/indexed)").
        # Plus the on-demand profile: SegmentBase/indexRange = ONE self-contained
        # fMP4 file, downloaded whole via the chosen Representation's <BaseURL>.
        tl_expected = hashlib.sha256(
            open(os.path.join(TESTS, "dash", "init.mp4"), "rb").read() +
            open(os.path.join(TESTS, "dash", "seg00001.m4s"), "rb").read() +
            open(os.path.join(TESTS, "dash", "seg00002.m4s"), "rb").read() +
            open(os.path.join(TESTS, "dash", "seg00003.m4s"), "rb").read()).hexdigest()
        tlr = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/dash/timeline.mpd', 'kind': 'mpd', 'name': 'e2e-dash-tl'}))})")
        check("direct mpd with SegmentTimeline enumerates <S t d r> entries",
              bool(tlr and tlr.get("ok") and tlr.get("dash")), json.dumps(tlr or {}))
        tl_file = wait_for_hash(TMP_DL, tl_expected, 60)
        check("SegmentTimeline download is init+seg00001..3 (byte-identical)", bool(tl_file), tl_file or "timeout")

        sb_expected = hashlib.sha256(open(os.path.join(TESTS, "dash", "single.mp4"), "rb").read()).hexdigest()
        sbr = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/dash/segmentbase.mpd', 'kind': 'mpd', 'name': 'e2e-dash-sb'}))})")
        check("direct mpd with SegmentBase/index downloads the single fMP4",
              bool(sbr and sbr.get("ok") and sbr.get("dash")), json.dumps(sbr or {}))
        sb_file = wait_for_hash(TMP_DL, sb_expected, 60)
        check("SegmentBase download is exactly the chosen <BaseURL> file (byte-identical)", bool(sb_file), sb_file or "timeout")

        # ==== master playlist quality + separate audio (v1.9.0 size fix) ======
        # The old variant loop compared raw BANDWIDTH only, so a master
        # WITHOUT BANDWIDTH attributes scored every variant 0 and silently
        # saved the FIRST (usually lowest) variant - a much smaller file than
        # the desktop app. RESOLUTION must break the tie, and a separate
        # audio track (EXT-X-MEDIA rendition / separate DASH AdaptationSet)
        # must be REFUSED: the browser cannot remux, so the old code saved a
        # smaller, SILENT file and claimed success.
        # chrome may still be finalizing UUID leftovers from earlier phases -
        # wait for the folder to go quiet, THEN snapshot the baseline, so a
        # late landing file cannot be mistaken for junk written by a refusal
        wait_downloads_quiet(TMP_DL)
        before_master = set(os.listdir(TMP_DL))
        masterr = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/hls/master.m3u8', 'kind': 'm3u8', 'name': 'e2e-hls-master'}))})")
        check("master playlist picks the highest-BANDWIDTH variant",
              bool(masterr and masterr.get("ok") and masterr.get("hls")), json.dumps(masterr or {}))
        master_file = wait_for_new_file(TMP_DL, before_master, 60)
        master_ok = bool(master_file) and hashlib.sha256(open(master_file, "rb").read()).hexdigest() == hls_expected
        check("master playlist download == HIGH variant bytes (seg1+seg2)", master_ok,
              os.path.basename(master_file or ""))

        wait_downloads_quiet(TMP_DL)
        before_nobw = set(os.listdir(TMP_DL))
        nobwr = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/hls/master-nobw.m3u8', 'kind': 'm3u8', 'name': 'e2e-hls-nobw'}))})")
        check("master WITHOUT BANDWIDTH still succeeds",
              bool(nobwr and nobwr.get("ok") and nobwr.get("hls")), json.dumps(nobwr or {}))
        nobw_file = wait_for_new_file(TMP_DL, before_nobw, 60)
        nobw_ok = bool(nobw_file) and hashlib.sha256(open(nobw_file, "rb").read()).hexdigest() == hls_expected
        check("no-BANDWIDTH master download == HIGH variant bytes (old code saved the low one)",
              nobw_ok, os.path.basename(nobw_file or ""))

        wait_downloads_quiet(TMP_DL)
        before_aud = set(os.listdir(TMP_DL))
        audr = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/hls/master-audio.m3u8', 'kind': 'm3u8', 'name': 'e2e-hls-audio'}))})")
        check("HLS master with a separate audio rendition is refused with a clear reason",
              bool(audr is not None) and not (audr or {}).get("ok")
              and "audio" in str((audr or {}).get("error", "")).lower(), json.dumps(audr or {}))
        # let chrome finalize leftovers from EARLIER phases before snapshotting
        wait_downloads_quiet(TMP_DL)
        time.sleep(2)
        check("separate-audio HLS refusal wrote no file",
              set(os.listdir(TMP_DL)) - before_aud == set(),
              str(set(os.listdir(TMP_DL)) - before_aud))

        dashaud = js(cdp, sid, f"({ASK})('debugDownload', {json.dumps(json.dumps({'url': f'http://127.0.0.1:{PORT}/dash/audio.mpd', 'kind': 'mpd', 'name': 'e2e-dash-audio'}))})")
        check("DASH with a separate audio AdaptationSet is refused with a clear reason",
              bool(dashaud is not None) and not (dashaud or {}).get("ok")
              and "audio" in str((dashaud or {}).get("error", "")).lower(), json.dumps(dashaud or {}))

    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        srv.shutdown()
        shutil.rmtree(TMP_PROFILE, ignore_errors=True)

    print(f"\nRESULT: {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED:", ", ".join(failed))
    return 0 if not failed else 1


def wait_for_new_file(folder, before, timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        now = set(os.listdir(folder))
        new = [f for f in now - before if not f.endswith((".crdownload", ".tmp"))]
        if new:
            path = os.path.join(folder, new[0])
            try:
                if os.path.getsize(path) > 0:
                    return path
            except OSError:
                pass
        time.sleep(0.5)
    return None


def wait_downloads_quiet(folder, extra=2.0, timeout_s=30):
    """chrome may still be renaming/finalizing earlier .crdownload files when a
    snapshot of the folder is taken (v1.9.0 e2e flake: two UUID leftovers
    landed right at the refusal check and looked like "junk written by the
    refusal"). Wait until the folder stops changing for at least `extra`
    seconds, then let the caller snapshot it."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        now = frozenset((f, os.path.getsize(os.path.join(folder, f)))
                        for f in os.listdir(folder) if not f.endswith((".tmp",)))
        if now == last and not any(f.endswith(".crdownload") for f, _ in now):
            return time.time()
        last = now
        time.sleep(0.5)
    return 0.0


ASK = """(type, value) => new Promise(res => {
  const reqId = 'q' + Math.random().toString(36).slice(2);
  const on = (e) => { const d = e.data;
    if (d && d.__vg === 'main' && d.type === type && d.reqId === reqId) { window.removeEventListener('message', on); res(d.resp); } };
  window.addEventListener('message', on);
  window.postMessage({ __vg: 'ui', type, reqId, value }, '*');
  setTimeout(() => res({ timeout: true }), 8000);
})"""


def click_when_ready(cdp, sid, kind, timeout_s=15, url_part=None):
    """Poll until a panel download button exists for `kind` (optionally whose
    item URL contains url_part), then click it."""
    part = url_part or ""
    expr = """(() => {
      const root = document.querySelector('#vg-content-host').shadowRoot;
      const btns = [...root.querySelectorAll('.dl[data-k="%s"]')];
      const b = btns.find(b => decodeURIComponent(b.getAttribute('data-u')).includes('%s'));
      if (b) { b.click(); return true; }
      return false;
    })()""" % (kind, part)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if js(cdp, sid, expr):
            return True
        time.sleep(0.5)
    return False


def wait_for_hash(folder, expected_hash, timeout_s):
    """Wait until some file in `folder` matches the expected SHA256 (skips
    partial .crdownload files and unrelated downloads from other phases)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in os.listdir(folder):
            if f.endswith((".crdownload", ".tmp")):
                continue
            p = os.path.join(folder, f)
            try:
                if os.path.getsize(p) > 0 and hashlib.sha256(open(p, "rb").read()).hexdigest() == expected_hash:
                    return p
            except OSError:
                pass
        time.sleep(0.5)
    return None


def unpacked_extension_id(path):
    """Chrome derives the unpacked-extension id from the SHA256 of its path."""
    h = hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in h)


def wake_service_worker(cdp):
    """Open the extension popup page to spawn the SW, then attach to it so the
    test can flip chrome.storage flags."""
    eid = unpacked_extension_id(EXT)
    popup = cdp.send("Target.createTarget", {"url": f"chrome-extension://{eid}/popup.html"})
    time.sleep(2.5)
    sw_sid = None
    for t in http_json("/json/list"):
        if t.get("type") == "service_worker" and f"chrome-extension://{eid}/" in t.get("url", ""):
            try:
                att = cdp.send("Target.attachToTarget", {"targetId": t["id"], "flatten": True})
                sw_sid = att["sessionId"]
                cdp.send("Runtime.enable", session=sw_sid)
            finally:
                break
    try:
        cdp.send("Target.closeTarget", {"targetId": popup["targetId"]})
    except Exception:
        pass
    return sw_sid


if __name__ == "__main__":
    sys.exit(main())
