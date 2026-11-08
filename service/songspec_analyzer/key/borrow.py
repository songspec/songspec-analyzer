"""'May borrow from ...': chords outside the key, traced to the scale they come from (modal interchange).

The song's key is a tonic plus a home scale (Major, Minor, or the detected mode such as Dorian). A recognised chord is "outside"
when one of its notes (all of them, so C7 in C major needs Bb) is not in the home scale. Each outside chord is credited to the
closest source scale that contains every note of it: closest = fewest notes changed from the home scale, plus a small penalty for
the rarer scales and a small bonus for the plain parallel Major/Minor, ties going to the more common name. So bVII in major reads
Mixolydian, II Lydian, bII Phrygian, iv / bVI the parallel Minor; in minor, V reads Harmonic Minor, IV Dorian, I the parallel Major.
The sources are every parallel mode (same tonic) plus, in a major key, the relative minor's Harmonic Minor (V/vi: E in C major).
When the outside chords of two or more sources all fit one source that is at most MERGE_SLACK further away and changes no note they
did not already change, they are named as that one (bVII + iv + bVI in major is "C Minor", the usual reading, not "C Mixolydian,
C Minor"; V + IV in minor stays "Harmonic Minor, Dorian", never "A Major").

This follows the tracker's notes ("Borrows heavily from F Harmonic Minor" in F Minor, "Might borrow from D Mixolydian" in D Major,
"Intro borrows from G Harmonic Minor" in Bb Major). Chord recognition is noisy: a chord only counts when it is held at least
CHORD_MIN_SECONDS over at least MIN_SEGMENTS segments, and a source is only named when its chords add up to MIN_SHARE of the
song's chord time and MIN_SECONDS. TONIC_RULE: chords on the key's own root do not count, because beats often leave the 3rd out
and the recogniser then flips the tonic between major and minor (eval/key_borrow.py: "F Major" in F Minor songs, "Phrygian
Dominant" where the tracker hears Phrygian). One exception, PARALLEL_MAJOR: in a minor key a MAJOR tonic chord (a Picardy third, or
the tracker's "Main chord Eb Major too") counts, credited to the parallel Major, when the song also clearly plays its minor tonic
(at least PICARDY_MINOR_RATIO of the major tonic's time: an alternation, not every tonic chord flipping) and has little Phrygian
bII (under PICARDY_BII_RATIO of it: in Phrygian songs the third-less tonic is what flips). The minor key's IV and ii (Dorian on
their own, as the tracker writes them) then merge into that "X Major", as the major direction of modal interchange.
"""
from ..chords.vocab import QUALITIES, split
from ..display.key import MODES, mode_name, mode_tonic, norm_mode

# (source id, tonic offset from the key's tonic, scale, penalty), most common first (that order breaks ties)
SOURCES = [
    ("ionian", 0, "ionian", -1.0), ("aeolian", 0, "aeolian", -1.0),
    ("mixolydian", 0, "mixolydian", 0), ("lydian", 0, "lydian", 0), ("dorian", 0, "dorian", 0), ("phrygian", 0, "phrygian", 0),
    ("harmonic_minor", 0, "harmonic_minor", 0), ("relative_harmonic_minor", 9, "harmonic_minor", 1.0),
    ("locrian", 0, "locrian", 0.5), ("melodic_minor", 0, "melodic_minor", 0.5), ("phrygian_dominant", 0, "phrygian_dominant", 1.0),
    ("harmonic_major", 0, "harmonic_major", 1.0), ("lydian_dominant", 0, "lydian_dominant", 0.5),
    ("hungarian_minor", 0, "hungarian_minor", 1.0),
]
CHORD_MIN_SECONDS = 5.0
MIN_SEGMENTS = 2
MIN_SHARE = 0.10        # eval/key_borrow.py sweep: 0.04 -> 0.10 trades 49 -> 43 % hits for 40 -> 48 % precision
MIN_SECONDS = 6.0
MAX_MODES = 3
MERGE_SLACK = 1.0
PICARDY_MINOR_RATIO = 0.5    # minor tonic time >= this x major tonic time (the catalog's Phrygian flips have almost no minor i)
PICARDY_BII_RATIO = 0.25     # bII time < this x major tonic time (Phrygian songs: bII 25 - 200 s; NATURAL CAUSES: 6 s of 44)
MAJOR_TRIADS = {"", "7", "maj7", "6", "9", "add9"}
MINOR_TRIADS = {"m", "m7", "m6", "m9"}
_NAME = {"aeolian": "Minor", "ionian": "Major"}           # the tracker says "C Minor", not "C Aeolian"
_SRC = {sid: (off, sc, pen) for sid, off, sc, pen in SOURCES}
_ORDER = [sid for sid, *_ in SOURCES]


def scale(mode, tonic=0):
    return frozenset((tonic + i) % 12 for i in MODES[mode][2])


def chord_notes(label):
    """'Bb7' -> {10, 2, 5, 8}; 'C/E' -> notes of C (the bass is one of them); 'N' -> None."""
    r, q = split(label.split("/")[0])
    if r is None or q not in QUALITIES:
        return None
    return frozenset((r + i) % 12 for i in QUALITIES[q][0])


def source_scale(sid, tonic):
    off, sc, _ = _SRC[sid]
    return scale(sc, tonic + off)


def source_name(sid, tonic):
    off, sc, _ = _SRC[sid]
    return mode_name((tonic + off) % 12, {"ionian": "major", "aeolian": "minor"}.get(sc, sc))


def borrowed(segments, tonic, mode):
    """-> [{'from': 'C Mixolydian', 'tonic': 'C', 'mode': 'mixolydian', 'chords': ['Bb', 'F7'], 'seconds', 'share'}], strongest first.
    `mode` is the key's mode name ('major', 'minor', 'dorian', ...); booleans are accepted as minor=True/False."""
    if isinstance(mode, bool):
        mode = "minor" if mode else "major"
    mode = norm_mode(mode)
    own = {"major": "ionian", "minor": "aeolian"}.get(mode, mode)
    home = scale(own, tonic)
    cands = [sid for sid in _ORDER if source_scale(sid, tonic) != home
             and (_SRC[sid][0] == 0 or own == "ionian")]     # relative sources only from a plain major key
    cost = {sid: len(source_scale(sid, tonic) - home) + _SRC[sid][2] for sid in cands}
    if own not in ("ionian", "aeolian"):               # a modal home: the plain parallel Major/Minor gets no bonus
        for sid in ("ionian", "aeolian"):
            if sid in cost:
                cost[sid] += 1.0
    total, held = 0.0, {}

    def root_q(label):
        return split(label.split("/")[0])

    def on_tonic(label):                                 # see TONIC_RULE
        return root_q(label)[0] == tonic % 12

    def seconds(root, quals):
        return sum(s["end"] - s["start"] for s in segments
                   if chord_notes(s["chord"]) is not None and root_q(s["chord"])[0] == root % 12 and root_q(s["chord"])[1] in quals)

    picardy = False                                      # see PARALLEL_MAJOR
    if MODES[own][0] == "Minor" and 3 in MODES[own][2]:
        major_i = seconds(tonic, MAJOR_TRIADS)
        picardy = major_i > 0 and seconds(tonic, MINOR_TRIADS) >= PICARDY_MINOR_RATIO * major_i \
            and seconds(tonic + 1, MAJOR_TRIADS | MINOR_TRIADS) < PICARDY_BII_RATIO * major_i
    for s in segments:
        ns = chord_notes(s["chord"])
        if ns is None:
            continue
        d = s["end"] - s["start"]
        total += d
        counts = not on_tonic(s["chord"]) or (picardy and root_q(s["chord"])[1] in MAJOR_TRIADS)
        if not ns <= home and counts:
            c = held.setdefault(s["chord"].split("/")[0], [ns, 0.0, 0])
            c[1] += d
            c[2] += 1
    if total <= 0:
        return []
    # each outside chord -> its closest mode
    chosen = {}
    for label, (ns, sec, n) in held.items():
        if sec < CHORD_MIN_SECONDS or n < MIN_SEGMENTS:
            continue
        fits = [m for m in cands if ns <= source_scale(m, tonic)]
        if not fits:
            continue
        m = min(fits, key=lambda m: (cost[m], cands.index(m)))
        chosen.setdefault(m, []).append((label, ns, sec, n))
    # merge modes whose chords all fit one slightly further mode
    while len(chosen) >= 2:
        best = None
        for m in cands:
            sc = source_scale(m, tonic)
            # in a minor key V (Harmonic Minor) is the key's own dominant, the tracker's most common note: it never merges away
            group = [k for k, v in chosen.items() if all(ns <= sc for _, ns, _, _ in v)
                     and not (k == "harmonic_minor" and MODES[own][0] == "Minor")]
            if len(group) >= 2:
                alt = source_scale(m, tonic) - home        # no new alterations: E + D in A minor never merge into A Major
                if alt <= frozenset().union(*(source_scale(k, tonic) - home for k in group))                         and cost[m] <= min(cost[k] for k in group) + MERGE_SLACK:
                    key = (-len(group), cost[m], cands.index(m))
                    if best is None or key < best[0]:
                        best = (key, m, group)
        if best is None:
            break
        _, m, group = best
        merged = [c for k in group for c in chosen.pop(k)]
        chosen.setdefault(m, []).extend(merged)
    found = []
    for m, cs in chosen.items():
        sec = sum(c[2] for c in cs)
        if sec < max(MIN_SECONDS, MIN_SHARE * total):
            continue
        off, sc, _ = _SRC[m]
        found.append({"from": source_name(m, tonic), "tonic": mode_tonic((tonic + off) % 12, {"ionian": "major", "aeolian": "minor"}.get(sc, sc)), "mode": sc,
                      "chords": [c[0] for c in sorted(cs, key=lambda c: -c[2])],
                      "seconds": round(sec, 1), "share": round(sec / total, 3)})
    found.sort(key=lambda f: -f["seconds"])
    return found[:MAX_MODES]


def borrow_note(found):
    return ("May borrow from " + ", ".join(f["from"] for f in found)) if found else None
