"""Fix BPM: BPM Fixer's own process() (verbatim in _bpmfix_fix.py) on the server. Beat This! comes from the service (vendored model),
Rubber Band from SONGSPEC_RUBBERBAND, else PATH (rubberband-r3 / rubberband), else an installed BPM Fixer's copy.
Rubber Band is GPL: it runs here, on the server, and is never shipped to clients (LICENSES.md)."""
import os
import shutil
import threading
from pathlib import Path

from . import _bpmfix_fix as bf
from . import engine

FORMATS = {"mp3": "MP3 320 kbps", "wav": "WAV 24-bit", "wav16": "WAV 16-bit", "flac": "FLAC", "aiff": "AIFF 24-bit",
           "m4a": "M4A (AAC 256 kbps)", "ogg": "OGG Vorbis"}
_LOCK = threading.Lock()          # process() uses module-level hooks; one fix at a time per process


def rubberband_path(settings=None):
    p = os.environ.get("SONGSPEC_RUBBERBAND")
    if p and Path(p).is_file():
        return p
    for name in ("rubberband-r3", "rubberband"):
        w = shutil.which(name)
        if w:
            return w
    installed = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "BPM Fixer" / "_internal" / "rubberband-r3.exe"
    return str(installed) if installed.is_file() else None


def run(path, out_dir, models_dir, target_bpm=None, signature="Auto", bpm_mode="quarter", fmt="mp3", keep_edges=True,
        loudness=True, verify=True, log=None):
    """-> (output path, info, log lines). Raises RuntimeError with BPM Fixer's own (user-readable) messages."""
    lines = []

    def _log(m):
        lines.append(str(m))
        if log:
            log(str(m))

    rb = rubberband_path()
    if not rb and not bf.HAVE_PYLIBRB:
        raise RuntimeError("Fix BPM is not available on this server (Rubber Band is not installed).")
    with _LOCK:
        bf.detect_beats = lambda y, sr, log=print: engine.detect_beats(y, sr, models_dir)
        bf.find_rubberband = lambda: rb
        out, info = bf.process(str(path), target_bpm=target_bpm, out_dir=str(out_dir), log=_log, signature=signature,
                               bpm_mode=bpm_mode, fmt=FORMATS.get(fmt, fmt), keep_edges=keep_edges, loudness=loudness,
                               verify=verify)
    return out, info, lines
