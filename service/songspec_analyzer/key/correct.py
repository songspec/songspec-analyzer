"""User key corrections ("the key is wrong, it's actually ___").

A correction is stored as feedback (field 'key') and shown on read: every view of that song (the same analysis, a cache hit of the
same file, a re-upload with the same fingerprint, a link matched to it) gets the corrected key, with what was detected kept under
key.corrected.detected. Roman numerals, 'May borrow from' and the detune sentence are recomputed for the corrected key.
The stored analysis itself is never rewritten, so a correction can be superseded (the latest one wins) and the detected key stays
available for retraining (eval/key_retrain.py learns from corrections + the key evidence saved per analysis).
"""
from ..display.key import FLAT_NAMES, MODES, house_key, is_minor, key_display, norm_mode, parse_key


def normalise(text):
    """'a# phrygian' / 'Bbm' / 'C# Major' -> ('Bb Minor*', tonic pc, mode). Raises ValueError for anything that is not a key."""
    tonic, mode = parse_key(str(text))
    return house_key(tonic, mode), tonic, mode


def choices():
    """Every key the correction picker can offer: 24 major/minor keys plus each mode on each tonic, house style."""
    out = []
    for t in range(12):
        for m in MODES:
            if m in ("ionian", "aeolian"):
                continue
            out.append({"value": f"{FLAT_NAMES[t]} {MODES[m][1]}", "key": house_key(t, m), "tonic": FLAT_NAMES[t], "mode": m,
                        "minor": is_minor(m)})
    return out


def apply(view, correction):
    """view: the public analysis dict; correction: {'value', 'created_at', 'user'} -> view with the corrected key (in place)."""
    key = view.get("key")
    if not key or not correction:
        return view
    try:
        _, tonic, mode = normalise(correction["value"])
    except ValueError:
        return view
    mode = norm_mode(mode)
    from .stage import _mode_name
    detected = {k: key.get(k) for k in ("tonic", "scale", "mode", "confidence", "camelot", "open_key", "borrows")}
    detected["display"] = key.get("display")
    kd = key_display(tonic, mode, source_text=correction["value"])
    minor = is_minor(mode)
    k = dict(key)
    k.update(tonic=FLAT_NAMES[tonic], scale="minor" if minor else "major",
             mode=None if mode in ("major", "minor", "ionian", "aeolian") else _mode_name(mode),
             camelot=kd["camelot"], open_key=kd["open_key"], ambiguous=False, sections=[], modulations=[],
             corrected={"key": kd["key"], "value": correction["value"], "at": correction.get("created_at"),
                        "by": correction.get("user"), "detected": detected})
    note = kd["note"]
    segs = (view.get("chords") or {}).get("segments")
    if segs:
        from .borrow import borrow_note, borrowed
        k["borrows"] = borrowed(segs, tonic, mode)
        bn = borrow_note(k["borrows"])
        note = f"{note} - {bn}" if note and bn else (note or bn)
        from ..chords import annotate, progressions
        ch = dict(view["chords"])
        ch["segments"] = annotate([dict(s) for s in segs], tonic, mode)
        downs = (view.get("tempo") or {}).get("downbeats")
        if downs and ch.get("progressions"):
            ch["progressions"] = progressions(ch["segments"], downs)
        view["chords"] = ch
    k["display"] = {"key": kd["key"], "note": note, "ambiguity_text": None}
    view["key"] = k
    if view.get("detune"):
        from ..detune import with_key_text
        view["detune"] = with_key_text(view["detune"], tonic, mode)
    return view

