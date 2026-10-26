#!/usr/bin/env python3
"""Put every vendored model into service/models/, verified by SHA-256 (service/models/manifest.json).

Order per model: already there -> a local copy (e.g. the installed BPM Fixer) -> each URL. A file whose hash differs from the
manifest is never used. Run:  .venv/Scripts/python tools/fetch_models.py  [--offline]
"""
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

MODELS = Path(__file__).resolve().parents[1] / "service" / "models"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def expand(p):
    return Path(os.path.expanduser(os.path.expandvars(p.replace("%PROGRAMFILES%", os.environ.get("PROGRAMFILES", "C:/Program Files")))))


def fetch(name, spec, offline=False):
    dest = MODELS / name
    if dest.exists() and sha256(dest) == spec["sha256"]:
        return f"{name}: ok (already there)"
    for cand in spec.get("local", []):
        p = expand(cand)
        if p.is_file() and sha256(p) == spec["sha256"]:
            shutil.copyfile(p, dest)
            return f"{name}: copied from {p}"
    if not offline:
        for url in spec.get("urls", []):
            part = dest.with_suffix(dest.suffix + ".part")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "songspec-analyzer-build"})
                with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                if sha256(part) == spec["sha256"]:
                    os.replace(part, dest)
                    return f"{name}: downloaded from {url}"
                print(f"  {url}: wrong SHA-256, not used")
            except Exception as e:
                print(f"  {url}: {str(e)[:120]}")
            part.unlink(missing_ok=True)
    return f"{name}: MISSING - copy it into {MODELS} by hand (SHA-256 {spec['sha256']})"


def main():
    offline = "--offline" in sys.argv
    manifest = json.loads((MODELS / "manifest.json").read_text(encoding="utf-8"))
    bad = 0
    for name, spec in manifest.items():
        if name.startswith("_"):
            continue
        msg = fetch(name, spec, offline)
        bad += "MISSING" in msg
        print(msg)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
