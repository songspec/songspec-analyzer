"""Fix BPM: BPM Fixer's own process() (verbatim in _bpmfix_fix.py) on the server, with BPM Fixer's defaults. Beat This! comes from
the service (vendored model). Rubber Band, as in the BPM Fixer app, is the bundled rubberband-r3.exe (service/vendor/rubberband,
filled by tools/fetch_vendor.py from the same conda-forge package), else SONGSPEC_RUBBERBAND, else PATH, else an installed BPM
Fixer's copy; only without any of those does it fall back to pylibrb (real-time, lower quality).
Rubber Band is GPL: it runs as a separate program on the server (LICENSES.md)."""
import os
import shutil
import threading
from pathlib import Path

from . import _bpmfix_fix as bf
from . import engine

FORMATS = {"mp3": "MP3 320 kbps", "wav": "WAV 24-bit", "wav16": "WAV 16-bit", "flac": "FLAC", "aiff": "AIFF 24-bit",
           "m4a": "M4A (AAC 256 kbps)", "ogg": "OGG Vorbis"}
_LOCK = threading.Lock()          # process() uses module-level hooks; one fix at a time per process


VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "rubberband"


def rubberband_path(settings=None):
    for p in (VENDOR / "rubberband-r3.exe", VENDOR / "rubberband-r3", os.environ.get("SONGSPEC_RUBBERBAND")):
        if p and Path(p).is_file():
            return str(p)
    for name in ("rubberband-r3", "rubberband"):
        w = shutil.which(name)
        if w:
            return w
    installed = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "BPM Fixer" / "_internal" / "rubberband-r3.exe"
    return str(installed) if installed.is_file() else None


D = bf.DEFAULTS                   # BPM Fixer's defaults (4/4, quarter-note BPM, keep edges, keep loudness, verify)


def run(path, out_dir, models_dir, target_bpm=None, signature=D["signature"], bpm_mode=D["bpm_mode"], fmt="mp3",
        keep_edges=D["keep_edges"], loudness=D["loudness"], verify=D["verify"], log=None):
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
