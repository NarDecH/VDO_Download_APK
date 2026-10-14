"""E2E update driver: drive the frozen app's update flow via CDP.

Usage: python scripts/e2e_update_driver.py [chrome_debug_port]
Steps: locate a ready page -> pywebview ready -> check_updates ->
       download_update -> apply_update. Prints each result as JSON.
The app will kill its own windows after apply_update; the websocket dying
is expected, so we only report before that.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import websocket  # requirements: websocket-client (used by test_extension E2E)

PORT = 9222  # module-level so Cdp can resolve the Origin header


def pages(port: int):
    with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=10) as r:
        return json.load(r)


class Cdp:
    def __init__(self, ws_url: str):
        # Chrome/Edge >=111 reject websocket uplinks bearing an Origin header
        # that the debug server has not allow-listed; Chromium accepts an
        # uplink with NO Origin header at all (suppress_origin = no relaunch
        # with --remote-allow-origins needed).
        self.w = websocket.create_connection(ws_url, timeout=60,
                                             suppress_origin=True)
        self.n = 0

    def eval(self, expr: str, awaitp: bool = True):
        self.n += 1
        rid = self.n
        self.w.send(json.dumps({"id": rid, "method": "Runtime.evaluate",
                                "params": {"expression": expr,
                                           "awaitPromise": awaitp,
                                           "returnByValue": True,
                                           "timeout": 45000}}))
        while True:
            msg = json.loads(self.w.recv())
            if msg.get("id") == rid:
                res = msg.get("result", {}).get("result", {})
                if msg.get("result", {}).get("exceptionDetails"):
                    raise RuntimeError(json.dumps(msg["result"]["exceptionDetails"])[:400])
                return res.get("value")


def main() -> int:
    global PORT
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9222
    deadline = time.time() + 60

    port = PORT
    tgt = None
    while time.time() < deadline and tgt is None:
        for p in pages(port):
            if p.get("type") != "page":
                continue
            print("page:", p.get("url", "?")[:120], flush=True)
            try:
                c = Cdp(p["webSocketDebuggerUrl"])
                if c.eval("(function(){return typeof window.pywebview!=='undefined'"
                          " && window.pywebview && window.pywebview.api"
                          " && typeof window.pywebview.api.check_updates==='function';})()"):
                    tgt = c
                    print("driver attached:", p.get("url", "")[:120], flush=True)
                    break
            except Exception as e:
                print("probe fail:", p.get("url", "")[:80], repr(e), flush=True)
        if tgt is None:
            time.sleep(2)
    if tgt is None:
        print("DRIVER_FAIL: no ready page", flush=True)
        return 1

    r1 = tgt.eval("window.pywebview.api.check_updates()")
    print("CHECK:", json.dumps(r1), flush=True)
    if not r1 or not r1.get("update_available"):
        print("DRIVER_FAIL: no update available", flush=True)
        return 2

    r2 = tgt.eval("window.pywebview.api.download_update()")
    print("DOWNLOAD:", json.dumps(r2), flush=True)
    if not r2 or not r2.get("ok"):
        print("DRIVER_FAIL: download failed", flush=True)
        return 3

    import websocket as _ws
    try:
        r3 = tgt.eval("window.pywebview.api.apply_update()")
        ok = bool(r3) and r3.get("ok")
    except _ws.WebSocketConnectionClosedException:
        # apply_update destroys the app's windows -> the debug websocket dies
        # before the response arrives. Treat loss-after-send as an explicit
        # outcome so the driver can exit cleanly and the .bat keeps running.
        print("APPLY: connection lost after send (windows destroyed - expected)", flush=True)
        ok = True
    print("APPLY_OK:", ok, flush=True)
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
