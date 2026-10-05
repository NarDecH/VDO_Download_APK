#!/usr/bin/env python3
"""Analyze an events.jsonl log (desktop or Android) - field-log triage tool.

Born from the v1.3.5 field log (Android 16, merrylion2.com): the three
problems reported by a real user (engine "done: null", WebView sniff crash
spam, blob: URLs reaching the engine) all show up in events.jsonl long
before the user notices anything - if you know what to look for.

Usage
-----
  python scripts/analyze_events.py                       # desktop log path
  python scripts/analyze_events.py path/to/events.jsonl  # any log file
  python scripts/analyze_events.py --event download_no_file --event download_error
  python scripts/analyze_events.py --json                # machine output

Exit codes: 0 = analyzed, 2 = log file missing/unreadable.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

# The debugging funnel, in the order a healthy download flows through it.
KNOWN_EVENTS = [
    "app_start", "engine_ready", "page_loaded", "media_found",
    "download_queued", "download_start", "download_rerouted",
    "download_skipped_blob", "download_not_media", "download_no_file",
    "download_no_file_probe", "download_done", "download_error",
    "download_failed", "download_canceled", "engine_download_start",
    "download_no_ffmpeg", "download_fallback",
]

# Problems we know how to name (field-log lessons).
PROBLEM_EVENTS = {
    "download_skipped_blob": "blob: URL - exists only inside the page, never downloadable",
    "download_not_media": "saved file was an HTML player page, not video",
    "download_no_file": "engine exited 0 but wrote no file",
    "download_error": "engine/download error (see the reason column)",
}


def default_log_path() -> str:
    """Desktop log path; on other OSes just a relative fallback."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "VDOGrabber", "logs", "events.jsonl")
    return os.path.join("logs", "events.jsonl")


def parse_events(lines):
    """Parse JSONL -> (records, bad_line_count). Tolerates junk lines."""
    records, bad = [], 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                records.append(rec)
            else:
                bad += 1
        except (json.JSONDecodeError, ValueError):
            bad += 1
    return records, bad


def _ts(rec: dict):
    """Best-effort timestamp -> float epoch or None."""
    for key in ("ts", "time", "t", "timestamp"):
        v = rec.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _url(rec: dict) -> str:
    return str(rec.get("url") or rec.get("page") or "")


def summarize(records: list[dict]) -> dict:
    """Counts per event + blob/no-file/red-flag facts the UI prints."""
    counts: dict[str, int] = {}
    for rec in records:
        counts[str(rec.get("event") or "?")] = counts.get(str(rec.get("event") or "?"), 0) + 1

    blob_urls = sorted({_url(r) for r in records if _url(r).startswith("blob:")})
    engine_ready = [r for r in records if r.get("event") == "engine_ready"]
    engine_version = engine_ready[-1].get("version") if engine_ready else None

    return {
        "total": len(records),
        "counts": counts,
        "blob_urls": blob_urls,
        "engine_ready_seen": bool(engine_ready),
        "engine_version": engine_version,
        "first_ts": next((t for t in (_ts(r) for r in records) if t is not None), None),
        "last_ts": next((t for t in (_ts(r) for r in reversed(records)) if t is not None), None),
    }


def diagnose(summary: dict, records: list[dict]) -> list[str]:
    """Human-readable findings, most actionable first (Thai, like the app UI)."""
    out: list[str] = []
    counts = summary["counts"]

    if summary["blob_urls"]:
        out.append("พบ blob: URL %d รายการ - เอนจินเข้าถึงไม่ได้ตามธรรมชาติ "
                   "(มีอยู่แค่ในหน้าเว็บ) ต้องใช้ลิงก์ไฟล์/ลิงก์สตรีมแทน" % len(summary["blob_urls"]))
    if counts.get("download_skipped_blob"):
        out.append("แอปกรอง blob: ออกก่อนเข้าเอนจินแล้ว %d ครั้ง (ทำงานถูกต้อง)"
                   % counts["download_skipped_blob"])
    if counts.get("download_no_file"):
        out.append("เอนจิน exit 0 แต่ไม่มีไฟล์ %d ครั้ง - ดูเหตุผลจริงจาก "
                   "download_no_file_probe (DRM/geo/โครงสร้างไม่รู้จัก)" % counts["download_no_file"])
    if counts.get("download_not_media"):
        out.append("ไฟล์ที่ได้เป็นหน้า HTML ปลอม %d ครั้ง - ถูกตรวจ/ลบ/re-route แล้ว"
                   % counts["download_not_media"])
    if summary["engine_ready_seen"] and not summary["engine_version"]:
        out.append("engine_ready แต่ version ว่าง (vnull) - ตรวจการติดตั้ง yt-dlp")
    if counts.get("media_found") and not (counts.get("download_queued") or counts.get("download_start")):
        out.append("มี media_found แต่ไม่มีการเริ่มดาวน์โหลดเลย - ผู้ใช้ยังไม่ได้กด (หรือปุ่มพัง)")
    if not out:
        out.append("ไม่พบสัญญาณปัญหาที่รู้จัก - ดูตาราง event ด้านบนเพื่อไล่ด้วยตัวเอง")
    return out


def _fmt_ts(ts) -> str:
    if ts is None:
        return "?"
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return str(ts)


def render_text(summary: dict, records: list[dict], findings: list[str],
                show_records: list[dict]) -> str:
    lines: list[str] = []
    counts = summary["counts"]
    lines.append("=== events.jsonl summary ===")
    lines.append("records: %d   span: %s .. %s"
                 % (summary["total"], _fmt_ts(summary["first_ts"]), _fmt_ts(summary["last_ts"])))
    lines.append("")
    lines.append("-- events (funnel order) --")
    for ev in KNOWN_EVENTS + sorted(set(counts) - set(KNOWN_EVENTS)):
        if counts.get(ev):
            lines.append("%5d  %-28s %s" % (counts[ev], ev, PROBLEM_EVENTS.get(ev, "")))
    if summary["engine_ready_seen"]:
        lines.append("engine: yt-dlp %s" % (summary["engine_version"] or "(version unknown!)"))
    lines.append("")
    lines.append("-- findings --")
    for f in findings:
        lines.append("* " + f)
    if show_records:
        lines.append("")
        lines.append("-- matched records (%d) --" % len(show_records))
        for rec in show_records:
            lines.append("%s  %-24s %s" % (_fmt_ts(_ts(rec)), rec.get("event") or "?",
                                           _url(rec)[:100] or rec.get("error") or ""))
    return "\n".join(lines)


def _force_utf8_stdout() -> None:
    """Findings are Thai; a cp1252 Windows console would raise
    UnicodeEncodeError on print. Python 3.7+ lets us reconfigure the
    stream - a StringIO (unit tests) has no reconfigure and never needs it.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(description="Analyze VDO Grabber events.jsonl")
    ap.add_argument("path", nargs="?", default=None, help="events.jsonl path (default: desktop log)")
    ap.add_argument("--event", action="append", default=[], help="show only this event (repeatable)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    path = args.path or default_log_path()
    if not os.path.isfile(path):
        print("log not found: %s" % path, file=sys.stderr)
        return 2
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            records, bad = parse_events(fh)
    except OSError as e:
        print("cannot read %s: %r" % (path, e), file=sys.stderr)
        return 2

    if args.event:
        records = [r for r in records if r.get("event") in set(args.event)]
    summary = summarize(records)
    findings = diagnose(summary, records)

    if args.json:
        print(json.dumps({"path": path, "bad_lines": bad, "summary": summary,
                          "findings": findings}, ensure_ascii=False, indent=2))
    else:
        print(render_text(summary, records, findings, records))
        if bad:
            print("\n(skipped %d unparseable line(s))" % bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
