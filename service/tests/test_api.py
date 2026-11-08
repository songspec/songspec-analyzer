import json
import os
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from conftest import synth_song
from songspec_analyzer.api.app import create_app
from songspec_analyzer.config import Settings


@pytest.fixture
def client(tmp_path):
    s = Settings(data_dir=tmp_path / "data", rate_per_min=0)
    with TestClient(create_app(s)) as c:
        c.settings = s
        yield c


def wait_done(client, aid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = client.get(f"/v1/analyses/{aid}").json()
        if v["status"] in ("done", "error"):
            return v
        time.sleep(0.1)
    raise AssertionError("analysis did not finish")


def upload(client, path, **kw):
    with open(path, "rb") as f:
        return client.post("/v1/analyses", files={"file": (os.path.basename(path), f, "audio/wav")}, **kw)


def test_upload_runs_and_deletes_audio(client, song):
    r = upload(client, song)
    assert r.status_code == 202
    aid = r.json()["id"]
    v = wait_done(client, aid)
    assert v["status"] == "done", v
    assert v["stages"]["decode"] == "done"
    assert v["track"]["duration"] == pytest.approx(8.0, abs=0.01)
    assert v["track"]["source"] == "upload"
    assert v["schema_version"]
    assert v["versions"]["engine"]
    assert "audio_path" not in v and "file_sha256" not in v           # internals never leak
    assert list(client.settings.uploads_dir.iterdir()) == []          # audio deleted after analysis


def test_same_file_is_cached(client, song):
    a = wait_done(client, upload(client, song).json()["id"])
    r = upload(client, song).json()
    assert r["status"] == "done" and r["cached"] is True
    assert r["track"]["duration"] == a["track"]["duration"]


def test_engine_version_change_invalidates_the_cache(client, song, monkeypatch):
    """A cached result from an older engine must never be served after an upgrade."""
    import songspec_analyzer.store as store_mod
    wait_done(client, upload(client, song).json()["id"])
    monkeypatch.setattr(store_mod, "ENGINE_VERSION", "999.0.0")
    r = upload(client, song).json()
    assert r["cached"] is False


def test_rejects_garbage_and_short_files(client, tmp_path):
    bad = tmp_path / "x.mp3"
    bad.write_bytes(b"this is not audio" * 100)
    v = wait_done(client, upload(client, str(bad)).json()["id"])
    assert v["status"] == "error" and "read" in v["error"].lower()
    short = synth_song(str(tmp_path / "short.wav"), seconds=2.0)
    v = wait_done(client, upload(client, short).json()["id"])
    assert v["status"] == "error" and "short" in v["error"]


def test_size_and_duration_limits(tmp_path):
    s = Settings(data_dir=tmp_path / "d", rate_per_min=0, max_upload_mb=0.05, max_duration_s=6)
    with TestClient(create_app(s)) as c:
        big = synth_song(str(tmp_path / "big.wav"), seconds=8.0)
        r = upload(c, big)
        assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    s2 = Settings(data_dir=tmp_path / "d2", rate_per_min=0, max_duration_s=6)
    with TestClient(create_app(s2)) as c:
        r = upload(c, synth_song(str(tmp_path / "long.wav"), seconds=8.0))
        assert r.status_code == 413 and r.json()["error"]["code"] == "too_long"
        assert list(s2.uploads_dir.iterdir()) == []


def test_rate_limit(tmp_path, song):
    s = Settings(data_dir=tmp_path / "d", rate_per_min=1, rate_burst=1)
    with TestClient(create_app(s)) as c:
        assert upload(c, song).status_code == 202
        r = upload(c, song)
        assert r.status_code == 429 and "Retry-After" in r.headers


def test_api_key(tmp_path, song):
    s = Settings(data_dir=tmp_path / "d", rate_per_min=0, api_keys=["k1"])
    with TestClient(create_app(s)) as c:
        assert upload(c, song).status_code == 401
        assert upload(c, song, headers={"X-API-Key": "k1"}).status_code == 202


def test_sse_stream_ends_with_done(client, song):
    aid = upload(client, song).json()["id"]
    events = []
    with client.stream("GET", f"/v1/analyses/{aid}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((ev, json.loads(line[5:])))
                if ev in ("done", "error"):
                    break
    assert events[-1][0] == "done"
    assert events[-1][1]["stages"]["decode"] == "done"


def test_feedback_and_exports(client, song):
    aid = upload(client, song).json()["id"]
    wait_done(client, aid)
    assert client.post(f"/v1/analyses/{aid}/feedback", json={"field": "key", "correct": "Bb Minor"}).status_code == 201
    assert client.post(f"/v1/analyses/{aid}/feedback", json={"field": "nope", "correct": "x"}).status_code == 422
    r = client.get(f"/v1/analyses/{aid}/exports/json")
    assert r.status_code == 200 and r.json()["id"] == aid
    assert client.get(f"/v1/analyses/{aid}/exports/tracker-row").status_code == 200
    assert client.get(f"/v1/analyses/{aid}/exports/bogus").status_code == 404


def _old_links_not_ready_yet(client):
    r = client.post("/v1/analyses", json={"url": "https://open.spotify.com/track/xyz"})
    assert r.status_code == 501 and r.json()["error"]["code"] == "links_not_ready"


def test_unknown_id(client):
    assert client.get("/v1/analyses/nope").status_code == 404


def test_sweeper_deletes_expired_audio(tmp_path):
    from songspec_analyzer.jobs import AudioSweeper
    from songspec_analyzer.store import Store
    s = Settings(data_dir=tmp_path / "d", audio_ttl_s=0)
    s.ensure_dirs()
    st = Store(s.db_path)
    p = s.uploads_dir / "old.wav"
    sf.write(p, np.zeros(1000, np.float32), 8000)
    aid = st.create("upload", audio_path=str(p))
    time.sleep(0.01)
    assert AudioSweeper(st, s).sweep_once() >= 1
    assert not p.exists() and st.get(aid)["audio_path"] is None


def test_key_correction_applies_to_the_song(client, song):
    aid = upload(client, song).json()["id"]
    v = wait_done(client, aid)
    assert v.get("key"), "the synthetic song should get a key"
    assert client.post(f"/v1/analyses/{aid}/feedback", json={"field": "key", "correct": "not a key"}).status_code == 422
    r = client.post(f"/v1/analyses/{aid}/feedback", json={"field": "key", "correct": "A# Phrygian"})
    assert r.status_code == 201 and r.json()["analysis"]["key"]["display"]["key"] == "Bb Minor*"
    k = client.get(f"/v1/analyses/{aid}").json()["key"]
    assert k["tonic"] == "Bb" and k["scale"] == "minor" and k["mode"] == "Phrygian" and k["camelot"] == "3A"
    assert k["display"]["note"].startswith("A# Phrygian")              # the user's spelling kept in the note
    assert k["corrected"]["detected"]["display"]["key"] == v["key"]["display"]["key"]
    again = upload(client, song).json()                                  # the same file again (cache hit): still corrected
    assert again["cached"] and again["key"]["corrected"]["key"] == "Bb Minor*"
    # the latest correction wins
    client.post(f"/v1/analyses/{aid}/feedback", json={"field": "key", "correct": "C Major"})
    assert client.get(f"/v1/analyses/{again['id']}").json()["key"]["display"]["key"] == "C Major"
    keys = client.get("/v1/keys").json()["keys"]
    assert {"Bb Minor", "Db Major", "C# Minor", "A Dorian", "F# Phrygian", "A# Phrygian"} <= {k["value"] for k in keys}


def test_key_evidence_is_saved_for_retraining(client, song):
    from songspec_analyzer.store import Store
    aid = upload(client, song).json()["id"]
    v = wait_done(client, aid)
    ev = Store(client.settings.data_dir / "analyzer.sqlite3").key_evidence(aid)
    assert ev is not None and "centsible" in ev["data"]["est"] and len(ev["data"]["est"]["centsible"]) == 24
    assert ev["data"]["detected"] in range(24)
