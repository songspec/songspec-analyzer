"""Exports of a finished analysis. Each exporter: fn(view, transpose=0, simplify=False) -> (body, media type, file extension).
Chord exports (MIDI, ChordPro, TXT) join with the chord milestone; client/exports.js produces the same files in the browser."""
import csv
import io
import json

from ..display.tracker import tracker_row


def _json(view, **_):
    return json.dumps(view, ensure_ascii=False, indent=2), "application/json", "json"


def _row(view):
    track = view.get("track") or {}
    tempo, key = view.get("tempo") or {}, view.get("key") or {}
    song = track.get("title") or (track.get("file_name") or "").rsplit(".", 1)[0]
    bpm = (tempo.get("display") or {}).get("bpm") or ""
    k = (key.get("display") or {}).get("key") or ""
    notes = [(key.get("display") or {}).get("note"), (tempo.get("display") or {}).get("note")]
    return song, bpm, k, notes


def _tracker(view, **_):
    return tracker_row(*_row(view)), "text/plain; charset=utf-8", "txt"


def _csv(view, **_):
    song, bpm, k, notes = _row(view)
    det = (view.get("detune") or {})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["song", "bpm", "key", "note", "camelot", "detune_cents", "signature", "key_confidence"])
    w.writerow([song, bpm, k, ". ".join(n for n in notes if n), (view.get("key") or {}).get("camelot", ""),
                det.get("cents", ""), (view.get("tempo") or {}).get("signature", ""), (view.get("key") or {}).get("confidence", "")])
    return buf.getvalue(), "text/csv; charset=utf-8", "csv"


EXPORTERS = {"json": _json, "csv": _csv, "tracker-row": _tracker}
