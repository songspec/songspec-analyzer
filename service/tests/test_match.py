import pytest

from songspec_analyzer.match import advise, camelot_relation


def view(tonic, scale, cents, bpm, aid="x"):
    return {"id": aid, "key": {"tonic": tonic, "scale": scale}, "detune": {"cents": cents}, "tempo": {"bpm": bpm}}


def test_same_mode_shift_and_fine_tuning():
    m = advise(view("A", "minor", 10.0, 128.0, "a"), view("F", "minor", -12.3, 122.0, "b"))
    assert m["key"]["semitones"] == 4 and m["key"]["b_after"] == "A Minor" and m["key"]["camelot_after"] == "same"
    assert m["tuning"]["fine_cents"] == pytest.approx(22.3)
    assert m["tuning"]["total_cents"] == pytest.approx(422.3)
    assert m["tempo"]["ratio"] == pytest.approx(128 / 122, abs=1e-4) and m["tempo"]["b_level"] is None
    assert m["display"]["key"].startswith("Pitch B +4 semitones: F Minor → A Minor")


def test_relative_key_and_half_time():
    # A: C Major at 140; B: B Minor at 70 -> aim at A's relative minor (A minor): -2 semitones; B's double time = 140
    m = advise(view("C", "major", 0.0, 140.0), view("B", "minor", 0.0, 70.0))
    assert m["key"]["semitones"] == -2 and m["key"]["b_after"] == "A Minor" and m["key"]["camelot_after"] == "relative"
    assert m["tempo"]["ratio"] == pytest.approx(1.0) and m["tempo"]["b_level"] == "double time"
    assert m["display"]["tuning"] == "tuning already matches"


def test_shift_stays_small_and_wraps():
    m = advise(view("C", "major", -40.0, 100.0), view("Db", "major", 45.0, 100.0))
    assert m["key"]["semitones"] == -1
    assert m["tuning"]["fine_cents"] == pytest.approx(15.0)             # -40 - 45 = -85, wrapped onto the circle
    assert m["tuning"]["total_cents"] == pytest.approx(-85.0)


def test_camelot_relations():
    assert camelot_relation((9, True), (0, False)) == "relative"         # A minor / C major
    assert camelot_relation((9, True), (4, True)) == "adjacent"          # 8A / 9A
    assert camelot_relation((9, True), (1, False)) is None
