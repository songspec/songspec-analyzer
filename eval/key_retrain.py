#!/usr/bin/env python3
"""Retrain the key fusion with users' key corrections ("the key is wrong, it's actually ___") added to the tracker labels.

Where corrections come from: the service's SQLite database(s). Every analysis saves what the key fusion saw (key_evidence: the
estimators' 24 probabilities, the tiebreak and chord features), and POST /v1/analyses/{id}/feedback {field: 'key'} stores the
correction. A tester's corrections travel as their data/analyzer.sqlite3 file; pass it with --db. Only numbers are stored, no audio.

Gate: cross-validation by group (eval/key_fusion.py's 5 artist groups; corrected songs are spread over them by fingerprint, so a
song is never trained on while it is scored), run twice on every held-out song with the INSTALLED configuration (estimator set,
features, L2): trained on tracker songs only vs on tracker songs + corrections. The configuration is held fixed because re-choosing
it is noisy: in a smoke test, one added song re-chose configurations and moved the tracker songs' score 0.601 -> 0.621 by chance.
The new model is installed only with --install and only if there are at least MIN_CORRECTIONS corrected songs, it scores better on
the corrected songs, no worse on the tracker songs (within TRACKER_SLACK), and still beats the best single estimator. One correction per song counts (the latest), and a correction only teaches the 24 major/minor
keys (its mode is kept with the correction and shown to users, but modes are not trained: too few labelled examples).
Run:  .venv/Scripts/python eval/key_retrain.py --db data/analyzer.sqlite3 [--db qo/analyzer.sqlite3] [--install]
"""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

from common import RESULTS, ROOT, md_table
from key_eval import mirex
from key_fusion import MODEL, SETS, fit, load, predict

from songspec_analyzer.display.key import is_minor, parse_key
from songspec_analyzer.key import KEY_NAMES, key_index
from songspec_analyzer.key.fusion import FEATURE_KINDS, feature_matrix

MIN_CORRECTIONS = 20
TRACKER_SLACK = 0.005
DEFAULT_DBS = [ROOT / "data" / "analyzer.sqlite3", ROOT / "data" / "dev" / "analyzer.sqlite3"]


def corrections(db_path):
    """Latest key correction per song in one database, joined with the key evidence of that song's analysis."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "key_evidence" not in have:
        return []
    rows = db.execute("SELECT f.*, a.cached_from, a.file_name FROM feedback f LEFT JOIN analyses a ON a.id = f.analysis_id "
                      "WHERE f.field = 'key' ORDER BY f.id").fetchall()
    latest = {}
    for r in rows:
        latest[r["fingerprint"] or r["file_sha256"] or r["cached_from"] or r["analysis_id"]] = r
    out = []
    for song, r in latest.items():
        ev = None
        for aid in (r["analysis_id"], r["cached_from"]):
            ev = ev or (aid and db.execute("SELECT * FROM key_evidence WHERE analysis_id = ?", (aid,)).fetchone())
        if not ev and r["fingerprint"]:
            ev = db.execute("SELECT * FROM key_evidence WHERE fingerprint = ? ORDER BY created_at DESC LIMIT 1",
                            (r["fingerprint"],)).fetchone()
        if not ev:
            continue
        try:
            tonic, mode = parse_key(r["value"])
        except ValueError:
            continue
        out.append({"song": song, "name": r["file_name"] or r["analysis_id"], "value": r["value"],
                    "y": key_index(tonic, is_minor(mode)), "evidence": json.loads(ev["data"]), "engine": ev["engine_version"]})
    return out


def as_row(c, names):
    d = c["evidence"]
    est = {n: np.asarray(v, float) for n, v in d["est"].items()}
    if not set(names) <= set(est):
        return None                                      # e.g. analysed without S-KEY installed: not comparable
    tie, chf = np.asarray(d["tie"], float), np.asarray(d["chf"], float)
    g = int(hashlib.sha1(c["song"].encode()).hexdigest(), 16) % 5
    return {"r": {"song": c["name"], "artist": "(correction)"}, "g": g, "est": est, "y": c["y"], "correction": True,
            "detected": d.get("detected"), "feats": {k: feature_matrix(k, tie, chf) for k in FEATURE_KINDS}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="append", type=Path, help="service database(s) holding corrections (repeatable)")
    ap.add_argument("--install", action="store_true", help="write the retrained model, only if it passes the gate")
    a = ap.parse_args()
    dbs = a.db or [p for p in DEFAULT_DBS if p.exists()]
    tracker = load()
    names = sorted(tracker[0]["est"])
    corr, skipped = [], 0
    for p in dbs:
        for c in corrections(p):
            row = as_row(c, names)
            if row is None:
                skipped += 1
            else:
                corr.append(row)
    print(f"{len(tracker)} tracker songs, {len(corr)} corrected songs from {[str(p) for p in dbs]}"
          + (f" ({skipped} skipped: evidence lacks an estimator)" if skipped else ""), flush=True)
    if not corr:
        print("no usable corrections yet: nothing to retrain")
        return 0
    installed = json.loads(MODEL.read_text(encoding="utf-8"))
    set_name = next(k for k, v in SETS.items() if v == installed["names"])
    cfg = (set_name, installed.get("feature_kind") or "none", installed["l2"])
    print(f"installed configuration: {cfg}", flush=True)
    data = tracker + corr
    groups = sorted({d["g"] for d in data})
    pred_old, pred_new = {}, {}
    for g in groups:
        tr_old = [d for d in tracker if d["g"] != g]
        tr_new = [d for d in data if d["g"] != g]
        m_old, m_new = fit(cfg, tr_old), fit(cfg, tr_new)
        for i, d in enumerate(data):
            if d["g"] == g:
                pred_old[i], pred_new[i] = predict(m_old, d), predict(m_new, d)
        print(f"  group {g} done", flush=True)

    def score(pred, sel):
        idx = [i for i, d in enumerate(data) if sel(d)]
        return np.mean([mirex(pred[i], data[i]["y"])[0] for i in idx]) if idx else float("nan"), len(idx)

    singles = {n: {i: int(np.argmax(d["est"][n])) for i, d in enumerate(data)} for n in names}
    best_single = max(singles, key=lambda n: score(singles[n], lambda d: True)[0])
    subsets = [("all held-out songs", lambda d: True), ("tracker songs", lambda d: not d.get("correction")),
               ("corrected songs", lambda d: d.get("correction"))]
    table = []
    for label, pred in (("current recipe (tracker only)", pred_old), ("with corrections", pred_new),
                        (f"best single: {best_single}", singles[best_single])):
        table.append([label] + [f"{score(pred, sel)[0]:.3f} (n={score(pred, sel)[1]})" for _, sel in subsets])
    tr, co = (lambda d: not d.get("correction")), (lambda d: d.get("correction"))
    s_new, s_single = score(pred_new, lambda d: True)[0], score(singles[best_single], lambda d: True)[0]
    checks = {f"at least {MIN_CORRECTIONS} corrected songs": len(corr) >= MIN_CORRECTIONS,
              "better on the corrected songs": score(pred_new, co)[0] > score(pred_old, co)[0],
              f"tracker songs no worse (within {TRACKER_SLACK})": score(pred_new, tr)[0] >= score(pred_old, tr)[0] - TRACKER_SLACK,
              "beats the best single estimator": s_new > s_single}
    passed = all(checks.values())
    fixed = [[d["r"]["song"][:40], KEY_NAMES[d["detected"]] if d.get("detected") is not None else "?", KEY_NAMES[d["y"]],
              KEY_NAMES[pred_new[i]]] for i, d in enumerate(data) if d.get("correction")]
    out = [f"# Key retraining with user corrections ({time.strftime('%Y-%m-%d')})", "",
           f"{len(tracker)} tracker songs + {len(corr)} corrected songs. Cross-validation by group with the installed configuration; MIREX weighted score on held-out songs.", "",
           md_table(["model", *[s for s, _ in subsets]], table), "",
           f"Installed configuration, held fixed: {cfg}.", "",
           "Gate: " + "; ".join(f"{k}: {'yes' if v else 'NO'}" for k, v in checks.items()) +
           f". **{'passed' if passed else 'NOT passed'}**.", "",
           "## Corrected songs", "", md_table(["song", "detected", "corrected to", "retrained model (held out)"], fixed), ""]
    (RESULTS / "key_retrain.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    if a.install:
        if not passed:
            print("not installed: the gate did not pass")
            return 1
        m = fit(cfg, data)
        d = m.to_dict()
        d.update(trained=time.strftime("%Y-%m-%d"), songs=len(tracker), corrections=len(corr),
                 nested_cv_mirex=round(float(s_new), 4), best_single=best_single, best_single_mirex=round(float(s_single), 4))
        MODEL.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
        print(f"installed {MODEL} (bump ENGINE_VERSION so cached results are recomputed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
