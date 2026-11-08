"""Tempo and beats: BPM Fixer's engine (verbatim in _bpmfix_port.py) wrapped for the service.

analyze() runs exactly the analysis half of BPM Fixer's process() (keep_edges=True, its default):
  Beat This! -> time signature (Auto) -> prepare_anchors -> level guard (enforce_level) -> refine_beats -> fill_weak_runs
and then adds what the website needs and BPM Fixer did not report: a precise global tempo (a line fit through the beats instead
of the median interval), signature confidence, tempo sections, the live / grid verdict and downbeats on the cleaned grid.
"""
import os
import threading
from pathlib import Path

import numpy as np

from ..pipeline import StageSkipped
from . import _bpmfix_port as bf

MODEL_NAME = "beat_this-final0.ckpt"
_MODEL = {}
_MODEL_LOCK = threading.Lock()


class TempoError(StageSkipped):
    """No usable beat (speech, ambient, silence). The message is safe to show."""


def device():
    """cuda when available; SONGSPEC_DEVICE=cpu forces the CPU (bit-for-bit comparisons with BPM Fixer, which runs on the CPU)."""
    forced = os.environ.get("SONGSPEC_DEVICE")
    if forced:
        return forced
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def checkpoint(models_dir) -> Path:
    p = Path(models_dir) / MODEL_NAME
    if not p.is_file() or p.stat().st_size < 1_000_000:
        raise RuntimeError(f"Beat This! model missing: run tools/fetch_models.py (expected {p})")
    return p


def detect_beats(y, sr, models_dir):
    """Beat This! (CPJKU, ISMIR 2024), dbn=False, as in BPM Fixer. Returns (beats, downbeats or None) in seconds."""
    from beat_this.inference import Audio2Beats
    with _MODEL_LOCK:                                   # one model per process; calls are serialised (GPU memory, thread safety)
        if "m" not in _MODEL:
            _MODEL["m"] = Audio2Beats(checkpoint_path=str(checkpoint(models_dir)), device=device(), dbn=False)
        beats, downbeats = _MODEL["m"](np.ascontiguousarray(y, dtype=np.float32), sr)
    beats = np.sort(np.asarray(beats, dtype=float))
    downbeats = np.sort(np.asarray(downbeats, dtype=float))
    if len(downbeats) > len(beats):
        beats, downbeats = downbeats, beats
    if len(beats) < 8:
        raise TempoError("No steady beat found in this file.")
    return beats, (downbeats if len(downbeats) >= 4 else None)


def signature_confidence(beats, down, name):
    """Share of bars whose tracker beat count matches the chosen signature (the vote infer_signature counts)."""
    if down is None or len(down) < 6 or len(beats) < 12:
        return None
    tol = 0.3 * float(np.median(np.diff(beats)))
    counts = np.diff(np.searchsorted(beats, down - tol))
    n = {"2/4": 2, "3/4": 3, "4/4": 4, "5/4": 5, "6/8": 6, "7/8": 7, "9/8": 9, "12/8": 12, "2/2": 2, "7/4": 7, "5/8": 5}.get(name)
    if n is None:
        return None
    ok = (counts == n) | ((counts == 8) & (n == 4))
    return round(float(np.mean(ok)), 3)


# 6/8 (eval/meter_feat.py, eval/REPORT.md 1.3). BPM Fixer's Auto signature needs 60 % of bars to agree, and on 6/8 songs the beat
# tracker's bars are messy (30 - 50 % agree), so it fell back to 4/4; with a clear majority of 3 it said 3/4. On the catalog the
# tracker's most common count is 3 or 6 beats per bar for 7 of the 8 songs the tracker calls 6/8 and for 1 of 149 others, so that
# is the rule: 3 per bar = eighth notes in dotted-quarter groups (the bar the tracker marks is half a 6/8 bar), 6 per bar = a
# whole 6/8 bar of eighths. Either way the eighth-note tempo is what the tracker writes ("176*").
COMPOUND_MIN_SHARE = 0.3
# A real 3/4 (waltz) also has 3 beats per bar, but bar after bar (the synthetic waltz test: 100 %); the catalog's 6/8 songs are
# messy (30 - 71 %). 3 per bar in at least this share of bars stays 3/4. Not measured on real 3/4 songs: the catalog has none.
WALTZ_MIN_SHARE = 0.85


def compound_meter(beats, down):
    """3 or 6 when the tracker's bars say 6/8 (see above), else None."""
    if down is None or len(down) < 6 or len(beats) < 12:
        return None
    tol = 0.3 * float(np.median(np.diff(beats)))
    counts = np.diff(np.searchsorted(beats, down - tol))
    vals, c = np.unique(counts, return_counts=True)
    m = int(vals[np.argmax(c)])
    share = c.max() / len(counts)
    if m == 3 and share >= WALTZ_MIN_SHARE:
        return None
    return m if m in (3, 6) and share >= COMPOUND_MIN_SHARE else None


def line_period(t):
    """Beat period from a robust straight-line fit of beat time against beat index (trims outliers twice).
    A quantised track gives its exact grid tempo, where the median interval is only good to ~0.3 BPM."""
    t = np.asarray(t, float)
    if len(t) < 4:
        return float(np.median(np.diff(t))) if len(t) > 1 else None
    i = np.arange(len(t), dtype=float)
    keep = np.ones(len(t), bool)
    for _ in range(3):
        a, b = np.polyfit(i[keep], t[keep], 1)
        r = t - (a * i + b)
        mad = np.median(np.abs(r[keep] - np.median(r[keep]))) + 1e-6
        new = np.abs(r) <= max(4.0 * 1.4826 * mad, 0.004)
        if new.sum() < max(4, 0.5 * len(t)) or (new == keep).all():
            break
        keep = new
    return float(a)


def segment_intervals(iv, min_beats=16, min_shift=0.02):
    """Binary segmentation of the per-beat log tempo (piecewise constant): a split must leave >= min_beats on each side and move
    the level by >= min_shift; neighbours closer than that are merged. Returns interval-index ranges [(a, b)]."""
    iv = np.asarray(iv, float)
    if len(iv) < 2 * min_beats:
        return [(0, len(iv))]
    lv = np.log(bf._rolling_median(iv, 5))

    def split(a, b):
        seg = lv[a:b]
        n = len(seg)
        if n < 2 * min_beats:
            return [(a, b)]
        cs, cs2 = np.cumsum(seg), np.cumsum(seg * seg)
        tot, tot2 = cs[-1], cs2[-1]
        best, bi = 0.0, None
        for k in range(min_beats, n - min_beats):
            l_n, r_n = k, n - k
            l_s, r_s = cs[k - 1], tot - cs[k - 1]
            gain = l_s * l_s / l_n + r_s * r_s / r_n - tot * tot / n          # SSE reduction of splitting at k
            if gain > best:
                best, bi = gain, k
        if bi is None:
            return [(a, b)]
        left, right = np.median(seg[:bi]), np.median(seg[bi:])
        if abs(left - right) < np.log1p(min_shift):
            return [(a, b)]
        return split(a, a + bi) + split(a + bi, b)

    segs = split(0, len(lv))
    merged = [list(segs[0])]
    for a, b in segs[1:]:
        if abs(np.median(lv[merged[-1][0]:merged[-1][1]]) - np.median(lv[a:b])) < np.log1p(min_shift):
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [tuple(m) for m in merged]


# BPM Fixer forces ONE metrical level on the whole song (its level guard re-tracks any run spaced 2/3, 3/2, 2x ... of the song's
# beat), which is right for evening out a tempo but hides a genuine beat switch (100 -> 140 BPM). A switch is therefore found on the
# RAW tracker beats first: intervals folded by octaves only (a half-time passage is not a tempo change), segmented, and a step is
# believed only if the BARS change by the same ratio (a 6/8 quarter / dotted-quarter mix-up changes the beat count per bar, not the
# bar length). Each confirmed section then goes through BPM Fixer's unchanged pipeline on its own.
SWITCH_MIN_BEATS = 32          # a switch must hold for 8 bars of 4/4
SWITCH_MIN_SHIFT = 0.06        # and move the tempo by at least 6 % (smaller moves are drift, handled as 'live')


def find_switches(raw, down, min_beats=SWITCH_MIN_BEATS, min_shift=SWITCH_MIN_SHIFT):
    """Times where the song switches tempo, confirmed by the bar length. [] for the usual one-tempo song."""
    iv = np.diff(raw)
    if len(iv) < 2 * min_beats or down is None or len(down) < 8:
        return []
    ref = float(np.median(iv))
    folded = iv * 2.0 ** np.round(np.log2(ref / iv))
    segs = segment_intervals(folded, min_beats, min_shift)
    if len(segs) < 2:
        return []
    cuts = []
    for (a0, a1), (b0, b1) in zip(segs[:-1], segs[1:]):
        r = np.median(folded[b0:b1]) / np.median(folded[a0:a1])          # > 1: the beats got longer (slower)
        # a switch is a JUMP: most of the change happens within 16 beats of the cut. A ramp (a band speeding up) splits into
        # segments too, but there the 16 beats either side of every cut are nearly the same tempo.
        k = 16
        near = np.median(folded[b0:min(b0 + k, b1)]) / np.median(folded[max(a0, b0 - k):b0])
        if abs(np.log(near)) < 0.7 * abs(np.log(r)):
            continue
        ta, tb = raw[a0], raw[b0]
        da = down[(down >= ta) & (down < tb)]
        db = down[(down >= tb) & (down < raw[min(b1, len(raw) - 1)])]
        if len(da) < 4 or len(db) < 4:
            continue
        rb = np.median(np.diff(db)) / np.median(np.diff(da))
        # bars follow the beats (same beats per bar) -> a real switch; allow the bar ratio to be off by an octave as well
        if min(abs(np.log(rb / r) - k * np.log(2)) for k in (-1, 0, 1)) < 0.05:
            cuts.append(float(tb))
    return cuts


def _pipeline(y, sr, raw, down, end, spec, bpm_lo, bpm_hi, log):
    """BPM Fixer's process() from prepare_anchors to fill_weak_runs (keep_edges=True), for the beats before `end` seconds."""
    prep = bf.prepare_anchors(raw, down, end, spec, None, bpm_lo, bpm_hi, log)
    fixes = []
    try:                                                   # level guard, exactly as in process()
        T_ref = bf.pick_reference_period(prep["anchors"], None)
        fixed, fixes = bf.enforce_level(prep["anchors"], y, sr, T_ref, log)
        if fixes:
            prep["anchors"] = fixed
    except Exception as e:                                 # process() logs and carries on; so do we
        log(f"level guard skipped: {type(e).__name__}: {e}")
    real, mask = bf.refine_beats(prep["anchors"], y, sr, log=log, return_mask=True)
    return bf.fill_weak_runs(real, mask), prep, fixes


# Steadiness of a section, in the tracker's three words: steady ("128"), moderately inconsistent ("~98"), inconsistent / played live
# ("~97 - 100"). Measured on the LOCAL tempo: a straight-line fit through every 32-beat window (hop 8 beats), which ignores how far
# single beats were pulled by syncopated hits; the spread is p90 - p10 of those local tempos in % of their median.
LOCAL_WINDOW, LOCAL_HOP = 32, 8
# Thresholds are deliberately conservative: on the user's catalog this drift barely predicts the tracker's "inconsistent" notes
# (eval/tempo_tune.py: cross-validated balanced accuracy 0.37 - 0.69, 18 positive songs, many of them only partly inconsistent),
# and a false "~122 - 127" on a quantised song is worse than a missed mild drift. Values above the 90th percentile of the songs
# the tracker does NOT call inconsistent (3.5 %) are flagged.
MODERATE_PCT = 3.0
LIVE_PCT = 6.0


def local_tempo(beats, spec, win=LOCAL_WINDOW, hop=LOCAL_HOP):
    """[(centre time, felt BPM)] from line fits over sliding windows of `win` beats."""
    out = []
    if len(beats) < win:
        p = line_period(beats)
        return [(float(np.mean(beats)), float(bf.period_to_bpm(p, spec, "felt")))] if p else []
    for i in range(0, len(beats) - win + 1, hop):
        w = beats[i:i + win]
        out.append((float(np.mean(w)), float(bf.period_to_bpm(line_period(w), spec, "felt"))))
    return out


CORE_BAND = 0.12            # local tempos within +-12 % of the dominant one are "the song's tempo"; level hops are >= 33 % (4:3)


def section_stats(sb, spec):
    """Tempo and steadiness of one section. Drift is measured on the CORE windows (near the dominant local tempo) only: windows
    where the tracker sat on another metrical level (3:2, 4:3 of the song's beat) are level hops, not a band speeding up, and
    must neither widen the range nor pull the BPM (on the catalog they turned steady songs into '~135 - 180')."""
    line_bpm = float(bf.period_to_bpm(line_period(sb), spec, "felt"))
    loc = np.array([b for _, b in local_tempo(sb, spec)])
    if len(loc) < 3:
        return {"start": round(float(sb[0]), 3), "end": round(float(sb[-1]), 3), "bpm": round(line_bpm, 2), "lo": None,
                "hi": None, "drift_pct": 0.0, "steadiness": "steady", "core_share": 1.0}
    mode = bf._log_mode(loc, np.ones(len(loc)))
    core = loc[np.abs(np.log(loc / mode)) <= np.log1p(CORE_BAND)]
    lo, med, hi = np.percentile(core, [10, 50, 90])
    drift = float(100.0 * (hi - lo) / med)
    steadiness = "live" if drift > LIVE_PCT else "moderate" if drift > MODERATE_PCT else "steady"
    # steady: the line fit is the precise grid tempo; unsteady: the typical local tempo (what the tracker writes down)
    whole_ok = abs(np.log(line_bpm / med)) <= np.log1p(0.01)
    bpm = line_bpm if steadiness == "steady" and whole_ok else float(med)
    return {"start": round(float(sb[0]), 3), "end": round(float(sb[-1]), 3), "bpm": round(bpm, 2),
            "lo": round(float(lo), 2), "hi": round(float(hi), 2), "drift_pct": round(drift, 2), "steadiness": steadiness,
            "core_share": round(float(len(core) / len(loc)), 3)}


def analyze(y, sr, models_dir, signature="Auto", bpm_lo=80.0, bpm_hi=160.0, log=lambda *a: None, tracked=None):
    """y: mono float32 at the file's own rate. Returns a dict (see the Tempo section of the contract) plus internals under '_'.
    tracked=(beats, downbeats) skips the neural tracker (evaluation caches its output)."""
    raw, down = tracked if tracked is not None else detect_beats(y, sr, models_dir)
    duration = len(y) / sr
    sig = signature or "Auto"
    sig_auto = sig == "Auto"
    compound = None
    if sig_auto:
        guess = bf.infer_signature(raw, down)
        sig = guess[0] if guess else "4/4"
        compound = compound_meter(raw, down)
        if compound == 3:
            sig = "3/4"            # cleaned as 3 eighths per (half) bar, reported as 6/8 below
        elif compound == 6:
            sig = "6/8"
    if sig not in bf.TIME_SIGNATURES:
        raise ValueError(f"unknown time signature {sig!r}")
    spec = bf.sig_spec(sig)
    switches = find_switches(raw, down)
    if not switches:                                       # the usual case: exactly BPM Fixer
        try:
            beats, prep, fixes = _pipeline(y, sr, raw, down, duration, spec, bpm_lo, bpm_hi, log)
        except RuntimeError as e:                          # BPM Fixer's own "could not find enough beats ..." messages
            raise TempoError(f"No steady beat found in this file ({e})") from e
    else:                                                  # one BPM Fixer run per tempo section, stitched
        edges = [0.0] + switches + [duration + 1.0]
        parts, fixes, prep = [], [], None
        for a, b in zip(edges[:-1], edges[1:]):
            # each section on its own slice of the audio (times shifted to the slice), so the level guard's onset envelope
            # costs the section's length, not the whole song's, for every section
            b_ = min(b, duration)
            ys = y[int(a * sr):int(b_ * sr)]
            r_ = raw[(raw >= a) & (raw < b)] - a
            d_ = down[(down >= a) & (down < b)] - a
            try:
                part, p_, f_ = _pipeline(ys, sr, r_, d_ if len(d_) >= 4 else None, b_ - a, spec, bpm_lo, bpm_hi, log)
            except RuntimeError:                           # too few beats for the cleaner: keep the tracker's, refined
                part = bf.refine_beats(r_, ys, sr, log=log) if len(r_) else r_
                p_, f_ = {"events": [], "kind": "beat", "bars_ok": False}, []
            part = np.asarray(part) + a
            f_ = [(t0 + a, t1 + a, rr) for t0, t1, rr in f_]
            parts.append(part[(part >= a) & (part < b)])
            fixes += f_
            prep = prep or p_
        beats = np.concatenate(parts)
        beats = beats[np.concatenate([[True], np.diff(beats) > 1e-3])]

    iv = np.diff(beats)
    if len(iv) < 6:
        raise TempoError("No steady beat found in this file.")
    felt_curve = bf.period_to_bpm(iv, spec, "felt")
    med_felt = bf.period_to_bpm(float(np.median(iv)), spec, "felt")
    # the song's own tempo at the level a person taps (70-180 felt), as BPM Fixer computes it
    own_period = float(np.median(iv))
    ref_mode = "felt" if spec["regular"] else "quarter"
    lo_t, hi_t = (70.0, 180.0) if spec["regular"] else (60.0, 180.0)
    while bf.period_to_bpm(own_period, spec, ref_mode) < lo_t:
        own_period /= 2
    while bf.period_to_bpm(own_period, spec, ref_mode) >= hi_t:
        own_period *= 2

    # sections = the confirmed beat switches (one section for the usual song); each with its own tempo and steadiness
    edges = [0.0] + switches + [duration + 1.0]
    sections = []
    for a, b in zip(edges[:-1], edges[1:]):
        sb = beats[(beats >= a) & (beats < b)]
        if len(sb) < 8:
            continue
        s = section_stats(sb, spec)
        # the raw tracker can hop to another level with its bars following (it passed find_switches) while BPM Fixer's cleaning
        # brings that part back to the song's tempo: then it is no switch, merge it with its neighbour
        if sections and abs(np.log(s["bpm"] / sections[-1]["bpm"])) < np.log(1.03):
            s = section_stats(beats[(beats >= sections[-1]["start"] - 1e-6) & (beats < b)], spec)
            sections[-1] = s
            continue
        sections.append(s)
    main = max(sections, key=lambda s: s["end"] - s["start"])
    bpm_raw = main["bpm"]
    irr = bf.irregularity(beats)
    live = any(s["steadiness"] == "live" for s in sections)

    # downbeats on the cleaned grid: the nearest anchor to each tracker downbeat (within a quarter beat)
    dbs = []
    if down is not None:
        idx = np.clip(np.searchsorted(beats, down), 1, len(beats) - 1)
        for d, j in zip(down, idx):
            k = j if abs(beats[j] - d) < abs(beats[j - 1] - d) else j - 1
            if abs(beats[k] - d) <= 0.25 * np.median(iv) and (not dbs or beats[k] > dbs[-1]):
                dbs.append(float(beats[k]))

    step = max(1, len(iv) // 1500)
    sig_conf = signature_confidence(raw, down, sig) if sig_auto else 1.0
    if compound:
        # the tracker's beats are eighth notes and the felt 6/8 beat (a dotted quarter) is three of them; BPM Fixer's cleaning
        # may have moved the grid to another octave (quarter notes, to land in 80 - 160), so the factor is measured
        f = 3.0 * 2.0 ** round(float(np.log2(float(np.median(np.diff(raw))) / float(np.median(iv)))))
        sig, spec = "6/8", bf.sig_spec("6/8")
        for s in sections:
            for k in ("bpm", "lo", "hi"):
                if s.get(k) is not None:
                    s[k] = round(s[k] / f, 2)
        felt_curve = felt_curve / f
        bpm_raw = bpm_raw / f
        med_felt /= f
    return {
        "bpm_raw": round(bpm_raw, 2),
        "signature": sig,
        "signature_auto": sig_auto,
        "signature_conf": sig_conf,
        "sections": sections,
        "irregularity_pct": round(irr, 2),
        "drift_pct": main["drift_pct"],
        "feel": "live" if live else "grid",
        "beats": [round(float(t), 3) for t in beats],
        "downbeats": [round(t, 3) for t in dbs],
        "curve": {"t": [round(float(t), 3) for t in beats[:-1][::step]], "bpm": [round(float(b), 2) for b in felt_curve[::step]]},
        "_": {"raw_beats": raw, "raw_downbeats": down, "spec": spec, "median_felt": float(med_felt),
              "own_felt": float(bf.period_to_bpm(own_period, spec, "felt")),
              "own_quarter": float(bf.period_to_bpm(own_period, spec, "quarter")),
              "events": prep["events"], "level_fixes": fixes, "kind": prep["kind"], "bars_ok": prep["bars_ok"]},
    }
