"""Sieve scrape API integration (scrape.usesieve.com).

Optional feature: when no API key is configured the whole module is dormant -
no request is ever made and every public entry point no-ops, so the rest of
the app behaves exactly as before.

Design notes
------------
* One HTTP client (:class:`SieveClient`) on top of the stdlib ``urllib`` stack
  already used by ``core/downloader.py`` and ``App._github_api`` - no second
  HTTP dependency.
* The API key lives in the app's existing config store (settings.json, key
  ``sieve_api_key``), with an optional ``SIEVE_API_KEY`` environment fallback.
  It is never logged, never returned to the UI and never exported.
* :class:`SieveManager` owns background polling threads and persists every run
  to ``<data_dir>/sieve_runs.json`` *before* polling, so a crash resumes an
  in-flight run instead of starting a duplicate (POST /api/scrapes has no
  idempotency key and accepted calls spend credits).

The HTTP boundary is injected (``transport``) so unit tests can feed recorded
responses while the parsing / retry / status logic under test stays real.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

SIEVE_BASE_URL = "https://scrape.usesieve.com"
# Self-reported tool name shown on the device-approval page (the approver is
# warned it is agent-supplied).
DEVICE_CLIENT_NAME = "VDO Grabber"
# Output schemas are capped by the API at 32 KiB.
OUTPUT_SCHEMA_MAX_BYTES = 32 * 1024

# Poll cadence: start at 5s, back off to ~30s. Runs take minutes, never use a
# short timeout for a scrape GET.
POLL_START = 5.0
POLL_MAX = 30.0
POLL_FACTOR = 1.5

# statuses a scrape can report.
SCRAPE_STATUSES = ("running", "done", "refused")


class SieveError(Exception):
    """A mapped Sieve failure.

    ``kind`` is a stable machine label (auth, credits, not_found, in_flight,
    rate_limit, client, server, network, timeout, unknown_status, ...);
    ``retryable`` says whether the *same* request may be sent again safely.
    """

    def __init__(self, message: str, *, kind: str = "unknown", status: int | None = None,
                 code: str = "", retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status = status
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after

    def __str__(self) -> str:  # noqa: D105
        extra = " (%s%s)" % (self.kind, "" if self.status is None else " %s" % self.status)
        return self.message + extra


def _json_detail(body_text: str) -> str:
    """Pull the human error string out of an API error body."""
    try:
        doc = json.loads(body_text or "")
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        for key in ("error", "detail", "message"):
            val = doc.get(key)
            if isinstance(val, str) and val:
                return val
            if val is not None and not isinstance(val, (dict, list)):
                return str(val)
        return ""
    return (body_text or "")[:200]


def retry_after_seconds(headers: dict | None) -> float | None:
    """Parse a Retry-After header (integer seconds) when present."""
    try:
        raw = (headers or {}).get("Retry-After") or (headers or {}).get("retry-after")
        if raw is None:
            return None
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def map_http_error(status: int, body_text: str = "", retry_after: float | None = None) -> SieveError:
    """Map an HTTP status + error body onto a :class:`SieveError`.

    Retry policy lives here: only 409/429/5xx (and network failures, raised by
    the transport) are retryable. 400/401/402/404 must not be retried.
    """
    detail = _json_detail(body_text)
    msg = detail or ("sieve HTTP %s" % status)
    if status == 400:
        return SieveError("invalid request: %s" % msg, kind="client", status=400, code=detail)
    if status == 401:
        return SieveError("sieve key missing or revoked: %s" % msg, kind="auth", status=401, code=detail)
    if status == 402:
        return SieveError("out of sieve credits: %s" % msg, kind="credits", status=402, code=detail)
    if status == 404:
        return SieveError("not found (or not yours): %s" % msg, kind="not_found", status=404, code=detail)
    if status == 409:
        return SieveError("a turn is already in flight: %s" % msg, kind="in_flight", status=409,
                          code=detail, retryable=True)
    if status == 429:
        return SieveError("rate limited: %s" % msg, kind="rate_limit", status=429, code=detail,
                          retryable=True, retry_after=retry_after)
    if 500 <= status < 600:
        return SieveError("sieve server error %s: %s" % (status, msg), kind="server", status=status,
                          code=detail, retryable=True, retry_after=retry_after)
    return SieveError("unexpected sieve HTTP %s: %s" % (status, msg), kind="unknown", status=status, code=detail)


def parse_scrape_status(payload: dict) -> str:
    """Return the scrape status, or raise for a value the contract forbids."""
    status = str((payload or {}).get("status") or "").strip()
    if status in SCRAPE_STATUSES:
        return status
    raise SieveError("unexpected scrape status: %r" % status, kind="unknown_status")


def turn_count(payload: dict) -> int:
    """Number of completed turns in a scrape payload (0 when unknown)."""
    turns = (payload or {}).get("turns")
    if isinstance(turns, list):
        return len(turns)
    if isinstance(turns, int):
        return turns
    try:
        return int((payload or {}).get("turn_count") or 0)
    except (TypeError, ValueError):
        return 0


def followup_ready(payload: dict, previous_turns: int) -> bool:
    """A follow-up answer is only ready when the run is done AND its turn count
    advanced past where it was when we sent the message; otherwise we would
    read the *previous* answer."""
    if parse_scrape_status(payload) != "done":
        return False
    return turn_count(payload) > int(previous_turns)


def next_poll_delay(previous: float, start: float = POLL_START, maximum: float = POLL_MAX) -> float:
    """Next poll delay: 5s, then back off (x1.5) capped at ~30s."""
    nxt = float(previous) * POLL_FACTOR if previous else start
    return max(start, min(maximum, nxt))


def encode_multipart(fields: dict, filename: str | None = None, content: bytes = b"") -> tuple[bytes, str]:
    """Minimal multipart/form-data encoder for the optional document upload."""
    boundary = "----VDOGrabber" + uuid.uuid4().hex
    parts: list[bytes] = []
    for key, val in (fields or {}).items():
        parts.append(("--%s\r\n" % boundary).encode())
        parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode())
        parts.append((val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)).encode("utf-8"))
        parts.append(b"\r\n")
    if filename:
        parts.append(("--%s\r\n" % boundary).encode())
        parts.append(('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % os.path.basename(filename)).encode())
        parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
        parts.append(content if isinstance(content, bytes) else str(content).encode("utf-8"))
        parts.append(b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def _urllib_transport(method: str, url: str, headers: dict, data, timeout: float):
    """Default transport: returns (status, headers, raw_body_bytes).

    Raises :class:`SieveError` (kind network/timeout, retryable) for connection
    problems - never for an HTTP status, which is returned to the caller so the
    mapping stays in one place (:func:`map_http_error`).
    """
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return e.code, dict(e.headers or {}), body
    except (socket.timeout, TimeoutError):
        raise SieveError("sieve request timed out", kind="timeout", retryable=True)
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise SieveError("sieve request timed out", kind="timeout", retryable=True)
        raise SieveError("sieve network error: %s" % e.reason, kind="network", retryable=True)
    except OSError as e:
        raise SieveError("sieve network error: %s" % e, kind="network", retryable=True)


class SieveClient:
    """Thin, testable wrapper over the Sieve HTTP contract."""

    def __init__(self, api_key: str = "", base_url: str = SIEVE_BASE_URL,
                 transport=None, timeout: float = 30.0):
        self.api_key = str(api_key or "")
        self.base_url = (base_url or SIEVE_BASE_URL).rstrip("/")
        self._transport = transport or _urllib_transport
        self.timeout = float(timeout)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    # ---------------------------------------------------------------- plumbing
    def url_for(self, path: str) -> str:
        """Absolute URL for a path or a server-relative file link."""
        if str(path).startswith(("http://", "https://")):
            return str(path)
        return self.base_url + ("" if str(path).startswith("/") else "/") + str(path)

    def request(self, method: str, path: str, *, json_body=None, form: dict | None = None,
                filename: str | None = None, content: bytes = b"", require_key: bool = True,
                timeout: float | None = None) -> dict:
        headers = {"Accept": "application/json", "User-Agent": "VDOGrabber"}
        if require_key:
            if not self.configured:
                raise SieveError("sieve is not configured", kind="auth")
            headers["Authorization"] = "Bearer " + self.api_key
        data = None
        if form is not None:
            data, ctype = encode_multipart(form, filename, content)
            headers["Content-Type"] = ctype
        elif json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, resp_headers, body = self._transport(method, self.url_for(path), headers, data,
                                                      timeout or self.timeout)
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
        if int(status) >= 400:
            raise map_http_error(int(status), text, retry_after_seconds(resp_headers))
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            raise SieveError("invalid json from sieve", kind="bad_response", status=int(status))

    def fetch_bytes(self, path: str, timeout: float | None = None) -> bytes:
        """GET a (relative) file URL with the Bearer header, returning raw bytes."""
        if not self.configured:
            raise SieveError("sieve is not configured", kind="auth")
        headers = {"Authorization": "Bearer " + self.api_key, "User-Agent": "VDOGrabber"}
        status, resp_headers, body = self._transport("GET", self.url_for(path), headers, None, timeout or self.timeout)
        if int(status) >= 400:
            raise map_http_error(int(status), body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else "",
                                 retry_after_seconds(resp_headers))
        return bytes(body or b"")

    # ----------------------------------------------------------------- device
    def device_code(self, client_name: str = DEVICE_CLIENT_NAME) -> dict:
        return self.request("POST", "/api/auth/device/code",
                            json_body={"client_name": client_name}, require_key=False)

    def device_token(self, device_code: str) -> dict:
        """Poll the token endpoint once.

        Returns {"status": pending|slow_down|denied|expired} or
        {"status": "ok", "api_key": ..., "key_name": ...}. The 400 error codes
        are part of the protocol, not failures.
        """
        try:
            out = self.request("POST", "/api/auth/device/token",
                               json_body={"device_code": device_code}, require_key=False)
        except SieveError as e:
            if e.status == 400:
                code = e.code or ""
                if code == "authorization_pending":
                    return {"status": "pending"}
                if code == "slow_down":
                    return {"status": "slow_down"}
                if code == "access_denied":
                    return {"status": "denied"}
                if code == "expired_token":
                    return {"status": "expired"}
            raise
        return {"status": "ok", "api_key": str(out.get("api_key") or ""), "key_name": str(out.get("key_name") or "")}

    # ------------------------------------------------------------------ scrapes
    def start_scrape(self, instruction: str, *, target_urls=None, fields=None, schema=None,
                     output_schema=None, table_shape: str | None = None,
                     compliance_mode: str = "regular", document=None) -> dict:
        """POST /api/scrapes - called exactly ONCE per run, never auto-retried.

        There is no idempotency key and an accepted call spends credits; after
        a timeout or network error the first call may already have succeeded, so
        retrying could pay twice and open a duplicate run.
        """
        body = {"instruction": str(instruction or ""), "compliance_mode": compliance_mode or "regular"}
        if target_urls:
            body["target_urls"] = list(target_urls)
        if fields:
            body["fields"] = list(fields)
        if schema:
            body["schema"] = schema
        if output_schema:
            body["output_schema"] = output_schema
        if table_shape:
            body["table_shape"] = table_shape
        if document:
            name, data = document
            form = {k: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                    for k, v in body.items()}
            return self.request("POST", "/api/scrapes", form=form, filename=name, content=data)
        return self.request("POST", "/api/scrapes", json_body=body)

    def get_scrape(self, session_id: str) -> dict:
        """GET /api/scrapes/<session_id> - one-shot; the caller backs off.

        This is a GET, so retrying is safe and the polling loop applies the
        exponential backoff itself (see :func:`next_poll_delay`).
        """
        return self.request("GET", "/api/scrapes/" + str(session_id), timeout=60.0)

    def send_message(self, session_id: str, instruction: str, **opts) -> dict:
        body = {"instruction": str(instruction or "")}
        for key in ("target_urls", "fields", "schema", "output_schema", "table_shape", "compliance_mode"):
            if opts.get(key):
                body[key] = opts[key]
        return self.request("POST", "/api/scrapes/%s/messages" % session_id, json_body=body)

    def credits(self) -> dict:
        return self.request("GET", "/api/me/credits")

    def download_file(self, relative_url: str) -> bytes:
        return self.fetch_bytes(relative_url)


# ---------------------------------------------------------------- persistence
class SieveStore:
    """Durable JSON list of scrape runs in the app data dir.

    Written atomically (tmp + os.replace) so a crash mid-write cannot leave a
    truncated file that would lose an in-flight session_id.
    """

    def __init__(self, data_dir: str, log=None):
        self.path = os.path.join(data_dir, "sieve_runs.json")
        self._log = log

    def load(self) -> dict:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:  # noqa: BLE001
            if self._log:
                self._log.log("sieve run store load failed: %r" % e, level="warning", event="sieve_store_error")
        return {}

    def save(self, runs: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(runs, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, self.path)
        except OSError as e:
            if self._log:
                self._log.log("sieve run store save failed: %r" % e, level="error", event="sieve_store_error")


class SieveManager:
    """Background scrape runs: start -> persist -> poll -> finish/cancel.

    The manager is safe to construct with no key: nothing happens until the
    user starts a run, and every entry point short-circuits when unconfigured.
    """

    def __init__(self, settings, log, data_dir: str, push_ui=None, client: SieveClient | None = None,
                 sleep=time.sleep):
        self.settings = settings
        self.log = log
        self.data_dir = data_dir
        self.push_ui = push_ui or (lambda *a, **k: None)
        self._sleep = sleep
        key = str(settings.get("sieve_api_key") or "") or os.environ.get("SIEVE_API_KEY", "")
        self.client = client or SieveClient(api_key=key)
        if self.client.api_key:
            self.client.api_key = key or self.client.api_key
        self.store = SieveStore(data_dir, log)
        self.runs: dict[str, dict] = self.store.load()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._login: dict | None = None

    # ------------------------------------------------------------------ state
    @property
    def configured(self) -> bool:
        return bool(str(self.client.api_key or ""))

    def _persist(self) -> None:
        with self._lock:
            self.store.save(self.runs)

    def _mark(self, run_id: str, **fields) -> None:
        run = self.runs.get(run_id)
        if not run:
            return
        run.update(fields)
        run["updated"] = time.time()
        self._persist()
        self.push_ui("sieve_run_update", self._public(run))

    @staticmethod
    def _public(run: dict) -> dict:
        return dict(run)

    def set_api_key(self, key: str) -> dict:
        """Store the key in the app's config store. The value is never logged."""
        key = str(key or "").strip()
        if not key:
            return {"ok": False, "error": "empty key"}
        self.settings.set("sieve_api_key", key)
        self.client.api_key = key
        self.log.log("sieve api key stored", event="sieve_key_set")
        return {"ok": True}

    def list_runs(self) -> list[dict]:
        return sorted(self.runs.values(), key=lambda r: r.get("created") or 0, reverse=True)

    def get_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)

    # ------------------------------------------------------------------ login
    def login_start(self) -> dict:
        """Begin the device-code flow; returns the prompt for the user.

        The user must open ``verification_uri_complete`` and approve the code
        themselves - we never open the link or approve on their behalf.
        Polling continues in a background thread.
        """
        if self.configured:
            return {"ok": False, "error": "already configured"}
        try:
            code = self.client.device_code(DEVICE_CLIENT_NAME)
        except SieveError as e:
            self.log.log("sieve device code failed: %s" % e, level="error", event="sieve_login_error", kind=e.kind)
            return {"ok": False, "error": e.message, "kind": e.kind}
        device_code = str(code.get("device_code") or "")
        if not device_code:
            return {"ok": False, "error": "no device_code in response"}
        interval = int(code.get("interval") or 5)
        expires = int(code.get("expires_in") or 600)
        self._login = {"device_code": device_code, "interval": interval,
                       "expires_at": time.time() + expires}
        threading.Thread(target=self._login_loop, name="sieve-login", daemon=True).start()
        self.log.log("sieve device login started", event="sieve_login_start", interval=interval, expires_in=expires)
        return {"ok": True, "user_code": code.get("user_code"), "verification_uri": code.get("verification_uri"),
                "verification_uri_complete": code.get("verification_uri_complete"),
                "expires_in": expires, "interval": interval}

    def login_cancel(self) -> dict:
        self._login = None
        return {"ok": True}

    def _login_loop(self) -> None:
        state = self._login
        if not state:
            return
        interval = state["interval"]
        while self._login is state:
            if time.time() >= state["expires_at"]:
                self.log.log("sieve device code expired", level="warning", event="sieve_login_expired")
                self.push_ui("sieve_login", {"status": "expired"})
                self._login = None
                return
            self._sleep(interval)
            if self._login is not state:
                return
            try:
                res = self.client.device_token(state["device_code"])
            except SieveError as e:
                if e.retryable:
                    interval = min(30, interval + 5)
                    continue
                self.log.log("sieve device login failed: %s" % e, level="error",
                             event="sieve_login_error", kind=e.kind)
                self.push_ui("sieve_login", {"status": "error", "error": e.message})
                self._login = None
                return
            status = res.get("status")
            if status == "ok":
                self.set_api_key(res.get("api_key") or "")
                self.push_ui("sieve_login", {"status": "ok", "key_name": res.get("key_name") or ""})
                self._login = None
                return
            if status == "pending":
                continue
            if status == "slow_down":
                interval += 5
                continue
            if status == "denied":
                self.log.log("sieve device login denied by user", level="warning", event="sieve_login_denied")
                self.push_ui("sieve_login", {"status": "denied"})
                self._login = None
                return
            if status == "expired":
                self.push_ui("sieve_login", {"status": "expired"})
                self._login = None
                return

    # ------------------------------------------------------------------- runs
    def start(self, instruction: str, **opts) -> dict:
        instruction = str(instruction or "").strip()
        if not instruction:
            return {"ok": False, "error": "instruction is required"}
        if not self.configured:
            return {"ok": False, "error": "sieve is not configured"}
        schema = opts.get("output_schema")
        if schema is not None:
            try:
                if len(json.dumps(schema, ensure_ascii=False).encode("utf-8")) > OUTPUT_SCHEMA_MAX_BYTES:
                    return {"ok": False, "error": "output_schema exceeds 32KB"}
            except (TypeError, ValueError) as e:
                return {"ok": False, "error": "output_schema is not JSON-serializable: %r" % e}
        try:
            resp = self.client.start_scrape(instruction, **opts)
        except SieveError as e:
            # Never retried here - see SieveClient.start_scrape.
            self.log.log("sieve run start failed: %s" % e, level="error",
                         event="sieve_run_error", kind=e.kind, status=e.status)
            return {"ok": False, "error": e.message, "kind": e.kind, "retryable": e.retryable}
        session_id = str(resp.get("session_id") or "")
        if not session_id:
            return {"ok": False, "error": "no session_id in response"}
        run = {
            "id": uuid.uuid4().hex[:12], "session_id": session_id, "instruction": instruction,
            "status": "queued", "created": time.time(), "updated": time.time(),
            "turn_count": 0, "poll": str(resp.get("poll") or ""),
            "compliance_mode": opts.get("compliance_mode") or "regular",
            "schema_conformance": None, "files": [], "result": None, "error": "",
            "awaiting_turn_from": None,
        }
        self.runs[run["id"]] = run
        # Durable before polling: a crash resumes this session instead of
        # starting a duplicate (which would spend credits twice).
        self._persist()
        self.log.log("sieve run queued: %s (session=%s)" % (run["id"], session_id),
                     event="sieve_run_queued", id=run["id"], session=session_id)
        self._start_poll(run["id"])
        return {"ok": True, "run": self._public(run)}

    def resume_pending(self) -> None:
        """Re-attach polling threads for runs persisted before a crash/restart."""
        if not self.configured:
            return
        for run_id, run in list(self.runs.items()):
            if run.get("awaiting_turn_from") is not None:
                self._start_followup_poll(run_id, int(run["awaiting_turn_from"]))
            elif run.get("status") in ("queued", "running"):
                self._start_poll(run_id)

    def _start_poll(self, run_id: str) -> None:
        self._start_thread(run_id, self._poll_loop, run_id)

    def _start_followup_poll(self, run_id: str, prev_turns: int) -> None:
        self._start_thread(run_id, self._followup_loop, run_id, prev_turns)

    def _start_thread(self, run_id: str, target, *args) -> None:
        existing = self._threads.get(run_id)
        if existing and existing.is_alive():
            return
        thread = threading.Thread(target=target, args=args, name="sieve-%s" % run_id, daemon=True)
        self._threads[run_id] = thread
        thread.start()

    def _poll_loop(self, run_id: str) -> None:
        delay = POLL_START
        while True:
            self._sleep(delay)
            run = self.runs.get(run_id)
            if not run or not self.configured:
                return
            try:
                payload = self.client.get_scrape(run["session_id"])
            except SieveError as e:
                if e.retryable:
                    delay = max(next_poll_delay(delay), e.retry_after or 0.0)
                    continue
                self._fail(run_id, e)
                return
            try:
                status = parse_scrape_status(payload)
            except SieveError as e:
                self._fail(run_id, e)
                return
            if status == "running":
                self._mark(run_id, status="running", turn_count=turn_count(payload))
                delay = next_poll_delay(delay)
                continue
            if status == "done":
                self._finish(run_id, payload)
                return
            refusal = payload.get("refusal") or {}
            self._mark(run_id, status="refused", error=str(refusal.get("code") or "refused"),
                       refusal=refusal, turn_count=turn_count(payload))
            self.log.log("sieve run refused: %s (%s)" % (run_id, refusal.get("code") or ""),
                         level="warning", event="sieve_run_refused", id=run_id, code=refusal.get("code") or "")
            return

    def _followup_loop(self, run_id: str, prev_turns: int) -> None:
        """Poll until the run is done AND its turn count advanced past the
        value captured when the message was sent."""
        delay = POLL_START
        while True:
            self._sleep(delay)
            run = self.runs.get(run_id)
            if not run or not self.configured:
                return
            try:
                payload = self.client.get_scrape(run["session_id"])
            except SieveError as e:
                if e.retryable:
                    delay = max(next_poll_delay(delay), e.retry_after or 0.0)
                    continue
                self._fail(run_id, e)
                return
            if followup_ready(payload, prev_turns):
                self._mark(run_id, awaiting_turn_from=None)
                self._finish(run_id, payload)
                return
            try:
                parse_scrape_status(payload)
            except SieveError as e:
                self._fail(run_id, e)
                return
            self._mark(run_id, status="running", turn_count=turn_count(payload))
            delay = next_poll_delay(delay)

    def _fail(self, run_id: str, err: SieveError) -> None:
        self._mark(run_id, status="error", error=err.message)
        self.log.log("sieve run error: %s (%s)" % (run_id, err.message), level="error",
                     event="sieve_run_error", id=run_id, kind=err.kind, status=err.status)

    def _finish(self, run_id: str, payload: dict) -> None:
        self._mark(
            run_id, status="done", turn_count=turn_count(payload),
            files=list(payload.get("files") or []),
            result=payload.get("result"),
            schema_conformance=payload.get("schema_conformance"),
            summary=payload.get("summary"),
            awaiting_turn_from=None, error="",
        )
        run = self.runs.get(run_id) or {}
        self.log.log("sieve run done: %s (%d file(s))" % (run_id, len(run.get("files") or [])),
                     event="sieve_run_done", id=run_id, files=len(run.get("files") or []),
                     schema_status=((run.get("schema_conformance") or {}) or {}).get("status") if isinstance(run.get("schema_conformance"), dict) else None)

    def followup(self, run_id: str, instruction: str, **opts) -> dict:
        run = self.runs.get(run_id)
        if not run:
            return {"ok": False, "error": "no such run"}
        if not self.configured:
            return {"ok": False, "error": "sieve is not configured"}
        prev = int(run.get("turn_count") or 0)
        try:
            self.client.send_message(run["session_id"], instruction, **opts)
        except SieveError as e:
            # 409: a turn is in flight - the caller waits, then resends.
            return {"ok": False, "error": e.message, "kind": e.kind,
                    "retryable": e.kind == "in_flight" or e.retryable}
        # Record the turn boundary before polling, so a restart resumes waiting
        # for the *new* answer rather than reading the previous one.
        self._mark(run_id, status="running", awaiting_turn_from=prev)
        self._start_followup_poll(run_id, prev)
        return {"ok": True, "run": self._public(self.runs.get(run_id) or run)}

    def credits(self) -> dict:
        if not self.configured:
            return {"ok": False, "error": "sieve is not configured"}
        try:
            out = self.client.credits()
        except SieveError as e:
            return {"ok": False, "error": e.message, "kind": e.kind}
        return {"ok": True, "credits": out}

    def download_files(self, run_id: str, dest_dir: str | None = None) -> dict:
        """Fetch every delivered file (relative url + Bearer header) to disk."""
        from .downloader import sanitize_filename, unique_stem  # reuse naming rules

        run = self.runs.get(run_id)
        if not run:
            return {"ok": False, "error": "no such run"}
        files = list(run.get("files") or [])
        if not files:
            return {"ok": False, "error": "this run has no delivered files"}
        if not self.configured:
            return {"ok": False, "error": "sieve is not configured"}
        dest = dest_dir or self.settings.get("download_dir")
        os.makedirs(dest, exist_ok=True)
        written = []
        for f in files:
            rel = str((f or {}).get("url") or "").strip()
            if not rel:
                continue
            name = sanitize_filename((f or {}).get("name") or "sieve-file")
            ext = str((f or {}).get("ext") or "").strip()
            if ext and not ext.startswith("."):
                ext = "." + ext
            if not os.path.splitext(name)[1] and ext:
                name = re.sub(r"[. ]+$", "", name) + ext
            stem, dot_ext = os.path.splitext(name)
            path = unique_stem(dest, sanitize_filename(stem) or "sieve-file", dot_ext)
            try:
                data = self.client.download_file(rel)
            except SieveError as e:
                return {"ok": False, "error": e.message, "kind": e.kind, "written": written}
            with open(path, "wb") as fh:
                fh.write(data)
            written.append(path)
            self.log.log("sieve file saved: %s" % path, event="sieve_file_saved", id=run_id, path=path)
        return {"ok": True, "files": written}


def device_login(client: SieveClient, *, on_prompt, sleep=time.sleep, max_wait: float | None = None) -> str:
    """Run the interactive device-code flow and return the API key.

    ``on_prompt(info)`` is called once with the user-facing fields; the caller
    is responsible for showing them to the user. The key is returned, never
    printed here. Raises :class:`SieveError` on expiry/denial/transport errors.
    """
    code = client.device_code(DEVICE_CLIENT_NAME)
    device_code = str(code.get("device_code") or "")
    if not device_code:
        raise SieveError("no device_code in response", kind="bad_response")
    interval = int(code.get("interval") or 5)
    expires = int(code.get("expires_in") or 600)
    on_prompt({
        "user_code": code.get("user_code"),
        "verification_uri": code.get("verification_uri"),
        "verification_uri_complete": code.get("verification_uri_complete"),
        "expires_in": expires, "interval": interval,
    })
    deadline = time.time() + (max_wait if max_wait is not None else expires)
    while time.time() < deadline:
        sleep(interval)
        res = client.device_token(device_code)
        status = res.get("status")
        if status == "ok":
            key = str(res.get("api_key") or "")
            if not key:
                raise SieveError("device login returned no api_key", kind="bad_response")
            return key
        if status == "pending":
            continue
        if status == "slow_down":
            interval += 5
            continue
        if status == "denied":
            raise SieveError("authorization denied by user", kind="denied")
        if status == "expired":
            raise SieveError("device code expired", kind="expired")
    raise SieveError("device code expired", kind="expired")
