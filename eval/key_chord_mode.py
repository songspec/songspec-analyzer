#!/usr/bin/env python3
"""Tonic and mode from the chords (Rust engine output), against the tracker. REPORT.md 3.5; songspec-core key::refine_with_chords.

The fused key hears the scale well but not always which note of it is home: Beautiful Liar sits on a G chord with an Ab over it
(G Phrygian Dominant); four of the five key estimators say G, S-KEY says C Minor and its weight carries the fusion. The chords
say where the song lives (how long each root is held) and which notes sit on top of the tonic, so they can name the mode.

What the catalog showed (first versions of this script):
- moving the tonic to the most-played chord on chord evidence alone fixes 4 songs and breaks 3 (the most-played chord is often the
  IV or the relative), and its "Lydian" results were all wrong moves. MANNEQUIN has Beautiful Liar's chords exactly (G 67 %, Ab
  33 %) and the tracker calls it C Phrygian. So a move also needs the key estimators: at least VOTES of the four plain estimators
  (Centsible, Albrecht-Shanahan on the scale and on the beats, Temperley) must name the chord as the tonic. MANNEQUIN gets 1 vote,
  Beautiful Liar 4;
- the b2 family (Phrygian, Locrian, Phrygian Dominant) is what the chords can name: a b2 in the chords is rarely just a borrowed
  chord. A natural 7 in minor, a 6 in minor or a b7 in major usually is (one V or IV or bVII chord): naming Harmonic Minor, Dorian or
  Mixolydian from them gave false modes, and the tracker writes those as borrows ("May borrow from ..."), which the app already does.

Input: the CLI's results for the catalog (JSON lists), e.g. from  songspec-cli FILES... --out base.json  (made before this rule).
Scoring: tracker key (first section) as tonic + Major/Minor parent; mode as eval/key_modes.label_mode reads it from the notes.
Parameters are chosen by NESTED leave-one-artist-group-out (key_eval.artist_group): every group is scored with the parameters that
did best on the other four.
Run:  .venv/Scripts/python eval/key_chord_mode.py RESULTS.json [MORE.json ...]
"""
import collections
import itertools
import json
import sys
from pathlib import Path

import numpy as np

from common import load_labels
from key_eval import artist_group, label_index
from key_modes import label_mode

NAMES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# mode -> parent minor?
MINOR_PARENT = {"major": False, "minor": True, "phrygian": True, "locrian": True, "phrygian_dominant": True}
# the Rust chord vocabulary (songspec-core chords::QUALITIES): quality -> intervals; the first three notes count fully, the rest half
QUAL = {"": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "dim": (0, 3, 6),
        "aug": (0, 4, 8), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "9": (0, 4, 7, 10, 2),
        "add9": (0, 4, 7, 2), "m7b5": (0, 3, 6, 10), "dim7": (0, 3, 6, 9)}
MAJOR_Q = {"", "7", "maj7", "6", "9", "add9"}
MINOR_Q = {"m", "m7", "m6"}
ESTIMATORS = ("centsible", "albrecht_shanahan@scale", "albrecht_shanahan@beat", "temperley@scale")


def pitch(s):
    pc = NAMES[s[0]]
    for c in s[1:]:
        pc += {"#": 1, "b": -1}.get(c, 0)
    return pc % 12


def split(name):
    """'Bbm7/F' -> (root pc, quality)."""
    name = name.split("/")[0]
    n = 2 if len(name) > 1 and name[1] in "#b" else 1
    return pitch(name[:n]), name[n:]


def evidence(a):
    """Chord-time evidence: root share R, root share as a major / minor chord, tone weight H."""
    R, Rmaj, Rmin, H = np.zeros(12), np.zeros(12), np.zeros(12), np.zeros(12)
    for s in a["chords"]["segments"]:
        if s["chord"] == "N":
            continue
        d = s["end"] - s["start"]
        r, q = split(s["chord"])
        R[r] += d
        if q in MAJOR_Q:
            Rmaj[r] += d
        elif q in MINOR_Q:
            Rmin[r] += d
        for j, i in enumerate(QUAL.get(q, (0, 4, 7))):
            H[(r + i) % 12] += d * (1.0 if j < 3 else 0.5)
    tot = max(R.sum(), 1e-9)
    return {"R": R / tot, "Rmaj": Rmaj / tot, "Rmin": Rmin / tot, "H": H / max(H.sum(), 1e-9)}


def votes(a, pc):
    return sum(1 for e in a["key"]["estimators"] if e["name"] in ESTIMATORS and pitch(e["key"].split()[0]) == pc)


def decide(a, p):
    """-> (tonic, mode, moved)."""
    ev = evidence(a)
    t, minor = pitch(a["key"]["tonic"]), a["key"]["scale"] == "minor"
    R, H = ev["R"], ev["H"]
    c = int(np.argmax(R))
    moved = False
    if c != t and R[c] >= p["home_min"] and R[c] >= p["home_ratio"] * R[t] and votes(a, c) >= p["votes"]:
        t, minor, moved = c, ev["Rmin"][c] > ev["Rmaj"][c], True

    def alt(nat, alt_):
        x, n = H[(t + alt_) % 12], H[(t + nat) % 12]
        return x >= p["alt_min"] and x / (x + n + 1e-9) >= p["alt_frac"]
    if minor:
        m = ("locrian" if alt(7, 6) else "phrygian") if alt(2, 1) else "minor"
    else:
        m = "phrygian_dominant" if alt(2, 1) and alt(9, 8) else "major"
    return t, m, moved


def load(paths):
    res = {}
    for p in paths:
        for a in json.loads(Path(p).read_text(encoding="utf-8")):
            if "key" in a and a.get("chords"):
                res[Path(a["file"]).name] = a
    return res


OFF = dict(home_min=9.0, home_ratio=9.0, votes=9, alt_min=9.0, alt_frac=9.0)
GRID = [dict(home_min=hm, home_ratio=hr, votes=v, alt_min=am, alt_frac=af)
        for hm, hr, v, am, af in itertools.product([0.3, 0.4, 0.5, 9.0], [1.5, 2.0, 3.0], [2, 3, 4], [0.02, 0.04, 0.06, 0.08],
                                                   [0.5, 0.6, 0.7, 0.8, 0.9])]


def score(rows, p):
    """Per row: (key exact: tonic + parent, mode right on mode-labelled rows (None when unlabelled), moved, key right before)."""
    out = []
    for r, a in rows:
        t, m, moved = decide(a, p)
        ref = label_index(r["keys"].split(";")[0])
        key_ok = (t + 12 * MINOR_PARENT[m]) == ref
        before = (pitch(a["key"]["tonic"]) + 12 * (a["key"]["scale"] == "minor")) == ref
        lab = label_mode(r)
        mode_ok = None
        if lab is not None:
            want = ("minor" if ref >= 12 else "major") if lab == "plain" else lab
            mode_ok = key_ok and m == want
        out.append((key_ok, mode_ok, moved, before, lab, m))
    return out


def summary(sc):
    m = [s[1] for s in sc if s[1] is not None]
    return float(np.mean([s[0] for s in sc])), float(np.mean(m)), len(m)


def objective(sc):
    k, m, _ = summary(sc)
    return k + m


def main(paths):
    res = load(paths)
    rows = [(r, res[Path(r["file"]).name]) for r in load_labels() if r["keys"].strip() and Path(r["file"]).name in res]
    groups = np.array([artist_group(r["artist"]) for r, _ in rows])
    print(f"{len(rows)} songs")
    k, m, n = summary(score(rows, OFF))
    print(f"before (fused key, plain major / minor): key exact {k:.3f}, mode right on {n} labelled songs {m:.3f}")
    cache = [score(rows, p) for p in GRID]
    nested = [None] * len(rows)
    for g in range(5):
        tr = [j for j in range(len(rows)) if groups[j] != g]
        best = max(range(len(GRID)), key=lambda i: (objective([cache[i][j] for j in tr]), -i))
        print(f"  group {g}: {GRID[best]}")
        for j in range(len(rows)):
            if groups[j] == g:
                nested[j] = cache[best][j]
    k, m, n = summary(nested)
    print(f"nested CV: key exact {k:.3f}, mode right {m:.3f}")
    ship = dict(home_min=0.3, home_ratio=2.0, votes=3, alt_min=0.04, alt_frac=0.7)
    sc = score(rows, ship)
    k, m, n = summary(sc)
    fixed = sum(1 for s in sc if s[2] and s[0] and not s[3])
    broke = sum(1 for s in sc if s[2] and s[3] and not s[0])
    print(f"shipped {ship}: key exact {k:.3f}, mode right {m:.3f}; tonic moves {sum(s[2] for s in sc)}: fixed {fixed}, broke {broke}")
    conf = collections.Counter((s[4], s[5] if s[5] not in ("major", "minor") else "plain") for s in sc if s[0] and s[4] is not None)
    print("  key right, label -> named:", dict(sorted(conf.items())))


if __name__ == "__main__":
    main(sys.argv[1:])
