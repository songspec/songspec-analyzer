"""The no-server entry point: analyze_file() and python -m songspec_analyzer."""
import json
import os

import pytest

from songspec_analyzer import analyze_file
from songspec_analyzer.__main__ import main


def test_analyze_file_returns_the_api_document_and_keeps_the_file(song):
    r = analyze_file(song)
    assert os.path.exists(song)                                    # the caller's file is never deleted
    assert r["status"] == "done", r
    assert r["track"]["duration"] == pytest.approx(8.0, abs=0.01)
    assert r["schema_version"] and r["versions"]["engine"]
    assert {"key", "tempo", "detune"} <= set(r)
    assert "audio_path" not in r and "file_sha256" not in r


def test_analyze_file_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        analyze_file(tmp_path / "nope.mp3")


def test_cli_writes_json(song, tmp_path):
    out = tmp_path / "r.json"
    assert main([song, "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["status"] == "done" and doc["track"]["duration"] == pytest.approx(8.0, abs=0.01)
