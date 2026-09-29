"""Regression test for the pywebview navigate-callback race.

The injected toolbar used to call pywebview.api.navigate() - a method that
navigates the very page the callback must be delivered to - which produced
'returnValuesCallbacks ... is not a function' TypeErrors. Now navigation is
deferred in Python and the toolbar navigates in-page.

This test drives a hidden window, issues both call styles and asserts:
  1. the page actually navigates
  2. stderr contains no returnValuesCallbacks errors
"""

import http.server
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import webview  # noqa: E402

from core.detector import TOOLBAR_JS  # noqa: E402
from main import App  # noqa: E402

PORT = 8797
DATA = os.path.join("tmp_navtest")


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body><h1 id=t>%s</h1></body></html>" % self.path.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main() -> int:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    shutil.rmtree(DATA, ignore_errors=True)

    app = App()
    app.settings.set("log_level", "DEBUG")

    # capture stderr (pywebview error threads print there)
    err_r, err_w = os.pipe()
    saved_err = os.dup(2)
    os.dup2(err_w, 2)
    err_chunks = []
    threading.Thread(target=lambda: [err_chunks.append(chunk) for chunk in iter(lambda: os.read(err_r, 65536), b"")], daemon=True).start()

    results = {}

    def run_probe():
        try:
            w = app.browser
            for _ in range(60):
                if w.evaluate_js("!!(window.pywebview && window.pywebview.api)"):
                    break
                time.sleep(0.25)

            # 1) the old failing call style: api.navigate
            w.evaluate_js("window.pywebview.api.navigate('http://127.0.0.1:%d/p1.html'); 'issued'" % PORT)
            time.sleep(1.5)
            results["url1"] = (w.get_current_url() or "")

            # 2) the new toolbar path: in-page navigation from the URL box
            w.evaluate_js(TOOLBAR_JS)
            time.sleep(0.5)
            w.evaluate_js("""
                (function () {
                    const host = document.querySelector('#vg-toolbar-host');
                    const box = host.shadowRoot.querySelector('input');
                    box.value = 'http://127.0.0.1:%d/p2.html';
                    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
                })();
            """ % PORT)
            time.sleep(1.5)
            results["url2"] = (w.get_current_url() or "")
        except Exception as e:
            results["probe_error"] = repr(e)
        finally:
            try:
                w.destroy()
            except Exception:
                pass

    app.browser = webview.create_window(
        "navtest", url=f"http://127.0.0.1:{PORT}/start.html", js_api=app.api,
        width=800, height=600, hidden=True, background_color="#0f1729",
    )
    webview.start(func=run_probe, gui="edgechromium")

    # restore stderr and inspect what pywebview's threads printed
    os.dup2(saved_err, 2)
    os.close(saved_err)
    time.sleep(0.5)  # let the reader thread drain
    text = b"".join(err_chunks).decode("utf-8", "replace")
    print("--- captured stderr (%d bytes) ---" % len(text))
    print(text[-1500:] if text.strip() else "(clean)")
    print("--- end stderr ---")

    srv.shutdown()
    shutil.rmtree(DATA, ignore_errors=True)

    ok = True
    if "/p1.html" not in results.get("url1", ""):
        print("FAIL api.navigate did not navigate:", results.get("url1"))
        ok = False
    else:
        print("PASS api.navigate navigated ->", results["url1"][-40:])
    if "/p2.html" not in results.get("url2", ""):
        print("FAIL toolbar in-page navigation did not navigate:", results.get("url2"))
        ok = False
    else:
        print("PASS toolbar Enter navigated ->", results["url2"][-40:])
    if "returnValuesCallbacks" in text:
        print("FAIL stderr still contains returnValuesCallbacks errors")
        for line in text.splitlines():
            if "returnValuesCallbacks" in line:
                print("   ", line[:160])
        ok = False
    else:
        print("PASS no returnValuesCallbacks errors on stderr")
    if results.get("probe_error"):
        print("PROBE ERROR:", results["probe_error"])
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
