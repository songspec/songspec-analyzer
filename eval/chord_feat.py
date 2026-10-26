#!/usr/bin/env python3
"""Run the chord engine on every tracker-labelled catalog song and cache the segments (eval/results/chords_v1), for the chord-based key
features. Reuses the cached raw Beat This! output (tempo eval) and the tuning from the key features (key eval). Gentle mode.
Run:  .venv/Scripts/python eval/chord_feat.py"""
import json
import sys
import time

import numpy as np

from common import cache_path, load_labels

from songspec_analyzer.audio.decode import load_audio
from songspec_analyzer.chords.engine import recognise


def main():
    rows = [r for r in load_labels() if r["keys"].strip() and r["path"].exists()]
    for i, r in enumerate(rows, 1):
        out = cache_path("chords_v1", r["file"])
        kf, bt = cache_path("key_feat_v1", r["file"]), cache_path("bt_raw", r["file"]).with_suffix(".npz")
        if out.exists() or not (kf.exists() and bt.exists()):
            continue
        t0 = time.time()
        import librosa
        a = load_audio(str(r["path"]))
        yh = librosa.effects.harmonic(librosa.resample(a.mono, orig_sr=a.sr, target_sr=22050), margin=2.0)
        z = np.load(bt)
        cents = json.loads(kf.read_text(encoding="utf-8"))["cents"]
        segs = recognise(yh, cents, z["beats"], z["down"], a.duration)
        out.write_text(json.dumps({"duration": a.duration, "segments": segs}), encoding="utf-8")
        print(f"[{i}/{len(rows)}] {r['file'][:50]:50s} {len(segs)} segments  {time.time() - t0:.1f} s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
