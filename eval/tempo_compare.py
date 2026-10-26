#!/usr/bin/env python3
"""Per-song: where does the service's BPM differ from Centsible's, and which one matches the tracker? (reads tempo_eval.py caches)"""
import json

import numpy as np

from common import cache_path, load_labels, md_table

rows = [r for r in load_labels() if r["bpm_list"] and r["path"].exists()]
cent = json.loads(cache_path("centsible_bpm", "|".join(sorted(r["file"] for r in rows))).read_text(encoding="utf-8"))
ok = lambda v, r: v is not None and any(abs(v - t) / t <= 0.01 for t in r["bpm_list"])
table, counts = [], {"both": 0, "service only": 0, "centsible only": 0, "neither": 0}
for r in rows:
    o = json.loads(cache_path("tempo_v3", r["file"]).read_text(encoding="utf-8"))
    s, c = o.get("bpm"), cent.get(r["file"])
    k = "both" if ok(s, r) and ok(c, r) else "service only" if ok(s, r) else "centsible only" if ok(c, r) else "neither"
    counts[k] += 1
    if k in ("centsible only", "service only"):
        table.append([k, r["file"][:50], r["bpms"][:20], o.get("display"), f"{c:.2f}" if c else "-",
                      f"{s / c:.3f}" if s and c else "-", o.get("sig")])
print(counts)
print(md_table(["who is right", "file", "tracker", "service", "centsible", "service / centsible", "sig"], sorted(table)))
