"""Fused key stage: replaces the quick key with the fusion of every estimator, the mode, section keys and modulations.

Uses key/fusion_model.json (trained by eval/key_fusion.py, installed only when it beat the best single estimator on the held-out
songs). Without it, the quick key stays and this stage only adds modes and sections.
"""
import json
from pathlib import Path

import numpy as np

from ..display.key import FLAT_NAMES, camelot, house_key, key_display, open_key, sections_display
from . import KEY_NAMES, estimators as E
from .fusion import KeyFusion, candidate_features
from .modes import centsible_mode, choose_mode

MODEL_PATH = Path(__file__).with_name("fusion_model.json")
AMBIGUOUS_P = 0.45            # below this the key is shown as "ambiguous between A and B"
WINDOW_BARS, HOP_BARS = 8, 4
CHANGE_PENALTY = 24.0         # nats to switch key between windows: 3 % false splits on one-key songs (eval/REPORT.md 3.4)
MIN_SECTION_BARS = 12        # a final-chorus lift often lasts 8 - 16 bars; not tuned (no section-labelled songs yet)


def load_model():
    if MODEL_PATH.exists():
        return KeyFusion.from_dict(json.loads(MODEL_PATH.read_text(encoding="utf-8")))
    return None


def _mode_name(m):
    return m.replace("_", " ").title()


def window_keys(seq, beat_times, downbeats):
    """Viterbi over 8-bar windows (hop 4 bars) of the per-beat chroma -> [(start, end, key index)] with a change penalty."""
    downs = [d for d in downbeats if d >= beat_times[0]]
    if len(downs) < WINDOW_BARS + 2:
        return []
    wins = []
    for i in range(0, len(downs) - WINDOW_BARS, HOP_BARS):
        a, z = downs[i], downs[i + WINDOW_BARS]
        sel = (beat_times >= a) & (beat_times < z)
        if sel.sum() < 8:
            continue
        g = E.global_chroma(seq[sel], np.ones(int(sel.sum())))
        pp = E.profile_probs(g)
        lp = 0.5 * (np.log(pp["temperley"] + 1e-6) + np.log(pp["albrecht_shanahan"] + 1e-6))
        wins.append((a, z, lp))
    if not wins:
        return []
    K = 24
    delta = wins[0][2].copy()
    back = []
    for _, _, lp in wins[1:]:
        best = int(np.argmax(delta))
        move = delta[best] - CHANGE_PENALTY
        take = move > delta
        back.append(np.where(take, best, np.arange(K)))
        delta = np.where(take, move, delta) + lp
    path = [int(np.argmax(delta))]
    for b in reversed(back):
        path.append(int(b[path[-1]]))
    path = path[::-1]
    return [(w[0], w[1], k) for w, k in zip(wins, path)]


def sections_from_windows(wk, duration, bar_len):
    """Merge windows into sections, drop sections shorter than MIN_SECTION_BARS (they join a neighbour)."""
    if not wk:
        return []
    secs = []
    for a, z, k in wk:
        if secs and secs[-1]["k"] == k:
            secs[-1]["end"] = z
        else:
            if secs:
                secs[-1]["end"] = a + (secs[-1]["end"] - a) / 2 if secs[-1]["end"] > a else secs[-1]["end"]
            secs.append({"start": a, "end": z, "k": k})
    secs[0]["start"] = 0.0
    secs[-1]["end"] = duration
    for i in range(1, len(secs)):
        secs[i]["start"] = secs[i - 1]["end"]
    changed = True
    while changed and len(secs) > 1:
        changed = False
        for i, s in enumerate(secs):
            if (s["end"] - s["start"]) < MIN_SECTION_BARS * bar_len:
                j = i - 1 if i > 0 else i + 1
                secs[j]["start"] = min(secs[j]["start"], s["start"])
                secs[j]["end"] = max(secs[j]["end"], s["end"])
                secs.pop(i)
                changed = True
                break
        merged = []
        for s in secs:
            if merged and merged[-1]["k"] == s["k"]:
                merged[-1]["end"] = s["end"]
            else:
                merged.append(s)
        secs = merged
    return secs


def modulation_kind(k1, k2):
    if (k1 >= 12) != (k2 >= 12):
        return "mode change" if k1 % 12 == k2 % 12 else "key change"
    d = (k2 - k1) % 12
    return {1: "half-step lift", 2: "whole-step lift", 11: "half-step drop", 10: "whole-step drop"}.get(d, "key change")


def stage(ctx):
    import librosa
    a = ctx.audio
    tempo = ctx.data.get("tempo")
    ck = ctx.data.get("centsible_key")
    cents = ctx.data.get("tuning_cents") or 0.0
    if ck is None:
        from ..pipeline import StageSkipped
        raise StageSkipped("No key could be read from this file.")
    yh = ctx.data.get("harmonic_full")
    if yh is None:
        yh = librosa.effects.harmonic(librosa.resample(a.mono, orig_sr=a.sr, target_sr=22050), margin=2.0)
        ctx.data["harmonic_full"] = yh
    beats = np.asarray(tempo["beats"]) if tempo else None
    seq, w = E.tuned_chroma(yh, cents, beats)
    g = E.global_chroma(seq, w)
    y22 = librosa.resample(a.mono, orig_sr=a.sr, target_sr=22050)
    est = E.estimator_pool(ck["key_prob"], g, ck["chroma_scale"], E.naive_chroma(y22))
    model = load_model()
    prev = ctx.store.get(ctx.aid)["result"].get("key") or {}
    if model is not None:
        p = model.predict_proba(est, candidate_features(ck["chroma_scale"], ck["chroma_bass"], seq))
        source = "fusion"
    else:
        p = est["centsible"]
        source = "centsible (no fusion model installed)"
    order = np.argsort(-p)[:3]
    top = int(order[0])
    tonic, minor = top % 12, top >= 12
    mode = centsible_mode(ck, tonic, minor)
    cands = [{"key": KEY_NAMES[int(i)], "p": round(float(p[i]), 3)} for i in order]
    ambiguous = float(p[top]) < AMBIGUOUS_P
    kd = key_display(tonic, mode)
    display = {"key": kd["key"], "note": kd["note"],
               "ambiguity_text": f"ambiguous between {cands[0]['key']} and {cands[1]['key']}" if ambiguous else None}
    sections, modulations = [], []
    if tempo and len(tempo["downbeats"]) > WINDOW_BARS + 2:
        bar = float(np.median(np.diff(tempo["downbeats"])))
        n = min(len(beats), len(seq))
        wk = window_keys(seq[:n], beats[:n], np.asarray(tempo["downbeats"]))
        secs = sections_from_windows(wk, a.duration, bar)
        if len(secs) >= 2:
            for s in secs:
                sel = (beats[:n] >= s["start"]) & (beats[:n] < s["end"])
                gs = E.global_chroma(seq[:n][sel], np.ones(int(sel.sum()))) if sel.sum() >= 8 else g
                m, _ = choose_mode(gs, s["k"] % 12, s["k"] >= 12)
                s["mode"] = m
            sd = sections_display([{"start": s["start"], "end": s["end"], "tonic": s["k"] % 12, "mode": s["mode"]} for s in secs])
            display = {"key": sd["key"], "note": sd["note"], "ambiguity_text": display["ambiguity_text"]}
            for s1, s2 in zip(secs, secs[1:]):
                modulations.append({"t": round(s2["start"], 2), "from": house_key(s1["k"] % 12, s1["mode"]),
                                    "to": house_key(s2["k"] % 12, s2["mode"]), "kind": modulation_kind(s1["k"], s2["k"])})
            sections = [{"start": round(s["start"], 2), "end": round(s["end"], 2),
                         "key": f"{FLAT_NAMES[s['k'] % 12]} {_mode_name(s['mode'])}", "display": house_key(s["k"] % 12, s["mode"])}
                        for s in secs]
    estimators = dict(prev.get("estimators") or {})
    for n in ("temperley2005@scale", "albrecht_shanahan@scale", "albrecht_shanahan@beat", "krumhansl@beat", "edma@naive"):
        i = int(np.argmax(est[n]))
        estimators[n] = {"key": KEY_NAMES[i], "p": round(float(est[n][i]), 3)}
    estimators["fusion"] = {"key": KEY_NAMES[top], "p": round(float(p[top]), 3), "source": source}
    out = {"key": {
        "tonic": FLAT_NAMES[tonic], "scale": "minor" if minor else "major",
        "mode": None if mode in ("major", "minor") else _mode_name(mode),
        "confidence": round(float(p[top]), 3), "ambiguous": ambiguous, "camelot": camelot(tonic, mode), "open_key": open_key(tonic, mode),
        "candidates": cands, "sections": sections, "modulations": modulations, "estimators": estimators,
        "metadata": prev.get("metadata") or {}, "preliminary": False, "display": display,
    }}
    from ..chords import annotate, progressions
    segs = ctx.data.get("chords")
    if segs:
        annotate(segs, tonic, mode)
        ch = ctx.store.get(ctx.aid)["result"].get("chords") or {}
        ch["segments"] = [{k: s[k] for k in ("start", "end", "chord", "triad", "roman", "bass", "conf", "on_downbeat")} for s in segs]
        if tempo:
            ch["progressions"] = progressions(segs, tempo["downbeats"])
        out["chords"] = ch
    from ..detune import with_key_text
    det = with_key_text(ctx.store.get(ctx.aid)["result"].get("detune"), tonic, mode)
    if det:
        out["detune"] = det
    return out
