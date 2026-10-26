"""The ORIGINAL BPM Fixer, imported read-only from 'Everything Claude Needs' (no bytecode is written there), and the analysis half of
its process() written out step by step with the original module's own functions. Used by the parity tests and eval/tempo_parity.py."""
import importlib.util
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BPMFIX = ROOT / "Everything Claude Needs" / "BPM-Fixer-source" / "bpm-fixer" / "app" / "bpmfix.py"


def load_original():
    if not BPMFIX.exists():
        return None
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("bpmfix_original", BPMFIX)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.dont_write_bytecode = old


def reference_beats(bf, y, sr, raw, down, signature="Auto", bpm_lo=80.0, bpm_hi=160.0):
    """process() lines 'sig = signature' .. 'beats = real' (keep_edges=True, target_bpm=None), with the tracker output given."""
    total = len(y) / sr
    sig = signature
    if sig == "Auto":
        guess = bf.infer_signature(raw, down)
        sig = guess[0] if guess else "4/4"
    spec = bf.sig_spec(sig)
    log = lambda *a: None
    prep = bf.prepare_anchors(raw, down, total, spec, None, bpm_lo, bpm_hi, log)
    try:
        T_ref = bf.pick_reference_period(prep["anchors"], None)
        fixed, fixes = bf.enforce_level(prep["anchors"], y, sr, T_ref, log)
        if fixes:
            prep["anchors"] = fixed
    except Exception:
        pass
    real, mask = bf.refine_beats(prep["anchors"], y, sr, log=log, return_mask=True)
    real = bf.fill_weak_runs(real, mask)
    return np.asarray(real), sig
