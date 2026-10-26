"""Streaming links -> track metadata (title, artist, duration, ISRC). Metadata only: no audio is fetched here.

- Spotify: oEmbed (no credentials) gives the title. With SONGSPEC_SPOTIFY_CLIENT_ID / _SECRET, the Web API (client credentials) adds
  artists, duration and the ISRC (`external_ids.isrc`; coded defensively, the field has come and gone).
- Apple Music / iTunes: the track id (`?i=` or `/song/<id>`) -> iTunes Lookup API (no credentials): title, artist, duration.
Responses are cached in memory for an hour (Apple asks large sites to cache lookups; its Search API allows ~20 calls a minute).
"""
import base64
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request

UA = {"User-Agent": "SongSpec-Analyzer/1.0 (+https://songspec.org)"}
_CACHE = {}
_TTL = 3600


class LinkError(ValueError):
    pass


def parse_link(url):
    """-> (service, id). Accepts open.spotify.com/track/<id>, spotify:track:<id>, music.apple.com/.../album/...?i=<id>,
    music.apple.com/.../song/<name>/<id>, itunes.apple.com/...?i=<id>."""
    u = url.strip()
    m = re.match(r"spotify:track:([A-Za-z0-9]{22})$", u)
    if m:
        return "spotify", m.group(1)
    p = urllib.parse.urlparse(u)
    host = (p.netloc or "").lower()
    if host.endswith("open.spotify.com"):
        m = re.search(r"/track/([A-Za-z0-9]{22})", p.path)
        if m:
            return "spotify", m.group(1)
        raise LinkError("That Spotify link is not a track (albums and playlists can't be analysed).")
    if host.endswith("music.apple.com") or host.endswith("itunes.apple.com"):
        q = urllib.parse.parse_qs(p.query)
        if q.get("i"):
            return "apple", q["i"][0]
        m = re.search(r"/song/(?:[^/]+/)?(\d+)", p.path)
        if m:
            return "apple", m.group(1)
        raise LinkError("That Apple Music link is not a song (open the song and copy its link).")
    raise LinkError("Paste a Spotify or Apple Music song link.")


def _get_json(url, headers=None, data=None, timeout=15):
    key = (url, data)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    req = urllib.request.Request(url, headers={**UA, **(headers or {})}, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read().decode("utf-8"))
    _CACHE[key] = (time.time(), out)
    return out


def _spotify_token(cid, secret):
    hit = _CACHE.get(("spotify-token", cid))
    if hit and time.time() < hit[0]:
        return hit[1]
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=b"grant_type=client_credentials",
                                 headers={**UA, "Authorization": f"Basic {auth}",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    _CACHE[("spotify-token", cid)] = (time.time() + int(d.get("expires_in", 3600)) - 60, d["access_token"])
    return d["access_token"]


def resolve(url, settings, fetch=None):
    """-> {'service', 'id', 'title', 'artist', 'duration', 'isrc', 'thumbnail', 'url', 'match'}"""
    fetch = fetch or _get_json                           # looked up at call time (tests replace _get_json)
    service, tid = parse_link(url)
    out = {"service": service, "id": tid, "title": None, "artist": None, "duration": None, "isrc": None, "thumbnail": None,
           "url": url, "match": "exact"}
    if service == "spotify":
        canon = f"https://open.spotify.com/track/{tid}"
        try:
            o = fetch("https://open.spotify.com/oembed?url=" + urllib.parse.quote(canon, safe=""))
            out["title"], out["thumbnail"] = o.get("title"), o.get("thumbnail_url")
        except Exception:
            pass
        if settings.spotify_client_id and settings.spotify_client_secret:
            try:
                tok = _spotify_token(settings.spotify_client_id, settings.spotify_client_secret)
                t = fetch(f"https://api.spotify.com/v1/tracks/{tid}", headers={"Authorization": f"Bearer {tok}"})
                out["title"] = t.get("name") or out["title"]
                out["artist"] = ", ".join(a["name"] for a in t.get("artists", [])) or None
                out["duration"] = (t.get("duration_ms") or 0) / 1000.0 or None
                out["isrc"] = (t.get("external_ids") or {}).get("isrc")          # may be missing: matched by title + artist then
            except Exception:
                pass
        if not out["title"]:
            raise LinkError("Couldn't read that Spotify track (is the link public?).")
    else:
        d = fetch(f"https://itunes.apple.com/lookup?id={urllib.parse.quote(tid)}&entity=song")
        songs = [r for r in d.get("results", []) if r.get("wrapperType") == "track" and str(r.get("trackId")) == str(tid)]
        if not songs:
            songs = [r for r in d.get("results", []) if r.get("kind") == "song"]
        if not songs:
            raise LinkError("Couldn't find that song on Apple Music.")
        s = songs[0]
        out.update(title=s.get("trackName"), artist=s.get("artistName"),
                   duration=(s.get("trackTimeMillis") or 0) / 1000.0 or None, thumbnail=s.get("artworkUrl100"))
    return out


def norm(text):
    """Normalised title / artist for matching: case, accents, punctuation, '(feat. ...)' and ' - Remastered' style suffixes."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\s*[\(\[](feat|ft|with|prod)[^)\]]*[\)\]]", "", t)
    t = re.sub(r"\s+-\s+(remaster|remastered|live|radio edit|single version|explicit|clean).*$", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def primary_artist(text):
    return norm(re.split(r",|&| x | feat\.? | ft\.? ", text or "", maxsplit=1)[0])
