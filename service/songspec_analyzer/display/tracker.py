"""'Copy as tracker row': Song <TAB> BPM <TAB> Key <TAB> Note, exactly in the house style, so it pastes straight into the Google Sheet."""


def _clean(s):
    return " ".join(str(s or "").replace("\t", " ").split())


def tracker_row(song, bpm_text, key_text, notes):
    """notes: the key note and the BPM note (either may be None); joined the way the tracker writes them ('C Phrygian. Played in 6/8 ...')."""
    note = ". ".join(_clean(n) for n in notes if n and _clean(n))
    return "\t".join([_clean(song), _clean(bpm_text), _clean(key_text), note])
