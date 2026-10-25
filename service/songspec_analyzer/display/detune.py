"""Detune strings: '+23.4 ¢', 'pitch by −23.4 ¢ to fix', and the wording for a song that sits a whole semitone (or more) away."""
import math

from .key import house_key

MINUS = "−"


def wrap_cents(c):
    """Onto [-50, +50)."""
    return c - 100.0 * math.floor((c + 50.0) / 100.0)


def signed(c, decimals=1):
    s = f"{abs(c):.{decimals}f}"
    if float(s) == 0.0:
        return f"{0:.{decimals}f}"
    return ("+" if c > 0 else MINUS) + s


def detune_display(cents, total_cents=None, tonic=None, mode=None):
    """cents: offset from A440 on [-50, 50). total_cents: the full transposition when it is known (a reference key from metadata or the
    original recording), e.g. +118 for a song sped up by a semitone and a bit. tonic/mode: the key as heard (on the tuned grid).
    Returns {'display', 'fix', 'a4_hz', 'semitones', 'total_text', 'key_text'}."""
    c = round(float(cents), 1)
    out = {"display": f"{signed(c)} ¢", "fix": f"pitch by {signed(-c)} ¢ to fix" if abs(c) >= 0.05 else "on A440, nothing to fix",
           "a4_hz": round(440.0 * 2.0 ** (c / 1200.0), 2), "semitones": 0, "total_text": None, "key_text": None}
    if total_cents is not None and abs(total_cents) >= 50.0:
        semis = int(round((total_cents - c) / 100.0))
        out["semitones"] = semis
        word = "semitone" if abs(semis) == 1 else "semitones"
        out["total_text"] = f"{'+' if semis > 0 else MINUS}{abs(semis)} {word}, {signed(c)} ¢"
    if tonic is not None and mode is not None and abs(c) >= 0.05:
        k = house_key(tonic, mode).rstrip("*")
        tonic_name = k.split()[0]
        direction = "above" if c > 0 else "below"
        verb = "down" if c > 0 else "up"
        out["key_text"] = (f"Plays in {k} ({signed(c)} ¢ {direction} {tonic_name}). "
                           f"If you pitch {verb} {abs(c):.1f} ¢, it's {k} at A440.")
    return out
