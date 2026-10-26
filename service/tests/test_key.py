"""Key v2 pieces: modes, section HMM, modulation names, the rotation-equivariant fusion."""
import numpy as np
import pytest

from songspec_analyzer.display.key import MODES
from songspec_analyzer.key.fusion import KeyFusion, candidate_features
from songspec_analyzer.key.modes import centsible_mode, choose_mode
from songspec_analyzer.key.stage import modulation_kind, sections_from_windows, window_keys


def scale_chroma(tonic, mode, tonic_w=2.0, noise=0.0, seed=0):
    c = np.full(12, 0.05)
    for i in MODES[mode][2]:
        c[(tonic + i) % 12] = 1.0
    c[tonic] = tonic_w
    c[(tonic + 7) % 12] = max(c[(tonic + 7) % 12], 1.4)
    if noise:
        c += noise * np.random.default_rng(seed).random(12)
    return c


@pytest.mark.parametrize("mode,minor", [("phrygian", True), ("dorian", True), ("mixolydian", False), ("lydian", False),
                                        ("harmonic_minor", True)])
def test_choose_mode_finds_clear_modes(mode, minor):
    got, _ = choose_mode(scale_chroma(5, mode), 5, minor, margin=0.05)    # the template machinery (off in production)
    assert got == mode


@pytest.mark.parametrize("mode,minor,plain", [("ionian", False, "major"), ("aeolian", True, "minor")])
def test_plain_scales_stay_plain(mode, minor, plain):
    assert choose_mode(scale_chroma(0, mode), 0, minor, margin=0.05)[0] == plain
    assert choose_mode(scale_chroma(5, "phrygian"), 5, True)[0] == "minor"   # production default: never


def test_window_hmm_finds_a_final_chorus_lift_and_ignores_blips():
    bar, bpb = 2.0, 4
    beats = np.arange(0, 64 * bar, bar / bpb)
    downs = beats[::bpb]
    seq = []
    for b in beats:
        bar_i = int(b // bar)
        if 40 <= bar_i < 42:                       # a two-bar blip in another key: must not become a section
            k = (2, "ionian")
        elif bar_i >= 48:                          # the last 16 bars: half-step lift C -> Db major
            k = (1, "ionian")
        else:
            k = (0, "ionian")
        seq.append(scale_chroma(*k))
    seq = np.array(seq)
    wk = window_keys(seq, beats, downs)
    secs = sections_from_windows(wk, 64 * bar, bar)
    assert [s["k"] for s in secs] == [0, 1]
    assert abs(secs[1]["start"] - 48 * bar) <= 6 * bar
    assert modulation_kind(secs[0]["k"], secs[1]["k"]) == "half-step lift"


def test_modulation_names():
    assert modulation_kind(0, 2) == "whole-step lift" and modulation_kind(0, 12) == "mode change"
    assert modulation_kind(21, 0) == "key change"


def test_fusion_learns_a_tiebreaker():
    """Two estimators that cannot tell A minor from C major; the bass knows. The fused model must learn to use the bass."""
    rng = np.random.default_rng(1)
    samples = []
    for i in range(80):
        tonic = int(rng.integers(12))
        minor = bool(rng.integers(2))
        y = tonic + (12 if minor else 0)
        rel = (tonic + 3) % 12 if minor else (tonic + 9) % 12 + 12      # its relative key
        p = np.full(24, 0.01)
        p[y] = p[rel] = 0.4
        p /= p.sum()
        chroma = scale_chroma(tonic, "aeolian" if minor else "ionian")
        bass = np.full(12, 0.1)
        bass[tonic] = 1.0
        samples.append(({"e1": p, "e2": p}, candidate_features(chroma, bass), y))
    tr, te = samples[:60], samples[60:]
    plain = np.mean([np.argmax(s[0]["e1"]) == s[2] for s in te])
    m = KeyFusion(["e1", "e2"], l2=0.01).fit(tr)
    fused = np.mean([np.argmax(m.predict_proba(s[0], s[1])) == s[2] for s in te])
    assert fused >= 0.95 and fused > plain
    m2 = KeyFusion.from_dict(m.to_dict())
    assert np.allclose(m2.predict_proba(te[0][0], te[0][1]), m.predict_proba(te[0][0], te[0][1]))


def test_centsible_mode_only_when_confident_and_agreeing():
    d = {"valid": True, "tonic": 5, "minor": True, "degree": 2, "mode_prob": [0.05, 0.1, 0.6, 0.05, 0.05, 0.1, 0.05]}
    assert centsible_mode(d, 5, True) == "phrygian"
    assert centsible_mode(dict(d, mode_prob=[0.1, 0.1, 0.45, 0.05, 0.05, 0.2, 0.05]), 5, True) == "minor"   # not confident
    assert centsible_mode(d, 7, True) == "minor"                         # reported key differs from Centsible's
    assert centsible_mode(dict(d, degree=5), 5, True) == "minor"         # aeolian is plain minor
