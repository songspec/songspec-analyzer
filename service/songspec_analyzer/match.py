"""Mashup helper: how to make song B sit on song A (key, tuning, tempo).

Key: shift B by whole semitones so its key becomes A's, or A's relative major / minor when the modes differ (they share all seven
notes, the convention of Centsible's match mode); the shift is kept within -6 .. +5. Tuning: B's offset from A440 is moved onto A's.
Tempo: the ratio A / B at the metrical level closest to 1 (B's half or double tempo counts: half-time feel is common in mashups).
"""
import math

from .display.detune import MINUS, signed, wrap_cents
from .display.key import camelot, house_key, parse_pitch


def _key(view):
    k = view.get("key") or {}
    if not k.get("tonic") or not k.get("scale"):
        return None
    return parse_pitch(k["tonic"]), k["scale"] == "minor"


def camelot_relation(a, b):
    """'same', 'relative', 'adjacent' (one step on the wheel, the classic DJ move) or None, for (tonic, minor) pairs."""
    ca, cb = camelot(a[0], "minor" if a[1] else "major"), camelot(b[0], "minor" if b[1] else "major")
    na, la, nb, lb = int(ca[:-1]), ca[-1], int(cb[:-1]), cb[-1]
    if ca == cb:
        return "same"
    if na == nb:
        return "relative"
    if la == lb and (na - nb) % 12 in (1, 11):
        return "adjacent"
    return None


def advise(a, b):
    """a, b: finished analyses (GET /v1/analyses/{id}). -> dict with numbers and display strings."""
    out = {"a": a.get("id"), "b": b.get("id"), "key": None, "tuning": None, "tempo": None, "display": {}}
    ka, kb = _key(a), _key(b)
    semis = 0
    if ka and kb:
        target = ka[0]
        if ka[1] != kb[1]:                                  # different modes: aim at A's relative key, which has B's mode
            target = (ka[0] + 3) % 12 if kb[1] is False and ka[1] else (ka[0] - 3) % 12
        semis = (target - kb[0]) % 12
        if semis > 5:
            semis -= 12
        after = ((kb[0] + semis) % 12, kb[1])
        out["key"] = {"semitones": semis, "a": house_key(*_mode(ka)), "b": house_key(*_mode(kb)), "b_after": house_key(*_mode(after)),
                      "camelot_before": camelot_relation(ka, kb), "camelot_after": camelot_relation(ka, after)}
    da, db = (a.get("detune") or {}).get("cents"), (b.get("detune") or {}).get("cents")
    fine = round(wrap_cents(da - db), 1) if da is not None and db is not None else 0.0
    total = semis * 100 + fine
    out["tuning"] = {"fine_cents": fine, "total_cents": round(total, 1)}
    ta, tb = (a.get("tempo") or {}).get("bpm"), (b.get("tempo") or {}).get("bpm")
    if ta and tb:
        best = min((tb * m for m in (0.5, 1.0, 2.0)), key=lambda x: abs(math.log(ta / x)))
        ratio = ta / best
        level = {0.5: "half time", 1.0: None, 2.0: "double time"}[round(best / tb, 1)]
        vs = tb * 2 ** (total / 1200.0)
        out["tempo"] = {"ratio": round(ratio, 4), "stretch_pct": round(100 * (ratio - 1), 2), "b_level": level,
                        "b_bpm_after": round(best * ratio, 2), "varispeed_bpm": round(vs, 2)}
    d = out["display"]
    if out["key"]:
        s = out["key"]["semitones"]
        d["key"] = (f"Pitch B {('+' if s > 0 else MINUS) + str(abs(s)) if s else '0'} semitone{'s' if abs(s) != 1 else ''}: "
                    f"{out['key']['b']} → {out['key']['b_after']} (A is {out['key']['a']})")
    d["tuning"] = f"then {signed(fine)} ¢ to match A's tuning" if abs(fine) >= 0.05 else "tuning already matches"
    if out["tempo"]:
        t = out["tempo"]
        lvl = f" ({t['b_level']})" if t["b_level"] else ""
        d["tempo"] = f"stretch B to {100 + t['stretch_pct']:.1f} %{lvl}: {tb:g} → {t['b_bpm_after']:g} BPM"
        d["varispeed"] = (f"or varispeed only ({signed(total)} ¢): B would run at {t['varispeed_bpm']:g} BPM")
    return out


def _mode(k):
    return k[0], "minor" if k[1] else "major"
