#!/usr/bin/env python3
"""Mode accuracy against the tracker, and the choice of modes.MODE_MARGIN (nested by artist group, see below).

Label: the tracker writes a modal key with an asterisk ("F Minor*") and names the mode in its notes ("F Phrygian - might be ...").
A song counts as modal when key_raw has '*' and the notes name a mode on the key's own tonic, NOT after "borrows from" (borrowing
is not the key's mode); a key without '*' is plain major / minor. Songs whose tonic we get wrong are left out (the mode is only
asked once the tonic is fixed).
The margin is chosen by NESTED leave-one-artist-group-out (key_eval.artist_group): each group is scored with the margin that does
best on the other four, so the reported accuracy is never tuned on the songs it is measured on.
Run:  .venv/Scripts/python eval/key_modes.py
"""
import json
import re
import sys

import numpy as np

from common import RESULTS, cache_path, load_labels, md_table
from key_eval import artist_group, label_index

from songspec_analyzer.display.key import parse_pitch
from songspec_analyzer.key.modes import choose_mode

MODE_RE = re.compile(r"(?<!borrows from )(?<!borrow from )(?<!borrows heavily from )\b([A-G][#b]?)\s+(phrygian dominant|harmonic minor|"
                     r"phrygian|dorian|dorion|mixolydian|lydian|locrian)", re.I)


def label_mode(r):
    main = r["key_raw"].split("/")[0]
    if "*" not in main:
        return "plain"
    tonic = label_index(r["keys"].split(";")[0]) % 12
    for m in MODE_RE.finditer(r["notes"] or ""):
        if parse_pitch(m.group(1)) == tonic:
            return m.group(2).lower().replace(" ", "_").replace("dorion", "dorian")
    return None                                         # modal but the mode is not named: not scored


def main():
    rows = []
    for r in load_labels():
        p = cache_path("key_feat_v1", r["file"])
        if r["keys"].strip() and p.exists():
            lab = label_mode(r)
            if lab is not None:
                rows.append((r, json.loads(p.read_text(encoding="utf-8")), lab))
    def correct(x, margin):
        r, f, lab = x
        y = label_index(r["keys"].split(";")[0])
        got, _ = choose_mode(f["chroma_beat"], y % 12, y >= 12, margin)
        return (got in ("major", "minor") and lab == "plain") or got == lab

    acc = lambda sel, m: float(np.mean([correct(x, m) for x in sel])) if sel else float("nan")
    grid = [0.0, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 9.0]
    groups = sorted({artist_group(x[0]["artist"]) for x in rows})
    nested, picks = [], []
    for g in groups:
        inner = [x for x in rows if artist_group(x[0]["artist"]) != g]
        m = max(grid, key=lambda mm: acc(inner, mm))
        picks.append(m)
        nested += [correct(x, m) for x in rows if artist_group(x[0]["artist"]) == g]
    modal = [x for x in rows if x[2] != "plain"]
    table = [[m, f"{acc(rows, m):.3f}", f"{acc(modal, m):.3f}"] for m in grid]
    best_all = max(grid, key=lambda mm: acc(rows, mm))
    out = ["# Mode accuracy (tonic given)", "",
           f"{len(rows)} songs with a usable mode label ({len(modal)} modal). Margin 9.0 = never report a mode (always plain "
           "major / minor).", "",
           md_table(["margin", "accuracy (all, in-sample)", "accuracy on the modal songs"], table), "",
           f"Nested leave-one-artist-group-out: **{np.mean(nested):.3f}** (margins chosen per group: {picks}); "
           f"never-modal baseline {acc(rows, 9.0):.3f}. Margin for the service (chosen on all songs): {best_all}.", ""]
    (RESULTS / "key_modes.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
