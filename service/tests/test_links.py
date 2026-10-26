"""Link input: parsing, metadata resolution (recorded API responses, no network), and the link -> earlier upload cache."""
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from conftest import synth_song
from songspec_analyzer.api.app import create_app
from songspec_analyzer.config import Settings
from songspec_analyzer.links import resolver as R

ITUNES = {"resultCount": 2, "results": [
    {"wrapperType": "collection", "collectionId": 1, "artistName": "Test Artist"},
    {"wrapperType": "track", "kind": "song", "trackId": 1440841363, "trackName": "Test Song (feat. Someone)",
     "artistName": "Test Artist", "trackTimeMillis": 8000, "artworkUrl100": "https://example.invalid/a.jpg"}]}
OEMBED = {"title": "Test Song", "thumbnail_url": "https://example.invalid/s.jpg", "provider_name": "Spotify"}


def fake_fetch(url, headers=None, data=None, timeout=15):
    if "itunes.apple.com/lookup" in url:
        return ITUNES if "1440841363" in url else {"resultCount": 0, "results": []}
    if "open.spotify.com/oembed" in url:
        return OEMBED
    raise AssertionError(f"unexpected request {url}")


@pytest.mark.parametrize("url,expect", [
    ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc", ("spotify", "4uLU6hMCjMI75M1A2tKUQC")),
    ("spotify:track:4uLU6hMCjMI75M1A2tKUQC", ("spotify", "4uLU6hMCjMI75M1A2tKUQC")),
    ("https://music.apple.com/us/album/test/1440841300?i=1440841363", ("apple", "1440841363")),
    ("https://music.apple.com/us/song/test-song/1440841363", ("apple", "1440841363")),
])
def test_parse_link(url, expect):
    assert R.parse_link(url) == expect


@pytest.mark.parametrize("url", ["https://open.spotify.com/album/4uLU6hMCjMI75M1A2tKUQC", "https://example.com/x",
                                 "https://music.apple.com/us/album/test/1440841300"])
def test_parse_link_rejects(url):
    with pytest.raises(R.LinkError):
        R.parse_link(url)


def test_resolve_apple_and_spotify():
    s = Settings()
    a = R.resolve("https://music.apple.com/us/album/x/1?i=1440841363", s, fetch=fake_fetch)
    assert (a["title"], a["artist"], a["duration"]) == ("Test Song (feat. Someone)", "Test Artist", 8.0)
    sp = R.resolve("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", s, fetch=fake_fetch)
    assert sp["title"] == "Test Song" and sp["artist"] is None                 # no credentials: oEmbed gives the title only


def test_norm():
    assert R.norm("Test Song (feat. Someone)") == "test song" == R.norm("TEST SONG - Remastered 2011")
    assert R.norm("Beyoncé") == "beyonce"
    assert R.primary_artist("Test Artist, Other & Third") == "test artist"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_get_json", fake_fetch)
    s = Settings(data_dir=tmp_path / "data", rate_per_min=0)
    with TestClient(create_app(s)) as c:
        yield c


def wait_done(c, aid, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = c.get(f"/v1/analyses/{aid}").json()
        if v["status"] in ("done", "error"):
            return v
        time.sleep(0.2)
    raise AssertionError("not done")


def test_unknown_song_needs_upload(client):
    r = client.post("/v1/analyses", json={"url": "https://music.apple.com/us/album/x/1?i=1440841363"})
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "upload_needed" and e["track"]["title"] == "Test Song (feat. Someone)"
    assert e["track"]["link"]["service"] == "apple"


def test_bad_link(client):
    r = client.post("/v1/analyses", json={"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_link"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed to write tags")
def test_link_finds_an_earlier_upload_by_tags(client, tmp_path):
    wav = synth_song(str(tmp_path / "s.wav"), seconds=8.0)
    flac = tmp_path / "tagged.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-metadata", "title=Test Song", "-metadata", "artist=Test Artist",
                    str(flac)], check=True)
    with open(flac, "rb") as f:
        up = client.post("/v1/analyses", files={"file": ("tagged.flac", f, "audio/flac")}).json()
    first = wait_done(client, up["id"])
    assert first["status"] == "done" and first["track"]["title"] == "Test Song"
    r = client.post("/v1/analyses", json={"url": "https://music.apple.com/us/album/x/1?i=1440841363"})
    assert r.status_code == 202, r.json()
    v = r.json()
    assert v["status"] == "done" and v["cached"] is True
    assert v["track"]["link"]["service"] == "apple" and v["track"]["duration"] == pytest.approx(8.0, abs=0.01)
