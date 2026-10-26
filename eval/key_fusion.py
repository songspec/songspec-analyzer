#!/usr/bin/env python3
"""Train and evaluate the key fusion (service/songspec_analyzer/key/fusion.py) on the cached key features (eval/key_eval.py).

Protocol: NESTED cross-validation by artist group (key_eval.artist_group: 5 song-balanced groups, 23 artists in the catalog).
  outer   for each group g: choose the configuration (estimator set, tiebreaker features on/off, L2) by cross-validation over the
          other four groups only, train it on those four, predict g. Every song is predicted by a model that never saw its artist
          and was never tuned on it ("hold out a test split you never tune on", without one lucky / unlucky held-out group).
  ship    the configuration chosen by 5-fold CV over all groups, trained on all songs (service/.../key/fusion_model.json).
Gate (PLAN.md): the fusion's outer score must beat the best single estimator (whose choice even gets to look at all songs);
otherwise no model is installed and the service keeps Centsible's key.
Run:  .venv/Scripts/python eval/key_fusion.py [--install]
"""
import argparse
import itertools
import json
import sys
import time

import numpy as np

from common import RESULTS, ROOT, cache_path, load_labels, md_table
from key_eval import artist_group, label_index, mirex

from songspec_analyzer.key import estimators as E
from songspec_analyzer.key.fusion import KeyFusion, candidate_features
from songspec_analyzer.key.profiles import PROFILES

MODEL = ROOT / "service" / "songspec_analyzer" / "key" / "fusion_model.json"
SETS = {"centsible": ["centsible"],
        "centsible+AS": ["centsible", "albrecht_shanahan@scale", "albrecht_shanahan@beat"],
        "centsible+6": ["centsible", "albrecht_shanahan@scale", "albrecht_shanahan@beat", "krumhansl@beat", "krumhansl@naive",
                        "edma@naive"],
        "all": ["centsible"] + [f"{p}@{c}" for c in ("beat", "scale", "naive") for p in PROFILES]}
GRID = list(itertools.product(SETS, (True, False), (0.03, 0.3, 3.0)))


def load():
    out = []
    for r in load_labels():
        p = cache_path("key_feat_v1", r["file"])
        if not (r["keys"].strip() and p.exists()):
            continue
        f = json.loads(p.read_text(encoding="utf-8"))
        chroma_log = E.global_chroma(f["chroma_beat_seq"])
        est = E.estimator_pool(f["centsible"], chroma_log, f["chroma_scale"], f["chroma_naive"])
        feats = candidate_features(f["chroma_scale"], f["chroma_bass"], f.get("chroma_beat_seq"))
        out.append({"r": r, "g": artist_group(r["artist"]), "est": est, "feats": feats,
                    "y": label_index(r["keys"].split(";")[0])})
    return out


def fit(cfg, rows):
    s, use_f, l2 = cfg
    return KeyFusion(SETS[s], l2=l2, use_features=use_f).fit([(d["est"], d["feats"], d["y"]) for d in rows])


def predict(model, d):
    return int(np.argmax(model.predict_proba(d["est"], d["feats"])))


def cv_score(cfg, rows):
    """Leave-one-artist-group-out over the groups present in rows (weighted per song)."""
    total, n = 0.0, 0
    for g in sorted({d["g"] for d in rows}):
        tr = [d for d in rows if d["g"] != g]
        te = [d for d in rows if d["g"] == g]
        m = fit(cfg, tr)
        total += sum(mirex(predict(m, d), d["y"])[0] for d in te)
        n += len(te)
    return total / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="write the model the service loads (only if it passes the gate)")
    a = ap.parse_args()
    data = load()
    groups = sorted({d["g"] for d in data})
    print(f"{len(data)} songs in artist groups {[sum(d['g'] == g for d in data) for g in groups]}", flush=True)

    outer_pred, chosen = {}, []
    for g in groups:
        inner = [d for d in data if d["g"] != g]
        best = max(GRID, key=lambda cfg: cv_score(cfg, inner))
        chosen.append(best)
        m = fit(best, inner)
        for i, d in enumerate(data):
            if d["g"] == g:
                outer_pred[i] = predict(m, d)
        print(f"  group {g}: chose {best}", flush=True)
    fused = [mirex(outer_pred[i], d["y"]) for i, d in enumerate(data)]
    singles = {n: [mirex(int(np.argmax(d["est"][n])), d["y"]) for d in data] for n in data[0]["est"]}
    best_single = max(singles, key=lambda n: np.mean([s for s, _ in singles[n]]))

    def row(name, res):
        s = np.array([x for x, _ in res])
        kinds = [k for _, k in res]
        per_group = [np.mean([res[i][0] for i, d in enumerate(data) if d["g"] == g]) for g in groups]
        return [name, f"{s.mean():.3f}", f"{100 * np.mean(s == 1):.1f} %", " / ".join(f"{v:.2f}" for v in per_group),
                kinds.count("fifth"), kinds.count("relative"), kinds.count("parallel"), kinds.count("other")]

    table = [row("fusion (nested CV)", fused)] + [row(n, singles[n]) for n in sorted(singles, key=lambda n: -np.mean([s for s, _ in singles[n]]))]
    f_mean, b_mean = np.mean([s for s, _ in fused]), np.mean([s for s, _ in singles[best_single]])
    # paired comparison with the best single estimator: how often each one is better
    better = sum(f > b for (f, _), (b, _) in zip(fused, singles[best_single]))
    worse = sum(f < b for (f, _), (b, _) in zip(fused, singles[best_single]))
    passed = f_mean > b_mean
    ship = max(GRID, key=lambda cfg: cv_score(cfg, data))
    out = [f"# Key fusion ({time.strftime('%Y-%m-%d')})", "",
           f"{len(data)} catalog songs with a tracker key, 5 artist groups of {[sum(d['g'] == g for d in data) for g in groups]} songs. "
           "Fusion = nested cross-validation (each group predicted by a model chosen and trained on the other four).", "",
           md_table(["estimator", "MIREX weighted", "exact", "MIREX per artist group", "fifth", "relative", "parallel", "other"], table), "",
           f"Best single estimator: **{best_single}** ({b_mean:.3f}). Fusion {f_mean:.3f}: better on {better} songs, worse on {worse}. "
           f"Gate: **{'passed' if passed else 'NOT passed'}**.",
           f"Configurations chosen per outer group: {chosen}. Shipped configuration (5-fold CV on all songs): {ship}.", ""]
    (RESULTS / "key_fusion.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    if a.install:
        if not passed:
            print("not installed: the fusion does not beat the best single estimator")
            return 1
        m = fit(ship, data)
        d = m.to_dict()
        d.update(trained=time.strftime("%Y-%m-%d"), songs=len(data), nested_cv_mirex=round(float(f_mean), 4),
                 best_single=best_single, best_single_mirex=round(float(b_mean), 4))
        MODEL.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
        print(f"installed {MODEL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
