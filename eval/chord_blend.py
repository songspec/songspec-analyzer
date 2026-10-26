#!/usr/bin/env python3
"""BTC + template chord evidence: how much BTC to blend in (engine.BTC_WEIGHT).

There are no chord labels for the catalog, so the yardstick is a PROXY: the key derived from the recognised chords (key/chordkey.py,
fixed weights) against the tracker's key. Better chords should give a better chord-derived key. Evidence per song (templates and BTC,
per beat) is cached in eval/results/chord_ev_v1/*.npz, so the sweep itself takes seconds.
Run:  .venv/Scripts/python eval/chord_blend.py
"""
import json
import sys
import time

import numpy as np

from common import RESULTS, cache_path, load_labels, md_table
from key_eval import label_index, mirex

from songspec_analyzer.audio.decode import load_audio
from songspec_analyzer.chords import btc
from songspec_analyzer.chords.engine import btc_beat_logp, decode, evidence
from songspec_analyzer.chords.vocab import labels
from songspec_analyzer.key.chordkey import chord_features
from songspec_analyzer.tempo.engine import device

WEIGHTS = [0.0, 0.25, 0.5, 0.75, 0.9, 1.0]


def build(r):
    import librosa
    a = load_audio(str(r["path"]))
    y22 = librosa.resample(a.mono, orig_sr=a.sr, target_sr=22050)
    yh = librosa.effects.harmonic(y22, margin=2.0)
    z = np.load(cache_path("bt_raw", r["file"]).with_suffix(".npz"))
    cents = json.loads(cache_path("key_feat_v1", r["file"]).read_text(encoding="utf-8"))["cents"]
    ev = evidence(yh, cents, z["beats"], z["down"], a.duration)
    t, P = btc.frame_probs(y22, cents, device())
    bl = btc_beat_logp(t, P, ev["starts"], ev["end"])
    return {"logp": ev["logp"].astype(np.float32), "bb": ev["bb"].astype(np.float32), "starts": ev["starts"],
            "end": np.array([ev["end"]]), "is_down": ev["is_down"], "btc": bl.astype(np.float32), "duration": np.array([a.duration])}


def chord_key(segs, duration):
    F = chord_features(segs, duration)
    return int(np.argmax(F[:, 0] + F[:, 1] + 0.5 * F[:, 3] + 0.25 * F[:, 2] + 0.1 * F[:, 4]))


def main():
    rows = [r for r in load_labels() if r["keys"].strip() and r["path"].exists()
            and cache_path("bt_raw", r["file"]).with_suffix(".npz").exists() and cache_path("key_feat_v1", r["file"]).exists()]
    data = []
    for i, r in enumerate(rows, 1):
        p = cache_path("chord_ev_v1", r["file"]).with_suffix(".npz")
        if not p.exists():
            t0 = time.time()
            np.savez_compressed(p, **build(r))
            print(f"[{i}/{len(rows)}] {r['file'][:50]:50s} {time.time() - t0:.1f} s", flush=True)
        data.append((r, dict(np.load(p))))
    labs = labels("full")
    table = []
    for w in WEIGHTS:
        sc, changes = [], []
        for r, z in data:
            ev = {"labs": labs, "logp": z["logp"].astype(float), "bb": z["bb"], "starts": z["starts"], "end": float(z["end"][0]),
                  "is_down": z["is_down"]}
            segs = decode(ev, z["btc"].astype(float), w)
            sc.append(mirex(chord_key(segs, float(z["duration"][0])), label_index(r["keys"].split(";")[0]))[0])
            changes.append(len(segs) / max(1e-6, float(z["duration"][0])) * 60)
        sc = np.array(sc)
        table.append([w, len(sc), f"{sc.mean():.3f}", f"{100 * np.mean(sc == 1):.1f} %", f"{np.median(changes):.0f}"])
        print(table[-1], flush=True)
    out = ["# Chord evidence blend (proxy: chord-derived key vs the tracker's key)", "",
           md_table(["BTC weight", "songs", "chord-key MIREX", "chord-key exact", "chord changes / minute (median)"], table), ""]
    (RESULTS / "chord_blend.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
