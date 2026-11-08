"""Key names in house style: the spellings of the SongSpec tracker. Every key name goes through key_tonic / mode_tonic below
(and their twins in client/formatters.js and rust/songspec-core/src/notation.rs; service/tests/display_cases.json checks all three).

- Key field, full words: Major tonics C Db D Eb E F F# G Ab A Bb B; Minor tonics C C# D Eb E F F# G G# A Bb B
  ("Eb Minor", "Db Major", "C# Minor", "F# Major").
- A modal key is shown as its parent major/minor with an asterisk, the real mode goes in the note: A# Phrygian -> key
  "Bb Minor*", note "A# Phrygian". Mode names take their parent's spelling (Db Mixolydian, C# Dorian, Eb Harmonic Minor), except
  Phrygian / Locrian / Phrygian Dominant on D#, G#, A#, which the tracker spells with sharps (D# Phrygian 170 times against
  Eb Phrygian 7 times in its 57,857 rows). "- Key from Hook Theory" when Hooktheory was the source.
- Chord roots follow the key signature: sharps in sharp keys (G D A E B F# Major and their relative minors: C#m, D#dim in
  B Major), flats otherwise (Bb, Eb, Ab in C Major).
- Several section keys: "C Minor* / C Major", note "Intro in C Phrygian Dominant, Verses & Choruses in C Major".
- Camelot (8A = A Minor, 8B = C Major) and Open Key (1m = A Minor, 1d = C Major) as secondary labels.
"""
import re

FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]      # internal chord labels
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]      # the tracker's Major keys
MINOR_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]      # the tracker's Minor keys
SHARP_MODES = {"phrygian", "locrian", "phrygian_dominant"}                          # D# / G# / A# Phrygian in the tracker
SHARP_MAJOR_KEYS = {7, 2, 9, 4, 11, 6}                                              # G D A E B F#: chords spelled with sharps
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# mode -> (parent "Major"/"Minor", display name, interval set from the tonic)
MODES = {
    "major":             ("Major", "Major",             (0, 2, 4, 5, 7, 9, 11)),
    "minor":             ("Minor", "Minor",             (0, 2, 3, 5, 7, 8, 10)),
    "ionian":            ("Major", "Ionian",            (0, 2, 4, 5, 7, 9, 11)),
    "dorian":            ("Minor", "Dorian",            (0, 2, 3, 5, 7, 9, 10)),
    "phrygian":          ("Minor", "Phrygian",          (0, 1, 3, 5, 7, 8, 10)),
    "lydian":            ("Major", "Lydian",            (0, 2, 4, 6, 7, 9, 11)),
    "mixolydian":        ("Major", "Mixolydian",        (0, 2, 4, 5, 7, 9, 10)),
    "aeolian":           ("Minor", "Aeolian",           (0, 2, 3, 5, 7, 8, 10)),
    "locrian":           ("Minor", "Locrian",           (0, 1, 3, 5, 6, 8, 10)),
    "harmonic_minor":    ("Minor", "Harmonic Minor",    (0, 2, 3, 5, 7, 8, 11)),
    "phrygian_dominant": ("Minor", "Phrygian Dominant", (0, 1, 4, 5, 7, 8, 10)),
    "melodic_minor":     ("Minor", "Melodic Minor",     (0, 2, 3, 5, 7, 9, 11)),
    "harmonic_major":    ("Major", "Harmonic Major",    (0, 2, 4, 5, 7, 8, 11)),
    "lydian_dominant":   ("Major", "Lydian Dominant",   (0, 2, 4, 6, 7, 9, 10)),
    "hungarian_minor":   ("Minor", "Hungarian Minor",   (0, 2, 3, 6, 7, 8, 11)),
}
PLAIN = {"major", "minor", "ionian", "aeolian"}      # shown without an asterisk


def parse_pitch(name):
    """'Bb' / 'A#' / 'c#' / 'Db' / 'E♭' -> pitch class 0..11."""
    m = re.fullmatch(r"\s*([A-Ga-g])\s*([#♯b♭]*)\s*", name)
    if not m:
        raise ValueError(f"not a note name: {name!r}")
    pc = _PC[m.group(1).upper()]
    for ch in m.group(2):
        pc += 1 if ch in "#♯" else -1
    return pc % 12


def norm_mode(mode):
    m = str(mode).strip().lower().replace(" ", "_").replace("-", "_")
    m = {"maj": "major", "min": "minor", "dorion": "dorian"}.get(m, m)
    if m not in MODES:
        raise ValueError(f"unknown mode: {mode!r}")
    return m


def parse_key(text):
    """'A# Phrygian' / 'Bb minor' / 'C# Major' / 'F#m' -> (tonic pc, mode). The tracker's own trailing '*' is ignored."""
    t = text.strip().rstrip("*").strip()
    m = re.fullmatch(r"([A-Ga-g][#♯b♭]?)\s*(m|maj|min|.*)", t)
    if not m:
        raise ValueError(f"not a key: {text!r}")
    tonic = parse_pitch(m.group(1))
    rest = m.group(2).strip()
    mode = "minor" if rest == "m" else norm_mode(rest or "major")
    return tonic, mode


def key_tonic(pc, mode="major"):
    """The tonic as the key field spells it, by the parent Major / Minor: 'C#' in C# Minor, 'Db' in Db Major."""
    return (MINOR_NAMES if MODES[norm_mode(mode)][0] == "Minor" else MAJOR_NAMES)[pc % 12]


def mode_tonic(pc, mode):
    """The tonic as a mode name spells it: 'A# Phrygian', 'Bb Dorian', 'Db Mixolydian'."""
    m = norm_mode(mode)
    if m in SHARP_MODES and pc % 12 in (3, 8, 10):
        return SHARP_NAMES[pc % 12]
    return key_tonic(pc, m)


def tonic_name(pc, spelling="flat"):
    return (FLAT_NAMES if spelling == "flat" else SHARP_NAMES)[pc % 12]


def mode_name(tonic, mode, spelling=None):
    """The real name of the key in the note: 'A# Phrygian', 'C# Minor', 'Eb Harmonic Minor' (tracker spelling)."""
    return f"{mode_tonic(tonic, mode)} {MODES[norm_mode(mode)][1]}"


def house_key(tonic, mode):
    """Key field in house style: 'Bb Minor', 'Bb Minor*' (modal), 'Db Major', 'C# Minor', 'F# Major'."""
    mode = norm_mode(mode)
    parent = MODES[mode][0]
    return f"{key_tonic(tonic, mode)} {parent}" + ("" if mode in PLAIN else "*")


def sharp_key(tonic, mode):
    """Is this a sharp key (chords spelled C#, F#, G#)? By the key signature of the parent major (relative major for minor)."""
    rel = tonic % 12 if MODES[norm_mode(mode)][0] == "Major" else (tonic + 3) % 12
    return rel in SHARP_MAJOR_KEYS


def spell_chord(label, tonic, mode):
    """A chord label ('Db', 'Ebm7/Bb', 'N') respelled for the key: 'C#', 'D#m7/A#' in a sharp key, unchanged otherwise."""
    if label in (None, "N", "X") or tonic is None or not sharp_key(tonic, mode):
        return label
    m = _CHORD_RE.match(label)
    if not m:
        return label
    out = SHARP_NAMES[parse_pitch(m.group(1))] + m.group(2)
    if m.group(3):
        out += "/" + SHARP_NAMES[parse_pitch(m.group(3))]
    return out


def is_minor(mode):
    return MODES[norm_mode(mode)][0] == "Minor"


def camelot(tonic, mode):
    """'3A' for Bb Minor, '8B' for C Major. Modes use their parent major/minor of the same tonic."""
    minor = is_minor(mode)
    major_pc = (tonic + 3) % 12 if minor else tonic % 12
    num = ((7 * major_pc) % 12 + 7) % 12 + 1
    return f"{num}{'A' if minor else 'B'}"


def open_key(tonic, mode):
    """Open Key notation: '1d' = C Major, '1m' = A Minor, '8m' = Bb Minor."""
    c = camelot(tonic, mode)
    num = (int(c[:-1]) - 8) % 12 + 1
    return f"{num}{'m' if c[-1] == 'A' else 'd'}"


def spelling_of(text):
    """Did the source spell its tonic with a sharp? ('A# Phrygian' -> 'sharp')"""
    return "sharp" if re.match(r"\s*[A-Ga-g]\s*[#♯]", text or "") else "flat"


def key_display(tonic, mode, source=None, source_text=None):
    """One key: {'key', 'note', 'camelot', 'open_key'} in house style.
    source='hooktheory' adds ' - Key from Hook Theory' to the note; source_text keeps the source's spelling in the note."""
    mode = norm_mode(mode)
    note = None
    if mode not in PLAIN:
        note = mode_name(tonic, mode)
    if source == "hooktheory":
        note = (note + " - Key from Hook Theory") if note else "Key from Hook Theory"
    return {"key": house_key(tonic, mode), "note": note, "camelot": camelot(tonic, mode), "open_key": open_key(tonic, mode)}


def sections_display(sections, labels=None):
    """Several section keys -> one key field and one note.
    sections: [{'start', 'end', 'tonic', 'mode'}] in time order. labels: optional names per section ('Intro', 'Verses & Choruses').
    Neighbouring sections with the same key are merged. Returns {'key', 'note'}."""
    merged = []
    for i, s in enumerate(sections):
        lab = labels[i] if labels else None
        k = (s["tonic"] % 12, norm_mode(s["mode"]))
        if merged and merged[-1]["k"] == k:
            merged[-1]["end"] = s["end"]
            if lab and lab not in merged[-1]["labels"]:
                merged[-1]["labels"].append(lab)
            continue
        merged.append({"k": k, "start": s["start"], "end": s["end"], "labels": [lab] if lab else []})
    if not merged:
        return {"key": None, "note": None}
    if len(merged) == 1:
        d = key_display(*merged[0]["k"])
        return {"key": d["key"], "note": d["note"]}
    names = []
    for m in merged:
        n = house_key(*m["k"])
        if n not in names:
            names.append(n)
    parts = []
    for m in merged:
        where = " & ".join(m["labels"]) if m["labels"] else f"{fmt_time(m['start'])}-{fmt_time(m['end'])}"
        parts.append(f"{where} in {mode_name(*m['k'])}")
    return {"key": " / ".join(names), "note": ", ".join(parts)}


def fmt_time(t):
    t = max(0, int(round(t)))
    return f"{t // 60}:{t % 60:02d}"


# ---------------------------------------------------------------------------------------------------- roman numerals
_ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII"]
_CHORD_RE = re.compile(r"^([A-G][#b]?)(.*?)(?:/([A-G][#b]?))?$")


def chord_name(root, quality, bass=None, sharp=False):
    """'Bbm', 'Ebmaj7', 'C/E' (flats), or 'C#m', 'D#7' with sharp=True (a sharp key, see sharp_key)."""
    names = SHARP_NAMES if sharp else FLAT_NAMES
    s = names[root % 12] + quality
    if bass is not None and bass % 12 != root % 12:
        s += "/" + names[bass % 12]
    return s


def roman(root, quality, tonic, mode):
    """Roman numeral of a chord relative to the key. Lower case for minor / diminished chords. Accidentals are relative to the key's
    PARENT scale (major scale for Major / Lydian / Mixolydian, natural minor for Minor / Dorian / Phrygian / ...), the pop convention:
    Bbm-Gb-Db-Ab in Bb Minor is i-VI-III-VII, a Bb chord in C Major (or C Mixolydian) is ♭VII, Db in C Phrygian is ♭II."""
    minor_key = is_minor(mode)
    scale = MODES["minor" if minor_key else "major"][2]
    iv = (root - tonic) % 12
    if iv in scale:
        deg, acc = scale.index(iv), ""
    elif minor_key and iv in (4, 9, 11):                  # the raised 3rd / 6th / 7th of minor (E, A, B in C Minor): ♯III, ♯VI, ♯VII
        deg, acc = scale.index((iv - 1) % 12), "♯"
    else:                                                 # ♭II, ♭III, ♭V, ♭VI, ♭VII
        deg, acc = scale.index((iv + 1) % 12), "♭"
    q = quality
    minorish = q.startswith("m") and not q.startswith("maj") or q.startswith("dim") or q == "m7b5"
    num = _ROMAN[deg].lower() if minorish else _ROMAN[deg]
    suffix = ""
    if q.startswith("dim7"):
        suffix = "°7"
    elif q.startswith("dim"):
        suffix = "°"
    elif q == "m7b5":
        suffix = "ø7"
    elif q.startswith("aug"):
        suffix = "+"
    elif q in ("7", "m7"):
        suffix = "7"
    elif q in ("maj7",):
        suffix = "maj7"
    elif q in ("9", "m9"):
        suffix = "9"
    elif q in ("6", "m6"):
        suffix = "6"
    elif q.startswith("sus"):
        suffix = q
    elif q == "add9":
        suffix = "add9"
    return acc + num + suffix
