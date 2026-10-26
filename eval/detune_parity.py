#!/usr/bin/env python3
"""Parity: the service's Centsible binding vs the plugin's own command-line analyzer (centsible_cli.exe, the same DSP as the VST3)
on the same files. Requirement (PLAN.md): |difference| <= 0.5 cents; expected: float noise only for lossless files.

MP3 / M4A files are decoded by different decoders (JUCE in the CLI, libsndfile / ffmpeg in the service), so they are listed apart.
Run:  .venv/Scripts/python eval/detune_parity.py [--limit 30]
"""
import argparse
import csv
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from common import CLI_JOBS, CREATIONFLAGS, RESULTS, ROOT, cached, load_labels, md_table

from songspec_analyzer.audio.decode import load_audio
from songspec_analyzer.detune import _centsible

CLI = Path(r"C:\cb\cli\centsible_cli_artefacts\Release\centsible_cli.exe")
PACK = ROOT / "Everything Claude Needs" / "Centsible Source"
wrap = lambda c: c - 100.0 * np.floor((c + 50.0) / 100.0)


def cli_batch(paths):
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "c.csv"
        subprocess.run([str(CLI), "--jobs", CLI_JOBS, "--csv", str(out)] + [str(p) for p in paths], capture_output=True, timeout=7200,
                       creationflags=CREATIONFLAGS)
        rows = {}
        with open(out, encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                rows[Path(r["file"]).name] = r
        return rows


def ours(path):
    a = load_audio(str(path))
    r = _centsible.analyze_track(a.mono, a.sr)
    return {"cents": r["tuning"]["cents"], "ci": r["tuning"]["ci_cents"], "key": r["key"].get("label"), "bpm": r["tempo"]["bpm"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    a = ap.parse_args()
    files = sorted((PACK / "reference-pack").glob("*.wav")) + sorted((PACK / "test-tones").glob("*.wav"))
    files += [r["path"] for r in load_labels() if r["path"].exists()][:a.limit]
    cli = cached("centsible_cli_parity", "|".join(p.name for p in files), lambda: cli_batch(files))
    table, diffs, lossy = [], [], []
    for p in files:
        c = cli.get(p.name)
        o = cached("centsible_binding", p.name, lambda: ours(p))
        if not c or not c.get("cents"):
            table.append([p.name[:50], "no CLI row", f"{o['cents']:+.2f}", "", "", ""])
            continue
        d = float(wrap(o["cents"] - float(c["cents"])))
        is_lossy = p.suffix.lower() in (".mp3", ".m4a", ".aac", ".ogg")
        (lossy if is_lossy else diffs).append(abs(d))
        key_same = (c.get("key") or "").split(" (")[0].lower() == (o["key"] or "").split(" (")[0].lower()
        table.append([p.name[:50], f"{float(c['cents']):+.2f}", f"{o['cents']:+.2f}", f"{d:+.3f}",
                      "same" if key_same else f"{c.get('key')} / {o['key']}", f"{float(c['bpm']):.1f} / {o['bpm']:.1f}"])
    out = ["# Detune parity: service binding vs centsible_cli.exe", "",
           f"Lossless files: {len(diffs)}, max |difference| {max(diffs):.3f} cents, mean {np.mean(diffs):.4f}." if diffs else "",
           f"Lossy files (different decoders): {len(lossy)}, max {max(lossy):.3f}, mean {np.mean(lossy):.4f}." if lossy else "", "",
           md_table(["file", "CLI cents", "service cents", "difference", "key", "BPM CLI / service"], table), ""]
    (RESULTS / "detune_parity.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out[:4]))


if __name__ == "__main__":
    sys.exit(main())
