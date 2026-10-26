#!/usr/bin/env python3
"""Section keys against the tracker: songs it lists with several keys ("F minor;C# minor") vs songs with one key.
Uses the cached per-beat chroma (key_feat_v1, whose first row is the audio before the first beat and is dropped here) and the cached
raw Beat This! beats / downbeats (tempo eval). Reports how often a multi-key song gets several sections and how often a one-key
song is split anyway (the false-alarm rate that matters most for the display).
Run:  .venv/Scripts/python eval/key_sections.py
"""
import json
import sys

import numpy as np

from common import RESULTS, cache_path, load_labels, md_table
from key_eval import label_index

from songspec_analyzer.key.stage import sections_from_windows, window_keys


def main():
    rows = []
    for r in load_labels():
        p, b = cache_path("key_feat_v1", r["file"]), cache_path("bt_raw", r["file"]).with_suffix(".npz")
        if not (r["keys"].strip() and p.exists() and b.exists()):
            continue
        f = json.loads(p.read_text(encoding="utf-8"))
        z = np.load(b)
        beats, downs = z["beats"], z["down"]
        seq = np.asarray(f["chroma_beat_seq"], float)[1:]
        if len(seq) != len(beats) - 1 and len(seq) > 2000:     # decimated in the cache for very long songs: skip
            continue
        n = min(len(seq), len(beats))
        keys = {label_index(k) for k in r["keys"].split(";") if k.strip()}
        bar = float(np.median(np.diff(downs))) if len(downs) > 2 else 2.0
        secs = sections_from_windows(window_keys(seq[:n], beats[:n], downs), float(beats[-1]) + bar, bar)
        rows.append((r, len(keys), max(1, len(secs)), [s["k"] for s in secs]))
    multi = [x for x in rows if x[1] >= 2]
    single = [x for x in rows if x[1] == 1]
    hit = sum(x[2] >= 2 for x in multi)
    false = sum(x[2] >= 2 for x in single)
    table = [["tracker lists several keys", len(multi), hit, f"{100 * hit / max(1, len(multi)):.0f} %"],
             ["tracker lists one key", len(single), false, f"{100 * false / max(1, len(single)):.0f} %"]]
    out = ["# Section keys vs the tracker", "", md_table(["songs", "count", "split into sections by us", "share"], table), ""]
    (RESULTS / "key_sections.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
