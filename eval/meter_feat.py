#!/usr/bin/env python3
"""Features for telling 6/8 (and 12/8) from 4/4 / 3/4, per catalog song, cached in eval/results/meter_v1.

From the raw Beat This! beats (bt_raw cache) and the song's onset envelope (librosa onset_strength, 22.05 kHz, hop 128):
  per_bar      the tracker's beats per downbeat (mode and its share)
  sub_ratio    median over beats of log(onset at 1/3 and 2/3 of the beat / onset at 1/2 of the beat): > 0 = triplet feel
  sub_ratio_2  the same measured over pairs of beats (a tracker on the eighth-note pulse of 6/8 puts 3 beats in a dotted quarter)
plus the tracker's labels: is the song 6/8 / 12/8 in the notes, its BPMs.
Run:  .venv/Scripts/python eval/meter_feat.py
"""
import json
import re
import sys

import numpy as np

from common import RESULTS, cache_path, load_labels

from songspec_analyzer.audio.decode import load_audio

SR, HOP = 22050, 128
COMPOUND = re.compile(r"\b(6/8|12/8|9/8)\b")


def env_at(env, t):
    """Max of the onset envelope within +-1 frame of time t."""
    f = int(round(t * SR / HOP))
    lo, hi = max(0, f - 1), min(len(env), f + 2)
    return float(env[lo:hi].max()) if hi > lo else 0.0


def sub_ratio(env, beats, step=1):
    """Triplet vs straight feel inside each span of `step` beats."""
    r = []
    for a, b in zip(beats[:-step:step], beats[step::step]):
        d = b - a
        tri = 0.5 * (env_at(env, a + d / 3) + env_at(env, a + 2 * d / 3))
        half = env_at(env, a + d / 2)
        r.append(np.log((tri + 1e-3) / (half + 1e-3)))
    return float(np.median(r)) if r else 0.0


def features(r):
    import librosa
    z = np.load(cache_path("bt_raw", r["file"]).with_suffix(".npz"))
    beats, down = np.asarray(z["beats"], float), np.asarray(z["down"], float)
    a = load_audio(str(r["path"]))
    y = librosa.resample(a.mono, orig_sr=a.sr, target_sr=SR)
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    env = env / (np.percentile(env, 99) + 1e-9)
    out = {"period": float(np.median(np.diff(beats))), "sub_ratio": sub_ratio(env, beats), "sub_ratio_2": sub_ratio(env, beats, 2),
           "sub_ratio_3": sub_ratio(env, beats, 3)}
    if len(down) >= 6:
        tol = 0.3 * out["period"]
        counts = np.diff(np.searchsorted(beats, down - tol))
        vals, c = np.unique(counts, return_counts=True)
        out["per_bar"] = int(vals[np.argmax(c)])
        out["per_bar_share"] = float(c.max() / len(counts))
        out["bar"] = float(np.median(np.diff(down)))
    return out


def main():
    rows = [r for r in load_labels() if r["path"].exists() and cache_path("bt_raw", r["file"]).with_suffix(".npz").exists()]
    for i, r in enumerate(rows, 1):
        p = cache_path("meter_v1", r["file"])
        if p.exists():
            continue
        f = features(r)
        f.update(compound=bool(COMPOUND.search(r["notes"] or "")), bpms=r["bpm_list"], song=r["song"])
        p.write_text(json.dumps(f), encoding="utf-8")
        print(f"[{i}/{len(rows)}] {r['song'][:30]:30s} compound={f['compound']!s:5s} per_bar={f.get('per_bar')} sub={f['sub_ratio']:+.2f} "
              f"sub2={f['sub_ratio_2']:+.2f}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
