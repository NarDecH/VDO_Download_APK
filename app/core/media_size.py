"""Best-effort file-size lookup for detected media URLs.

Desktop only (Android keeps its own UI-side estimate): a background thread
issues one `Range: bytes=0-0` request per URL straight from Python - no
CORS applies to our own urllib, and a 1-byte range keeps the cost at a few
hundred bytes per probe.

Result sources, in order of trust:
  1. Content-Range of the 206 answer  ("bytes 0-0/1234567" -> full size)
  2. Content-Length of a 200 answer (only when the server ignored the range)
  3. byte_hint from the page (transferSize of an observed response) - already
     filled in by the detector JS when the browser itself downloaded the file

Failures (403, HTML answer, redirects to signed URLs...) just leave None -
the UI then shows "?" instead of a size. Never blocks the browser thread:
probe_media_size runs detached and pushes the result via a callback.
"""

from __future__ import annotations

import threading
import urllib.request

# Not every kind can be measured with a range probe; blobs live in the
# page, manifests grow while playing (size is meaningless), page links are
# small HTML responses whose Content-Length is the page, not the video.
_SKIP_KINDS = {"blob", "embed", "hls", "m3u8", "mpd", "dash"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def probe_size(url: str, kind: str = "media", timeout: float = 8.0,
               referer: str = "", byte_hint: int = 0) -> int:
    """Return the full file size in bytes, 0 when unknown."""
    kind = (kind or "").lower()
    if kind in _SKIP_KINDS or url.startswith("blob:"):
        return 0
    req = urllib.request.Request(
        url, method="HEAD",
        headers={"User-Agent": UA,
                 "Range": "bytes=0-0",
                 **({"Referer": referer} if referer else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            code = getattr(resp, "status", resp.getcode() or 0)
            cr = resp.headers.get("Content-Range") or ""
            cl = resp.headers.get("Content-Length") or ""
            ct = (resp.headers.get("Content-Type") or "").lower()
            # a range answer wins: "bytes 0-0/N" carries the TRUE total
            if code == 206 and "/" in cr:
                total = cr.rsplit("/", 1)[-1].strip()
                if total.isdigit():
                    return int(total)
            # 200 with a length: only trust it for media-ish content types -
            # a body of text/html would be the error page length, not the file
            if code == 200 and cl.isdigit() and not ct.startswith("text/html"):
                return int(cl)
    except Exception:
        pass
    return int(byte_hint or 0)


def probe_async(report: dict, on_result, limit_per_run: int = 4) -> None:
    """Probe `report['url']` in a daemon thread; call on_result(size, report).

    Used straight from report_media(): the JsApi callback thread must stay
    responsive, so the network round-trip never runs there.
    """
    url = report.get("url") or ""
    if not url.startswith(("http://", "https://")):
        return

    def run():
        size = probe_size(url, report.get("kind") or "media",
                          referer=report.get("page_url") or "",
                          byte_hint=report.get("byte_hint") or 0)
        try:
            on_result(size, report)
        except Exception:
            pass

    threading.Thread(target=run, name="media-size-probe", daemon=True).start()
