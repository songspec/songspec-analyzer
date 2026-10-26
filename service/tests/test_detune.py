"""Detune: the Centsible binding, the fusion rule, the stages through the API. Synthetic songs of exactly known tuning."""
import os
import time

import numpy as np
import pytest
import soundfile as sf

from conftest import synth_song

try:
    from songspec_analyzer.detune import _centsible
except ImportError:
    _centsible = None

from songspec_analyzer.detune import estimators as est
from songspec_analyzer.detune import fuse, measure

needs_core = pytest.mark.skipif(_centsible is None, reason="Centsible binding not built (tools/build_native.py)")


def pad(cents, seconds=25.0, sr=44100, root_semis=0, minor=True):
    """Sustained triads (i - VI - III - VII in A minor) with harmonics, tuned `cents` off A440, plus a bass note."""
    t = np.arange(int(seconds * sr)) / sr
    ref = 440.0 * 2 ** (cents / 1200)
    prog = [(0, 3, 7), (-4, 0, 3), (3, 7, 10), (-2, 2, 5)] if minor else [(0, 4, 7), (5, 9, 12), (7, 11, 14), (0, 4, 7)]
    y = np.zeros_like(t)
    seg = len(t) // 8
    for i in range(8):
        chord = prog[i % 4]
        a, b = i * seg, (i + 1) * seg
        for s in chord:
            f = ref * 2 ** ((s + root_semis) / 12)
            for h in range(1, 7):
                y[a:b] += 0.06 / h * np.sin(2 * np.pi * f * h * t[a:b])
        fb = ref * 2 ** ((chord[0] + root_semis - 24) / 12)
        for h in range(1, 4):
            y[a:b] += 0.1 / h * np.sin(2 * np.pi * fb * h * t[a:b])
    return (0.8 * y / np.abs(y).max()).astype(np.float32), sr


@needs_core
@pytest.mark.parametrize("cents", [0.0, 23.4, -37.0, 49.0])
def test_centsible_reads_known_tuning(cents):
    y, sr = pad(cents)
    r = _centsible.analyze_track(y, sr)
    assert r["tuning"]["valid"]
    assert est.wrap(r["tuning"]["cents"] - cents) == pytest.approx(0.0, abs=0.3)


@needs_core
def test_analyze_tuning_is_analyze_tracks_tuning():
    """The service's fast path (tuning only) must read exactly what analyzeTrack (the CLI / plugin file analysis) reads."""
    for cents in (7.5, -31.0):
        y, sr = pad(cents, seconds=30)
        full = _centsible.analyze_track(y, sr)["tuning"]
        fast = _centsible.analyze_tuning(y, sr)
        assert fast == full


@needs_core
def test_key_probabilities_match_centsibles_own_decision():
    """The binding repeats estimateKey's model weights; this catches a drift between the two copies."""
    for cents, minor in ((12.0, True), (-20.0, False)):
        y, sr = pad(cents, minor=minor, root_semis=3)
        a = _centsible.analyze_track(y, sr)
        k = _centsible.key_probabilities(y, sr, a["tuning"]["cents"])
        assert k["valid"] and a["key"]["valid"]
        assert (k["tonic"], k["minor"]) == (a["key"]["root"], a["key"]["minor"])
        assert sum(k["key_prob"]) == pytest.approx(1.0, abs=1e-4)


def test_fusion_rule():
    """Measured policy (eval/REPORT.md section 2): Centsible whenever it reads, even with a wide interval; CQT-120 otherwise."""
    assert fuse({"centsible": (12.3, 0.4), "chroma120": (20.0, 3.0), "librosa": (25.0, 4.0)}) == (12.3, 0.4, "centsible")
    assert fuse({"centsible": (12.3, 6.0), "chroma120": (14.0, 3.0), "librosa": (15.0, 4.0)}) == (12.3, 6.0, "centsible")
    c, ci, rule = fuse({"centsible": (None, 0.0), "chroma120": (14.0, 3.0), "librosa": (15.0, 4.0)})
    assert (c, ci) == (14.0, 5.0) and rule.startswith("chroma120")
    assert fuse({"centsible": (None, 0.0), "chroma120": (None, 0), "librosa": (3.0, 4.0)})[0] is None   # librosa never decides


@needs_core
def test_cross_checks_agree_on_clean_pad():
    y, sr = pad(23.4)
    m = measure(y, sr)
    assert m["rule"] == "centsible"
    assert est.wrap(m["votes"]["chroma120"][0] - 23.4) == pytest.approx(0, abs=2.0)
    assert est.wrap(m["votes"]["librosa"][0] - 23.4) == pytest.approx(0, abs=3.0)


@needs_core
def test_detune_and_quick_key_through_the_api(tmp_path):
    from fastapi.testclient import TestClient
    from songspec_analyzer.api.app import create_app
    from songspec_analyzer.config import Settings
    y, sr = pad(23.4, seconds=30)
    p = tmp_path / "pad.wav"
    sf.write(p, y, sr)
    with TestClient(create_app(Settings(data_dir=tmp_path / "d", rate_per_min=0))) as c:
        with open(p, "rb") as f:
            aid = c.post("/v1/analyses", files={"file": ("pad.wav", f, "audio/wav")}).json()["id"]
        t0 = time.time()
        while True:
            v = c.get(f"/v1/analyses/{aid}").json()
            if v["status"] in ("done", "error") or time.time() - t0 > 180:
                break
            time.sleep(0.2)
    assert v["status"] == "done", v
    assert v["stages"]["detune"] == "done" and v["stages"]["key_quick"] == "done"
    d = v["detune"]
    assert d["cents"] == pytest.approx(23.4, abs=0.3)
    assert d["display"]["display"] == "+23.4 ¢" and d["display"]["fix"] == "pitch by −23.4 ¢ to fix"
    # the quick key only (Centsible's detector): i-VI-III-VII with equal bass notes is genuinely ambiguous between the relative
    # pair, Centsible's documented weak spot; the fused key (milestone 6) adds cadence / bass-tonic evidence for that
    assert v["key"]["display"]["key"] in ("A Minor", "C Major") and v["key"]["preliminary"] is True
    assert {c["key"] for c in v["key"]["candidates"][:2]} == {"A Minor", "C Major"}
    assert v["stages"]["detune_drift"] in ("done", "skipped")
