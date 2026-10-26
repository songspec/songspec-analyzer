#!/usr/bin/env python3
"""Parity with the SHIPPED BPM Fixer: run the installed bpmfix-cli.exe and the service's tempo engine on the same songs and compare
the beat grids (the exe's @@RESULT curve times are its anchors). Both use Beat This! on the CPU (set here), signature Auto.

The exe also time-stretches (that is its job), so this takes a few minutes per song; its output files go to a temp folder and
are deleted. Run:  .venv/Scripts/python eval/tempo_parity.py [--limit 8]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["SONGSPEC_DEVICE"] = "cpu"

import numpy as np

from common import RESULTS, cached, load_labels, md_table

from songspec_analyzer.audio.decode import load_audio
from songspec_analyzer.config import Settings
from songspec_analyzer.tempo import engine

EXE = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "BPM Fixer" / "bpmfix-cli.exe"


def run_exe(path):
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([str(EXE), "--cli", str(path), "--machine", "--no-verify", "--signature", "Auto",
                            "--format", "WAV 16-bit", "--out-dir", td], capture_output=True, text=True, timeout=3600,
                           encoding="utf-8", errors="replace")
    for line in r.stdout.splitlines():
        if line.startswith("@@RESULT "):
            d = json.loads(line[9:])
            return {"t": d["curve"]["t"], "signature": d["signature"], "target": d["target"]}
    return {"error": (r.stdout + r.stderr)[-400:]}


def run_ours(path):
    a = load_audio(str(path))
    r = engine.analyze(a.mono, a.sr, Settings().models_dir)
    return {"beats": r["beats"], "signature": r["signature"], "switches": len(r["sections"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    a = ap.parse_args()
    if not EXE.exists():
        print(f"BPM Fixer not installed at {EXE}")
        return 1
    rows = [r for r in load_labels() if r["path"].exists()][:a.limit]
    table, exact = [], 0
    for i, r in enumerate(rows, 1):
        ex = cached("parity_exe", r["file"], lambda: run_exe(r["path"]))
        ours = cached("parity_ours", r["file"], lambda: run_ours(r["path"]))
        if "error" in ex:
            table.append([r["file"][:50], "exe error", "", "", ""])
            continue
        t_exe = np.array(ex["t"])
        t_us = np.round(np.array(ours["beats"][:-1]), 3)
        n = min(len(t_exe), len(t_us))
        same_len = len(t_exe) == len(t_us)
        dmax = float(np.max(np.abs(t_exe[:n] - t_us[:n]))) if n else float("nan")
        ok = same_len and dmax <= 0.0015 and ex["signature"] == ours["signature"]
        exact += ok
        table.append([r["file"][:50], f"{ex['signature']} / {ours['signature']}", f"{len(t_exe)} / {len(t_us)}",
                      f"{dmax * 1000:.1f} ms", "same" if ok else "DIFFERENT"])
        print(f"[{i}/{len(rows)}] {table[-1]}", flush=True)
    out = ["# Parity with the installed BPM Fixer (bpmfix-cli.exe)", "",
           f"{exact} / {len(table)} songs give the same beat grid (same count, every beat within 1.5 ms, same signature).", "",
           md_table(["file", "signature exe / ours", "beats exe / ours", "max difference", "verdict"], table), ""]
    (RESULTS / "tempo_parity.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out[:3]))


if __name__ == "__main__":
    sys.exit(main())
