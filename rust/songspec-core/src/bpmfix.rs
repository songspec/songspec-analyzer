//! BPM Fixer's analysis half, ported line for line from service/songspec_analyzer/tempo/_bpmfix_port.py (itself a verbatim
//! copy of BPM Fixer's app/bpmfix.py): time signature, beat cleaning (the flywheel), metrical level, level guard (re-tracking
//! wrong-level runs from the audio), refining beats onto drum hits. Function names and comments follow the original.

use crate::dsp;
use crate::np::{self, argmax, diff, median, searchsorted};

pub const HOP: usize = 256;
pub const RISE_FRAC: f64 = 0.2;
pub const LEVEL_MULTS: [f64; 5] = [1.0 / 3.0, 0.5, 1.0, 2.0, 3.0];
pub const WRONG_LEVEL: [(f64, f64); 6] = [(0.28, 0.40), (0.42, 0.57), (0.57, 0.78), (1.30, 1.75), (1.75, 2.30), (2.60, 3.40)];

#[derive(Clone, Debug)]
pub struct Spec {
    pub name: &'static str,
    pub beats: usize,
    pub beat_q: f64,
    pub regular: bool,
    pub bar_q: f64,
}

pub const TIME_SIGNATURES: [(&str, usize, f64, bool); 11] = [
    ("4/4", 4, 1.0, true),
    ("3/4", 3, 1.0, true),
    ("2/4", 2, 1.0, true),
    ("2/2", 2, 2.0, true),
    ("6/8", 2, 1.5, true),
    ("9/8", 3, 1.5, true),
    ("12/8", 4, 1.5, true),
    ("5/4", 5, 1.0, false),
    ("7/4", 7, 1.0, false),
    ("5/8", 5, 0.5, false),
    ("7/8", 7, 0.5, false),
];

pub fn sig_spec(name: &str) -> Option<Spec> {
    TIME_SIGNATURES.iter().find(|t| t.0 == name).map(|&(name, n, q, reg)| Spec { name, beats: n, beat_q: q, regular: reg, bar_q: n as f64 * q })
}

pub fn anchor_q(s: &Spec) -> f64 {
    if !s.regular { s.bar_q } else { s.beat_q }
}

#[derive(Clone, Copy, PartialEq)]
pub enum BpmMode {
    Felt,
    Quarter,
}

pub fn period_to_bpm(period: f64, s: &Spec, mode: BpmMode) -> f64 {
    if mode == BpmMode::Quarter || !s.regular { 60.0 * anchor_q(s) / period } else { 60.0 / period }
}

/// Auto mode: count the beats the tracker puts between downbeats. Returns (name, count) or None.
pub fn infer_signature(beats: &[f64], down: Option<&[f64]>) -> Option<(&'static str, usize)> {
    let down = down?;
    if down.len() < 6 || beats.len() < 12 {
        return None;
    }
    let tol = 0.3 * median(&diff(beats));
    let idx: Vec<i64> = down.iter().map(|d| searchsorted(beats, d - tol) as i64).collect();
    let counts: Vec<i64> = idx.windows(2).map(|w| w[1] - w[0]).collect();
    let mut vals: Vec<i64> = counts.clone();
    vals.sort();
    vals.dedup();
    let c: Vec<usize> = vals.iter().map(|v| counts.iter().filter(|x| *x == v).count()).collect();
    let best = (0..vals.len()).fold(0, |b, i| if c[i] > c[b] { i } else { b });
    let m = vals[best];
    if (c[best] as f64) / (counts.len() as f64) < 0.6 {
        return None;
    }
    let name = match m {
        2 => "2/4",
        3 => "3/4",
        4 => "4/4",
        5 => "5/4",
        6 => "6/8",
        7 => "7/8",
        8 => "4/4",
        9 => "9/8",
        12 => "12/8",
        _ => return None,
    };
    Some((name, m as usize))
}

/// The most common value (weighted by how much of the song it covers), found on a log scale.
pub fn log_mode(values: &[f64], weights: &[f64]) -> f64 {
    let (res, spread) = (0.02, 0.06);
    let (v, w): (Vec<f64>, Vec<f64>) = values.iter().zip(weights).filter(|(v, _)| **v > 1e-3).map(|(a, b)| (*a, *b)).unzip();
    let lv: Vec<f64> = v.iter().map(|x| x.ln()).collect();
    let mn = lv.iter().cloned().fold(f64::INFINITY, f64::min);
    let mx = lv.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let bins = np::arange(mn - 0.1, mx + 0.1 + res, res);
    let h = np::histogram(&lv, &w, &bins);
    let k = 1usize.max((spread / res).round() as usize);
    let h = np::box_same(&h, k);
    let c = bins[argmax(&h)] + res / 2.0;
    let (mut s, mut sw) = (0.0, 0.0);
    for (l, ww) in lv.iter().zip(&w) {
        if (l - c).abs() < 0.1 {
            s += l * ww;
            sw += ww;
        }
    }
    (s / sw).exp()
}

/// The song's own pulse period: the level that most of the song (by time) is tracked at. Bars vote too.
fn level_reference(beats: &[f64], down: Option<&[f64]>, spec: &Spec) -> (f64, Option<f64>, bool, Option<f64>) {
    let iv = diff(beats);
    let mut vals = iv.clone();
    let mut wts = iv.clone();
    let (mut bars_ok, mut lm, mut l_mode) = (false, None, None);
    if let Some(d) = down.filter(|d| d.len() >= 6) {
        let l = diff(d);
        let m = median(&l);
        lm = Some(m);
        l_mode = Some(log_mode(&l, &l));
        bars_ok = l.iter().filter(|x| (*x / m - 1.0).abs() < 0.18).count() as f64 / l.len() as f64 >= 0.6;
        let b = if spec.regular { spec.beats as f64 } else { 1.0 };
        vals.extend(l.iter().map(|x| x / b));
        wts.extend(l.iter().map(|x| 0.7 * x));
    }
    (log_mode(&vals, &wts), lm, bars_ok, l_mode)
}

/// Pick the anchor period: r_det times a factor of 1/3, 1/2, 1, 2 or 3 (the usual level mistakes).
fn choose_level(r_det: f64, lm: Option<f64>, spec: &Spec, bpm_lo: f64, bpm_hi: f64) -> f64 {
    let regular = spec.regular;
    let unit = if regular { spec.beat_q } else { spec.bar_q };
    let r_det = if !regular && lm.is_some() { lm.unwrap() } else { r_det };
    let (mut best, mut best_s) = (r_det, -1.0);
    for m in LEVEL_MULTS {
        let c = m * r_det;
        let bpm = 60.0 * unit / c;
        let mut s_ = (if bpm_lo <= bpm && bpm < bpm_hi { 1.0 } else { 0.0 }) - 0.03 * m.ln().abs();
        if let Some(l) = lm {
            let bn = if regular { spec.beats as f64 } else { 1.0 };
            if (c * bn / l).ln().abs() < 0.12 {
                s_ += 0.3;
            }
        }
        if s_ > best_s {
            best = c;
            best_s = s_;
        }
    }
    best
}

/// A flywheel that remembers the tempo (see the original's docstring). Returns (anchors, number of add/drop events).
fn pll(b: &[f64], s: usize, r0: f64) -> (Vec<f64>, usize) {
    let tol = 0.28;
    let n = b.len();
    let mut cur = b[s];
    let mut r = r0;
    let mut out = vec![cur];
    let mut events = 0usize;
    let mut i = s + 1;
    let mut coast = 0usize;
    while i < n {
        let exp = cur + r;
        let w = (tol + 0.1 * coast.min(2) as f64).min(0.48);
        while i < n && b[i] < exp - w * r {
            events += 1;
            i += 1;
        }
        if i >= n {
            break;
        }
        let new;
        let mut step;
        if b[i] <= exp + w * r {
            let (mut j, mut best) = (i, i);
            while j < n && b[j] <= exp + w * r {
                if (b[j] - exp).abs() < (b[best] - exp).abs() {
                    best = j;
                }
                j += 1;
            }
            new = b[best];
            i = best + 1;
            step = new - cur;
            if w > tol + 1e-9 {
                step = r;
            }
            coast = 0;
        } else {
            let gap = b[i] - cur;
            let q = gap / r;
            let k = 1i64.max(q.round_ties_even() as i64) as usize;
            let run = &b[i..(i + 4).min(n)];
            let steady = run.len() == 4 && run.windows(2).all(|x| (x[1] - x[0]) > 0.8 * r && (x[1] - x[0]) < 1.25 * r);
            if k >= 2 && ((q - k as f64).abs() <= tol || steady) {
                new = b[i];
                i += 1;
                step = gap / k as f64;
                for m_ in 1..k {
                    out.push(cur + step * m_ as f64);
                    events += 1;
                }
                if steady && (q - k as f64).abs() > tol {
                    step = r;
                }
                coast = 0;
            } else if steady {
                new = b[i];
                i += 1;
                step = r;
                coast = 0;
            } else {
                new = exp;
                step = r;
                events += 1;
                coast += 1;
            }
        }
        r = (0.7 * r + 0.3 * step).max(0.7 * r0).min(1.4 * r0);
        cur = new;
        out.push(cur);
    }
    (out, events)
}

/// A good place to start: inside a steady run of beats (preferably on a downbeat).
fn start_index(b: &[f64], r0: f64, down: Option<&[f64]>) -> usize {
    let mut steady = Vec::new();
    for i in 0..b.len().saturating_sub(3) {
        if b[i..i + 4].windows(2).all(|x| {
            let q = (x[1] - x[0]) / r0;
            q > 0.75 && q < 1.3
        }) {
            steady.push(i);
        }
    }
    if !steady.is_empty() {
        if let Some(d) = down {
            for &i in steady.iter().take(60) {
                let m = d.iter().map(|x| (x - b[i]).abs()).fold(f64::INFINITY, f64::min);
                if m < 0.2 * r0 {
                    return i;
                }
            }
        }
        return steady[0];
    }
    0
}

fn clean_sequence(b: &[f64], r0: f64, start: usize) -> (Vec<f64>, usize) {
    let (fwd, e1) = pll(b, start, r0);
    if start > 0 {
        let rev: Vec<f64> = b[..start + 1].iter().rev().map(|t| -t).collect();
        let (bwd, e2) = pll(&rev, 0, r0);
        let mut back: Vec<f64> = bwd[1..].iter().map(|t| -t).collect();
        back.reverse();
        back.extend(fwd);
        return (back, e1 + e2);
    }
    (fwd, e1)
}

/// Typical interval, ignoring which octave each interval is in, folded into [plo, 2 plo).
fn circular_period(iv: &[f64], plo: f64) -> f64 {
    let (mut re, mut im) = (0.0, 0.0);
    let mut n = 0.0;
    for &v in iv.iter().filter(|v| **v > 0.0) {
        let a = 2.0 * std::f64::consts::PI * (v / plo).log2();
        re += a.cos();
        im += a.sin();
        n += 1.0;
    }
    let ang = (im / n).atan2(re / n).rem_euclid(2.0 * std::f64::consts::PI);
    plo * 2f64.powf(ang / (2.0 * std::f64::consts::PI))
}

/// The period the whole song should be tracked at. Returns (period, bars_ok).
fn bars_reference(beats: &[f64], down: Option<&[f64]>, spec: &Spec, bpm_lo: f64, bpm_hi: f64) -> (f64, bool) {
    let (b, bq, regular) = (spec.beats as f64, spec.beat_q, spec.regular);
    let iv = diff(beats);
    let m_b = median(&iv);
    let (mut bars_ok, mut lm) = (false, 0.0);
    if let Some(d) = down.filter(|d| d.len() >= 6) {
        let l = diff(d);
        lm = median(&l);
        bars_ok = l.iter().filter(|x| (*x / lm - 1.0).abs() < 0.18).count() as f64 / l.len() as f64 >= 0.6;
    }
    let r0 = if !regular {
        if bars_ok { lm } else { b * circular_period(&iv, 60.0 * bq / bpm_hi) }
    } else if bars_ok {
        let mut r0 = lm / b;
        let ratio = m_b / r0;
        if 0.42 < ratio && ratio < 0.6 {
            let bpm_c = 60.0 * bq / r0;
            if !(bpm_lo <= bpm_c && bpm_c < bpm_hi) && bpm_lo <= 2.0 * bpm_c && 2.0 * bpm_c < bpm_hi {
                r0 /= 2.0;
            }
        }
        r0
    } else {
        circular_period(&iv, 60.0 * bq / bpm_hi)
    };
    (r0, bars_ok)
}

/// Regular meters: split every cleaned bar into B equal beats, snap each to a tracker beat if one is close.
fn anchors_from_bars(bars: &[f64], bn: usize, beats: &[f64], total_dur: f64) -> (Vec<f64>, f64) {
    let snap = 0.18;
    let lens = diff(bars);
    let lm = median(&lens);
    let (mut out, mut per) = (Vec::new(), Vec::new());
    for j in 0..bars.len() {
        let l = if j < lens.len() { lens[j] } else { lm };
        for k in 0..bn {
            let t = bars[j] + l * k as f64 / bn as f64;
            if t < total_dur - 0.05 {
                out.push(t);
                per.push(l / bn as f64);
            }
        }
    }
    if let Some(&last) = beats.last() {
        let keep: Vec<bool> = out.iter().zip(&per).map(|(o, p)| *o <= last + 0.5 * p).collect();
        out = out.iter().zip(&keep).filter(|(_, k)| **k).map(|(o, _)| *o).collect();
        per = per.iter().zip(&keep).filter(|(_, k)| **k).map(|(p, _)| *p).collect();
    }
    let mut matched = 0.0;
    if !beats.is_empty() && !out.is_empty() {
        let mut ok_n = 0;
        for i in 0..out.len() {
            let idx = searchsorted(beats, out[i]).clamp(1, beats.len() - 1);
            let (left, right) = (beats[idx - 1], beats[idx]);
            let near = if (out[i] - left).abs() <= (out[i] - right).abs() { left } else { right };
            if (near - out[i]).abs() <= snap * per[i] {
                out[i] = near;
                ok_n += 1;
            }
        }
        matched = ok_n as f64 / out.len() as f64;
    }
    for k in 1..out.len() {
        if out[k] <= out[k - 1] + 1e-3 {
            out[k] = out[k - 1] + 1e-3;
        }
    }
    (out, matched)
}

pub struct Prep {
    pub anchors: Vec<f64>,
    pub r0: f64,
    pub bars_ok: bool,
    pub kind: &'static str,
}

#[derive(Debug)]
pub struct BeatError(pub String);

/// Raw tracker output -> a clean list of anchors (felt beats for regular meters, bars for irregular ones), one tempo level.
pub fn prepare_anchors(beats: &[f64], down: Option<&[f64]>, total_dur: f64, spec: &Spec, bpm_lo: f64, bpm_hi: f64) -> Result<Prep, BeatError> {
    let b = np::unique_round5(beats);
    if b.len() < 8 {
        return Err(BeatError("Could not find enough beats in this file.".into()));
    }
    let d: Option<Vec<f64>> = down.filter(|d| d.len() >= 4).map(np::unique_round5);
    let d_ref = d.as_deref();
    let (r_det, _lm, bars_ok, l_mode) = level_reference(&b, d_ref, spec);
    let seq: Vec<f64>;
    let kind;
    if spec.regular && bars_ok && d.is_some() {
        let dd = d.as_ref().unwrap();
        let (r_bar, _) = bars_reference(&b, d_ref, spec, bpm_lo, bpm_hi);
        let bn = spec.beats;
        let st = start_index(dd, r_bar * bn as f64, None);
        let (bars, _) = clean_sequence(dd, r_bar * bn as f64, st);
        if bars.len() < 4 {
            return Err(BeatError("Could not find enough bars in this file.".into()));
        }
        seq = anchors_from_bars(&bars, bn, &b, total_dur).0;
        kind = "beat";
    } else if spec.regular {
        let r0 = choose_level(r_det, l_mode, spec, bpm_lo, bpm_hi);
        let mut cand: Vec<f64> = b.clone();
        if let Some(dd) = &d {
            cand.extend(dd.iter().cloned());
            cand.sort_by(|x, y| x.partial_cmp(y).unwrap());
        }
        let mut keep = vec![cand[0]];
        for w in cand.windows(2) {
            if w[1] - w[0] > 0.15 * r0 {
                keep.push(w[1]);
            }
        }
        let st = start_index(&keep, r0, d_ref);
        seq = clean_sequence(&keep, r0, st).0;
        kind = "beat";
    } else {
        kind = "bar";
        let (r_bar, _) = bars_reference(&b, d_ref, spec, bpm_lo, bpm_hi);
        let seq0: Vec<f64> = if bars_ok && d.is_some() {
            d.clone().unwrap()
        } else {
            let r_pulse = r_bar / spec.beats as f64;
            let st = start_index(&b, r_pulse, None);
            let (pulses, _) = clean_sequence(&b, r_pulse, st);
            pulses.iter().step_by(spec.beats).cloned().collect()
        };
        let st = start_index(&seq0, r_bar, None);
        seq = clean_sequence(&seq0, r_bar, st).0;
    }
    if seq.len() < 6 {
        return Err(BeatError("Could not find enough beats/bars in this file.".into()));
    }
    // stage 2: one tempo level for the whole song
    let iv = diff(&seq);
    let r_a = log_mode(&iv, &iv);
    let t = choose_level(r_a, l_mode, spec, bpm_lo, bpm_hi);
    let st = start_index(&seq, t, None);
    let (anchors, _) = clean_sequence(&seq, t, st);
    if anchors.len() < 6 {
        return Err(BeatError("Beat list collapsed during cleaning - this file may not have a clear beat.".into()));
    }
    Ok(Prep { anchors, r0: t, bars_ok, kind })
}

pub fn rolling_median(x: &[f64], w: usize) -> Vec<f64> {
    let h = w / 2;
    let n = x.len();
    (0..n)
        .map(|i| {
            let mut win: Vec<f64> = (0..w)
                .map(|k| {
                    let j = i as isize + k as isize - h as isize;
                    x[j.clamp(0, n as isize - 1) as usize]
                })
                .collect();
            dsp::median_f64(&mut win)
        })
        .collect()
}

fn wmedian(v: &[f64], w: &[f64]) -> f64 {
    let mut o: Vec<usize> = (0..v.len()).collect();
    o.sort_by(|a, b| v[*a].partial_cmp(&v[*b]).unwrap());
    let mut cs = Vec::with_capacity(o.len());
    let mut acc = 0.0;
    for &i in &o {
        acc += w[i];
        cs.push(acc);
    }
    let half = 0.5 * w.iter().sum::<f64>();
    let k = searchsorted(&cs, half).min(o.len() - 1);
    v[o[k]]
}

/// The felt-beat spacing the song is REALLY at: the time-weighted median spacing.
pub fn pick_reference_period(anchors: &[f64]) -> f64 {
    let iv = diff(anchors);
    let sm = rolling_median(&iv, 9);
    wmedian(&sm, &iv)
}

fn find_bad_runs(anchors: &[f64], t: f64) -> Vec<(usize, usize)> {
    let (min_len, merge_gap) = (4, 10);
    let iv = diff(anchors);
    let sm: Vec<f64> = rolling_median(&iv, 9).iter().map(|x| x / t).collect();
    let bad: Vec<bool> = sm.iter().map(|&v| WRONG_LEVEL.iter().any(|(lo, hi)| v >= *lo && v <= *hi)).collect();
    let mut runs: Vec<(usize, usize)> = Vec::new();
    let mut i = 0;
    while i < bad.len() {
        if bad[i] {
            let mut j = i;
            while j + 1 < bad.len() && bad[j + 1] {
                j += 1;
            }
            runs.push((i, j + 1));
            i = j + 1;
        } else {
            i += 1;
        }
    }
    let mut out: Vec<(usize, usize)> = Vec::new();
    for r in runs {
        if let Some(last) = out.last_mut() {
            if r.0 - last.1 <= merge_gap {
                last.1 = r.1;
                continue;
            }
        }
        out.push(r);
    }
    out.into_iter().filter(|(a, b)| b - a >= min_len).collect()
}

fn onset_env(y: &[f32], sr: f64, hop: usize) -> Vec<f32> {
    let y_perc = dsp::hpss_signal(y, 2.0, false);
    let env = dsp::onset_strength(&y_perc, sr, 2048, hop, 1, 3, dsp::Aggregate::Median, 128);
    drop(y_perc);
    let full = dsp::onset_strength(y, sr, 2048, hop, 1, 1, dsp::Aggregate::Mean, 128);
    let em = env.iter().cloned().fold(f32::NEG_INFINITY, f32::max) + 1e-9;
    let fm = full.iter().cloned().fold(f32::NEG_INFINITY, f32::max) + 1e-9;
    env.iter().zip(&full).map(|(e, f)| e / em + 0.5 * f / fm).collect()
}

fn retrack_region(env: &[f32], sr: f64, hop: usize, t_a: f64, t_b: f64, t: f64) -> Option<Vec<f64>> {
    let fa = (t_a * sr / hop as f64) as usize;
    let fb = ((t_b * sr / hop as f64) as usize + 1).min(env.len());
    if fa >= fb || fb - fa < 8 {
        return None;
    }
    let seg = &env[fa..fb];
    let fr = dsp::beat_track(seg, sr, hop, 60.0 / t, 80.0);
    Some(fr.iter().map(|&f| t_a + (f * hop) as f64 / sr).collect())
}

/// After re-tracking: no gap bigger than 1.4 T (fill evenly) and no beat closer than 0.7 T (drop it).
fn tidy(beats: &[f64], t: f64) -> Vec<f64> {
    let (lo, hi) = (0.7, 1.4);
    let mut out = vec![beats[0]];
    for &b in &beats[1..] {
        let gap = b - out[out.len() - 1];
        if gap < lo * t {
            continue;
        }
        if gap > hi * t {
            let k = (gap / t).round_ties_even() as usize;
            let base = out[out.len() - 1];
            for m in 1..k {
                out.push(base + gap * m as f64 / k as f64);
            }
        }
        out.push(b);
    }
    out
}

/// Find stretches whose spacing is NOT the song's own level and re-track them from the audio with the tempo pinned.
pub fn enforce_level(anchors: &[f64], y: &[f32], sr: f64, t_ref: f64) -> (Vec<f64>, Vec<(f64, f64, f64)>) {
    let runs = find_bad_runs(anchors, t_ref);
    if runs.is_empty() {
        return (anchors.to_vec(), vec![]);
    }
    let env = onset_env(y, sr, HOP);
    let n = anchors.len();
    let iv = diff(anchors);
    let mut out: Vec<f64> = Vec::new();
    let mut fixes = Vec::new();
    let mut last = 0usize;
    for (a, b) in runs {
        let ia = a.saturating_sub(1);
        let at_end = b >= n - 2;
        let ib = if at_end { n - 1 } else { b + 1 };
        let t_a = anchors[ia];
        let t_b = if at_end { anchors[n - 1] + 0.5 * t_ref } else { anchors[ib] };
        let Some(beats) = retrack_region(&env, sr, HOP, t_a, t_b, t_ref) else { continue };
        let lim = if at_end { t_b } else { t_b - 0.5 * t_ref };
        let keep: Vec<f64> = beats.into_iter().filter(|&x| x > t_a + 0.5 * t_ref && x < lim).collect();
        if keep.len() < 3 {
            continue;
        }
        let mut s = vec![t_a];
        s.extend(keep);
        if !at_end {
            s.push(t_b);
        }
        let seg = tidy(&s, t_ref);
        if ia > last {
            out.extend_from_slice(&anchors[last..ia]);
        }
        out.extend(seg);
        last = if at_end { n } else { ib + 1 };
        let med = median(&iv[a..b.min(iv.len())]);
        fixes.push((anchors[a], anchors[b.min(n - 1)], med / t_ref));
    }
    if last < n {
        out.extend_from_slice(&anchors[last..]);
    }
    (np::unique_round5(&out), fixes)
}

struct Window {
    times: Vec<f64>,
    env: Vec<f64>,
}

/// Fine-resolution onset envelope around time t.
fn window_env(y: &[f32], sr: f64, t: f64, max_shift: f64) -> Option<Window> {
    let half = max_shift + 0.04;
    let a = (0f64.max((t - half) * sr)) as usize;
    let z = ((y.len() as f64).min((t + half) * sr)) as usize;
    if z <= a || z - a < 1024 {
        return None;
    }
    let env = dsp::onset_strength(&y[a..z], sr, 512, 32, 1, 1, dsp::Aggregate::Mean, 40);
    if env.len() < 8 {
        return None;
    }
    let times = (0..env.len()).map(|i| a as f64 / sr + (i * 32) as f64 / sr).collect();
    Some(Window { times, env: env.iter().map(|&v| v as f64).collect() })
}

/// Where does the hit near t START? Strongest clear transient near t, then back to where it begins to rise.
fn pick_peak(w: &Window, t: f64, max_shift: f64, refv: f64) -> Option<f64> {
    let (times, env) = (&w.times, &w.env);
    let n = env.len();
    let emax = env.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let mut best: Option<(usize, f64)> = None;
    for j in 1..n - 1 {
        let d = times[j] - t;
        if env[j] >= env[j - 1] && env[j] >= env[j + 1] && d.abs() <= max_shift && env[j] > 0.35 * refv && env[j] >= 0.6 * emax {
            let score = env[j] * (-0.5 * (d / (max_shift * 0.6)).powi(2)).exp();
            if best.map_or(true, |(_, s)| score > s) {
                best = Some((j, score));
            }
        }
    }
    let (j, _) = best?;
    let level = RISE_FRAC * env[j];
    let mut i = j;
    while i > 1 && env[i - 1] > level && env[i - 1] <= env[i] * 1.001 {
        i -= 1;
    }
    if i > 0 && env[i - 1] <= level && level < env[i] {
        let frac = (level - env[i - 1]) / (env[i] - env[i - 1] + 1e-12);
        return Some(times[i - 1] + frac * (times[i] - times[i - 1]));
    }
    Some(times[i])
}

fn windows(y: &[f32], sr: f64, points: &[f64], max_shift: f64) -> (Vec<Option<Window>>, f64) {
    let mut wins: Vec<Option<Window>> = Vec::with_capacity(points.len());
    #[cfg(feature = "rayon")]
    {
        use rayon::prelude::*;
        wins.extend(points.par_iter().map(|&t| window_env(y, sr, t, max_shift)).collect::<Vec<_>>());
    }
    #[cfg(not(feature = "rayon"))]
    {
        wins.extend(points.iter().map(|&t| window_env(y, sr, t, max_shift)));
    }
    let peaks: Vec<f64> = wins.iter().flatten().map(|w| w.env.iter().cloned().fold(f64::NEG_INFINITY, f64::max)).collect();
    let refv = if peaks.is_empty() { 0.0 } else { median(&peaks) };
    (wins, refv)
}

/// Move each beat onto the nearest strong transient (within +-max_shift seconds). Returns (beats, locked mask).
pub fn refine_beats(beats: &[f64], y: &[f32], sr: f64) -> (Vec<f64>, Vec<bool>) {
    let max_shift = 0.045;
    let mut out = beats.to_vec();
    let mut mask = vec![false; beats.len()];
    let (wins, refv) = windows(y, sr, beats, max_shift);
    for (k, w) in wins.iter().enumerate() {
        if let Some(w) = w {
            if let Some(p) = pick_peak(w, beats[k], max_shift, refv) {
                out[k] = p;
                mask[k] = true;
            }
        }
    }
    for k in 1..out.len() {
        out[k] = out[k].max(out[k - 1]); // np.maximum.accumulate
    }
    for k in 1..out.len() {
        if out[k] <= out[k - 1] + 1e-3 {
            out[k] = out[k - 1] + 1e-3;
        }
    }
    (out, mask)
}

/// Stretches without a clear transient: space those beats evenly between the nearest locked beats.
pub fn fill_weak_runs(times: &[f64], mask: &[bool]) -> Vec<f64> {
    let max_run = 32;
    let mut t = times.to_vec();
    let n = t.len();
    let mut i = 0;
    while i < n {
        if mask[i] {
            i += 1;
            continue;
        }
        let mut j = i;
        while j < n && !mask[j] {
            j += 1;
        }
        if i > 0 && j < n && (j - i) <= max_run {
            let ls = np::linspace(t[i - 1], t[j], j - i + 2);
            t[i..j].copy_from_slice(&ls[1..ls.len() - 1]);
        }
        i = j;
    }
    t
}

pub fn irregularity(beats: &[f64]) -> f64 {
    let iv = diff(beats);
    np::std(&iv) / np::mean(&iv) * 100.0
}
