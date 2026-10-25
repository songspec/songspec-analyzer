"""BPM in house style (the user's tracker; it matches BPM Fixer's default 80-160 range).

- below 80 -> doubled (57 -> 114); 160 or above -> halved (165 -> 82.5); a compound meter (6/8, 9/8, 12/8) keeps its raw felt value.
- 2 decimals internally; shown as a whole number when within +-0.05 of one, otherwise with one decimal.
- clearly different section tempos (live drift): '~122 - 127' with the note 'BPM inconsistent - played live'.
"""
import math

COMPOUND = {"6/8", "9/8", "12/8"}
LIVE_NOTE = "BPM inconsistent - played live"


def house_bpm(raw, signature="4/4"):
    """(bpm, compound) with the house octave rule applied."""
    if raw is None or not math.isfinite(raw) or raw <= 0:
        return None, False
    if signature in COMPOUND:
        return round(raw, 2), True
    b = float(raw)
    while b < 80.0:
        b *= 2.0
    while b >= 160.0:
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


def alternates(raw):
    """The usual metrical-level alternatives a user may want instead: half and double of the raw felt tempo."""
    if not raw:
        return []
    return [round(raw / 2.0, 2), round(raw, 2), round(raw * 2.0, 2)]


def bpm_display(raw, signature="4/4", sections=None, live=False):
    """The BPM field. sections: [{'bpm': raw felt bpm, 'start', 'end'}]; live=True when the tempo engine flags drift between them.
    Returns {'bpm', 'display', 'note', 'compound', 'range'}."""
    bpm, compound = house_bpm(raw, signature)
    out = {"bpm": bpm, "display": fmt_bpm(bpm), "note": None, "compound": compound, "range": None}
    if live and sections and len(sections) >= 2:
        vals = [house_bpm(s["bpm"], signature)[0] for s in sections if s.get("bpm")]
        # every section must sit at the same metrical level as the headline before taking the range
        vals = [v * 2 if v < 0.75 * bpm else v / 2 if v > 1.5 * bpm else v for v in vals]
        lo, hi = min(vals), max(vals)
        if round(hi) - round(lo) >= 2:
            out["range"] = [round(lo, 2), round(hi, 2)]
            out["display"] = f"~{int(round(lo))} - {int(round(hi))}"
            out["note"] = LIVE_NOTE
    if signature and signature != "4/4":
        sig_note = f"Played in {signature} time signature"
        out["note"] = f"{out['note']}. {sig_note}" if out["note"] else sig_note
    return out
