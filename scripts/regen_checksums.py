"""Rebuild a release's checksums.txt from the ACTUAL published assets.

Why this exists (v1.9.15 audit): desktop.yml merges only its own exe .sha256
into whatever checksums.txt already holds. If an asset gets re-uploaded
outside CI (--clobber), the merged file silently keeps the STALE hash.
Rebuilding from the assets themselves can never go stale.

Usage:
  python scripts/regen_checksums.py v1.9.15          # rebuild + upload --clobber
  python scripts/regen_checksums.py v1.9.15 --verify # compare only, no upload

Rules honored (SESSION_NOTES อุบัติเหตุ checksums.txt):
  - every non-checksums asset is downloaded fresh before hashing
  - upload is --clobber only after ALL downloads+hashes succeeded
  - output format: "<hex>  <filename>" (no binary marker, sha256sum -c compatible)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request


def all_assets(tag: str) -> list[dict]:
    out = subprocess.run(["gh", "api", f"repos/NarDecH/VDO_Download_APK/releases/tags/{tag}",
                          "-q", ".assets"], capture_output=True, text=True, check=True, encoding="utf-8")
    assets = json.loads(out.stdout)
    if not assets:
        raise SystemExit(f"release {tag} has no assets")
    return assets


def build(tag: str) -> list[tuple[str, str]]:
    """[(sha256_hex, asset_name)] for every asset except checksums.txt itself."""
    rows = []
    tmpdir = tempfile.mkdtemp(prefix=f"regen-{tag.lstrip('v')}-")
    for a in all_assets(tag):
        name = a["name"]
        if name.lower() == "checksums.txt":
            continue
        url = a["browser_download_url"]
        dest = os.path.join(tmpdir, name)
        print(f"download {name} ({a['size'] // (1 << 20)} MB)", flush=True)
        with urllib.request.urlopen(url, timeout=600) as r, open(dest, "wb") as fh:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
        h = hashlib.sha256()
        with open(dest, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        rows.append((h.hexdigest(), name))
        os.remove(dest)
    return rows


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if not argv:
        print("usage: regen_checksums.py v<semver> [--verify]", file=sys.stderr)
        return 2
    tag = argv[0]
    verify_only = "--verify" in flags

    rows = build(tag)
    lines = ["%s  %s" % (digest, name) for digest, name in sorted(rows, key=lambda r: r[1])]

    remote = subprocess.run(
        ["gh", "api", f"repos/NarDecH/VDO_Download_APK/releases/tags/{tag}",
         "-q", '[.assets[] | select(.name=="checksums.txt") | '
               '.browser_download_url] | .[0]'],
        capture_output=True, text=True, check=True, encoding="utf-8").stdout.strip()
    with tempfile.TemporaryDirectory() as td:
        cur = os.path.join(td, "current_checksums.txt")
        with urllib.request.urlopen(remote, timeout=60) as r, open(cur, "wb") as fh:
            fh.write(r.read())
        diffs = []
        old = {ln.split(" ", 1)[1].lstrip("*").strip(): ln.split(" ", 2)[0].strip()
               for ln in open(cur, encoding="utf-8").read().splitlines() if ln.strip()}
        for digest, name in rows:
            if old.get(name) not in (None, digest):
                diffs.append(f"  {name}: release-served checksums.txt={old[name]} actual={digest}")
        if diffs:
            print("MISMATCH between published checksums.txt and real assets:")
            print("\n".join(diffs))
        else:
            print("published checksums.txt already matches all assets")

    if verify_only:
        return 0
    with tempfile.TemporaryDirectory() as td:
        new = os.path.join(td, "checksums.txt")
        with open(new, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        rc = subprocess.run(["gh", "release", "upload", tag, "checksums.txt",
                             "--repo", "NarDecH/VDO_Download_APK", "--clobber"],
                            cwd=td).returncode
    print("UPLOADED" if rc == 0 else "UPLOAD FAILED", tag)
    return rc


if __name__ == "__main__":
    sys.exit(main())
