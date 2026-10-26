#!/usr/bin/env python3
"""S-KEY (Deezer, ICASSP 2025) on every tracker-labelled catalog song; 24 key probabilities cached in eval/results/skey_v1.
Run:  .venv/Scripts/python eval/skey_feat.py"""
import json
import sys
import time

from common import cache_path, load_labels

from songspec_analyzer.audio.decode import load_audio
from songspec_analyzer.key import estimators as E
from songspec_analyzer.tempo.engine import device


def main():
    dev = device()
    rows = [r for r in load_labels() if r["keys"].strip() and r["path"].exists()]
    for i, r in enumerate(rows, 1):
        out = cache_path("skey_v1", r["file"])
        if out.exists():
            continue
        t0 = time.time()
        a = load_audio(str(r["path"]))
        p = E.skey_probs(a.mono, a.sr, dev)
        out.write_text(json.dumps({"p": [float(x) for x in p]}), encoding="utf-8")
        print(f"[{i}/{len(rows)}] {r['file'][:50]:50s} {time.time() - t0:.1f} s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
