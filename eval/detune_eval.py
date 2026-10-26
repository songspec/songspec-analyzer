#!/usr/bin/env python3
"""Detune accuracy on exactly shifted real music (PLAN.md section 5: MAE < 2 cents).

For N catalog songs (a 60 s excerpt from 0:45), every estimator reads the excerpt and copies of it shifted by
-49 ... +49 cents in two ways:
  resample    exact resampling by 2^(c/1200) with soxr VHQ (pitch AND tempo move, like a sped-up record; exact pitch)
  rubberband  Rubber Band R3 pitch shift, tempo kept (rubberband-r3.exe from the installed BPM Fixer), as the brief asks
Error = (reading of the shifted copy - reading of the original) - shift, wrapped to the 100-cent circle. Real songs have no known
absolute tuning (Centsible REPORT.md: the user's pitching tool adds 1.4 - 2.3 cents beyond its labels), so this measures what can be
measured exactly: whether every shift is recovered. Estimators: centsible, chroma120, librosa, and the service's fusion.
Run:  .venv/Scripts/python eval/detune_eval.py [--songs 12]
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from common import CREATIONFLAGS, RESULTS, cached, load_labels, md_table

from songspec_analyzer.audio.decode import load_audio
from songspec_analyzer.detune import estimators as est
from songspec_analyzer.detune import measure

SHIFTS = [-49, -37, -25, -12, -3, 3, 12, 25, 37, 49]
RB = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "BPM Fixer" / "_internal" / "rubberband-r3.exe"
NAMES = ["centsible", "chroma120", "librosa", "fused"]


def excerpt(path, start=45.0, length=60.0):
    a = load_audio(str(path))
    y = a.mono
    i = int(start * a.sr)
    if len(y) < i + int(20 * a.sr):
        i = 0
    return y[i:i + int(length * a.sr)], a.sr


def resample_shift(y, sr, cents):
    import soxr
    ratio = 2.0 ** (cents / 1200.0)
    # play the samples faster by `ratio`: same samples, read at sr*ratio, written back at sr
    return soxr.resample(y, sr * ratio, sr, quality="VHQ").astype(np.float32)


def rubberband_shift(y, sr, cents):
    with tempfile.TemporaryDirectory() as td:
        i, o = Path(td) / "i.wav", Path(td) / "o.wav"
        sf.write(i, y, sr, subtype="FLOAT")
        subprocess.run([str(RB), "-3", "-p", f"{cents / 100.0:.6f}", str(i), str(o)], capture_output=True, check=True,
                       creationflags=CREATIONFLAGS)
        z, _ = sf.read(o, dtype="float32")
        return z if z.ndim == 1 else z.mean(axis=1)


def readings(y, sr):
    m = measure(y, sr)
    v = m["votes"]
    return {"centsible": v.get("centsible", (None,))[0], "chroma120": v.get("chroma120", (None,))[0],
            "librosa": v.get("librosa", (None,))[0], "fused": m["cents"], "ci": m["ci"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", type=int, default=12)
    a = ap.parse_args()
    rows = [r for r in load_labels() if r["path"].exists() and r["path"].suffix.lower() in (".flac", ".wav")]
    step = max(1, len(rows) // a.songs)
    rows = rows[::step][:a.songs]
    methods = {"resample": resample_shift}
    if RB.exists():
        methods["rubberband"] = rubberband_shift
    errs = {(m, n): [] for m in methods for n in NAMES}
    per_song = []
    for k, r in enumerate(rows, 1):
        y, sr = excerpt(r["path"])
        base = cached("detune_base", r["file"], lambda: readings(y, sr))
        worst = 0.0
        for mname, fn in methods.items():
            for c in SHIFTS:
                rd = cached(f"detune_{mname}", f"{r['file']}|{c}", lambda: readings(fn(y, sr, c), sr))
                for n in NAMES:
                    if rd.get(n) is not None and base.get(n) is not None:
                        e = float(est.wrap(rd[n] - base[n] - c))
                        errs[(mname, n)].append(abs(e))
                        if n == "fused":
                            worst = max(worst, abs(e))
        per_song.append([r["file"][:50], f"{base['fused']:+.2f}" if base["fused"] is not None else "-", f"{base['ci']:.2f}"
                         if base.get("ci") else "-", f"{worst:.2f}"])
        print(f"[{k}/{len(rows)}] {r['file'][:50]:50s} base {base['fused']}  worst fused error {worst:.2f}", flush=True)

    table = []
    for (m, n), e in errs.items():
        if not e:
            continue
        e = np.array(e)
        table.append([m, n, len(e), f"{e.mean():.3f}", f"{np.median(e):.3f}", f"{np.percentile(e, 95):.3f}", f"{e.max():.2f}",
                      f"{100 * np.mean(e <= 0.5):.1f} %", f"{100 * np.mean(e <= 2.0):.1f} %"])
    out = ["# Detune on exactly shifted real music", "",
           f"{len(rows)} catalog songs (60 s excerpts), shifts {SHIFTS} cents. Error = shifted reading - original reading - shift.", "",
           md_table(["shift method", "estimator", "cases", "MAE (cents)", "median", "p95", "max", "within 0.5", "within 2"], table), "",
           "## Per song (fused)", "", md_table(["file", "original reading", "95 % interval", "worst error over all shifts"], per_song), ""]
    (RESULTS / "detune_summary.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out[:12]))


if __name__ == "__main__":
    sys.exit(main())
