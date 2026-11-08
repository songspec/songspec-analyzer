#!/usr/bin/env python3
"""Export the service's three neural networks to ONNX for the Rust engine (rust/songspec-core runs them with tract, pure Rust).

  rust/models/beat_this.onnx   Beat This! final0 (the service's vendored checkpoint): log-mel (1, T, 128) -> beat, downbeat logits
  rust/models/skey.onnx        S-KEY (HCQT front end + ChromaNet): waveform (1, 1, N) at 22.05 kHz -> 24 probabilities (S-KEY order)
  rust/models/btc.onnx         BTC large vocabulary: normalised log-CQT (1, 108, 144) -> (1, 108, 170) chord probabilities
  rust/models/btc_norm.json    BTC's feature mean / std

The graphs are the same networks with the same weights; two S-KEY layers are rewritten in equivalent form only because the ONNX
exporter cannot express them with a variable audio length (F.layer_norm over x.shape[1:] -> the same mean / variance written out;
AdaptiveAvgPool2d((12, 1)) after the octave pool -> the mean over time). Every export is checked against PyTorch.
Run:  .venv/Scripts/python tools/export_onnx.py      (needs: pip install onnx onnxruntime)
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as Fn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "service"))
OUT = ROOT / "rust" / "models"
TOL = 1e-4


def check(name, onnx_path, feeds, torch_out):
    import onnxruntime as ort
    got = ort.InferenceSession(str(onnx_path)).run(None, feeds)
    diff = max(float(np.abs(g - t).max()) for g, t in zip(got, torch_out))
    print(f"  {name}: max |PyTorch - ONNX| = {diff:.2e}")
    if diff > TOL:
        raise SystemExit(f"{name}: export differs from PyTorch by {diff}")


def beat_this():
    from beat_this.inference import load_model
    from songspec_analyzer.config import get_settings
    m = load_model(str(Path(get_settings().models_dir) / "beat_this-final0.ckpt"), "cpu").eval()

    class W(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.m = m

        def forward(self, x):
            p = self.m(x)
            return p["beat"], p["downbeat"]

    w = W().eval()
    p = OUT / "beat_this.onnx"
    torch.onnx.export(w, (torch.randn(1, 1500, 128),), str(p), input_names=["spect"], output_names=["beat", "downbeat"],
                      opset_version=17, dynamic_axes={"spect": {1: "T"}, "beat": {1: "T"}, "downbeat": {1: "T"}}, dynamo=False)
    for T in (1500, 700):
        x = torch.randn(1, T, 128)
        with torch.no_grad():
            ref = [t.numpy() for t in w(x)]
        check(f"beat_this T={T}", p, {"spect": x.numpy()}, ref)


def _ln(x, shape, eps=1e-5):
    dims = tuple(range(1, x.dim()))
    mu = x.mean(dim=dims, keepdim=True)
    var = ((x - mu) ** 2).mean(dim=dims, keepdim=True)
    return (x - mu) / torch.sqrt(var + eps)


class _MeanTime(torch.nn.Module):
    def forward(self, x):
        return x.mean(dim=3, keepdim=True)


def skey():
    from songspec_analyzer.key import estimators as E
    hcqt, chromanet, crop, ksr, index, _ = E._skey_load("cpu")
    assert ksr == 22050, ksr

    class SK(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.hcqt, self.chromanet, self.crop = hcqt, chromanet, crop

        def forward(self, x):
            return self.chromanet(self.crop(self.hcqt(x), torch.zeros(1)))

    sk = SK().eval()
    x0 = torch.randn(1, 1, ksr * 20) * 0.1
    with torch.no_grad():
        before = sk(x0)
    for mod in sk.modules():
        if getattr(mod, "norm", None) is Fn.layer_norm:
            mod.norm = _ln
    sk.chromanet.global_average_pool = _MeanTime()
    with torch.no_grad():
        after = sk(x0)
    print(f"  skey: equivalent rewrite changes the output by {float((before - after).abs().max()):.2e}")
    p = OUT / "skey.onnx"
    torch.onnx.export(sk, (x0,), str(p), input_names=["audio"], output_names=["probs"], opset_version=17,
                      dynamic_axes={"audio": {2: "N"}}, dynamo=False)
    for secs in (20, 7):
        x = torch.randn(1, 1, ksr * secs) * 0.1
        with torch.no_grad():
            ref = [sk(x).numpy()]
        check(f"skey {secs} s", p, {"audio": x.numpy()}, ref)
    print(f"  skey index (S-KEY class -> tonic + 12 * minor): {index.tolist()}")


def btc():
    from songspec_analyzer.chords import btc as B
    model, mean, std, _ = B._load("cpu")

    class W(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.m = model

        def forward(self, x):
            h, _ = self.m.self_attn_layers(x)
            return torch.softmax(self.m.output_layer.output_projection(h), -1)

    w = W().eval()
    p = OUT / "btc.onnx"
    x = torch.randn(1, B.TIMESTEP, B.N_BINS)
    torch.onnx.export(w, (x,), str(p), input_names=["feat"], output_names=["probs"], opset_version=17, dynamo=False)
    with torch.no_grad():
        ref = [w(x).numpy()]
    check("btc", p, {"feat": x.numpy()}, ref)
    (OUT / "btc_norm.json").write_text(json.dumps({"mean": float(mean), "std": float(std)}) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for f in (beat_this, skey, btc):
        print(f.__name__)
        f()
    print(f"wrote {OUT}: {', '.join(sorted(q.name for q in OUT.iterdir()))}")


if __name__ == "__main__":
    main()
