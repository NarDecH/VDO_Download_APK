"""Build the standalone VDOGrabber.exe with PyInstaller.

Result: dist/VDOGrabber.exe  — a single portable file, no Python install needed.
Bundled inside: ui/ (control-center HTML) + bin/yt-dlp.exe (download engine).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_ytdlp() -> None:
    """The bundled yt-dlp.exe is not committed (keeps the repo lean);
    fetch the latest release binary before packaging."""
    target = os.path.join(ROOT, "app", "bin", "yt-dlp.exe")
    if os.path.exists(target) and os.path.getsize(target) > 1_000_000:
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    print(">> downloading yt-dlp.exe ...")
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "VDOGrabber-build"})
    with urllib.request.urlopen(req, timeout=120) as r, open(target, "wb") as f:
        f.write(r.read())
    print(">> yt-dlp.exe: %.1f MB" % (os.path.getsize(target) / 1048576))


def main() -> int:
    os.chdir(ROOT)
    ensure_ytdlp()
    dist = os.path.join(ROOT, "dist")
    build = os.path.join(ROOT, "build")
    shutil.rmtree(dist, ignore_errors=True)
    shutil.rmtree(build, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", "VDOGrabber",
        "--icon", os.path.join(ROOT, "assets", "icon.ico"),
        "--add-data", os.path.join(ROOT, "app", "ui") + os.pathsep + "ui",
        "--add-data", os.path.join(ROOT, "app", "tests") + os.pathsep + "tests",
        "--add-data", os.path.join(ROOT, "app", "bin", "yt-dlp.exe") + os.pathsep + "bin",
        "--version-file", os.path.join(ROOT, "scripts", "version_info.txt"),
        os.path.join(ROOT, "app", "main.py"),
    ]
    print(">>", " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        print("PyInstaller failed with", rc)
        return rc
    out = os.path.join(dist, "VDOGrabber.exe")
    print("\nBUILT:", out, "(%.1f MB)" % (os.path.getsize(out) / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
