#!/usr/bin/env python3
"""Key v1 against the user's tracker keys (catalog songs with a tracker key; the first listed key is the main key).

Estimators on the same audio (features cached per song in eval/results/key_feat_v1, so the fusion in milestone 6 trains in seconds):
  centsible            Centsible's two-stage detector on its own tuning (the plugin's key)
  <profile>            each profile in key/profiles.py on the tuning-corrected, beat-synchronous chroma (harmonic part)
  librosa-ks           baseline: Krumhansl-Kessler on librosa's default chroma_cqt, no tuning correction, no HPSS, no beat sync
                       (the usual "autokey in 20 lines")
Metrics: MIREX weighted score (exact 1, fifth 0.5, relative 0.3, parallel 0.2; mir_eval convention), exact accuracy, and the
confusion breakdown. Keys are compared by pitch class (the tracker writes C#, F#, G# minor).
Held-out split: songs whose artist hashes to fold 0 of 5 (about 20 %): reported apart and NEVER used for tuning.
Run:  .venv/Scripts/python eval/key_eval.py [--limit N]
"""
import argparse
import hashlib
import json
import sys
import time

import numpy as np

from common import RESULTS, cache_path, load_labels, md_table

from songspec_analyzer.audio.decode import load_audio
from songspec_analyzer.display.key import parse_key
from songspec_analyzer.key import KEY_NAMES, estimators as E
from songspec_analyzer.key.profiles import PROFILES

SR = 22050


def label_index(text):
    tonic, mode = parse_key(text.strip())
    from songspec_analyzer.display.key import is_minor
    return tonic + (12 if is_minor(mode) else 0)


def held_out(artist):
    return int(hashlib.sha1(artist.strip().lower().encode()).hexdigest(), 16) % 5 == 0


def mirex(est, ref):
    """mir_eval.key.weighted_score convention for keys given as index tonic + 12 * minor."""
    if est == ref:
        return 1.0, "exact"
    et, em, rt, rm = est % 12, est >= 12, ref % 12, ref >= 12
    if em == rm and (et - rt) % 12 in (5, 7):
        return 0.5, "fifth"
    if not rm and em and (et - rt) % 12 == 9:          # reference major, estimate its relative minor
        return 0.3, "relative"
    if rm and not em and (et - rt) % 12 == 3:          # reference minor, estimate its relative major
        return 0.3, "relative"
    if et == rt and em != rm:
        return 0.2, "parallel"
    return 0.0, "other"


def features(path, beats):
    import librosa
    from songspec_analyzer.detune import _centsible as C
    a = load_audio(str(path))
    t = C.analyze_tuning(a.mono, a.sr) if hasattr(C, "analyze_tuning") else C.analyze_track(a.mono, a.sr)["tuning"]
    cents = float(t["cents"]) if t["valid"] else 0.0
    ck = C.key_probabilities(a.mono, a.sr, cents)
    y22 = librosa.resample(a.mono, orig_sr=a.sr, target_sr=SR)
    yh = librosa.effects.harmonic(y22, margin=2.0)
    Fb, wb = E.tuned_chroma(yh, cents, beats)
    Ff, wf = E.tuned_chroma(yh, cents, None)
    plain = librosa.feature.chroma_cqt(y=y22, sr=SR)                       # the naive baseline's chroma
    return {"cents": cents, "tuning_ci": t.get("ci_cents"),
            "centsible": ck["key_prob"], "centsible_mode": ck["mode_prob"], "chroma_bass": ck["chroma_bass"],
            "chroma_scale": ck["chroma_scale"],
            "chroma_beat": E.global_chroma(Fb, wb).tolist(), "chroma_frames": E.global_chroma(Ff, wf).tolist(),
            "chroma_naive": (plain.mean(axis=1) / plain.mean(axis=1).sum()).tolist(),
            "chroma_beat_seq": Fb.tolist() if len(Fb) < 2000 else Fb[:: len(Fb) // 2000 + 1].tolist()}


def cached_beats(name):
    p = cache_path("bt_raw", name).with_suffix(".npz")
    return np.load(p)["beats"] if p.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    rows = [r for r in load_labels() if r["keys"].strip() and r["path"].exists()]
    if a.limit:
        rows = rows[:a.limit]
    data = []
    for i, r in enumerate(rows, 1):
        p = cache_path("key_feat_v1", r["file"])
        if p.exists():
            f = json.loads(p.read_text(encoding="utf-8"))
        else:
            t0 = time.time()
            try:
                f = features(r["path"], cached_beats(r["file"]))
            except Exception as e:
                print(f"[{i}/{len(rows)}] {r['file'][:50]} ERROR {e}", flush=True)
                continue
            p.write_text(json.dumps(f), encoding="utf-8")
            print(f"[{i}/{len(rows)}] {r['file'][:50]:50s} {time.time() - t0:.1f} s", flush=True)
        data.append((r, f))

    def ests(f):
        out = {"centsible": np.asarray(f["centsible"])}
        g = np.asarray(f["chroma_beat"])
        for name, p in E.profile_probs(g).items():
            out[name] = p
        out["librosa-ks"] = E.profile_probs(np.asarray(f["chroma_naive"]))["krumhansl"]
        return out

    names = ["centsible"] + list(PROFILES) + ["librosa-ks"]
    scores = {n: {"all": [], "held": []} for n in names}
    conf = {n: {} for n in names}
    for r, f in data:
        ref = label_index(r["keys"].split(";")[0])
        for n, p in ests(f).items():
            s, kind = mirex(int(np.argmax(p)), ref)
            scores[n]["all"].append(s)
            if held_out(r["artist"]):
                scores[n]["held"].append(s)
            conf[n][kind] = conf[n].get(kind, 0) + 1
    table = []
    for n in names:
        al, he = np.array(scores[n]["all"]), np.array(scores[n]["held"])
        c = conf[n]
        table.append([n, len(al), f"{al.mean():.3f}", f"{100 * np.mean(al == 1):.1f} %",
                      f"{he.mean():.3f}" if len(he) else "-", f"{100 * np.mean(he == 1):.1f} %" if len(he) else "-",
                      c.get("fifth", 0), c.get("relative", 0), c.get("parallel", 0), c.get("other", 0)])
    table.sort(key=lambda r: -float(r[2]))
    n_held = sum(held_out(r["artist"]) for r, _ in data)
    out = [f"# Key v1 vs tracker keys ({time.strftime('%Y-%m-%d')})", "",
           f"{len(data)} catalog songs with a tracker key; held-out split (artist fold 0 of 5): {n_held} songs.", "",
           md_table(["estimator", "songs", "MIREX weighted (all)", "exact (all)", "MIREX (held-out)", "exact (held-out)",
                     "fifth", "relative", "parallel", "other"], table), ""]
    (RESULTS / "key_summary.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
