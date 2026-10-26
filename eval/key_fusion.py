#!/usr/bin/env python3
"""Train and evaluate the key fusion (service/songspec_analyzer/key/fusion.py) on the cached key features (eval/key_eval.py).

Protocol (PLAN.md section 5, "hold out a test split you never tune on"):
  held-out  artist fold 0 of 5 (key_eval.held_out): touched ONCE, at the end
  dev       everything else: 4-fold cross-validation by artist (a different hash) to choose the estimator set, the use of the
            tiebreaker features and the L2 strength
Then the chosen configuration is trained on all of dev and scored on held-out, next to every single estimator on the same songs.
Gate: the fusion must beat the best single estimator on held-out; otherwise the report says so and the model is not installed.
Run:  .venv/Scripts/python eval/key_fusion.py [--install]
"""
import argparse
import hashlib
import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np

from common import RESULTS, ROOT, cache_path, load_labels, md_table
from key_eval import held_out, label_index, mirex

from songspec_analyzer.key import KEY_NAMES, estimators as E
from songspec_analyzer.key.fusion import KeyFusion, candidate_features
from songspec_analyzer.key.profiles import PROFILES

MODEL = ROOT / "service" / "songspec_analyzer" / "key" / "fusion_model.json"


def dev_fold(artist, k=4):
    return int(hashlib.sha1(("dev:" + artist.strip().lower()).encode()).hexdigest(), 16) % k


def load():
    out = []
    for r in load_labels():
        p = cache_path("key_feat_v1", r["file"])
        if not (r["keys"].strip() and p.exists()):
            continue
        f = json.loads(p.read_text(encoding="utf-8"))
        est = {"centsible": np.asarray(f["centsible"])}
        est.update(E.profile_probs(np.asarray(f["chroma_beat"])))
        feats = candidate_features(f["chroma_beat"], f["chroma_bass"], f.get("chroma_beat_seq"))
        out.append({"r": r, "est": est, "feats": feats, "y": label_index(r["keys"].split(";")[0])})
    return out


def score(model, rows):
    return np.mean([mirex(int(np.argmax(model.predict_proba(d["est"], d["feats"]))), d["y"])[0] for d in rows])


def single(name, rows):
    return np.mean([mirex(int(np.argmax(d["est"][name])), d["y"])[0] for d in rows])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="write the model the service loads (only if it passes the gate)")
    a = ap.parse_args()
    data = load()
    held = [d for d in data if held_out(d["r"]["artist"])]
    dev = [d for d in data if not held_out(d["r"]["artist"])]
    print(f"{len(data)} songs: dev {len(dev)}, held-out {len(held)}")

    sets = {"centsible": ["centsible"],
            "centsible+4": ["centsible", "temperley", "edma", "albrecht_shanahan", "bgate"],
            "all": ["centsible"] + list(PROFILES)}
    grid = list(itertools.product(sets, (True, False), (0.03, 0.3, 3.0)))
    cv = []
    for set_name, use_f, l2 in grid:
        folds = []
        for k in range(4):
            tr = [d for d in dev if dev_fold(d["r"]["artist"]) != k]
            te = [d for d in dev if dev_fold(d["r"]["artist"]) == k]
            m = KeyFusion(sets[set_name], l2=l2, use_features=use_f).fit([(d["est"], d["feats"], d["y"]) for d in tr])
            folds.append(score(m, te) * len(te))
        cv.append((sum(folds) / len(dev), set_name, use_f, l2))
        print(f"  CV {cv[-1][0]:.3f}  estimators={set_name:12s} features={use_f!s:5s} l2={l2}", flush=True)
    best = max(cv)
    _, set_name, use_f, l2 = best
    model = KeyFusion(sets[set_name], l2=l2, use_features=use_f).fit([(d["est"], d["feats"], d["y"]) for d in dev])

    singles = {n: (single(n, dev), single(n, held)) for n in ["centsible"] + list(PROFILES)}
    best_single = max(singles, key=lambda n: singles[n][0])          # chosen on dev, like the fusion
    fused_held = score(model, held)
    exact = lambda rows, f: 100 * np.mean([int(np.argmax(f(d))) == d["y"] for d in rows])
    rows = [["fusion (" + set_name + (", features" if use_f else "") + f", l2 {l2})", f"{best[0]:.3f}", f"{fused_held:.3f}",
             f"{exact(held, lambda d: model.predict_proba(d['est'], d['feats'])):.1f} %"]]
    for n, (sd, sh) in sorted(singles.items(), key=lambda kv: -kv[1][0]):
        rows.append([n, f"{sd:.3f}", f"{sh:.3f}", f"{exact(held, lambda d: d['est'][n]):.1f} %"])
    passed = fused_held > singles[best_single][1]
    out = [f"# Key fusion ({time.strftime('%Y-%m-%d')})", "",
           f"Dev {len(dev)} songs (4-fold CV by artist), held-out {len(held)} songs (artist fold 0 of 5, used once).", "",
           md_table(["model", "MIREX dev (CV for the fusion, in-sample for singles)", "MIREX held-out", "exact held-out"], rows), "",
           f"Best single estimator chosen on dev: **{best_single}** (held-out {singles[best_single][1]:.3f}). "
           f"Gate (fusion beats it on held-out): **{'passed' if passed else 'NOT passed'}** ({fused_held:.3f}).", ""]
    (RESULTS / "key_fusion.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    if a.install:
        if not passed:
            print("not installed: the fusion does not beat the best single estimator on held-out")
            return 1
        d = model.to_dict()
        d.update(trained=time.strftime("%Y-%m-%d"), songs=len(dev), cv_mirex=round(best[0], 4), heldout_mirex=round(fused_held, 4))
        MODEL.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
        print(f"installed {MODEL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
