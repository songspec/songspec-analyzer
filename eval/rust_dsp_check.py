#!/usr/bin/env python3
"""Check the Rust DSP layer (rust/songspec-core/src/dsp.rs) against librosa / torchaudio on a 30 s excerpt of a real song.

The Rust CLI dumps every stage (`songspec-cli --dsp-check FILE DIR`); this script recomputes each stage in Python FROM THE RUST
INPUT OF THAT STAGE where possible, so an error in one stage does not show up as an error in the next. Reports the largest
difference relative to the stage's own scale.
Run:  .venv/Scripts/python eval/rust_dsp_check.py SONG.flac
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "rust" / "target" / "release" / "songspec-cli.exe"


def load(d, name):
    shape = json.loads((d / f"{name}.json").read_text())
    return np.fromfile(d / f"{name}.f32", dtype="<f4").reshape(shape)


def report(name, ours, ref, note=""):
    ours, ref = np.asarray(ours, float), np.asarray(ref, float)
    if ours.shape != ref.shape:
        print(f"{name:28s} SHAPE {ours.shape} vs librosa {ref.shape} {note}")
        n = tuple(min(a, b) for a, b in zip(ours.shape, ref.shape))
        sl = tuple(slice(0, k) for k in n)
        ours, ref = ours[sl], ref[sl]
    scale = np.abs(ref).max() + 1e-12
    err = np.abs(ours - ref).max() / scale
    corr = np.corrcoef(ours.ravel(), ref.ravel())[0, 1] if ours.size > 1 else float("nan")
    print(f"{name:28s} max |diff| / max |ref| = {err:.2e}   corr {corr:.6f} {note}")


def main():
    song = sys.argv[1]
    import librosa
    import torch
    import torchaudio
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        meta = json.loads(subprocess.run([str(CLI), "--dsp-check", song, td], capture_output=True, text=True, check=True).stdout)
        sr = meta["sr"]
        x = load(d, "x")
        y22 = load(d, "y22")
        report("resample_hq -> 22050", y22, librosa.resample(x, orig_sr=sr, target_sr=22050), "(soxr_hq stand-in)")
        report("harmonic (from our y22)", load(d, "harm22"), librosa.effects.harmonic(y22, margin=2.0))
        perc = load(d, "perc")
        report("percussive", perc, librosa.effects.percussive(x, margin=2.0))
        env = load(d, "env_perc")
        report("onset perc/median/max3", env, librosa.onset.onset_strength(y=perc, sr=sr, hop_length=256, aggregate=np.median, lag=1, max_size=3))
        report("onset full/mean", load(d, "env_full"), librosa.onset.onset_strength(y=x, sr=sr, hop_length=256))
        report("onset fine 512/32/40", load(d, "env_fine"),
               librosa.onset.onset_strength(y=x[:sr], sr=sr, n_fft=512, hop_length=32, lag=1, center=True, n_mels=40))
        _, ref_beats = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=256, bpm=120.0, tightness=80, trim=False)
        ours = load(d, "beats").astype(int)
        print(f"{'beat_track (from our env)':28s} {len(ours)} vs {len(ref_beats)} beats, identical: {np.array_equal(ours, ref_beats)}")
        yh = load(d, "harm22")
        C = np.abs(librosa.cqt(yh, sr=22050, hop_length=512, fmin=librosa.note_to_hz("C1"), n_bins=252, bins_per_octave=36, tuning=0.1))
        report("cqt 252 bins (from our yh)", load(d, "cqt252"), C.T)
        B = np.abs(librosa.cqt(y22[:220500], sr=22050, n_bins=144, bins_per_octave=24, hop_length=2048, tuning=0.05))
        report("cqt 144 bins hop 2048", load(d, "cqt144"), B.T)
        r = torchaudio.functional.resample(torch.from_numpy(x)[None], sr, 22050)[0].numpy()
        report("torchaudio resample", load(d, "torch22"), r)


if __name__ == "__main__":
    main()
