#!/usr/bin/env python3
"""Fetch the third-party models the service can use, at pinned commits, and check their weights by SHA-256.

  third_party/skey  Deezer S-KEY (key, ICASSP 2025), MIT        github.com/deezer/skey
  third_party/btc   BTC (chords, ISMIR 2019), MIT                github.com/jayg996/BTC-ISMIR19
Both are optional: without them the key falls back to Centsible's detector and the chords to the template engine.
Run:  python tools/fetch_third_party.py
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOS = {
    "skey": ("https://github.com/deezer/skey.git", "918b83d273568d5041569bb8068843d19a335726",
             {"skey/models/skey.pt": "78dfd0ad4fa9434bf7cec70a25934b7c575bda9c80e994700140770ad3a5ead4"}),
    "btc": ("https://github.com/jayg996/BTC-ISMIR19.git", "2682317be668032e6e4b269ded36adaa2ad57df0",
            {"test/btc_model_large_voca.pt": "1673d23f8f9a55ae7f9e8b80a51da616debb22675b8d8b67ea6ce0ef37b0ab51"}),
}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    bad = 0
    for name, (url, commit, files) in REPOS.items():
        dest = ROOT / "third_party" / name
        if not dest.exists():
            subprocess.run(["git", "clone", "-q", url, str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), "fetch", "-q", "--depth", "1", "origin", commit], check=False)
        subprocess.run(["git", "-C", str(dest), "checkout", "-q", commit], check=True)
        for rel, digest in files.items():
            ok = (dest / rel).exists() and sha256(dest / rel) == digest
            bad += not ok
            print(f"{name}/{rel}: {'ok' if ok else 'WRONG SHA-256 - remove third_party/' + name}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
