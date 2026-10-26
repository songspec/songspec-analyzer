"""Chords: rendered progressions with exactly known chords, beats and tuning."""
import numpy as np
import pytest

from songspec_analyzer.chords import annotate, progressions, summary
from songspec_analyzer.chords.engine import recognise
from songspec_analyzer.chords.vocab import FLAT, QUALITIES, labels, split, templates

SR = 22050


def render(chords, bpm=100.0, beats_per_chord=4, cents=0.0, seed=0):
    """chords: labels like 'Am', 'F', 'C/E'. Sustained harmonic tones + a bass note per chord + a little noise."""
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    dur = beat * beats_per_chord
    n = int(dur * SR)
    t = np.arange(n) / SR
    ref = 440.0 * 2 ** (cents / 1200)
    out = []
    for lab in chords:
        bass_pc = None
        if "/" in lab:
            lab, b = lab.split("/")
            bass_pc = FLAT.index(b)
        root, q = split(lab)
        y = np.zeros(n)
        for iv in QUALITIES[q][0]:
            pc = (root + iv) % 12
            f = ref * 2 ** ((pc - 9) / 12)                     # pitch class above A3-ish
            f *= 2 if f < 200 else 1
            for h in range(1, 6):
                y += 0.05 / h * np.sin(2 * np.pi * f * h * t)
        bp = root if bass_pc is None else bass_pc
        fb = ref * 2 ** ((bp - 9) / 12) / 4                   # two octaves down
        for h in range(1, 4):
            y += 0.12 / h * np.sin(2 * np.pi * fb * h * t)
        env = np.minimum(1, t / 0.02) * np.minimum(1, (dur - t) / 0.05)
        out.append(y * env)
    y = np.concatenate(out)
    y += 0.003 * rng.standard_normal(len(y))
    beats = np.arange(len(chords) * beats_per_chord) * beat
    downbeats = beats[::4]
    return (y / np.abs(y).max()).astype(np.float32), beats, downbeats, dur


def beat_truth(chords, beats_per_chord=4):
    return [c.split("/")[0] for c in chords for _ in range(beats_per_chord)]


def chord_at(segs, t):
    for s in segs:
        if s["start"] - 1e-3 <= t < s["end"]:
            return s
    return segs[-1]


def test_vocabulary_templates():
    labs, T, prior = templates()
    assert len(labs) == 1 + 12 * len(QUALITIES)
    assert labs[0] == "N" and np.allclose(np.linalg.norm(T, axis=1), 1.0)
    assert split("Ebmaj7") == (3, "maj7") and split("C/E") == (0, "") and split("N") == (None, None)
    assert labels("majmin")[1:3] == ["C", "Db"]


@pytest.mark.parametrize("prog,cents", [
    (["Am", "F", "C", "G"] * 3, 0.0),
    (["Bbm", "Gb", "Db", "Ab"] * 3, 23.0),
    (["C", "Am", "F", "G"] * 3, -37.0),
    (["E", "B", "C#m".replace("C#", "Db"), "A"] * 3, 12.0),
])
def test_roots_and_majmin_on_known_progressions(prog, cents):
    y, beats, downs, dur = render(prog, cents=cents)
    segs = recognise(y, cents, beats, downs, len(y) / SR)
    truth = beat_truth(prog)
    got = [chord_at(segs, b)["triad"] for b in beats]
    root_ok = np.mean([split(g)[0] == split(t)[0] for g, t in zip(got, truth)])
    majmin_ok = np.mean([g == t for g, t in zip(got, truth)])
    assert root_ok >= 0.95, list(zip(truth, got))
    assert majmin_ok >= 0.95, list(zip(truth, got))


def test_sevenths_and_slash():
    prog = ["Cmaj7", "Am7", "Dm7", "G7"] * 2
    y, beats, downs, _ = render(prog)
    segs = recognise(y, 0.0, beats, downs, len(y) / SR)
    got = [chord_at(segs, b)["chord"].split("/")[0] for b in beats[::4]]
    assert sum(g == t for g, t in zip(got, prog)) >= 6, got
    y, beats, downs, _ = render(["C", "C/E", "F", "G"] * 2)
    segs = recognise(y, 0.0, beats, downs, len(y) / SR)
    assert chord_at(segs, beats[5])["chord"] == "C/E"


def test_no_one_beat_blips():
    y, beats, downs, _ = render(["Am", "F", "C", "G"] * 3)
    segs = recognise(y, 0.0, beats, downs, len(y) / SR)
    short = [s for s in segs if s["end"] - s["start"] < 0.7 * (beats[1] - beats[0]) * 2]
    assert not short


def _view(segs, beats, downs):
    return {"track": {"file_name": "song.wav"}, "tempo": {"beats": list(beats), "downbeats": list(downs), "display": {"bpm": "100"}},
            "key": {"display": {"key": "A Minor"}}, "chords": {"segments": segs, "progressions": []}}


def test_midi_round_trip_lands_on_the_audio_times():
    """Read our MIDI back with an independent reader (mido): every chord onset, converted through the file's own tempo map, must
    land on the analysed time within 10 ms, also on a drifting tempo and with a lead-in before the first beat."""
    mido = pytest.importorskip("mido")
    import io
    from songspec_analyzer.export.chords import chord_notes, midi
    beats = 0.73 + np.cumsum(np.concatenate([[0], 60 / np.linspace(96, 104, 31)]))         # drifting 96 -> 104 BPM
    downs = beats[::4]
    names = ["Am", "F", "C/E", "G"] * 2
    segs = [{"start": float(downs[i]), "end": float(downs[i + 1]), "chord": names[i]} for i in range(len(downs) - 1)]
    body, media, ext = midi(_view(segs, beats, downs))
    assert media == "audio/midi" and ext == "mid"
    mf = mido.MidiFile(file=io.BytesIO(body))
    onsets, t, tempo = [], 0.0, 500000
    for msg in mido.merge_tracks(mf.tracks):                     # delta ticks -> seconds at the tempo in force
        t += mido.tick2second(msg.time, mf.ticks_per_beat, tempo)
        if msg.type == "set_tempo":
            tempo = msg.tempo
        if msg.type == "note_on" and msg.velocity > 0:
            onsets.append((round(t, 4), msg.note))
    starts = sorted({o for o, _ in onsets})
    assert len(starts) == len(segs)
    for s, got in zip(segs, starts):
        assert abs(got - s["start"]) < 0.01, (s["start"], got)
    first_notes = sorted(n for o, n in onsets if o == starts[2])
    assert first_notes == sorted(chord_notes("C/E"))                   # bass E an octave down, C E G voicing


def test_transform_and_text_exports():
    from songspec_analyzer.export.chords import chordpro, transform, txt
    assert transform("Ebmaj7/G", 2) == "Fmaj7/A" and transform("Dm7b5", 0, simplify=True) == "Ddim" and transform("N", 5) == "N"
    beats = np.arange(16) * 0.6
    downs = beats[::4]
    segs = [{"start": float(downs[i]), "end": float(downs[i] + 2.4), "chord": c} for i, c in enumerate(["Am", "F", "C", "G"])]
    cho = chordpro(_view(segs, beats, downs), transpose=1)[0]
    assert "{title: song}" in cho and "{key: A Minor}" in cho and "[Bbm]" in cho and "transposed +1" in cho
    text = txt(_view(segs, beats, downs))[0]
    assert "Am | F | C | G" in text


FIXTURE = __import__("pathlib").Path(__file__).resolve().parents[2] / "client" / "test" / "exports_fixture.json"


def fixture_text():
    """The exports for a fixed analysis (lead-in, drifting tempo, a slash chord, 'N'), as the client must reproduce them byte for byte."""
    import base64
    import json
    from songspec_analyzer.export.chords import chordpro, midi, txt
    beats = [round(0.73 + i * 0.6 + 0.002 * i * i, 3) for i in range(24)]
    downs = beats[::4]
    names = ["Am", "Fmaj7", "C/E", "N", "G7", "Dm7b5"]
    segs = [{"start": downs[i], "end": downs[i + 1] if i + 1 < len(downs) else beats[-1] + 0.6, "chord": c}
            for i, c in enumerate(names)]
    view = {"track": {"file_name": "fixture song.flac", "artist": "Someone"},
            "tempo": {"beats": beats, "downbeats": downs, "display": {"bpm": "99.4"}},
            "key": {"display": {"key": "A Minor* / C Major"}},
            "chords": {"segments": segs, "progressions": [{"display": "i–VI–III–VII ×2", "chords": "Am-F-C-G"}]}}
    cases = []
    for tr, simp in ((0, False), (2, False), (-3, True)):
        cases.append({"transpose": tr, "simplify": simp,
                      "midi_b64": base64.b64encode(midi(view, tr, simp)[0]).decode(),
                      "chordpro": chordpro(view, tr, simp)[0], "txt": txt(view, tr, simp)[0]})
    return json.dumps({"_comment": "written by service/tests/test_chords.py; client/test checks exports.js against it",
                       "analysis": view, "cases": cases}, indent=1, ensure_ascii=False) + "\n"


def test_client_export_fixture_is_current():
    if not FIXTURE.exists() or FIXTURE.read_text(encoding="utf-8") != fixture_text():
        FIXTURE.write_text(fixture_text(), encoding="utf-8")
        pytest.fail("client/test/exports_fixture.json was stale and has been rewritten: re-run the client tests and commit it")


def test_romans_summary_progressions():
    prog = ["Bbm", "Gb", "Db", "Ab"] * 4
    y, beats, downs, _ = render(prog)
    segs = annotate(recognise(y, 0.0, beats, downs, len(y) / SR), 10, "minor")
    assert [chord_at(segs, b)["roman"] for b in downs[:4]] == ["i", "VI", "III", "VII"]
    s = summary(segs)
    assert {x["chord"] for x in s} == {"Bbm", "Gb", "Db", "Ab"}
    p = progressions(segs, downs)
    assert p and p[0]["roman"] == "i-VI-III-VII" and p[0]["count"] == 4 and p[0]["display"] == "i–VI–III–VII ×4"
