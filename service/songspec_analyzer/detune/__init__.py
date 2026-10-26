"""Detune (Centsible) and the quick key.

stage(ctx)        -> `detune`: Centsible's reading (the plugin's DSP), cross-checks, fusion, display strings
key_quick(ctx)    -> `key` (preliminary): Centsible's two-stage key detector on the tuning-corrected chroma
drift_stage(ctx)  -> `detune.drift`: tuning per 10 s window (Centsible's detune curve), flagged when it moves > 5 cents

Fusion rule: Centsible's own 95 % interval decides. When it is tight (<= 2 cents) and Centsible does not report two competing
tunings, its value IS the answer (it is ~15x more accurate than librosa on real music, Centsible REPORT.md); the cross-checks are
only recorded. Otherwise all estimates enter a weighted circular median (weights 1 / ci^2; cross-checks count as ci 3 / 4 cents).
"""
import numpy as np

from ..display.detune import detune_display
from ..display.key import FLAT_NAMES, house_key, key_display
from . import estimators as est

MODE_BY_DEGREE = ["ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "locrian"]
TIGHT_CI = 2.0
DRIFT_FLAG_CENTS = 5.0


def _core():
    from . import _centsible
    return _centsible


def fuse(votes):
    """votes: {name: (cents, ci)} -> (cents, ci, rule)."""
    c = votes.get("centsible")
    if c and c[0] is not None and c[1] <= TIGHT_CI and not votes.get("_competing"):
        return c[0], c[1], "centsible"
    vals = [vc for k, vc in votes.items() if not k.startswith("_") and vc[0] is not None]
    if not vals:
        return None, None, "none"
    w = [1.0 / max(ci, 0.5) ** 2 for _, ci in vals]
    m = est.circ_wmedian([v for v, _ in vals], w)
    spread = max(abs(est.wrap(v - m)) for v, _ in vals)
    return m, max(min(ci for _, ci in vals), spread), "weighted circular median"


CROSS_CHECK_SECONDS = 90.0      # the cross-checks read the middle 90 s (they only matter when Centsible is unsure)


def _middle(y, sr, seconds):
    n = int(seconds * sr)
    if len(y) <= n:
        return y
    a = (len(y) - n) // 2
    return y[a:a + n]


def measure(y, sr, cross_checks=True):
    """Every estimator on one mono signal -> dict(centsible={'tuning': ...}, votes, cents, ci, rule, harmonic).
    Centsible reads the whole track (its interval tightens with more audio); the cross-checks read the middle 90 s."""
    core = _core()
    t = core.analyze_tuning(y, sr) if hasattr(core, "analyze_tuning") else core.analyze_track(y, sr)["tuning"]
    r = {"tuning": t}
    out = {"centsible": r, "votes": {}, "cents": None, "ci": None, "rule": "none", "harmonic": None}
    if not t["valid"]:
        return out
    votes = {"centsible": (round(t["cents"], 2), t["ci_cents"]), "_competing": t["competing"]}
    if cross_checks:
        yh = est.harmonic_22k(_middle(y, sr, CROSS_CHECK_SECONDS), sr)
        out["harmonic"] = yh
        c120, _ = est.chroma120(yh)
        lib, _ = est.librosa_tuning(yh)
        votes["chroma120"] = (c120, 3.0) if c120 is not None else (None, 0)
        votes["librosa"] = (lib, 4.0)
    out["votes"] = votes
    out["cents"], out["ci"], out["rule"] = fuse(votes)
    return out


def stage(ctx):
    a = ctx.audio
    m = measure(a.mono, a.sr)
    r = m["centsible"]
    ctx.data["centsible"] = r
    t = r["tuning"]
    if not t["valid"]:
        from ..pipeline import StageSkipped
        raise StageSkipped("No steady tonal content found - detune can't be measured on this file.")
    ctx.data["harmonic_22k"] = m["harmonic"]
    cents, ci, rule = m["cents"], m["ci"], m["rule"]
    c120, lib = m["votes"]["chroma120"][0], m["votes"]["librosa"][0]
    ctx.data["tuning_cents"] = cents
    secondary = None
    if t["has_secondary"]:
        secondary = {"cents": round(t["secondary_cents"], 1), "share": round(t["secondary_share"], 3)}
    disp = detune_display(cents)                 # key_text is added by the key stage once the key is known
    return {"detune": {
        "cents": round(cents, 1), "ci_cents": round(ci, 2), "confidence": round(float(t["confidence"]), 3),
        "semitones": 0, "total_cents": None, "a4_hz": disp["a4_hz"],
        "drift": [], "drift_flag": bool(t["has_drift"]), "secondary": secondary,
        "estimators": {"centsible": round(t["cents"], 2), "chroma120": None if c120 is None else round(c120, 2),
                       "librosa": round(lib, 2)},
        "fusion": rule, "locked": bool(t["locked"]), "competing": bool(t["competing"]),
        "display": disp,
    }}


def with_key_text(detune, tonic, mode):
    """The detune section with its 'Plays in Bb Minor (+38.0 ¢ above Bb) ...' sentence for this key."""
    if not detune or detune.get("cents") is None:
        return detune
    d = dict(detune)
    d["display"] = detune_display(d["cents"], d.get("total_cents"), tonic, mode)
    return d


def key_quick(ctx):
    """Preliminary key from Centsible's detector (scale from Temperley profiles, tonic from the bass), measured against the fused
    tuning. Replaced by the fused key stage when it runs."""
    a = ctx.audio
    cents = ctx.data.get("tuning_cents")
    if cents is None:
        from ..pipeline import StageSkipped
        raise StageSkipped("Key needs a tuning reading first.")
    d = _core().key_probabilities(a.mono, a.sr, float(cents))
    ctx.data["centsible_key"] = d
    if not d["valid"]:
        from ..pipeline import StageSkipped
        raise StageSkipped("No key could be read from this file.")
    p = np.asarray(d["key_prob"])
    order = np.argsort(-p)[:3]
    cands = [{"key": house_key(int(i % 12), "minor" if i >= 12 else "major"), "p": round(float(p[i]), 3)} for i in order]
    mode = MODE_BY_DEGREE[d["degree"]]
    tonic = int(d["tonic"])
    kd = key_display(tonic, mode)
    top = float(p[order[0]])
    ambiguous = top < 0.45
    amb_text = f"ambiguous between {cands[0]['key']} and {cands[1]['key']}" if ambiguous else None
    out = {}
    detune = with_key_text(ctx.store.get(ctx.aid)["result"].get("detune"), tonic, mode)
    if detune:
        out["detune"] = detune
    out["key"] = {
        "tonic": FLAT_NAMES[tonic], "scale": "minor" if d["minor"] else "major",
        "mode": None if mode in ("ionian", "aeolian") else mode.capitalize(),
        "confidence": round(top, 3), "ambiguous": ambiguous, "camelot": kd["camelot"], "open_key": kd["open_key"],
        "candidates": cands, "sections": [], "modulations": [],
        "estimators": {"centsible": {"key": cands[0]["key"], "p": cands[0]["p"], "mode": mode,
                                     "mode_prob": [round(x, 3) for x in d["mode_prob"]]}},
        "metadata": {}, "preliminary": True,
        "display": {"key": kd["key"], "note": kd["note"], "ambiguity_text": amb_text},
    }
    return out


def drift_stage(ctx):
    """Tuning per 10 s window (hop 5 s), Centsible's detune curve. drift_flag when the valid windows span > 5 cents."""
    a = ctx.audio
    pts = _core().detune_curve(a.mono, a.sr, 10.0, 5.0)
    good = [p for p in pts if p["valid"]]
    res = ctx.store.get(ctx.aid)["result"].get("detune") or {}
    if not res:
        return {}
    drift = [{"t": round(p["t"], 1), "cents": round(p["cents"], 1), "ci": round(p["ci"], 1)} for p in good]
    if len(good) >= 3:
        ref = res["cents"]
        rel = np.array([est.wrap(p["cents"] - ref) for p in good])
        lo, hi = np.percentile(rel, [5, 95])
        res["drift_flag"] = bool(res.get("drift_flag") or (hi - lo) > DRIFT_FLAG_CENTS)
        res["drift_range_cents"] = round(float(hi - lo), 1)
    res["drift"] = drift
    return {"detune": res}
