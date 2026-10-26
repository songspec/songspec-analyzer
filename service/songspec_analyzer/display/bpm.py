"""BPM in house style (the user's BPM & Key Tracker; it matches BPM Fixer's default 80-160 range).

- below 80 -> doubled (57 -> 114); 160 or above -> halved (165 -> 82.5), decided on the value as displayed (159.98 shows as 80).
- compound meters (6/8, 9/8, 12/8) keep their raw pulse and are not folded: the tracker writes the EIGHTH-note tempo with an
  asterisk ("180*", "213*" = 3 x the dotted-quarter feel), plus "may work in 4/4 at <2 x the feel>". Other non-4/4 meters get the
  asterisk too ("120*", "Played in 3/4 time signature").
- 2 decimals internally; shown as a whole number when within +-0.05 of one, otherwise with one decimal.
- per section, in the tracker's words: steady "128", moderately inconsistent "~98.1", inconsistent / played live "~122 - 127";
  a beat switch lists the sections in order: "153 / 104".
"""
import math

COMPOUND = {"6/8", "9/8", "12/8"}
LIVE_NOTE = "BPM inconsistent - played live"
MODERATE_NOTE = "BPM moderately inconsistent"


def house_bpm(raw, signature="4/4"):
    """(bpm, compound). raw = the felt tempo (dotted quarters in compound meters)."""
    if raw is None or not math.isfinite(raw) or raw <= 0:
        return None, False
    if signature in COMPOUND:
        return round(raw * 3.0, 2), True
    b = float(raw)
    while round(b, 1) < 79.95:
        b *= 2.0
    while round(b, 1) >= 159.95:
        b /= 2.0
    return round(b, 2), False


def fmt_bpm(b):
    """114.02 -> '114', 82.5 -> '82.5', 127.96 -> '128'."""
    if b is None:
        return None
    r = round(b)
    if abs(b - r) <= 0.05 + 1e-9:
        return str(int(r))
    return f"{b:.1f}"


def _half(x):
    return round(x * 2.0) / 2.0


def section_text(sec, signature="4/4"):
    """One section's BPM text: '128', '~98.1', '~122 - 127' (asterisk for non-4/4 meters)."""
    bpm, _ = house_bpm(sec["bpm"], signature)
    if bpm is None:
        return "N/A"
    star = "*" if signature and signature != "4/4" else ""
    st = sec.get("steadiness", "steady")
    if st == "live" and sec.get("lo") and sec.get("hi"):
        f = bpm / sec["bpm"]                                   # the same octave as the section's own value
        lo, hi = _half(sec["lo"] * f), _half(sec["hi"] * f)
        if fmt_bpm(lo) != fmt_bpm(hi):
            return f"~{fmt_bpm(lo)} - {fmt_bpm(hi)}{star}"
    if st in ("live", "moderate"):
        return f"~{fmt_bpm(bpm)}{star}"
    return f"{fmt_bpm(bpm)}{star}"


def _where(i, n, sec):
    if n == 1:
        return ""
    if i == 0:
        return "Intro "
    if i == n - 1:
        return "Outro "
    m = lambda t: f"{int(t) // 60}:{int(t) % 60:02d}"
    return f"{m(sec['start'])}-{m(sec['end'])} "


def bpm_display(raw, signature="4/4", sections=None):
    """raw: the main section's felt BPM. sections: [{'start', 'end', 'bpm' (felt), 'lo', 'hi', 'steadiness'}] in time order
    (one per beat-switch part). Returns {'bpm', 'display', 'note', 'compound', 'range'}."""
    bpm, compound = house_bpm(raw, signature)
    star = "*" if signature and signature != "4/4" else ""
    out = {"bpm": bpm, "display": fmt_bpm(bpm) + star if bpm else None, "note": None, "compound": compound, "range": None}
    notes = []
    if sections:
        out["display"] = " / ".join(section_text(s, signature) for s in sections)
        for i, s in enumerate(sections):
            st = s.get("steadiness", "steady")
            if st == "live":
                notes.append(_where(i, len(sections), s) + LIVE_NOTE)
            elif st == "moderate":
                notes.append(_where(i, len(sections), s) + MODERATE_NOTE)
        if len(sections) == 1 and sections[0].get("steadiness") == "live":
            f = bpm / sections[0]["bpm"]
            out["range"] = [round(sections[0]["lo"] * f, 2), round(sections[0]["hi"] * f, 2)]
    if signature and signature != "4/4":
        notes.append(f"Played in {signature} time signature")
    notes = [n[0].upper() + n[1:] for n in notes]
    out["note"] = ". ".join(notes) or None
    return out
