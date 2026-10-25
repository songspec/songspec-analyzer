"""Spotify / Apple Music / Deezer link input: metadata and official 30 s previews only (milestone 7)."""
from ..api.errors import ApiError


async def resolve_and_fetch(url, options, store, queue, settings):
    raise ApiError(501, "links_not_ready", "Streaming links are not supported by this engine version yet - upload the file.")
