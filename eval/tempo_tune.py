#!/usr/bin/env python3
"""Pick the steadiness thresholds (engine.MODERATE_PCT / LIVE_PCT) from the tracker's notes, honestly.

Label: the tracker's notes mention "inconsistent" / "played live" (positive) or not (negative). Feature: the largest section
drift_pct of the song (eval/results/tempo_v3, written by tempo_eval.py). The flag threshold (= MODERATE_PCT) is chosen by
2-fold cross-validation (songs split by artist, so no artist is in both halves): each half is scored with the threshold that
maximises balanced accuracy on the other half. LIVE_PCT then separates "inconsistent / played live" from "moderately inconsistent".
Run:  .venv/Scripts/python eval/tempo_tune.py
"""
import hashlib
import json
import re

import numpy as np

from common import cache_path, load_labels, md_table

INC = re.compile(r"inconsistent|played live", re.I)
MODERATE = re.compile(r"(slightly|moderately) inconsistent", re.I)


def load():
    out = []
    for r in load_labels():
        p = cache_path("tempo_v3", r["file"])
        if r["bpm_list"] and p.exists():
            o = json.loads(p.read_text(encoding="utf-8"))
            if "sections" in o:
                out.append((r, max(s["drift_pct"] for s in o["sections"])))
    return out


def balanced(th, data):
    pos = [d for r, d in data if INC.search(r["notes"])]
    neg = [d for r, d in data if not INC.search(r["notes"])]
    if not pos or not neg:
        return 0.0
    return 0.5 * (np.mean([d > th for d in pos]) + np.mean([d <= th for d in neg]))


def main():
    data = load()
    fold = lambda r: int(hashlib.sha1(r["artist"].lower().encode()).hexdigest(), 16) % 2
    grid = np.round(np.arange(0.5, 12.01, 0.25), 2)
    rows = []
    for k in (0, 1):
        train = [x for x in data if fold(x[0]) != k]
        test = [x for x in data if fold(x[0]) == k]
        th = max(grid, key=lambda t: balanced(t, train))
        rows.append([f"fold {k}", len(train), len(test), th, f"{balanced(th, train):.3f}", f"{balanced(th, test):.3f}"])
    th_all = max(grid, key=lambda t: balanced(t, data))
    pos = sorted(d for r, d in data if INC.search(r["notes"]))
    neg = sorted(d for r, d in data if not INC.search(r["notes"]))
    mod = sorted(d for r, d in data if MODERATE.search(r["notes"]))
    print(md_table(["split", "train", "test", "threshold %", "train balanced acc", "test balanced acc"], rows))
    print(f"\nall songs: best threshold {th_all} % (balanced accuracy {balanced(th_all, data):.3f}); "
          f"{len(pos)} positive, {len(neg)} negative")
    print("drift % of songs the tracker calls inconsistent:", np.round(pos, 1).tolist())
    print("... of those called slightly / moderately inconsistent:", np.round(mod, 1).tolist())
    print("negatives, percentiles 50 / 75 / 90 / 95:", np.round(np.percentile(neg, [50, 75, 90, 95]), 1).tolist())


if __name__ == "__main__":
    main()
