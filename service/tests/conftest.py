import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

# every test run gets its own data folder (uploads + database), set before the app module is imported
_TMP = tempfile.mkdtemp(prefix="songspec-test-")
os.environ["SONGSPEC_DATA_DIR"] = _TMP
os.environ.setdefault("SONGSPEC_RATE_PER_MIN", "0")          # no rate limit unless a test asks for one
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def synth_song(path, seconds=8.0, sr=44100, bpm=120.0, cents=0.0, seed=0):
    """A tiny synthetic 'song': an A minor triad pad (tuned `cents` off A440) plus a click on every beat."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    ref = 440.0 * 2 ** (cents / 1200)
    y = np.zeros_like(t)
    for semis in (0, 3, 7, -12):
        f = ref * 2 ** (semis / 12)
        for h in range(1, 6):
            y += 0.08 / h * np.sin(2 * np.pi * f * h * t)
    beat = 60.0 / bpm
    for bt in np.arange(0, seconds, beat):
        i = int(bt * sr)
        n = min(len(t) - i, 2000)
        y[i:i + n] += 0.5 * np.exp(-np.arange(n) / 200.0) * rng.standard_normal(n)
    y = 0.8 * y / np.abs(y).max()
    sf.write(path, np.stack([y, y], axis=1).astype(np.float32), sr, subtype="PCM_16")
    return path


@pytest.fixture
def song(tmp_path):
    return synth_song(str(tmp_path / "song.wav"))
