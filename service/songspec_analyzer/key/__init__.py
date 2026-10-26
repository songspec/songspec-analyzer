"""Key detection: independent estimators (estimators.py), profiles (profiles.py), fusion (milestone 6)."""

KEY_NAMES = [f"{n} {'Minor' if i >= 12 else 'Major'}" for i, n in
             enumerate(["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"] * 2)]


def key_index(tonic, minor):
    return tonic % 12 + (12 if minor else 0)
