"""Tempo: the port must give BPM Fixer's exact beats; the service's additions (precise BPM, sections, feel) are checked on
synthetic songs of known tempo. Beat This! itself is replaced by a fake tracker with known mistakes, so these run without the model."""
import numpy as np
import pytest

from bpmfix_reference import load_original, reference_beats
from songspec_analyzer.tempo import _bpmfix_port as port
from songspec_analyzer.tempo import engine, section_from_engine

SR = 22050
ORIG = load_original()


def click_song(beat_times, seconds, sr=SR, seed=0):
    """Audio with a sharp drum hit on every beat (so refine_beats has transients) plus a quiet pad."""
    rng = np.random.default_rng(seed)
    y = 0.02 * np.sin(2 * np.pi * 220 * np.arange(int(seconds * sr)) / sr)
    for i, t in enumerate(beat_times):
        a = int(t * sr)
        n = min(len(y) - a, 1500)
        if n <= 0:
            continue
        amp = 0.9 if i % 4 == 0 else 0.6
        y[a:a + n] += amp * np.exp(-np.arange(n) / 120.0) * rng.standard_normal(n)
    return (y / np.abs(y).max() * 0.8).astype(np.float32)


def grid(bpm, seconds, start=0.5, bpm_end=None):
    """Beat times at a steady (or linearly drifting) tempo."""
    out, t = [], start
    while t < seconds - 0.3:
        out.append(t)
        b = bpm if bpm_end is None else bpm + (bpm_end - bpm) * t / seconds
        t += 60.0 / b
    return np.array(out)


def fake_tracker(true_beats, every=4, jitter=0.008, drop=(), half_time=None, seed=1):
    """What a neural tracker hands over: jittered beats, some missing, maybe a half-time stretch; downbeats every `every`."""
    rng = np.random.default_rng(seed)
    b = true_beats + rng.normal(0, jitter, len(true_beats))
    keep = np.ones(len(b), bool)
    for i in drop:
        keep[i] = False
    if half_time:
        a, z = half_time
        keep[a:z:2] = True
        keep[a + 1:z:2] = False
    down = b[::every]
    return np.sort(b[keep]), np.sort(down)


CASES = {
    "steady-120": dict(bpm=120, seconds=40),
    "steady-57-halftime-level": dict(bpm=57, seconds=50),
    "drift-118-126": dict(bpm=118, bpm_end=126, seconds=60),
    "missed-beats": dict(bpm=100, seconds=40, drop=(10, 11, 30, 31, 32)),
    "half-time-passage": dict(bpm=128, seconds=45, half_time=(30, 60)),
    "waltz-3-4": dict(bpm=150, seconds=40, every=3),
}


def _case(name):
    c = dict(CASES[name])
    tb = grid(c.pop("bpm"), c["seconds"], bpm_end=c.pop("bpm_end", None))
    y = click_song(tb, c["seconds"])
    raw, down = fake_tracker(tb, every=c.pop("every", 4), drop=c.pop("drop", ()), half_time=c.pop("half_time", None))
    return y, tb, raw, down


def run_engine(y, raw, down, monkeypatch, signature="Auto"):
    monkeypatch.setattr(engine, "detect_beats", lambda y_, sr_, md: (raw, down))
    return engine.analyze(y, SR, "unused", signature=signature)


@pytest.mark.skipif(ORIG is None, reason="BPM Fixer source not present")
def test_port_is_verbatim():
    """Every ported function has the same bytecode-relevant source as the original."""
    import inspect
    for name in [n for n in dir(port) if callable(getattr(port, n)) and getattr(getattr(port, n), "__module__", "") == port.__name__]:
        assert inspect.getsource(getattr(port, name)) == inspect.getsource(getattr(ORIG, name)), name
    for const in ("TIME_SIGNATURES", "LEVEL_MULTS", "WRONG_LEVEL", "RISE_FRAC", "HOP"):
        assert getattr(port, const) == getattr(ORIG, const), const


@pytest.mark.skipif(ORIG is None, reason="BPM Fixer source not present")
@pytest.mark.parametrize("name", list(CASES))
def test_same_beats_as_bpm_fixer(name, monkeypatch):
    y, tb, raw, down = _case(name)
    ref, ref_sig = reference_beats(ORIG, y, SR, raw, down)
    r = run_engine(y, raw, down, monkeypatch)
    assert r["signature"] == ref_sig
    np.testing.assert_allclose(r["beats"], np.round(ref, 3), atol=1e-9)


@pytest.mark.parametrize("name,expect_bpm,expect_sig,expect_feel", [
    ("steady-120", 120.0, "4/4", "grid"),
    ("steady-57-halftime-level", 114.0, "4/4", "grid"),
    ("missed-beats", 100.0, "4/4", "grid"),
    ("half-time-passage", 128.0, "4/4", "grid"),
    ("waltz-3-4", 150.0, "3/4", "grid"),
])
def test_tempo_values(name, expect_bpm, expect_sig, expect_feel, monkeypatch):
    y, tb, raw, down = _case(name)
    r = run_engine(y, raw, down, monkeypatch)
    t = section_from_engine(r)
    assert r["signature"] == expect_sig
    assert t["bpm"] == pytest.approx(expect_bpm, abs=0.15)          # precise line fit, not the ±0.3 median
    assert t["display"]["bpm"] == str(int(expect_bpm)) + ("*" if expect_sig != "4/4" else "")   # tracker: '120*' for 3/4
    assert r["feel"] == expect_feel
    assert len(r["sections"]) == 1


def test_gentle_drift_is_moderate(monkeypatch):
    y, tb, raw, down = _case("drift-118-126")                       # ~5 % between p10 and p90 of the local tempo
    r = run_engine(y, raw, down, monkeypatch)
    t = section_from_engine(r)
    assert r["sections"][0]["steadiness"] == "moderate"
    assert t["display"]["note"] == "BPM moderately inconsistent"
    assert t["display"]["bpm"].startswith("~") and " - " not in t["display"]["bpm"]
    assert 120 <= t["bpm"] <= 124


def test_strong_drift_is_live_with_range(monkeypatch):
    tb = grid(105, 90, bpm_end=130)
    y = click_song(tb, 90)
    raw, down = fake_tracker(tb)
    r = run_engine(y, raw, down, monkeypatch)
    t = section_from_engine(r)
    assert r["feel"] == "live"
    assert t["display"]["note"] == "BPM inconsistent - played live"
    lo, hi = t["range"]
    assert t["display"]["bpm"] == f"~{lo:.0f} - {hi:.0f}" or t["display"]["bpm"].startswith("~")
    assert 104 <= lo <= 112 and 122 <= hi <= 131


def test_tempo_step_gives_two_sections(monkeypatch):
    a = grid(100, 30)
    b = grid(140, 60, start=a[-1] + 60 / 100.0)
    b = b[b > a[-1]]
    tb = np.concatenate([a, b])
    y = click_song(tb, 60)
    raw, down = fake_tracker(tb)
    r = run_engine(y, raw, down, monkeypatch)
    bpms = sorted(s["bpm"] for s in r["sections"])
    assert len(bpms) == 2
    assert bpms[0] == pytest.approx(100, abs=1.0) and bpms[1] == pytest.approx(140, abs=1.0)


def test_six_eight_keeps_felt_value(monkeypatch):
    y, tb, raw, down = _case("steady-120")
    r = run_engine(y, raw, down, monkeypatch, signature="6/8")
    t = section_from_engine(r)
    assert r["signature"] == "6/8" and t["compound"] is True
    assert "6/8" in t["display"]["note"]
    assert any("quarter notes" in a for a in t["display"]["alternates"])


def _have_model():
    try:
        import beat_this  # noqa: F401
        from songspec_analyzer.config import Settings
        return engine.checkpoint(Settings().models_dir).exists()
    except Exception:
        return False


@pytest.mark.skipif(not _have_model(), reason="Beat This! or its vendored model not installed (tools/fetch_models.py)")
def test_real_tracker_on_synthetic_song():
    """End to end with the real neural tracker: a 30 s synthetic 4/4 groove at 96 BPM."""
    from songspec_analyzer.config import Settings
    sr = 44100
    tb = grid(96, 30)
    y = click_song(tb, 30, sr=sr)
    r = engine.analyze(y, sr, Settings().models_dir)
    t = section_from_engine(r)
    assert t["bpm"] == pytest.approx(96, abs=0.5)
    assert len(r["beats"]) == pytest.approx(len(tb), abs=4)


def test_line_period_exact_on_grid():
    t = 0.37 + np.arange(200) * (60 / 128.0)
    t[50] += 0.05                                                    # one bad beat must not move it
    assert 60 / engine.line_period(t) == pytest.approx(128.0, abs=0.01)
