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


def bar_contents(segments, downbeats, min_share=0.25):
    """Per bar, the chords that sound in it (in order, repeats merged; fragments under a quarter of the bar ignored):
    [((triad, roman), ...), ...]. A bar can hold several chords (Gb Ab Bbm in one bar)."""
    downs = list(downbeats)
    if len(downs) >= 2 and segments:                    # close the last bar: one bar length on, at most where the chords end
        downs.append(min(downs[-1] + (downs[-1] - downs[-2]), max(s["end"] for s in segments)))
    bars = []
    for i, d in enumerate(downs[:-1]):
        e = downs[i + 1]
        items = []
        for s in segments:
            ov = min(s["end"], e) - max(s["start"], d)
            if ov >= min_share * (e - d) and s["triad"] != "N":
                it = (s["triad"], s.get("roman") or s["triad"])
                if not items or items[-1] != it:
                    items.append(it)
        bars.append(tuple(items))
    return bars


def _flat(bars):
    out = []
    for b in bars:
        for it in b:
            if not out or out[-1] != it:
                out.append(it)
    return out


def progressions(segments, downbeats, lengths=(4, 2, 1), min_count=3):
    """Repeating chord loops over 4, 2 or 1 bars, longest first: [{roman, chords, count, display}]. A loop counts when it occurs at
    least `min_count` times back to back or apart (non-overlapping); a shorter loop already inside a reported one is skipped."""
    bars = bar_contents(segments, downbeats)
    found, covered = [], []
    for L in lengths:
        counts, first = Counter(), {}
        for i in range(len(bars) - L + 1):
            w = tuple(bars[i:i + L])
            if any(not b for b in w) or len({t for t, _ in _flat(w)}) < 2:
                continue
            counts[w] += 1
            first.setdefault(w, i)
        for w, _ in counts.most_common():
            k, c = 0, 0
            while k + L <= len(bars):
                if tuple(bars[k:k + L]) == w:
                    c += 1
                    k += L
                else:
                    k += 1
            flat = _flat(w)
            names = [t for t, _ in flat]
            if c < min_count or any(_inside(names, f) for f in covered):
                continue
            covered.append(names)
            r = [ro for _, ro in flat]
            found.append({"roman": "-".join(r), "chords": "-".join(names), "count": c, "display": f"{'–'.join(r)} ×{c}"})
    return found[:5]


def _inside(names, longer):
    """Is the chord loop `names` contained (cyclically) in `longer`?"""
    s = "|" + "|".join(names) + "|"
    return s in "|" + "|".join(longer + longer) + "|"


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
    cents = ctx.data.get("tuning_cents") or 0.0
    from . import btc
    from .engine import BTC_WEIGHT
    btc_in, engine_name = None, "templates+viterbi (beat-synchronous, tuned)"
    if btc.available() and BTC_WEIGHT > 0:
        from ..tempo.engine import device
        y22 = librosa.resample(a.mono, orig_sr=a.sr, target_sr=22050)
        btc_in = btc.frame_probs(y22, cents, device())
        engine_name = f"BTC (large vocabulary) + templates, weight {BTC_WEIGHT}, beat-synchronous Viterbi, tuned"
    segs = recognise(yh, cents, tempo["beats"], tempo["downbeats"], a.duration, btc=btc_in)
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
                       "engine": engine_name}}
