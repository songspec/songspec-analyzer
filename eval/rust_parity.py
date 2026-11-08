#!/usr/bin/env python3
"""Parity between the desktop app's Rust engine and the Python service on the same songs.

Both analyse every song from scratch (the service on the CPU, as the app runs); compared: the key (display, tonic, scale, mode),
the BPM (number and display), the time signature, detune (must agree within 0.5 cents), and for information the borrowed-from
names and the chords (share of the song's time where both name the same chord). Writes eval/results/rust_parity.md.
Run:  .venv/Scripts/python eval/rust_parity.py [N songs | files...]      (build first: cargo build --release -p songspec-cli)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
os.environ.setdefault("SONGSPEC_DEVICE", "cpu")
from common import RESULTS, load_labels, md_table  # noqa: E402

_BUILDS = [p for p in (ROOT / "rust/target/release/songspec-cli.exe", ROOT / "rust/target/fast/songspec-cli.exe") if p.exists()]
CLI = os.environ.get("SONGSPEC_CLI") or (str(max(_BUILDS, key=lambda p: p.stat().st_mtime)) if _BUILDS else None)  # the newest build
DETUNE_TOL = 0.5


def chord_agreement(a, b):
    """Share of time where both name the same chord (root + quality, bass ignored), over the span both cover."""
    def at(segs, t):
        for s in segs:
            if s["start"] <= t < s["end"]:
                return s["chord"].split("/")[0]
        return None
    end = min(a[-1]["end"], b[-1]["end"]) if a and b else 0
    ts = [x * 0.1 for x in range(int(end * 10))]
    same = sum(at(a, t) == at(b, t) for t in ts)
    return same / len(ts) if ts else float("nan")


def main():
    args = sys.argv[1:]
    if args and not args[0].isdigit():
        files = args
    else:
        n = int(args[0]) if args else 20
        files = [str(r["path"]) for r in load_labels() if r["path"].exists()][:n]
    if not CLI:
        raise SystemExit("build the CLI first: cargo build --release -p songspec-cli (in rust/)")
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "rust.json"
        subprocess.run([CLI, *files, "--out", str(out)], check=True, stderr=subprocess.DEVNULL)
        rust = {r["file"]: r for r in json.loads(out.read_text(encoding="utf-8"))}
    t_rust = time.time() - t0
    from songspec_analyzer import analyze_file
    rows, stats = [], {"key": 0, "bpm": 0, "sig": 0, "detune": 0, "all": 0, "n": 0}
    t_py = 0.0
    for f in files:
        t1 = time.time()
        py = analyze_file(f)
        t_py += time.time() - t1
        rs = rust[f]
        if "error" in rs or py.get("status") != "done":
            rows.append([Path(f).name[:34], "ERROR", rs.get("error", ""), py.get("error", ""), "", "", "", ""])
            continue
        pk, rk = py.get("key") or {}, rs.get("key") or {}
        pt, rt = py.get("tempo") or {}, rs.get("tempo") or {}
        pd, rd = py.get("detune") or {}, rs.get("detune") or {}
        key_ok = (pk.get("display") or {}).get("key") == (rk.get("display") or {}).get("key") and pk.get("mode") == rk.get("mode")
        bpm_ok = pt.get("bpm") == rt.get("bpm") and (pt.get("display") or {}).get("bpm") == (rt.get("display") or {}).get("bpm")
        sig_ok = pt.get("signature") == rt.get("signature")
        det_ok = pd.get("cents") is not None and rd.get("cents") is not None and abs(pd["cents"] - rd["cents"]) <= DETUNE_TOL
        ok = key_ok and bpm_ok and sig_ok and det_ok
        stats["n"] += 1
        for k, v in (("key", key_ok), ("bpm", bpm_ok), ("sig", sig_ok), ("detune", det_ok), ("all", ok)):
            stats[k] += v
        pb = ", ".join(b["from"] for b in pk.get("borrows") or []) or "-"
        rb = ", ".join(b["from"] for b in rk.get("borrows") or []) or "-"
        agree = chord_agreement((py.get("chords") or {}).get("segments") or [], (rs.get("chords") or {}).get("segments") or [])
        mark = lambda b: "" if b else " **≠**"
        rows.append([
            Path(f).name[:34] + ("" if ok else " ✗"),
            f"{(pk.get('display') or {}).get('key')} / {(rk.get('display') or {}).get('key')}{mark(key_ok)}",
            f"{(pt.get('display') or {}).get('bpm')} / {(rt.get('display') or {}).get('bpm')}{mark(bpm_ok)}",
            f"{pt.get('signature')} / {rt.get('signature')}{mark(sig_ok)}",
            f"{pd.get('cents')} / {rd.get('cents')}{mark(det_ok)}",
            f"{pb} / {rb}" + ("" if pb == rb else " (differ)"),
            f"{100 * agree:.0f} %",
        ])
        print(f"{'OK  ' if ok else 'DIFF'} {Path(f).name[:40]:40s} key {key_ok} bpm {bpm_ok} sig {sig_ok} detune {det_ok}  chords {100 * agree:.0f} %",
              flush=True)
    n = max(1, stats["n"])
    summary = [["key (display + mode)", f"{stats['key']}/{stats['n']}"], ["BPM (value + display)", f"{stats['bpm']}/{stats['n']}"],
               ["time signature", f"{stats['sig']}/{stats['n']}"], [f"detune within {DETUNE_TOL} cents", f"{stats['detune']}/{stats['n']}"],
               ["all four", f"{stats['all']}/{stats['n']} ({100 * stats['all'] / n:.0f} %)"]]
    text = [f"# Rust engine vs Python service ({time.strftime('%Y-%m-%d')})", "",
            f"{stats['n']} songs, each analysed from scratch by both (service on the CPU). Rust {t_rust:.0f} s in total, "
            f"Python {t_py:.0f} s.", "", md_table(["", "same"], summary), "",
            md_table(["song", "key (py / rust)", "BPM", "signature", "detune ¢", "may borrow from", "chords same"], rows), ""]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "rust_parity.md").write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text[:8]))


if __name__ == "__main__":
    main()
