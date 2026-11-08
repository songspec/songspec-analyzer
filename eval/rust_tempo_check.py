#!/usr/bin/env python3
"""Tempo parity, BPM Fixer port only: Python's raw Beat This! beats go into both the service's engine.analyze and the Rust port
(songspec-cli --tempo FILE RAW.json); beats, BPM, signature and the display must match.
Run:  .venv/Scripts/python eval/rust_tempo_check.py [N songs]"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from common import cache_path, load_labels  # noqa: E402

from songspec_analyzer.audio.decode import load_audio  # noqa: E402
from songspec_analyzer.tempo import section_from_engine  # noqa: E402
from songspec_analyzer.tempo.engine import analyze  # noqa: E402

CLI = os.environ.get("SONGSPEC_CLI", str(ROOT / "rust" / "target" / "fast" / "songspec-cli.exe"))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    rows = [r for r in load_labels() if r["path"].exists() and cache_path("bt_raw", r["file"]).with_suffix(".npz").exists()][:n]
    same = 0
    for r in rows:
        z = np.load(cache_path("bt_raw", r["file"]).with_suffix(".npz"))
        raw, down = np.asarray(z["beats"], float), np.asarray(z["down"], float)
        a = load_audio(str(r["path"]))
        py = section_from_engine(analyze(a.mono, a.sr, None, tracked=(raw, down if len(down) >= 4 else None)))
        with tempfile.TemporaryDirectory() as td:
            j = Path(td) / "raw.json"
            j.write_text(json.dumps({"beats": raw.tolist(), "downbeats": down.tolist()}))
            rs = json.loads(subprocess.run([CLI, "--tempo", str(r["path"]), str(j)], capture_output=True, text=True, check=True).stdout)
        pb, rb = np.asarray(py["beats"]), np.asarray(rs["beats"])
        beats_eq = len(pb) == len(rb) and np.abs(pb - rb).max() <= 0.0011
        ok = beats_eq and py["bpm"] == rs["bpm"] and py["signature"] == rs["signature"] and py["display"]["bpm"] == rs["display"]["bpm"]
        same += ok
        print(f"{'OK  ' if ok else 'DIFF'} {r['file'][:40]:40s} bpm py {py['bpm']} rust {rs['bpm']}  sig {py['signature']}/{rs['signature']}  "
              f"beats {len(pb)}/{len(rb)}{'' if beats_eq else ' (differ)'}  display {py['display']['bpm']!r}/{rs['display']['bpm']!r}", flush=True)
    print(f"\n{same}/{len(rows)} identical")


if __name__ == "__main__":
    main()
