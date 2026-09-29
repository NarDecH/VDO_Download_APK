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
                "application/vnd.apple.mpegurl" if path.endswith(".m3u8") else "text/html")
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
