"""Detune (Centsible) and the quick key.

stage(ctx)        -> `detune`: Centsible's reading (the plugin's DSP), cross-checks, fusion, display strings
key_quick(ctx)    -> `key` (preliminary): Centsible's two-stage key detector on the tuning-corrected chroma
drift_stage(ctx)  -> `detune.drift`: tuning per 10 s window (Centsible's detune curve), flagged when it moves > 5 cents

Fusion rule (measured, eval/REPORT.md section 2): Centsible's reading IS the answer whenever it has one. On exactly shifted real
music it recovers every shift with MAE 0.26 cents, CQT-120 with 1.24 and librosa with 6.4; letting the cross-checks vote when
Centsible's interval was wide made the result worse (max error 1.2 -> 17.6 cents), so they don't. When Centsible has too little steady
tonal evidence to read anything, CQT-120 answers with a 5-cent interval and low confidence. librosa is recorded, never used.
"""
import numpy as np

from ..display.detune import detune_display
from ..display.key import FLAT_NAMES, house_key, key_display
from . import estimators as est

MODE_BY_DEGREE = ["ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "locrian"]
DRIFT_FLAG_CENTS = 5.0
FALLBACK_CI = 5.0               # CQT-120 alone: p95 error 7 cents on shifted real music


def _core():
    from . import _centsible
    return _centsible


def fuse(votes):
    """votes: {name: (cents, ci)} -> (cents, ci, rule). Centsible when it has a reading, else CQT-120, else nothing."""
    c = votes.get("centsible")
    if c and c[0] is not None:
        return c[0], c[1], "centsible"
    q = votes.get("chroma120")
    if q and q[0] is not None:
        return q[0], max(q[1], FALLBACK_CI), "chroma120 (no Centsible reading)"
    return None, None, "none"


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
    votes = {"centsible": (round(t["cents"], 2), t["ci_cents"]) if t["valid"] else (None, 0.0)}
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
    ctx.data["harmonic_22k"] = m["harmonic"]
    cents, ci, rule = m["cents"], m["ci"], m["rule"]
    if cents is None:
        from ..pipeline import StageSkipped
        raise StageSkipped("No steady tonal content found - detune can't be measured on this file.")
    c120, lib = m["votes"]["chroma120"][0], m["votes"]["librosa"][0]
    ctx.data["tuning_cents"] = cents
    secondary = None
    if t["valid"] and t["has_secondary"]:
        secondary = {"cents": round(t["secondary_cents"], 1), "share": round(t["secondary_share"], 3)}
    confidence = float(t["confidence"]) if t["valid"] else 0.2
    if not t["valid"]:
        ctx.warnings.append("Detune is approximate: the song has little steady tonal content (read with the 120-bin chroma).")
    disp = detune_display(cents)                 # key_text is added by the key stage once the key is known
    return {"detune": {
        "cents": round(cents, 1), "ci_cents": round(ci, 2), "confidence": round(confidence, 3),
        "semitones": 0, "total_cents": None, "a4_hz": disp["a4_hz"],
        "drift": [], "drift_flag": bool(t["valid"] and t["has_drift"]), "secondary": secondary,
        "estimators": {"centsible": round(t["cents"], 2) if t["valid"] else None,
                       "chroma120": None if c120 is None else round(c120, 2), "librosa": round(lib, 2)},
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
    tonic = int(d["tonic"])
    from ..key.modes import centsible_mode
    mode = centsible_mode(d, tonic, d["minor"])                # only when Centsible is confident (eval/REPORT.md section 3)
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
        "mode": None if mode in ("major", "minor") else mode.replace("_", " ").title(),
        "confidence": round(top, 3), "ambiguous": ambiguous, "camelot": kd["camelot"], "open_key": kd["open_key"],
        "candidates": cands, "sections": [], "modulations": [],
        "estimators": {"centsible": {"key": cands[0]["key"], "p": cands[0]["p"], "mode": MODE_BY_DEGREE[d["degree"]],
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
