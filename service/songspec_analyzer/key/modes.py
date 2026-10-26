"""Mode of a song once its tonic is known: Ionian, Dorian, Phrygian, Lydian, Mixolydian, Aeolian, Locrian, Harmonic Minor,
Phrygian Dominant. A mode is reported only when it clearly beats plain major / natural minor (MODE_MARGIN, set on the dev songs'
mode labels by eval/key_modes.py)."""
import numpy as np

from ..display.key import MODES

CANDIDATES = ["ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "locrian", "harmonic_minor", "phrygian_dominant"]
# Measured on the tracker's mode labels (eval/key_modes.py, eval/REPORT.md section 3): no margin makes these chroma templates useful
# (at best 16 of 34 modal songs found against 20 - 60 false modes), so they never override plain major / minor. The song's mode
# comes from Centsible's decision instead (centsible_mode, below); the templates remain for experiments.
MODE_MARGIN = 9.0
CENTSIBLE_MODE_P = 0.5           # Centsible's mode is reported when its probability reaches this (2 found / 1 false on 128 songs)
DEGREE_MODES = ["ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "locrian"]


def centsible_mode(key_decision, tonic, minor):
    """The mode from Centsible's key decision (its tonic degree within the scale) when it is confident and agrees with the reported
    key; plain 'major' / 'minor' otherwise."""
    if key_decision and key_decision.get("valid") and int(key_decision["tonic"]) == tonic % 12 \
            and bool(key_decision["minor"]) == bool(minor):
        d = int(key_decision["degree"])
        p = float(key_decision["mode_prob"][d])
        m = DEGREE_MODES[d]
        if m not in ("ionian", "aeolian") and p >= CENTSIBLE_MODE_P:
            return m
    return "minor" if minor else "major"


def template(mode, tonic):
    t = np.full(12, -1.0)
    for i in MODES[mode][2]:
        t[(tonic + i) % 12] = 1.0
    t[tonic % 12] = 2.0
    t[(tonic + 7) % 12] = max(t[(tonic + 7) % 12], 1.5) if 7 in MODES[mode][2] else t[(tonic + 7) % 12]
    return t


def mode_scores(chroma, tonic):
    c = np.asarray(chroma, float)
    c = (c - c.mean()) / (c.std() + 1e-12)
    return {m: float(np.dot(c, template(m, tonic)) / 12.0) for m in CANDIDATES}


def choose_mode(chroma, tonic, minor, margin=None):
    """-> (mode name, scores). Plain 'major' / 'minor' unless a mode beats the plain parent by `margin`."""
    margin = MODE_MARGIN if margin is None else margin
    s = mode_scores(chroma, tonic)
    plain = "aeolian" if minor else "ionian"
    family = [m for m in CANDIDATES if (MODES[m][0] == "Minor") == minor]
    best = max(family, key=lambda m: s[m])
    if best != plain and s[best] - s[plain] >= margin:
        return best, s
    return ("minor" if minor else "major"), s
