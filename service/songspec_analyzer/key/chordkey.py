"""Chord-based key evidence (PLAN.md 3.4, estimator 3): how well the recognised chord sequence fits each of the 24 keys.

Per candidate key, rotation-equivariant features (fed to the fusion, which learns their weights):
  diatonic   share of chord time whose triad belongs to the key (minor keys also accept the harmonic-minor V and vii°)
  tonic      share of chord time on the tonic triad
  first      the first chord (after 'N') is the tonic triad
  last       the last chord is the tonic triad
  cadence    resolutions INTO the tonic per minute: V -> I, IV -> I, bVII -> I (major); V or v -> i, iv -> i, VII -> i (minor)
"""
import numpy as np

from ..chords.vocab import TRIAD_OF, split

CHORD_FEATURES = ["ch_diatonic", "ch_tonic", "ch_first", "ch_last", "ch_cadence"]
# allowed triad qualities per scale degree (semitones above the tonic); minor also takes harmonic minor's major V and vii°
MAJOR = {0: {""}, 2: {"m"}, 4: {"m"}, 5: {""}, 7: {""}, 9: {"m"}, 11: {"dim"}}
MINOR = {0: {"m"}, 2: {"dim"}, 3: {""}, 5: {"m"}, 7: {"m", ""}, 8: {""}, 10: {""}, 11: {"dim"}}


def _triads(segments):
    out = []
    for s in segments:
        r, q = split(s["chord"].split("/")[0])
        if r is None:
            continue
        out.append((r, TRIAD_OF.get(q, ""), s["end"] - s["start"]))
    return out


def chord_features(segments, duration=None):
    """24 x len(CHORD_FEATURES); zeros when there are no chords."""
    F = np.zeros((24, len(CHORD_FEATURES)))
    tri = _triads(segments)
    if not tri:
        return F
    total = sum(d for *_, d in tri) or 1.0
    minutes = (duration or total) / 60.0
    for k in range(24):
        t, minor = k % 12, k >= 12
        table = MINOR if minor else MAJOR
        tq = "m" if minor else ""
        dia = sum(d for r, q, d in tri if q in table.get((r - t) % 12, ()))
        ton = sum(d for r, q, d in tri if r == t and q == tq)
        F[k, 0] = dia / total
        F[k, 1] = ton / total
        F[k, 2] = float(tri[0][0] == t and tri[0][1] == tq)
        F[k, 3] = float(tri[-1][0] == t and tri[-1][1] == tq)
        cad = 0
        for (r1, q1, _), (r2, q2, _) in zip(tri, tri[1:]):
            if r2 != t or q2 != tq:
                continue
            iv = (r1 - t) % 12
            if (iv == 7 and (q1 == "" or (minor and q1 == "m"))) or (iv == 5 and q1 == ("m" if minor else ""))                     or (iv == 10 and q1 == ""):
                cad += 1
        F[k, 4] = cad / max(minutes, 0.5)
    return F
