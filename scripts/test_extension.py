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
            ctype = "video/mp4" if path.endswith(".mp4") else (
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
    keep = "--keep" in sys.argv
    sample = os.path.join(TESTS, "sample.mp4")
    sample_hash = hashlib.sha256(open(sample, "rb").read()).hexdigest()

    srv = serve()
    shutil.rmtree(TMP_DL, ignore_errors=True)
    os.makedirs(TMP_DL, exist_ok=True)
    if not keep:
        shutil.rmtree(TMP_PROFILE, ignore_errors=True)

    chrome = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
    if not chrome:
        print("NO CHROME/EDGE FOUND")
        return 2

    proc = subprocess.Popen([
        chrome,
        f"--user-data-dir={TMP_PROFILE}",
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
        time.sleep(5)  # blob creation + content script mount + first scans

        mounted = js(cdp, sid, "!!document.querySelector('#vg-content-host')")
        check("content script UI mounted", bool(mounted))

        video_src = js(cdp, sid, "document.querySelector('#v').src") or ""
        check("test page created blob: video src", video_src.startswith("blob:"),
              video_src[:60])
        expected_blob_prefix = f"blob:http://127.0.0.1:{PORT}/"
        check("blob URL origin matches page origin", video_src.startswith(expected_blob_prefix))

        count = int(js(cdp, sid,
                       "document.querySelector('#vg-content-host').shadowRoot.querySelector('#vg-count').textContent")
                    or 0)
        check("extension detected media (>=3: blob + direct + m3u8)", count >= 3, f"count={count}")

        kinds = js(cdp, sid,
                   "[...document.querySelector('#vg-content-host').shadowRoot.querySelectorAll('.dl')]"
                   ".map(b => b.getAttribute('data-k'))") or []
        check("blob item present in panel", "blob" in kinds, str(kinds))
        check("m3u8 item detected via hooks", "m3u8" in kinds, str(kinds))

        # ---- download the blob item -------------------------------------
        before = set(os.listdir(TMP_DL))
        js(cdp, sid,
           "document.querySelector('#vg-content-host').shadowRoot"
           ".querySelector('.dl[data-k=\"blob\"]').click()")
        blob_file = wait_for_new_file(TMP_DL, before, 40)
        check("blob download produced a file", bool(blob_file), blob_file or "timeout")
        if blob_file:
            got = hashlib.sha256(open(blob_file, "rb").read()).hexdigest()
            check("downloaded blob bytes == source video bytes", got == sample_hash,
                  f"{got[:16]} vs {sample_hash[:16]}")
            size = os.path.getsize(blob_file)
            check("blob file size sane (>= 900 KB)", size >= 900_000, f"{size} bytes")

        # ---- direct http download --------------------------------------
        before = set(os.listdir(TMP_DL))
        js(cdp, sid,
           "document.querySelector('#vg-content-host').shadowRoot"
           ".querySelector('.dl[data-k=\"mp4\"]').click()")
        direct_file = wait_for_new_file(TMP_DL, before, 30)
        check("direct http download produced a file", bool(direct_file), direct_file or "timeout")
        if direct_file:
            got = hashlib.sha256(open(direct_file, "rb").read()).hexdigest()
            check("direct download bytes == source video bytes", got == sample_hash)

    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        srv.shutdown()

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


if __name__ == "__main__":
    sys.exit(main())
