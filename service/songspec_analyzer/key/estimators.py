"""Independent key estimators. Each returns a 24-way probability vector indexed tonic + 12 * minor (0 = C major, 12 = C minor,
21 = A minor), so they can be compared, stacked and fused.

profiles   correlation of the tuning-corrected, beat-synchronous chroma with every profile in profiles.py, softmaxed
centsible  Centsible's two-stage detector (Temperley scale + bass tonic), via the binding (computed in the quick-key stage)
skey       S-KEY (Deezer, ICASSP 2025; MIT code, weights shipped in the same repo), third_party/skey
"""
import sys
from pathlib import Path

import numpy as np

from .profiles import PROFILES

SR = 22050
ROOT = Path(__file__).resolve().parents[3]


def softmax(x, temp=1.0):
    z = np.asarray(x, float) / temp
    z = np.exp(z - z.max())
    return z / z.sum()


def tuned_chroma(yh, tuning_cents, beats=None, hop=512):
    """Chroma (CQT, 36 bins/octave, C1-C8) of the harmonic signal at 22.05 kHz, measured against the song's tuning.
    With beats: one frame per beat (median), so a long held note counts per beat, not per frame. Returns (frames x 12, weights)."""
    import librosa
    C = librosa.feature.chroma_cqt(y=yh, sr=SR, hop_length=hop, tuning=float(tuning_cents) / 100.0, bins_per_octave=36,
                                   n_octaves=7, fmin=librosa.note_to_hz("C1"), norm=None)
    energy = np.sqrt((C ** 2).sum(axis=0))
    if beats is not None and len(beats) > 8:
        fr = librosa.time_to_frames(np.asarray(beats), sr=SR, hop_length=hop)
        fr = fr[(fr > 0) & (fr < C.shape[1])]
        # sync returns the stretch BEFORE the first boundary as segment 0: drop it, so row i is beat i (beat i .. beat i+1)
        C = librosa.util.sync(C, fr, aggregate=np.median)[:, 1:]
        energy = librosa.util.sync(energy[None, :], fr, aggregate=np.mean)[0][1:]
    return C.T, energy


def global_chroma(frames, weights=None):
    """Song-level chroma: log-compressed (log1p(100 x / max)), every non-silent frame weighted equally, L1-normalised.
    Raw magnitudes let one loud sub-bass note (an 808) swamp the scale, and energy weighting lets the loudest section decide; on the
    catalog (eval/REPORT.md section 3) this aggregation lifted Albrecht-Shanahan from 0.39 to 0.51 MIREX. `weights` only marks
    silent frames (< 5 % of the median energy), which are left out."""
    F = np.asarray(frames, float)
    if weights is not None:
        w = np.asarray(weights, float)
        if (w > 0).any():
            F = F[w > 0.05 * np.median(w[w > 0])]
    if not len(F):
        return np.full(12, 1 / 12)
    F = np.log1p(100.0 * F / (F.max() + 1e-12))
    F = F / (F.sum(axis=1, keepdims=True) + 1e-12)
    g = F.sum(axis=0)
    return g / (g.sum() + 1e-12)


def naive_chroma(y22):
    """librosa's default chroma_cqt averaged over the song (no tuning, no HPSS): kept as an estimator input because it carries
    information the tuned chroma does not (it was the best single profile input before the log aggregation)."""
    import librosa
    c = librosa.feature.chroma_cqt(y=y22, sr=SR).mean(axis=1)
    return c / (c.sum() + 1e-12)


def estimator_pool(centsible_key_prob, chroma_log, chroma_scale, chroma_naive):
    """Every estimator the fusion can use: Centsible's decision and each profile on three chromas (name '<profile>@<chroma>')."""
    est = {"centsible": np.asarray(centsible_key_prob, float)}
    for tag, c in (("beat", chroma_log), ("scale", chroma_scale), ("naive", chroma_naive)):
        for name, p in profile_probs(np.asarray(c, float)).items():
            est[f"{name}@{tag}"] = p
    return est


def profile_scores(chroma, profile):
    """Pearson correlation of a 12-bin chroma with the profile rotated to every key -> 24 scores (index tonic + 12 * minor)."""
    maj, minr = (np.asarray(p, float) for p in PROFILES[profile])
    out = np.zeros(24)
    for t in range(12):
        out[t] = np.corrcoef(chroma, np.roll(maj, t))[0, 1]
        out[12 + t] = np.corrcoef(chroma, np.roll(minr, t))[0, 1]
    return np.nan_to_num(out, nan=-1.0)


def profile_probs(chroma, temp=0.1):
    """{profile name: 24 probabilities}. temp: softmax temperature over correlations (refit by the fusion calibration)."""
    return {name: softmax(profile_scores(chroma, name), temp) for name in PROFILES}


# ------------------------------------------------------------------------------------------------ S-KEY
_SKEY = {}


def skey_available():
    return (ROOT / "third_party" / "skey" / "skey" / "models" / "skey.pt").exists()


def _skey_load(device):
    if "m" in _SKEY:
        return _SKEY["m"]
    import torch
    sys.path.insert(0, str(ROOT / "third_party" / "skey"))
    from skey.key_detection import key_map, load_model_components
    ckpt_path = ROOT / "third_party" / "skey" / "skey" / "models" / "skey.pt"
    # weights_only=True: no arbitrary pickle code. The checkpoint stores a few numpy scalars (its config), so exactly those numpy
    # types are allow-listed; anything else in the file would still be refused.
    from numpy._core import multiarray
    # the file was written with numpy 1.x, which kept `scalar` in numpy.core (numpy 2: numpy._core): register it under the old name
    safe = [(multiarray.scalar, "numpy.core.multiarray.scalar"), np.dtype] + \
        [type(np.dtype(t)) for t in ("float64", "float32", "int64", "int32", "bool")]
    try:
        with torch.serialization.safe_globals(safe):
            ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    except Exception as e:
        raise RuntimeError(f"S-KEY checkpoint does not load with weights_only=True; refusing to unpickle it ({str(e)[-200:]})")
    hcqt, chromanet, crop = load_model_components(ckpt, torch.device(device))
    from ..display.key import parse_key
    index = []
    for i in range(24):
        tonic, mode = parse_key(key_map[i])
        index.append(tonic + (12 if mode == "minor" else 0))
    _SKEY["m"] = (hcqt, chromanet, crop, int(ckpt["audio"]["sr"]), np.array(index), device)
    return _SKEY["m"]


def skey_probs(y, sr, device="cpu"):
    """S-KEY on a mono signal -> 24 probabilities in our index order (softmax of the time-averaged logits)."""
    import torch
    import torchaudio
    hcqt, chromanet, crop, ksr, index, dev = _skey_load(device)
    x = torch.from_numpy(np.ascontiguousarray(y, dtype=np.float32))[None, :]
    if sr != ksr:
        x = torchaudio.functional.resample(x, sr, ksr)
    m = x.abs().max()
    if m > 0:
        x = x / m
    with torch.no_grad():
        probs = chromanet(crop(hcqt(x.unsqueeze(0).to(dev)), torch.zeros(1).to(dev)))   # ChromaNet ends in a softmax
        pm = torch.mean(probs, dim=0).cpu().numpy().ravel()
    p_theirs = pm / pm.sum()
    out = np.zeros(24)
    out[index] = p_theirs
    return out
