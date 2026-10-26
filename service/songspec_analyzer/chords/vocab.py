"""Chord vocabulary (the brief's list), as pitch-class templates relative to the root, with a prior that keeps the decoder on triads
unless an extension is clearly there."""
import re

import numpy as np

# quality: (intervals from the root, per-interval weight, log-prior)
QUALITIES = {
    "":     ((0, 4, 7),          (1.0, 0.9, 0.8),            0.0),
    "m":    ((0, 3, 7),          (1.0, 0.9, 0.8),            0.0),
    "7":    ((0, 4, 7, 10),      (1.0, 0.9, 0.7, 0.7),       -0.6),
    "maj7": ((0, 4, 7, 11),      (1.0, 0.9, 0.7, 0.7),       -0.7),
    "m7":   ((0, 3, 7, 10),      (1.0, 0.9, 0.7, 0.7),       -0.6),
    "dim":  ((0, 3, 6),          (1.0, 0.9, 0.8),            -0.9),
    "aug":  ((0, 4, 8),          (1.0, 0.9, 0.8),            -1.2),
    "sus2": ((0, 2, 7),          (1.0, 0.8, 0.8),            -0.9),
    "sus4": ((0, 5, 7),          (1.0, 0.8, 0.8),            -0.8),
    "6":    ((0, 4, 7, 9),       (1.0, 0.9, 0.7, 0.6),       -1.0),
    "m6":   ((0, 3, 7, 9),       (1.0, 0.9, 0.7, 0.6),       -1.1),
    "9":    ((0, 4, 7, 10, 2),   (1.0, 0.9, 0.6, 0.6, 0.5),  -1.1),
    "add9": ((0, 4, 7, 2),       (1.0, 0.9, 0.7, 0.5),       -1.0),
    "m7b5": ((0, 3, 6, 10),      (1.0, 0.9, 0.8, 0.7),       -1.0),
    "dim7": ((0, 3, 6, 9),       (1.0, 0.9, 0.8, 0.7),       -1.3),
}
TRIAD_OF = {"": "", "m": "m", "7": "", "maj7": "", "m7": "m", "dim": "dim", "aug": "aug", "sus2": "", "sus4": "",
            "6": "", "m6": "m", "9": "", "add9": "", "m7b5": "dim", "dim7": "dim"}
FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def labels(vocab="full"):
    qs = list(QUALITIES) if vocab == "full" else ["", "m"]
    return ["N"] + [FLAT[r] + q for q in qs for r in range(12)]


def templates(vocab="full"):
    """(label list, templates K x 12 L2-normalised, log-priors K). Index 0 is 'N' (no chord: a flat template)."""
    labs = labels(vocab)
    T = [np.ones(12) / np.sqrt(12)]
    pri = [-0.5]
    for lab in labs[1:]:
        root, q = split(lab)
        iv, w, lp = QUALITIES[q]
        t = np.zeros(12)
        for i, wi in zip(iv, w):
            t[(root + i) % 12] = wi
        T.append(t / np.linalg.norm(t))
        pri.append(lp)
    return labs, np.array(T), np.array(pri)


_ROOT = re.compile(r"^([A-G]b?)(.*?)(?:/([A-G]b?))?$")


def split(label):
    """'Ebmaj7' -> (3, 'maj7'); 'C/E' -> (0, ''); 'N' -> (None, None). Labels use flat spellings only."""
    if label in ("N", "X"):
        return None, None
    m = _ROOT.match(label)
    return FLAT.index(m.group(1)), m.group(2)
