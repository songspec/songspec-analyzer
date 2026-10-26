"""Chords stage: beat-synchronous recognition (engine.py) -> segments with roman numerals, a summary and repeating progressions.

Roman numerals are relative to the key known when the stage runs (the quick key); the fused key stage re-annotates them.
"""
from collections import Counter

import numpy as np

from ..display.key import parse_key, roman
from .vocab import FLAT, split

VOCABULARY = ["maj", "min", "7", "maj7", "m7", "dim", "aug", "sus2", "sus4", "6", "m6", "9", "add9", "m7b5", "dim7"]


def annotate(segments, tonic, mode):
    for s in segments:
        r, q = split(s["chord"])
        s["roman"] = None if r is None or tonic is None else roman(r, q, tonic, mode)
    return segments


def summary(segments):
    secs, cnt = Counter(), Counter()
    for s in segments:
        if s["chord"] != "N":
            secs[s["chord"]] += s["end"] - s["start"]
            cnt[s["chord"]] += 1
    return [{"chord": c, "seconds": round(t, 1), "count": cnt[c]} for c, t in secs.most_common()]


def bar_sequence(segments, downbeats):
    """The chord sounding at each downbeat (one entry per bar)."""
    out = []
    j = 0
    for d in downbeats:
        while j + 1 < len(segments) and segments[j + 1]["start"] <= d + 1e-3:
            j += 1
        if segments and segments[j]["start"] <= d + 0.05:
            out.append(j)
    return out


def progressions(segments, downbeats, lengths=(4, 2), min_count=3):
    """Repeating chord loops over bars, longest first: [{roman, chords, count, display}]. A loop is counted when it occurs at least
    `min_count` times (non-overlapping); a 2-bar loop inside a reported 4-bar loop is not reported again."""
    seq_idx = bar_sequence(segments, downbeats)
    names = [segments[i]["triad"] for i in seq_idx]
    romans = [segments[i].get("roman") or segments[i]["triad"] for i in seq_idx]
    found, covered = [], set()
    for L in lengths:
        counts, first = Counter(), {}
        i = 0
        while i + L <= len(names):
            w = tuple(names[i:i + L])
            if "N" in w or len(set(w)) < 2:
                i += 1
                continue
            counts[w] += 1
            first.setdefault(w, i)
            i += 1
        for w, n in counts.most_common():
            # non-overlapping count
            k, c = 0, 0
            while k + L <= len(names):
                if tuple(names[k:k + L]) == w:
                    c += 1
                    k += L
                else:
                    k += 1
            if c < min_count or any(_sub(w, f) for f in covered):
                continue
            covered.add(w)
            r = romans[first[w]:first[w] + L]
            found.append({"roman": "-".join(r), "chords": "-".join(w), "count": c, "display": f"{'–'.join(r)} ×{c}"})
    return found[:5]


def _sub(w, f):
    s = "|".join(w)
    return s in "|".join(f + f)


def stage(ctx):
    from .engine import recognise
    a = ctx.audio
    tempo = ctx.data.get("tempo")
    if not tempo:
        from ..pipeline import StageSkipped
        raise StageSkipped("Chords need the beat grid, and no steady beat was found.")
    import librosa
    yh = ctx.data.get("harmonic_full")
    if yh is None:
        y22 = librosa.resample(a.mono, orig_sr=a.sr, target_sr=22050)
        yh = librosa.effects.harmonic(y22, margin=2.0)
        ctx.data["harmonic_full"] = yh
    segs = recognise(yh, ctx.data.get("tuning_cents") or 0.0, tempo["beats"], tempo["downbeats"], a.duration)
    key = (ctx.store.get(ctx.aid)["result"].get("key") or {})
    tonic, mode = None, None
    if key.get("display"):
        try:
            tonic, mode = parse_key(key["tonic"] + " " + (key.get("mode") or key["scale"]))
        except (KeyError, ValueError, TypeError):
            pass
    annotate(segs, tonic, mode)
    ctx.data["chords"] = segs
    public = [{k: s[k] for k in ("start", "end", "chord", "triad", "roman", "bass", "conf", "on_downbeat")} for s in segs]
    return {"chords": {"segments": public, "summary": summary(segs),
                       "progressions": progressions(segs, tempo["downbeats"]), "vocabulary": VOCABULARY,
                       "engine": "templates+viterbi (beat-synchronous, tuned)"}}
