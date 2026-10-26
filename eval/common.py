"""Shared helpers for the evaluation scripts: where the user's catalog lives, label loading, result caching.

The catalog is the user's own audio and stays where it is (default C:\\dev\\detunetest\\testdata, override in eval/local.toml:
    catalog = "D:/music/testdata"
or with SONGSPEC_CATALOG). Nothing from it is copied into this repo; per-song results go to eval/results/ (git-ignored).
"""
import csv
import hashlib
import json
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"
sys.path.insert(0, str(ROOT / "service"))
sys.path.insert(0, str(ROOT / "service" / "tests"))


def catalog_dir() -> Path:
    if os.environ.get("SONGSPEC_CATALOG"):
        return Path(os.environ["SONGSPEC_CATALOG"])
    local = ROOT / "eval" / "local.toml"
    if local.exists():
        return Path(tomllib.loads(local.read_text(encoding="utf-8"))["catalog"])
    return Path(r"C:\dev\detunetest\testdata")


def load_labels():
    """key_labels.csv rows: file, artist, song, keys ('F minor;C# minor'), bpms ('159.5;80.0'), validity, tier, key_raw, notes."""
    rows = []
    with open(catalog_dir() / "key_labels.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["path"] = catalog_dir() / "real" / r["file"]
            r["bpm_list"] = [float(b) for b in r["bpms"].split(";") if b.strip()] if r["bpms"] else []
            rows.append(r)
    return rows


def cache_path(kind, name):
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    p = RESULTS / kind / f"{h}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def cached(kind, name, fn, refresh=False):
    p = cache_path(kind, name)
    if p.exists() and not refresh:
        return json.loads(p.read_text(encoding="utf-8"))
    out = fn()
    p.write_text(json.dumps(out), encoding="utf-8")
    return out


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)
