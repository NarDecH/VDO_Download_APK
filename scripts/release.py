#!/usr/bin/env python3
"""One-command release for VDO Grabber (desktop + Android).

Encodes the release checklist that used to live in AGENTS.md as code:

  python scripts/release.py 1.4.0 --yes \
      --notes "หัวข้อ A; หัวข้อ B"        # bump + checks + commit + tag + push
  python scripts/release.py 1.4.0 --verify-only
                                      # wait for CI, check assets/checksums/APK

Bump points (all five, kept in sync):
  app/core/logger.py              APP_VERSION = "X.Y.Z"
  scripts/version_info.txt        filevers/prodvers (X,Y,Z,0) + 2 strings
  android/app/build.gradle.kts    versionCode (auto-increment) / versionName
  android/.../MainActivity.kt     "VDO Grabber X.Y.Z starting" log line
  docs/changelog.md               new ## [X.Y.Z] - <today> section

Checks run before anything is committed: python scripts/test_units.py,
python app/main.py --selftest, and (unless --skip-gradle) the Android JVM
test + Kotlin compile task. git push uses the DNS-flake retry (3 attempts).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

EXPECTED_ASSETS = 8  # 4 APKs + exe + checksums.txt + 2x .sha256


def run(cmd: list[str], **kw) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def die(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)


# ------------------------------------------------------------------ bumping
def current_version() -> str:
    m = re.search(r'APP_VERSION = "([^"]+)"', read("app/core/logger.py"))
    if not m:
        die("cannot find APP_VERSION in app/core/logger.py")
    return m.group(1)


def next_version_code() -> int:
    m = re.search(r"versionCode\s*=\s*(\d+)", read("android/app/build.gradle.kts"))
    if not m:
        die("cannot find versionCode in android/app/build.gradle.kts")
    return int(m.group(1)) + 1


def bump_version(new: str, notes: list[str]) -> int:
    old = current_version()
    if old == new:
        die(f"version is already {new}")
    print(f"bump: {old} -> {new}")

    logger = read("app/core/logger.py")
    write("app/core/logger.py", logger.replace(f'APP_VERSION = "{old}"', f'APP_VERSION = "{new}"'))

    vi = read("scripts/version_info.txt")
    trio = ",".join(new.split(".") + ["0"])
    vi = vi.replace(
        "filevers=(%s)" % ",".join(old.split(".") + ["0"]),
        "filevers=(%s)" % trio).replace(
        "prodvers=(%s)" % ",".join(old.split(".") + ["0"]),
        "prodvers=(%s)" % trio).replace(
        "'FileVersion', '%s.0'" % old, "'FileVersion', '%s.0'" % new).replace(
        "'ProductVersion', '%s.0'" % old, "'ProductVersion', '%s.0'" % new)
    write("scripts/version_info.txt", vi)

    gradle = read("android/app/build.gradle.kts")
    vc = next_version_code()
    gradle = re.sub(r"versionCode\s*=\s*\d+", f"versionCode = {vc}", gradle)
    gradle = gradle.replace(f'versionName = "{old}"', f'versionName = "{new}"')
    write("android/app/build.gradle.kts", gradle)

    main_activity = "android/app/src/main/java/com/vdograbber/app/MainActivity.kt"
    ma = read(main_activity)
    write(main_activity, ma.replace(f"VDO Grabber {old} starting", f"VDO Grabber {new} starting"))

    changelog = read("docs/changelog.md")
    bullets = "\n".join("- " + n.strip() for n in notes if n.strip()) or "- (ระบุภายหลัง)"
    today = datetime.date.today().isoformat()
    section = f"## [{new}] — {today}\n\n### การเปลี่ยนแปลง\n{bullets}\n\n"
    anchor = "และใช้ [Semantic Versioning](https://semver.org/th/)\n"
    if anchor not in changelog:
        die("changelog anchor line not found - insert the section manually")
    write("docs/changelog.md", changelog.replace(anchor, anchor + "\n" + section, 1))
    return vc


def verify_bump(new: str, vc: int) -> None:
    checks = [
        ('APP_VERSION = "%s"' % new) in read("app/core/logger.py"),
        ("versionCode = %d" % vc) in read("android/app/build.gradle.kts"),
        ('versionName = "%s"' % new) in read("android/app/build.gradle.kts"),
        (f"VDO Grabber {new} starting") in read("android/app/src/main/java/com/vdograbber/app/MainActivity.kt"),
        ("## [%s]" % new) in read("docs/changelog.md"),
        ("'%s.0'" % new) in read("scripts/version_info.txt"),
    ]
    if not all(checks):
        die(f"bump incomplete: {checks}")


# ------------------------------------------------------------------- checks
def run_checks(skip_gradle: bool) -> None:
    rc, out = run([sys.executable, "scripts/test_units.py"])
    if rc != 0:
        die(f"desktop unit tests failed:\n{out[-1200:]}")
    print("PASS  desktop unit tests")
    rc, out = run([sys.executable, "app/main.py", "--selftest"])
    if rc != 0:
        die(f"desktop selftest failed:\n{out[-1200:]}")
    print("PASS  desktop selftest")
    if not skip_gradle:
        # absolute wrapper path: cmd/CreateProcess do not reliably search the
        # child cwd (NoDefaultCurrentDirectoryInExePath hosts), and the
        # absolute .bat path runs fine through subprocess on Windows
        wrapper = "gradlew.bat" if os.name == "nt" else "gradlew"
        gradle_cmd = [os.path.join(ROOT, "android", wrapper), "testDebugUnitTest",
                      ":app:compileDebugKotlin", "--console=plain"]
        rc, out = run(gradle_cmd, cwd=os.path.join(ROOT, "android"))
        if rc != 0:
            die(f"android gradle check failed:\n{out[-1200:]}")
        print("PASS  android gradle check")


# --------------------------------------------------------------------- git
def git_ready() -> None:
    rc, out = run(["git", "status", "--porcelain"])
    if rc != 0:
        die("not a git repo?")
    dirty = [l for l in out.splitlines() if l.strip() and ".freebuff/" not in l]
    if dirty:
        die("working tree has uncommitted changes:\n" + "\n".join(dirty))
    rc, out = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if out.strip() != "main":
        die(f"release from main only (on {out.strip()})")


def commit_and_tag(version: str) -> None:
    files = ["app/core/logger.py", "scripts/version_info.txt",
             "android/app/build.gradle.kts",
             "android/app/src/main/java/com/vdograbber/app/MainActivity.kt",
             "docs/changelog.md"]
    rc, out = run(["git", "add"] + files)
    if rc != 0:
        die(f"git add failed: {out}")
    rc, out = run(["git", "commit", "-m", f"release v{version}"])
    if rc != 0:
        die(f"git commit failed: {out}")
    rc, out = run(["git", "tag", f"v{version}"])
    if rc != 0:
        die(f"git tag failed: {out}")
    print(f"commit + tag v{version}: done")


def push_with_retry(retries: int = 3, wait: int = 25) -> None:
    for args in (["origin", "main"], ["origin", "v" + current_version()]):
        for attempt in range(1, retries + 1):
            rc, out = run(["git", "push"] + args)
            if rc == 0:
                print(f"push {' '.join(args)}: ok (attempt {attempt})")
            elif attempt == retries:
                die("push failed - DNS flake? run: git push " + " ".join(args))
            else:
                print(f"push {' '.join(args)} failed (attempt {attempt}): {out.strip()[-160:]}")
                time.sleep(wait)


# ------------------------------------------------------------------ verify
def gh(args: list[str]) -> tuple[int, str]:
    return run(["gh"] + args)


def wait_for_ci(tag: str, timeout_min: int = 30) -> bool:
    sha = run(["git", "rev-parse", tag])[1].strip()[:7]
    deadline = time.time() + timeout_min * 60
    workflows = ["Android APK", "Desktop selftest + exe"]
    while time.time() < deadline:
        pending = []
        for wf in workflows:
            rc, out = gh(["run", "list", "--workflow=" + wf.replace(" ", "%20"),
                          "--limit", "5",
                          "--json", "headSha,status,conclusion"])
            rows = []
            try:
                rows = json.loads(out) if rc == 0 and out.strip() else []
            except json.JSONDecodeError:
                pass
            for r in rows:
                if (r.get("headSha") or "").startswith(sha):
                    pending.append((wf, r.get("status")))
                    break
        statuses = {wf: st for wf, st in pending}
        done = all(statuses.get(wf) == "completed" for wf in workflows)
        if done:
            conclusions = []
            for wf in workflows:
                rc, out = gh(["run", "list", "--workflow=" + wf.replace(" ", "%20"),
                              "--limit", "5", "--json", "headSha,conclusion"])
                rows = json.loads(out) if rc == 0 and out.strip() else []
                c = next((r.get("conclusion") for r in rows
                          if (r.get("headSha") or "").startswith(sha)), "unknown")
                conclusions.append((wf, c))
            for wf, c in conclusions:
                print(f"CI   {wf}: {c}")
            return all(c == "success" for _, c in conclusions)
        print(f"CI   waiting: {statuses}")
        time.sleep(60)
    print("CI   timeout waiting for runs")
    return False


def verify_release(version: str, wait: bool) -> None:
    tag = "v" + version
    if wait and not wait_for_ci(tag):
        die("CI did not go green - fix or rerun the failed jobs, then re-verify")
    rc, out = gh(["release", "view", tag, "--json", "isDraft,assets",
                  "--template", "{{.isDraft}}|{{len .assets}}"])
    if rc != 0:
        die(f"release {tag} not found (CI attaches assets on a green tag build)")
    draft, n_assets = out.strip().split("|")
    if draft == "true":
        die("release is still a draft")
    if int(n_assets) != EXPECTED_ASSETS:
        die(f"expected {EXPECTED_ASSETS} assets, found {n_assets}")
    print(f"PASS  release {tag}: {n_assets} assets, published")

    tmp = os.path.join("build", "release-verify")
    os.makedirs(tmp, exist_ok=True)
    rc, out = gh(["release", "download", tag, "--pattern", "checksums.txt",
                  "--pattern", f"VDOGrabber-{version}-android-universal.apk",
                  "--clobber", "--output-dir", tmp])
    if rc != 0:
        die(f"download failed: {out[-300:]}")
    sums = read(os.path.join(tmp, "checksums.txt"))
    lines = [l for l in sums.splitlines() if l.strip()]
    hashes = [l.split()[0] for l in lines]
    if len(hashes) != 5 or len(set(hashes)) != len(hashes):
        die(f"checksums.txt must hold 5 unique hashes, got {len(hashes)} lines / {len(set(hashes))} unique")
    print("PASS  checksums.txt: 5 unique hashes")
    apk = os.path.join(tmp, f"VDOGrabber-{version}-android-universal.apk")
    rc, out = run(["git", "hash-object", apk])  # cheap existence+read check
    if rc != 0:
        die("universal APK missing from the release download")
    aapt = os.environ.get("AAPT") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "build-tools", "36.0.0", "aapt.exe")
    if os.path.exists(aapt):
        rc, out = run([aapt, "dump", "badging", apk])
        m = re.search(r"versionCode='(\d+)' versionName='([^']+)'", out)
        if not m or m.group(1) != str(next_version_code() - 1) or m.group(2) != version:
            die(f"APK badging mismatch: {m.groups() if m else out[:200]}")
        print(f"PASS  APK badging: versionCode={m.group(1)} versionName={m.group(2)}")
    else:
        print("SKIP  aapt not found (set AAPT env var) - badging not verified")


# --------------------------------------------------------------------- main
def main() -> int:
    # Thai notes/sections are normal output - a cp1252 Windows console would
    # raise UnicodeEncodeError on print() without this (same as analyze_events)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="One-command VDO Grabber release")
    ap.add_argument("version", help="e.g. 1.4.0")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--notes", action="append", default=[],
                    help="changelog bullet (repeat; 'a; b' splits into two)")
    ap.add_argument("--skip-gradle", action="store_true")
    ap.add_argument("--bump-only", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    version = args.version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        die("version must look like 1.4.0")

    notes = [n for chunk in args.notes for n in chunk.split(";")]
    if args.verify_only:
        verify_release(version, wait=True)
        print("RELEASE VERIFIED")
        return 0

    git_ready()
    vc = next_version_code()
    print(f"plan: v{version} (versionCode {vc}) notes={notes}")
    if args.dry_run:
        print("dry-run: no files touched")
        return 0
    if not args.yes:
        if input("continue? [y/N] ").strip().lower() != "y":
            print("aborted")
            return 1

    bump_version(version, notes)
    verify_bump(version, vc)
    print("PASS  bump verified (5 files + changelog)")
    run_checks(args.skip_gradle)
    if args.bump_only:
        print("bump-only: review the tree, then commit/tag/push manually")
        return 0
    commit_and_tag(version)
    push_with_retry()
    print(f"pushed - CI builds v{version}; verify later with:")
    print(f"  python scripts/release.py {version} --verify-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
