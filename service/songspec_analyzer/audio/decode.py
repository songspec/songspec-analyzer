"""Read almost any audio/video file. Same strategy as BPM Fixer's load_audio: soundfile first, then ffmpeg, then librosa."""
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import numpy as np
import soundfile as sf

# BPM Fixer's list (app/bpmfix.py AUDIO_EXTS): anything ffmpeg decodes is fine, these are the ones we advertise
AUDIO_EXTS = ("wav mp3 flac ogg oga opus m4a aac mp4 m4b wma aiff aif aifc caf w64 wv ape mka mp2 ac3 amr "
              "webm mov 3gp au").split()


class AudioError(RuntimeError):
    """A file we cannot or will not analyse. The message is safe to show to the user."""


@dataclass
class Audio:
    samples: np.ndarray    # samples x channels, float32, original sample rate
    sr: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sr

    @property
    def mono(self) -> np.ndarray:
        return np.ascontiguousarray(self.samples.mean(axis=1), dtype=np.float32)


def find_ffmpeg(name="ffmpeg"):
    p = shutil.which(name)
    if p:
        return p
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


def probe_duration(path):
    """Duration in seconds without decoding the whole file (ffprobe, else soundfile). None if unknown."""
    try:
        return float(sf.info(path).duration)
    except Exception:
        pass
    fp = find_ffmpeg("ffprobe")
    if fp:
        r = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True)
        try:
            return float(r.stdout.strip())
        except ValueError:
            return None
    return None


def read_tags(path):
    """{'title', 'artist', 'isrc'} from the file's own metadata (ffprobe; ID3 TSRC / Vorbis ISRC). Missing values are None."""
    import json
    fp = find_ffmpeg("ffprobe")
    out = {"title": None, "artist": None, "isrc": None}
    if not fp:
        return out
    try:
        r = subprocess.run([fp, "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        tags = {k.lower(): v for k, v in (json.loads(r.stdout).get("format", {}).get("tags") or {}).items()}
    except Exception:
        return out
    out["title"] = tags.get("title")
    out["artist"] = tags.get("artist") or tags.get("album_artist")
    isrc = tags.get("isrc") or tags.get("tsrc")
    if isrc and len(isrc.replace("-", "")) == 12:
        out["isrc"] = isrc.replace("-", "").upper()
    return out


def load_audio(path) -> Audio:
    err = None
    try:
        y, sr = sf.read(path, dtype="float32", always_2d=True)
        if len(y):
            return Audio(np.ascontiguousarray(y), int(sr))
    except Exception as e:
        err = e
    ff = find_ffmpeg()
    if ff:
        with tempfile.TemporaryDirectory() as td:
            tmp = os.path.join(td, "dec.wav")
            r = subprocess.run([ff, "-v", "error", "-y", "-i", path, "-vn", "-ac", "2", "-c:a", "pcm_f32le", tmp],
                               capture_output=True, text=True)
            if r.returncode == 0 and os.path.isfile(tmp):
                y, sr = sf.read(tmp, dtype="float32", always_2d=True)
                if len(y):
                    return Audio(np.ascontiguousarray(y), int(sr))
            err = RuntimeError(r.stderr.strip()[-300:] or "ffmpeg could not decode it")
    try:
        import librosa
        y, sr = librosa.load(path, sr=None, mono=False)
        y = y[np.newaxis, :] if y.ndim == 1 else y
        return Audio(np.ascontiguousarray(y.T.astype(np.float32)), int(sr))
    except Exception as e:
        err = err or e
    raise AudioError(f"Could not read this file as audio ({str(err)[:160]}).")
