"""Fix BPM end to end: BPM Fixer's process() with the real tracker and Rubber Band (skipped when either is missing)."""
import io
import json
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from test_tempo import _have_model, click_song, grid
from songspec_analyzer.api.app import create_app
from songspec_analyzer.config import Settings
from songspec_analyzer.tempo.fixbpm import rubberband_path

pytestmark = pytest.mark.skipif(not (_have_model() and rubberband_path()), reason="needs Beat This! and Rubber Band")


def test_fix_bpm_makes_a_drifting_song_steady(tmp_path):
    sr = 44100
    tb = grid(108, 40, bpm_end=122)                                  # drifts 108 -> 122 BPM
    y = click_song(tb, 40, sr=sr)
    src = tmp_path / "drifty.wav"
    sf.write(src, np.stack([y, y], axis=1), sr, subtype="PCM_16")
    with TestClient(create_app(Settings(data_dir=tmp_path / "d", rate_per_min=0))) as c:
        with open(src, "rb") as f:
            r = c.post("/v1/fix-bpm", files={"file": ("drifty.wav", f, "audio/wav")},
                       data={"options": json.dumps({"target_bpm": 115, "format": "wav", "signature": "4/4"})})
        assert r.status_code == 202, r.json()
        fid = r.json()["id"]
        t0 = time.time()
        while time.time() - t0 < 600:
            v = c.get(f"/v1/fix-bpm/{fid}").json()
            if v["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        assert v["status"] == "done", v
        assert v["fix"]["target_bpm"] == pytest.approx(115) and v["fix"]["file_name"] == "drifty_fixed_115bpm.wav"
        d = c.get(v["download"])
        assert d.status_code == 200 and "drifty_fixed_115bpm.wav" in d.headers["content-disposition"]
        out, osr = sf.read(io.BytesIO(d.content), always_2d=True)
    # the clicks themselves must now be evenly spaced at 115 BPM (direct check, no tracker in the loop)
    import librosa
    mono = out.mean(axis=1).astype(np.float32)
    on = librosa.onset.onset_detect(y=mono, sr=osr, units="time", backtrack=False, hop_length=256)
    iv = np.diff(on)
    iv = iv[(iv > 0.3) & (iv < 0.8)][2:-2]     # click-to-click gaps; the ends are untouched by design (keep_edges)
    assert len(iv) > 40
    assert np.median(iv) == pytest.approx(60 / 115, abs=0.004)
    assert np.std(iv) / np.mean(iv) < 0.02                            # steady; the source drifted 108 -> 122 (13 %)
    assert list((tmp_path / "d" / "uploads").iterdir()) == []         # the input was deleted
