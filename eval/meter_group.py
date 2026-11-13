#!/usr/bin/env python3
"""Beat-level grouping features for 6/8 / 3/4, per catalog song, cached in eval/results/meter_v2.

The beat tracker's bar lines are unreliable on 6/8 songs (Are We Still Friends: 4 per bar in half its bars), but the music
repeats every 3 (or 6, 12) beats either way. From the raw Beat This! beats (bt_raw cache) and the audio, sampled on that beat grid:
  on_ac[L]    autocorrelation of the onset strength at each beat, at a lag of L beats
  bass_ac[L]  the same for low-band (< 150 Hz) onsets: kick and bass, the clearest marker of the bar
  ch_sim[L]   mean cosine similarity of the beat chroma L beats apart (harmonic rhythm)
for L in 2, 3, 4, 6, 8, 12. Labels: the tracker's notes (strict: the song as a whole is in 6/8 / 12/8; partial mentions are kept
apart as "partial").
Run:  .venv/Scripts/python eval/meter_group.py      (then eval/meter_group.py --report)
"""
import json
import re
import sys

import numpy as np

from common import cache_path, load_labels

SR, HOP = 22050, 128
LAGS = [2, 3, 4, 6, 8, 12]
STRICT = re.compile(r"(song is in|song (is )?played in|played in|main song.*played in)\s+(6/8|12/8)", re.I)
ANY = re.compile(r"\b(6/8|12/8|9/8)\b")


def label(r):
    n = r["notes"] or ""
    if STRICT.search(n) and not re.search(r"(intro|outro) (is )?(played )?in (6/8|12/8)", n, re.I):
        return "compound"
    return "partial" if ANY.search(n) else "simple"


def features(path, beats):
    import librosa
    from songspec_analyzer.audio.decode import load_audio
    a = load_audio(str(path))
    y = librosa.resample(a.mono, orig_sr=a.sr, target_sr=SR)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)
    env = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S, ref=np.max), sr=SR, hop_length=HOP)
    low = np.log1p(S[freqs < 150].sum(0))
    low_on = np.maximum(0, np.diff(low, prepend=low[0]))
    chroma = librosa.feature.chroma_stft(S=S ** 2, sr=SR, hop_length=HOP)
    fr = librosa.time_to_frames(beats, sr=SR, hop_length=HOP)
    fr = fr[(fr > 1) & (fr < min(len(env), chroma.shape[1]) - 2)]

    def at(x, f):
        return float(x[f - 1:f + 2].max())
    on = np.array([at(env, f) for f in fr])
    lo = np.array([at(low_on, f) for f in fr])
    ch = librosa.util.sync(chroma, fr, aggregate=np.mean).T[1:-1]
    ch = ch / (np.linalg.norm(ch, axis=1, keepdims=True) + 1e-9)
    return {"on_ac": ac(on), "bass_ac": ac(lo), "ch_sim": {L: float(np.mean(np.sum(ch[:-L] * ch[L:], 1))) for L in LAGS}}


def ac(x):
    x = (x - x.mean()) / (x.std() + 1e-9)
    return {L: float(np.mean(x[:-L] * x[L:])) for L in LAGS}


def main():
    rows = [r for r in load_labels() if r["path"].exists() and cache_path("bt_raw", r["file"]).with_suffix(".npz").exists()]
    seen = set()
    for i, r in enumerate(rows, 1):
        if r["file"] in seen:
            continue
        seen.add(r["file"])
        p = cache_path("meter_v2", r["file"])
        if p.exists():
            continue
        z = np.load(cache_path("bt_raw", r["file"]).with_suffix(".npz"))
        f = features(r["path"], np.asarray(z["beats"], float))
        beats, down = np.asarray(z["beats"], float), np.asarray(z["down"], float)
        if len(down) >= 6:
            counts = np.diff(np.searchsorted(beats, down - 0.3 * np.median(np.diff(beats))))
            f["per_bar"] = {int(v): int(c) for v, c in zip(*np.unique(counts, return_counts=True))}
        f.update(label=label(r), bpms=r["bpm_list"], song=r["song"], period=float(np.median(np.diff(beats))))
        p.write_text(json.dumps(f), encoding="utf-8")
        print(f"[{i}/{len(rows)}] {r['song'][:30]:30s} {f['label']:8s}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
