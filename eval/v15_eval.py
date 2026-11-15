#!/usr/bin/env python3
"""The 1.5 issue list (eval/v15_labels.json) against the engine's results.

Run the CLI on the songs first (one JSON object per line):
  songspec-cli C:/dev/detunetest/testdata/v15/*.m4a > v15.jsonl
then:  .venv/Scripts/python eval/v15_eval.py v15.jsonl [MORE.jsonl ...]
Per song: BPM (every section), time signature, keys (every section), detune, against the tracker; then totals.
"""
import json
import re
import sys
from pathlib import Path

LABELS = json.loads((Path(__file__).parent / "v15_labels.json").read_text(encoding="utf-8"))
NUM = re.compile(r"\d+(?:\.\d+)?")


def plain_key(k):
    return k.replace("*", "").strip().lower()


def bpm_values(t):
    """Our BPM per section, as the display shows them ('130 / 88', '~126 - 137' = one live section)."""
    d = (t.get("display") or {}).get("bpm") or ""
    if " / " in d:
        return [float(NUM.findall(p)[0]) for p in d.split(" / ") if NUM.findall(p)]
    return [t["bpm"]] if t.get("bpm") else []


def near(a, b, tol):
    return abs(a - b) / b <= tol


def key_values(k):
    if k.get("sections"):
        out = []
        for s in k["sections"]:
            d = plain_key(s["display"])
            if not out or out[-1] != d:
                out.append(d)
        return out
    return [plain_key(k["display"]["key"])]


def load(paths):
    res = {}
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                a = json.loads(line)
                res[Path(a["file"]).stem] = a
    return res


def main(paths):
    res = load(paths)
    tot = {"bpm_main": 0, "bpm_all": 0, "bpm_sections": 0, "sig": 0, "key_main": 0, "key_sections": 0, "n": 0}
    rows = []
    for name, lab in LABELS.items():
        if name.startswith("_") or name not in res:
            continue
        a = res[name]
        t, k, d = a.get("tempo") or {}, a.get("key") or {}, a.get("detune") or {}
        ours_b = bpm_values(t)
        want_b = lab["bpm"]
        # main: our first BPM against the tracker's first; all: every tracker value matched by one of ours (1 %)
        b_main = bool(ours_b) and near(ours_b[0], want_b[0], 0.01)
        b_all = all(any(near(o, w, 0.015) for o in ours_b) for w in set(want_b))
        b_sec = len(ours_b) > 1 if len(set(round(w) for w in want_b)) > 1 else len(ours_b) == 1
        sigs = [s["sig"] for s in t.get("signature_sections", [])] or [t.get("signature")]
        s_ok = sigs[0] == lab["sig"][0] and (len(lab["sig"]) == 1) == (len(set(sigs)) == 1)
        ours_k = key_values(k)
        want_k = [plain_key(x) for x in lab["keys"]]
        k_main = ours_k[0] == want_k[0]
        k_sec = (len(set(want_k)) > 1) == (len(ours_k) > 1) and set(want_k) <= set(ours_k) | {ours_k[0]}
        tot["n"] += 1
        for key, v in (("bpm_main", b_main), ("bpm_all", b_all), ("bpm_sections", b_sec), ("sig", s_ok), ("key_main", k_main), ("key_sections", k_sec)):
            tot[key] += bool(v)
        det = d.get("cents")
        rows.append((name, " / ".join(f"{x:g}" for x in ours_b), " / ".join(f"{x:g}" for x in want_b), "ok" if b_all else "--",
                     " / ".join(sigs), " / ".join(lab["sig"]), "ok" if s_ok else "--",
                     " / ".join(ours_k), " / ".join(want_k), "ok" if k_main and k_sec else ("main" if k_main else "--"),
                     f"{det:+.1f}" if det is not None else "", f"{lab['detune_cents']:+d}" if "detune_cents" in lab else ""))
    w = [22, 18, 26, 3, 10, 10, 3, 34, 40, 4, 7, 5]
    head = ("song", "BPM ours", "BPM tracker", "", "sig ours", "tracker", "", "key ours", "key tracker", "", "detune", "trk")
    print("  ".join(h.ljust(x) for h, x in zip(head, w)))
    for r in rows:
        print("  ".join(str(c)[:x].ljust(x) for c, x in zip(r, w)))
    n = tot["n"]
    print(f"\n{n} songs: BPM main {tot['bpm_main']}, every tracker BPM found {tot['bpm_all']}, BPM section count {tot['bpm_sections']}, "
          f"time signature {tot['sig']}, key (first) {tot['key_main']}, key switches {tot['key_sections']}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
