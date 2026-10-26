"""Spotify / Apple Music link input.

A link is resolved to metadata (resolve.py). If anyone has uploaded that song before (same ISRC, or same normalised artist + title
with a duration within 3 s), the link returns that full analysis at once (`cached: true`, derived data only, never audio).
Otherwise the response is 422 `upload_needed` with the resolved track, so the site can say "Found <title> by <artist>: upload the
file to analyse it".

Previews are NOT analysed: Apple's terms say previews are "streamed only, and not downloaded, saved, cached", and Deezer's API is
for non-commercial use only (both read 2026-10-26, LICENSES.md). Analysing official 30 s previews server-side waits for SongSpec's
decision.
"""
import asyncio

from ..api.errors import ApiError
from . import resolve as resolver                       # the module (links.resolve), not shadowed by its function
from .resolve import LinkError, norm, primary_artist


async def resolve_and_fetch(url, options, store, queue, settings):
    from ..api.app import public_view
    try:
        meta = await asyncio.to_thread(resolver.resolve, url, settings)
    except LinkError as e:
        raise ApiError(422, "bad_link", str(e))
    except Exception:
        raise ApiError(502, "link_lookup_failed", "Couldn't reach the streaming service to look that link up. Try again or upload the file.")
    track = {"title": meta["title"], "artist": meta["artist"], "isrc": meta["isrc"], "duration": meta["duration"],
             "link": {"service": meta["service"], "id": meta["id"], "url": meta["url"], "thumbnail": meta["thumbnail"]}}
    hit = store.find_by_meta(isrc=meta["isrc"], norm_title=norm(meta["title"]), norm_artist=primary_artist(meta["artist"]),
                             duration=meta["duration"])
    if not hit:
        raise ApiError(422, "upload_needed",
                       "We can't analyse streaming audio. Upload the song's file and you get the full analysis "
                       "(and the next person who pastes this link gets it instantly).", extra={"track": track})
    aid = store.create(f"{meta['service']}-link", options=options, isrc=meta["isrc"] or hit.get("isrc"))
    res = dict(hit["result"])
    res["track"] = {**res.get("track", {}), **{k: v for k, v in track.items() if v}, "source": "upload"}
    store.update(aid, status="done", result=res, stages=hit["stages"], cached_from=hit["id"], fingerprint=hit["fingerprint"],
                 duration=hit["duration"])
    return public_view(store.get(aid), store)
