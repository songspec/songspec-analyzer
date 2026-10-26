"""BTC (Park et al., ISMIR 2019: Bi-directional Transformer for Chord recognition; MIT licence) as a chord evidence source.

third_party/btc is the official repository (github.com/jayg996/BTC-ISMIR19 @ 2682317). This wrapper reproduces its own feature
extraction (test.py / utils/mir_eval_modules.py: 22.05 kHz, CQT 144 bins at 24 per octave, hop 2048, computed in 10 s pieces, log
magnitude), with one addition the brief asks for: the CQT is measured against the song's tuning. It returns BTC's per-frame
PROBABILITIES over its 170 chords mapped onto our vocabulary, so the beat-synchronous decoder can use them (not only BTC's top chord).
"""
import sys
from pathlib import Path

import numpy as np

from .vocab import FLAT, labels as vocab_labels

ROOT = Path(__file__).resolve().parents[3] / "third_party" / "btc"
SR, N_BINS, BPO, HOP, CHUNK_S, TIMESTEP = 22050, 144, 24, 2048, 10.0, 108
FRAME_S = CHUNK_S / TIMESTEP                           # BTC's own time unit (~0.0926 s)
_Q = ["min", "maj", "dim", "aug", "min6", "maj6", "min7", "minmaj7", "maj7", "7", "dim7", "hdim7", "sus2", "sus4"]
OURS = {"min": "m", "maj": "", "dim": "dim", "aug": "aug", "min6": "m6", "maj6": "6", "min7": "m7", "minmaj7": "m",
        "maj7": "maj7", "7": "7", "dim7": "dim7", "hdim7": "m7b5", "sus2": "sus2", "sus4": "sus4"}
_MODEL = {}


def available():
    return (ROOT / "test" / "btc_model_large_voca.pt").exists()


def _load(device):
    if "m" in _MODEL:
        return _MODEL["m"]
    import torch
    import yaml
    sys.path.insert(0, str(ROOT))
    if "float" not in np.__dict__:          # BTC (2019) uses np.float, removed in numpy 1.24; it meant the builtin float
        np.float = float
    from btc_model import BTC_model
    cfg = yaml.safe_load((ROOT / "run_config.yaml").read_text())["model"]
    cfg.update(num_chords=170, probs_out=True)
    model = BTC_model(config=cfg).to(device)
    from numpy._core import multiarray
    safe = [(multiarray._reconstruct, "numpy.core.multiarray._reconstruct"), (multiarray.scalar, "numpy.core.multiarray.scalar"),
            np.ndarray, np.dtype] + [type(np.dtype(t)) for t in ("float64", "float32", "int64", "int32")]
    with torch.serialization.safe_globals(safe):          # no arbitrary pickle code: only these numpy types are allowed
        ck = torch.load(ROOT / "test" / "btc_model_large_voca.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(ck["model"])
    model.eval()
    _MODEL["m"] = (model, np.asarray(ck["mean"], float), np.asarray(ck["std"], float), device)
    return _MODEL["m"]


def mapping():
    """BTC class index -> index in our vocabulary (vocab.labels('full')); BTC's 'X' and 'N' -> 'N'."""
    ours = vocab_labels("full")
    idx = {l: i for i, l in enumerate(ours)}
    m = np.zeros(170, int)
    for i in range(168):
        root, q = i // 14, _Q[i % 14]
        m[i] = idx[FLAT[root] + OURS[q]]
    m[168] = m[169] = idx["N"]
    return m


def features(y22, tuning_cents=0.0):
    import librosa
    pieces, step = [], int(SR * CHUNK_S)
    for a in range(0, len(y22), step):
        seg = y22[a:a + step]
        if len(seg) < HOP:
            break
        pieces.append(librosa.cqt(seg, sr=SR, n_bins=N_BINS, bins_per_octave=BPO, hop_length=HOP, tuning=float(tuning_cents) / 100))
    return np.log(np.abs(np.concatenate(pieces, axis=1)) + 1e-6)


def frame_probs(y22, tuning_cents=0.0, device="cpu"):
    """-> (times of the frames in seconds, frames x len(our vocabulary) probabilities)."""
    import torch
    model, mean, std, dev = _load(device)
    f = ((features(y22, tuning_cents).T - mean) / std).astype(np.float32)
    n = f.shape[0]
    pad = (-n) % TIMESTEP
    f = np.pad(f, ((0, pad), (0, 0)))
    out = []
    with torch.no_grad():
        x = torch.from_numpy(f).unsqueeze(0).to(dev)
        for t in range(f.shape[0] // TIMESTEP):
            h, _ = model.self_attn_layers(x[:, t * TIMESTEP:(t + 1) * TIMESTEP, :])
            out.append(torch.softmax(model.output_layer.output_projection(h), -1).squeeze(0).cpu().numpy())
    p170 = np.concatenate(out)[:n]
    m = mapping()
    P = np.zeros((n, len(vocab_labels("full"))))
    np.add.at(P.T, m, p170.T)
    return np.arange(n) * FRAME_S, P
