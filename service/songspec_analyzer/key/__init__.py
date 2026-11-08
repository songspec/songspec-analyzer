"""Key detection: independent estimators (estimators.py), profiles (profiles.py), fusion (milestone 6)."""

from ..display.key import house_key

# index tonic + 12 * minor -> "Db Major", "C# Minor" (the tracker's spellings)
KEY_NAMES = [house_key(i % 12, "minor" if i >= 12 else "major") for i in range(24)]


def key_index(tonic, minor):
    return tonic % 12 + (12 if minor else 0)
