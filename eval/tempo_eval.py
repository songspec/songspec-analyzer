#!/usr/bin/env python3
"""Tempo against the user's tracker BPMs (catalog songs with a tracker BPM).

Estimators, all on the same decoded audio:
  service      the service's tempo stage (BPM Fixer engine + line fit + house rule)
  beatthis     Beat This! raw beats, median interval, house rule (no BPM Fixer cleaning)
  librosa      librosa.beat.beat_track tempo, house rule
  centsible    Centsible's own tempo (centsible_cli --csv), already reported in 80-160
Metrics: within 1 % of the tracker's main (first-listed) BPM, Acc1 (4 %), Acc2 (4 %, octave / third errors allowed);
"any listed" counts a match with any of the song's listed section tempos. Steadiness is scored against the tracker's notes
("BPM inconsistent", "played live", "moderately inconsistent").

Caches (eval/results/): raw Beat This! output per song (the network runs once), the engine result, the librosa tempo.
Run:  .venv/Scripts/python eval/tempo_eval.py [--limit N] [--refresh]
"""
import argparse
import csv
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from common import RESULTS, cache_path, cached, catalog_dir, load_labels, md_table

from songspec_analyzer.audio.decode import load_audio
from songspec_analyzer.config import Settings
from songspec_analyzer.display.bpm import house_bpm
from songspec_analyzer.tempo import engine, section_from_engine

CENTSIBLE_CLI = Path(r"C:\cb\cli\centsible_cli_artefacts\Release\centsible_cli.exe")
INCONSISTENT = re.compile(r"inconsistent|played live", re.I)


def tracked(name, a):
    """Raw Beat This! output, cached per song as .npz."""
    p = cache_path("bt_raw", name).with_suffix(".npz")
    if p.exists():
        z = np.load(p, allow_pickle=False)
        return z["beats"], (z["down"] if z["down"].size else None)
    b, d = engine.detect_beats(a.mono, a.sr, Settings().models_dir)
    np.savez(p, beats=b, down=d if d is not None else np.array([]))
    return b, d


def run_service(name, path):
    a = load_audio(str(path))
    t0 = time.time()
    raw, down = tracked(name, a)
    r = engine.analyze(a.mono, a.sr, Settings().models_dir, tracked=(raw, down))
    sec = section_from_engine(r)
    return {"bpm": sec["bpm"], "bpm_raw": r["bpm_raw"], "display": sec["display"]["bpm"], "note": sec["display"]["note"],
            "sig": sec["signature"], "feel": sec["feel"], "sections": r["sections"], "irr": sec["irregularity_pct"],
            "beatthis": house_bpm(60.0 / float(np.median(np.diff(raw))))[0], "seconds": round(time.time() - t0, 2)}


def librosa_tempo(path):
    import librosa
    a = load_audio(str(path))
    y = librosa.resample(a.mono, orig_sr=a.sr, target_sr=22050)
    tempo, _ = librosa.beat.beat_track(y=y, sr=22050)
    return house_bpm(float(np.atleast_1d(tempo)[0]))[0]


def run_centsible(paths):
    """{file name: bpm} from one batch run of the plugin's command-line analyzer."""
    if not CENTSIBLE_CLI.exists():
        return {}
    out = {}
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "c.csv"
        subprocess.run([str(CENTSIBLE_CLI), "--csv", str(csv_path)] + [str(p) for p in paths], capture_output=True, timeout=7200)
        if csv_path.exists():
            with open(csv_path, encoding="utf-8", errors="replace") as f:
                for r in csv.DictReader(f):
                    try:
                        out[Path(r["file"]).name] = float(r.get("bpm") or "nan")
                    except ValueError:
                        pass
    return out


def within(ours, truth, tol):
    return ours is not None and bool(truth) and abs(ours - truth) / truth <= tol


def acc2(ours, truth, tol=0.04):
    return ours is not None and any(within(ours * m, truth, tol) for m in (1, 2, 0.5, 3, 1 / 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true", help="re-run the engine (the tracker cache is kept)")
    ap.add_argument("--no-baselines", action="store_true")
    a = ap.parse_args()
    rows = [r for r in load_labels() if r["bpm_list"] and r["path"].exists()]
    if a.limit:
        rows = rows[:a.limit]
    print(f"{len(rows)} labelled songs from {catalog_dir()}  (Beat This! on {engine.device()})", flush=True)
    res = []
    for i, r in enumerate(rows, 1):
        try:
            o = cached("tempo_v3", r["file"], lambda: run_service(r["file"], r["path"]), refresh=a.refresh)
            if not a.no_baselines:
                o["librosa"] = cached("librosa_bpm", r["file"], lambda: {"bpm": librosa_tempo(r["path"])})["bpm"]
        except Exception as e:
            o = {"error": f"{type(e).__name__}: {e}"}
        res.append((r, o))
        print(f"[{i}/{len(rows)}] {r['file'][:50]:50s} tracker {r['bpms']:16s} ours {str(o.get('display')):18s} "
              f"({o.get('seconds', '-')} s)", flush=True)
    cent = {} if a.no_baselines else cached("centsible_bpm", "|".join(sorted(r["file"] for r, _ in res)),
                                            lambda: run_centsible([r["path"] for r, _ in res]))

    est = {"service": lambda r, o: o.get("bpm"), "beatthis (raw)": lambda r, o: o.get("beatthis"),
           "librosa": lambda r, o: o.get("librosa"), "centsible": lambda r, o: cent.get(r["file"])}
    table = []
    for name, f in est.items():
        vals = [(f(r, o), r) for r, o in res]
        vals = [(v, r) for v, r in vals if v is not None and np.isfinite(v)]
        if not vals:
            continue
        t1 = lambda v, r: r["bpm_list"][0]
        table.append([name, len(vals),
                      f"{100 * np.mean([within(v, t1(v, r), 0.01) for v, r in vals]):.1f} %",
                      f"{100 * np.mean([any(within(v, t, 0.01) for t in r['bpm_list']) for v, r in vals]):.1f} %",
                      f"{100 * np.mean([within(v, t1(v, r), 0.04) for v, r in vals]):.1f} %",
                      f"{100 * np.mean([acc2(v, t1(v, r)) for v, r in vals]):.1f} %"])

    ok = [(r, o) for r, o in res if "bpm" in o]
    multi = [(r, o) for r, o in ok if len(r["bpm_list"]) >= 2]
    single = [(r, o) for r, o in ok if len(r["bpm_list"]) == 1]
    lab_inc = [(r, o) for r, o in ok if INCONSISTENT.search(r["notes"])]
    lab_st = [(r, o) for r, o in ok if not INCONSISTENT.search(r["notes"])]
    flag = lambda o: any(s["steadiness"] != "steady" for s in o["sections"])
    sw = lambda o: len(o["sections"]) >= 2
    steady_rows = [
        ["tracker notes say inconsistent / live", len(lab_inc), sum(flag(o) for _, o in lab_inc)],
        ["tracker notes say nothing about it", len(lab_st), sum(flag(o) for _, o in lab_st)],
        ["tracker lists several BPMs", len(multi), sum(sw(o) or flag(o) for _, o in multi)],
        ["tracker lists one BPM", len(single), sum(sw(o) for _, o in single)],
    ]
    misses = [[r["file"][:55], r["bpms"], o.get("display"), o.get("sig"),
               f"{o.get('bpm', 0) / r['bpm_list'][0]:.3f}" if o.get("bpm") else "", (r["notes"] or "")[:60]]
              for r, o in res if "bpm" in o and not any(within(o["bpm"], t, 0.01) for t in r["bpm_list"])]
    errors = [[r["file"][:60], o["error"][:100]] for r, o in res if "error" in o]
    secs = [o["seconds"] for r, o in res if "seconds" in o]

    out = [f"# Tempo vs tracker BPM ({time.strftime('%Y-%m-%d')})", "",
           f"{len(res)} catalog songs with a tracker BPM ({catalog_dir()}). Beat This! on {engine.device()}; median "
           f"{np.median(secs):.1f} s per song for BPM Fixer's cleaning + the service's additions (tracker output cached).", "",
           md_table(["estimator", "songs", "within 1 % (main BPM)", "within 1 % (any listed)", "Acc1 (4 %)", "Acc2 (4 %, x2 x3)"],
                    table), "",
           "## Sections and steadiness", "",
           md_table(["songs", "count", "flagged by us (switch or inconsistent)"], steady_rows), "",
           "## Songs whose BPM matches none of the tracker's listed BPMs (service)", "",
           md_table(["file", "tracker", "ours", "sig", "ours / tracker", "tracker notes"], misses) if misses else "none", ""]
    if errors:
        out += ["## Errors", "", md_table(["file", "error"], errors), ""]
    (RESULTS / "tempo_summary.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out[:14]))
    print(f"\nwrote {RESULTS / 'tempo_summary.md'}")


if __name__ == "__main__":
    sys.exit(main())
