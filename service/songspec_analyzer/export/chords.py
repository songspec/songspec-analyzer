"""Chord exports: MIDI (chords on the detected beat grid, with a tempo map so the notes line up with the audio in a DAW), ChordPro and
plain text. transpose / simplify are applied to the chord names first (the same transforms as client/formatters.js)."""
import struct

from ..chords.vocab import FLAT, QUALITIES, TRIAD_OF, split

PPQ = 480


def transform(name, transpose=0, simplify=False):
    if name in ("N", "X"):
        return name
    base, _, bass = name.partition("/")
    root, q = split(base)
    if simplify:
        q, bass = TRIAD_OF[q], ""
    out = FLAT[(root + transpose) % 12] + q
    if bass:
        out += "/" + FLAT[(FLAT.index(bass) + transpose) % 12]
    return out


def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def _track(events):
    """events: [(abs_tick, bytes)] -> an MTrk chunk."""
    data, last = b"", 0
    for tick, ev in sorted(events, key=lambda e: e[0]):
        data += _vlq(tick - last) + ev
        last = tick
    data += _vlq(0) + b"\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(data)) + data


def chord_notes(name):
    """MIDI notes of a chord: bass an octave below, voicing around middle C."""
    if name in ("N", "X"):
        return []
    base, _, bass = name.partition("/")
    root, q = split(base)
    notes = [48 + (root + i) % 12 + (12 if (root + i) % 12 < 5 else 0) for i in QUALITIES[q][0]]
    b = FLAT.index(bass) if bass else root
    return [36 + b] + sorted(set(notes))


def midi(view, transpose=0, simplify=False):
    """Type-1 MIDI: track 0 = tempo map (one tempo per beat, so tick = beat), track 1 = chords, track 2 = markers (key)."""
    tempo = view.get("tempo") or {}
    beats = list(tempo.get("beats") or [])
    segs = (view.get("chords") or {}).get("segments") or []
    if len(beats) < 2 or not segs:
        raise ValueError("needs beats and chords")
    import bisect
    # the grid: every beat is one quarter note at its own tempo; audio before the first beat is one more "quarter" (the lead-in)
    grid = ([0.0] if beats[0] > 0.02 else []) + beats

    def tick(t):
        i = min(max(bisect.bisect_right(grid, t) - 1, 0), len(grid) - 2)
        return max(0, int(round((i + (t - grid[i]) / (grid[i + 1] - grid[i])) * PPQ)))

    tempo_events = [(k * PPQ, b"\xff\x51\x03" + min(int(round((grid[k + 1] - grid[k]) * 1e6)), 0xFFFFFF).to_bytes(3, "big"))
                    for k in range(len(grid) - 1)]
    t0 = [(0, b"\xff\x03" + _vlq(9) + b"Tempo map")] + tempo_events
    t1 = [(0, b"\xff\x03" + _vlq(6) + b"Chords")]
    for s in segs:
        name = transform(s["chord"], transpose, simplify)
        a = tick(s["start"])
        z = max(tick(s["end"]) - 1, a + 1)
        for n in chord_notes(name):
            t1.append((a, bytes([0x90, n, 80])))
            t1.append((z, bytes([0x80, n, 0])))
        if name != "N":
            txt = name.encode("utf-8")
            t1.append((a, b"\xff\x06" + _vlq(len(txt)) + txt))
    key = ((view.get("key") or {}).get("display") or {}).get("key")
    t2 = [(0, b"\xff\x03" + _vlq(4) + b"Info")]
    if key:
        k = f"Key: {key}".encode("utf-8")
        t2.append((0, b"\xff\x01" + _vlq(len(k)) + k))
    body = b"MThd" + struct.pack(">IHHH", 6, 1, 3, PPQ) + _track(t0) + _track(t1) + _track(t2)
    return body, "audio/midi", "mid"


def _bars(view, transpose, simplify):
    """Chord names per bar (downbeats), for ChordPro / TXT."""
    tempo = view.get("tempo") or {}
    segs = (view.get("chords") or {}).get("segments") or []
    downs = list(tempo.get("downbeats") or [])
    if not downs:
        return [[transform(s["chord"], transpose, simplify)] for s in segs]
    bars = []
    for i, d in enumerate(downs):
        e = downs[i + 1] if i + 1 < len(downs) else float("inf")
        names = [transform(s["chord"], transpose, simplify) for s in segs if s["end"] > d + 1e-3 and s["start"] < e - 1e-3]
        bars.append(names or ["N"])
    return bars


def _header(view):
    t = view.get("track") or {}
    title = t.get("title") or (t.get("file_name") or "Untitled").rsplit(".", 1)[0]
    key = ((view.get("key") or {}).get("display") or {}).get("key")
    bpm = ((view.get("tempo") or {}).get("display") or {}).get("bpm")
    return title, t.get("artist"), key, bpm


def chordpro(view, transpose=0, simplify=False):
    title, artist, key, bpm = _header(view)
    lines = [f"{{title: {title}}}"]
    if artist:
        lines.append(f"{{artist: {artist}}}")
    if key:
        lines.append(f"{{key: {key.split(' / ')[0].replace('*', '')}}}")
    if bpm:
        lines.append(f"{{tempo: {bpm}}}")
    if transpose:
        lines.append(f"{{comment: transposed {transpose:+d} semitones}}")
    bars = _bars(view, transpose, simplify)
    for i in range(0, len(bars), 4):
        lines.append(" ".join("| " + " ".join(f"[{c}]" for c in b) for b in bars[i:i + 4]) + " |")
    return "\n".join(lines) + "\n", "text/plain; charset=utf-8", "cho"


def txt(view, transpose=0, simplify=False):
    title, artist, key, bpm = _header(view)
    head = f"{title}" + (f" - {artist}" if artist else "") + (f"  ({key}, {bpm} BPM)" if key and bpm else "")
    bars = _bars(view, transpose, simplify)
    rows = [" | ".join(" ".join(b) for b in bars[i:i + 4]) for i in range(0, len(bars), 4)]
    progs = (view.get("chords") or {}).get("progressions") or []
    tail = ["", "Progressions: " + ", ".join(p["display"] for p in progs)] if progs else []
    return "\n".join([head, ""] + rows + tail) + "\n", "text/plain; charset=utf-8", "txt"
