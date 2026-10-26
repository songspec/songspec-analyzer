import json
from pathlib import Path

import pytest

from songspec_analyzer.display import key as K
from songspec_analyzer.display.bpm import bpm_display, house_bpm
from songspec_analyzer.display.detune import detune_display
from songspec_analyzer.display.tracker import tracker_row

CASES = json.loads((Path(__file__).parent / "display_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("c", CASES["house_key"], ids=lambda c: c["key"])
def test_house_key(c):
    assert K.house_key(*K.parse_key(c["key"])) == c["expect"]


@pytest.mark.parametrize("c", CASES["key_note"], ids=lambda c: f"{c['key']}-{c['source']}")
def test_key_note(c):
    tonic, mode = K.parse_key(c["key"])
    assert K.key_display(tonic, mode, source=c["source"], source_text=c["key"])["note"] == c["expect"]


@pytest.mark.parametrize("c", CASES["camelot"], ids=lambda c: c["key"])
def test_camelot(c):
    tonic, mode = K.parse_key(c["key"])
    assert K.camelot(tonic, mode) == c["camelot"]
    assert K.open_key(tonic, mode) == c["open_key"]


@pytest.mark.parametrize("c", CASES["sections"], ids=lambda c: c["expect_key"])
def test_sections(c):
    secs = [dict(start=s["start"], end=s["end"], tonic=K.parse_key(s["key"])[0], mode=K.parse_key(s["key"])[1])
            for s in c["sections"]]
    d = K.sections_display(secs, c["labels"])
    assert d["key"] == c["expect_key"]
    assert d["note"] == c["expect_note"]


@pytest.mark.parametrize("c", CASES["house_bpm"], ids=lambda c: f"{c['raw']}-{c['sig']}")
def test_house_bpm(c):
    d = bpm_display(c["raw"], c["sig"])
    assert d["bpm"] == pytest.approx(c["bpm"])
    assert d["display"] == c["display"]


@pytest.mark.parametrize("c", CASES["bpm_sections"], ids=lambda c: c["display"])
def test_bpm_sections(c):
    d = bpm_display(c["raw"], c["sig"], c["sections"])
    assert d["display"] == c["display"]
    assert d["note"] == c["note"]
    assert d["range"] == (pytest.approx(c["range"]) if c["range"] else None)


@pytest.mark.parametrize("c", CASES["detune"], ids=lambda c: str(c["cents"]))
def test_detune(c):
    tonic = K.parse_pitch(c["tonic"]) if "tonic" in c else None
    d = detune_display(c["cents"], c.get("total_cents"), tonic, c.get("mode"))
    for k in ("display", "fix", "key_text", "total_text"):
        if k in c:
            assert d[k] == c[k]


@pytest.mark.parametrize("c", CASES["roman"], ids=lambda c: f"{c['chord']}-in-{c['key']}")
def test_roman(c):
    m = K._CHORD_RE.match(c["chord"])
    root, quality = K.parse_pitch(m.group(1)), m.group(2)
    tonic, mode = K.parse_key(c["key"])
    assert K.roman(root, quality, tonic, mode) == c["expect"]


@pytest.mark.parametrize("c", CASES["tracker_row"], ids=lambda c: c["song"])
def test_tracker_row(c):
    assert tracker_row(c["song"], c["bpm"], c["key"], c["notes"]) == c["expect"]


def test_compound_flag():
    assert house_bpm(62, "6/8") == (186, True)
    assert house_bpm(62, "4/4") == (124, False)
