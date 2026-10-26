#!/usr/bin/env python3
"""'May borrow from ...' (key/borrow.py) against the tracker's notes.

The tracker's notes name borrowed scales in free text ("Borrows heavily from F Harmonic Minor", "Might borrow from D Mixolydian",
"F Phrygian - might be or work with F Locrian"). For each single-key labelled song with cached chord evidence, the production chord
decode (BTC blend) is run, borrow detection is applied relative to the TRACKER's key (so key errors do not count here), and the
named sources are compared with every "<tonic> <scale>" the note mentions other than the key itself.

  - songs whose note names a scale: how often we name at least one of those (hit), and how many of our names it mentions (precision)
  - songs whose note names no scale: how often we name one anyway. A silent note is not proof of no borrowing, so this is an upper
    bound on false alarms, not a measured false-alarm rate.
Run:  .venv/Scripts/python eval/key_borrow.py
"""
import re
import sys

import numpy as np

from common import RESULTS, cache_path, load_labels, md_table

from songspec_analyzer.chords.engine import decode
from songspec_analyzer.chords.vocab import labels
from songspec_analyzer.display.key import MODES, norm_mode, parse_pitch
from songspec_analyzer.key.borrow import borrowed

_SCALES = sorted({v[1] for v in MODES.values()} | {"Dorion"}, key=len, reverse=True)
_RX = re.compile(r"\b([A-G][#b♭♯]?)\s+(" + "|".join(_SCALES) + r")\b", re.I)


def canon(tonic, scale):
    m = norm_mode(scale)
    m = {"major": "ionian", "minor": "aeolian"}.get(m, m)
    return parse_pitch(tonic), m


def mentioned(note, key_pc, key_mode):
    out = set()
    for t, s in _RX.findall(note or ""):
        try:
            c = canon(t, s)
        except ValueError:
            continue
        if c != (key_pc, key_mode):
            out.add(c)
    return out


def main():
    labs = labels("full")
    rows = [r for r in load_labels() if r["keys"].strip() and ";" not in r["keys"]
            and cache_path("chord_ev_v1", r["file"]).with_suffix(".npz").exists()]
    named, silent, examples = [], [], []
    for r in rows:
        z = dict(np.load(cache_path("chord_ev_v1", r["file"]).with_suffix(".npz")))
        ev = {"labs": labs, "logp": z["logp"].astype(float), "bb": z["bb"], "starts": z["starts"], "end": float(z["end"][0]),
              "is_down": z["is_down"]}
        segs = decode(ev, z["btc"].astype(float))
        t, m = r["keys"].split()
        pc, mode = canon(t, m)
        ours = {canon(f["tonic"], f["mode"]) for f in borrowed(segs, pc, mode)}
        theirs = mentioned(r["notes"], pc, mode)
        names = sorted(f"{t} {m}" for t, m in ours)
        if theirs:
            named.append((bool(ours & theirs), len(ours & theirs), len(ours)))
            examples.append([r["song"][:30], r["key_raw"], (r["notes"] or "")[:70].replace("|", "/"), ", ".join(
                f"{f['from']} ({'+'.join(f['chords'][:3])})" for f in borrowed(segs, pc, mode)) or "-"])
        else:
            silent.append(bool(ours))
    hit = np.array([h for h, _, _ in named])
    prec = sum(a for _, a, _ in named) / max(1, sum(b for _, _, b in named))
    table = [["note names a scale", len(named), f"{100 * hit.mean():.0f} %" if len(hit) else "-", f"{100 * prec:.0f} %"],
             ["note names none", len(silent), f"we name one in {100 * np.mean(silent):.0f} %" if silent else "-", "-"]]
    out = ["# 'May borrow from' vs the tracker's notes", "",
           "Relative to the tracker's own key (single-key songs). Chord evidence: production decode (templates + BTC).", "",
           md_table(["songs", "n", "we name at least one of the note's scales", "share of our names the note mentions"], table), "",
           "## Songs whose note names a scale", "",
           md_table(["song", "tracker key", "tracker note", "ours (main chords)"], examples), ""]
    (RESULTS / "key_borrow.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
