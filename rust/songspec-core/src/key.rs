//! The fused key: service/songspec_analyzer/key/{estimators, fusion, modes, stage, borrow}.py.
//! Estimators: Centsible's detector, S-KEY, Albrecht-Shanahan on Centsible's scale chroma and on the tuned beat chroma; fused by
//! the service's installed model (key/fusion_model.json, read at build time). Then Centsible's mode, section keys, borrows.

use serde::Serialize;
use tract_onnx::prelude::*;

use crate::centsible::KeyDecision;
use crate::chords::{self, Segment};
use crate::dsp;
use crate::models::{run1, ModelBytes, PlanCache};
use crate::notation::{self as nt};
use crate::np::{self, median};

const AMBIGUOUS_P: f64 = 0.45;
const WINDOW_BARS: usize = 8;
const HOP_BARS: usize = 4;
const CHANGE_PENALTY: f64 = 24.0;
const MIN_SECTION_BARS: f64 = 12.0;
const CENTSIBLE_MODE_P: f64 = 0.5;
const DEGREE_MODES: [&str; 7] = ["ionian", "dorian", "phrygian", "lydian", "mixolydian", "aeolian", "locrian"];
const FUSION_MODEL: &str = include_str!("../../../service/songspec_analyzer/key/fusion_model.json");

pub const PROFILES: [(&str, [f64; 12], [f64; 12]); 2] = [
    ("temperley", [5.0, 2.0, 3.5, 2.0, 4.5, 4.0, 2.0, 4.5, 2.0, 3.5, 1.5, 4.0], [5.0, 2.0, 3.5, 4.5, 2.0, 4.0, 2.0, 4.5, 3.5, 2.0, 1.5, 4.0]),
    (
        "albrecht_shanahan",
        [0.238, 0.006, 0.111, 0.006, 0.137, 0.094, 0.016, 0.214, 0.009, 0.080, 0.008, 0.081],
        [0.220, 0.006, 0.104, 0.123, 0.019, 0.103, 0.012, 0.214, 0.062, 0.022, 0.061, 0.052],
    ),
];

fn softmax(x: &[f64], temp: f64) -> Vec<f64> {
    let z: Vec<f64> = x.iter().map(|v| v / temp).collect();
    let m = z.iter().cloned().fold(f64::MIN, f64::max);
    let e: Vec<f64> = z.iter().map(|v| (v - m).exp()).collect();
    let s: f64 = e.iter().sum();
    e.iter().map(|v| v / s).collect()
}

fn corr(a: &[f64; 12], b: &[f64]) -> f64 {
    let ma = a.iter().sum::<f64>() / 12.0;
    let mb = b.iter().sum::<f64>() / 12.0;
    let (mut ab, mut aa, mut bb) = (0.0, 0.0, 0.0);
    for i in 0..12 {
        ab += (a[i] - ma) * (b[i] - mb);
        aa += (a[i] - ma) * (a[i] - ma);
        bb += (b[i] - mb) * (b[i] - mb);
    }
    let r = ab / (aa * bb).sqrt();
    if r.is_nan() { -1.0 } else { r }
}

/// Pearson correlation with the profile rotated to every key -> softmax at temperature 0.1 (24, index tonic + 12 * minor).
pub fn profile_probs(chroma: &[f64; 12], name: &str) -> Vec<f64> {
    let (_, maj, min) = PROFILES.iter().find(|p| p.0 == name).unwrap();
    let mut s = vec![0f64; 24];
    for t in 0..12 {
        let rm: Vec<f64> = (0..12).map(|i| maj[(i + 12 - t) % 12]).collect();
        let rn: Vec<f64> = (0..12).map(|i| min[(i + 12 - t) % 12]).collect();
        s[t] = corr(chroma, &rm);
        s[12 + t] = corr(chroma, &rn);
    }
    softmax(&s, 0.1)
}

/// Beat chroma: chroma of the tuned CQT (36 / octave), one row per beat (median), plus per-beat energy (mean).
pub fn tuned_beat_chroma(c: &dsp::Cqt, beats: &[f64]) -> (Vec<[f64; 12]>, Vec<f64>) {
    let ch = dsp::chroma_from_cqt(c, 36);
    let energy: Vec<f64> = ch.iter().map(|r| r.iter().map(|v| (*v as f64).powi(2)).sum::<f64>().sqrt()).collect();
    let n = ch.len();
    let rows: Vec<[f64; 12]> = ch.iter().map(|r| r.map(|v| v as f64)).collect();
    if beats.len() <= 8 {
        return (rows, energy);
    }
    // sync(C, fr, median) with pad=True: boundaries 0, the beats' frames, n; the stretch before the first beat is dropped
    let mut b: Vec<usize> = beats
        .iter()
        .map(|&t| dsp::time_to_frames(t, chords::SR, chords::HOP))
        .filter(|&f| f > 0 && (f as usize) < n)
        .map(|f| f as usize)
        .collect();
    b.push(0);
    b.push(n);
    b.sort();
    b.dedup();
    let mut out = Vec::new();
    let mut en = Vec::new();
    for w in b.windows(2).skip(1) {
        let mut r = [0f64; 12];
        for (k, o) in r.iter_mut().enumerate() {
            let v: Vec<f64> = rows[w[0]..w[1]].iter().map(|x| x[k]).collect();
            *o = median(&v);
        }
        out.push(r);
        en.push(np::mean(&energy[w[0]..w[1]]));
    }
    (out, en)
}

/// Song-level chroma: log-compressed, silent frames left out, every frame weighted equally, L1-normalised.
pub fn global_chroma(frames: &[[f64; 12]], weights: Option<&[f64]>) -> [f64; 12] {
    let mut f: Vec<[f64; 12]> = frames.to_vec();
    if let Some(w) = weights {
        let pos: Vec<f64> = w.iter().cloned().filter(|&x| x > 0.0).collect();
        if !pos.is_empty() {
            let thr = 0.05 * median(&pos);
            f = frames.iter().zip(w).filter(|(_, &ww)| ww > thr).map(|(r, _)| *r).collect();
        }
    }
    if f.is_empty() {
        return [1.0 / 12.0; 12];
    }
    let mx = f.iter().flat_map(|r| r.iter()).cloned().fold(f64::MIN, f64::max);
    let mut g = [0f64; 12];
    for r in &f {
        let l: Vec<f64> = r.iter().map(|v| (100.0 * v / (mx + 1e-12)).ln_1p()).collect();
        let s: f64 = l.iter().sum::<f64>() + 1e-12;
        for i in 0..12 {
            g[i] += l[i] / s;
        }
    }
    let s: f64 = g.iter().sum::<f64>() + 1e-12;
    g.map(|v| v / s)
}

/// S-KEY on the mono signal: torchaudio resampling to 22.05 kHz, peak normalisation, the network -> 24 probabilities (our order).
pub fn skey_probs(mono: &[f32], sr: u32, models: &ModelBytes, cache: &PlanCache) -> TractResult<Vec<f64>> {
    let mut x = dsp::resample_torch(mono, sr, 22050);
    let m = x.iter().fold(0f32, |a, v| a.max(v.abs()));
    if m > 0.0 {
        for v in x.iter_mut() {
            *v /= m;
        }
    }
    let n = x.len();
    let plan = cache.get(1, &models.skey, &[1, 1, n])?;
    let input = tract_ndarray::Array3::from_shape_vec((1, 1, n), x)?.into_tensor();
    let out = run1(&plan, input)?;
    let p: Vec<f64> = out[0].to_plain_array_view::<f32>()?.iter().map(|v| *v as f64).collect();
    let s: f64 = p.iter().sum();
    // S-KEY's classes: A Major ... G# Major, then B minor ... Bb minor
    const INDEX: [usize; 24] = [9, 10, 11, 0, 1, 2, 3, 4, 5, 6, 7, 8, 23, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22];
    let mut ours = vec![0f64; 24];
    for i in 0..24 {
        ours[INDEX[i]] = p[i] / s;
    }
    Ok(ours)
}

struct Fusion {
    names: Vec<String>,
    w: Vec<f64>,
}

fn fusion() -> Fusion {
    let v: serde_json::Value = serde_json::from_str(FUSION_MODEL).unwrap();
    Fusion {
        names: v["names"].as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect(),
        w: v["w"].as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect(),
    }
}

/// KeyFusion.predict_proba without extra features: score(k) = sum_n w_n log p_n(k) + w_minor [k minor].
fn fuse(est: &[(&str, Vec<f64>)]) -> Vec<f64> {
    let f = fusion();
    let mut s = vec![0f64; 24];
    for k in 0..24 {
        let mut v = 0.0;
        for (i, n) in f.names.iter().enumerate() {
            let p = &est.iter().find(|e| e.0 == n).unwrap().1;
            v += f.w[i] * p[k].clamp(1e-4, 1.0).ln();
        }
        v += f.w[f.names.len()] * if k >= 12 { 1.0 } else { 0.0 };
        s[k] = v;
    }
    softmax(&s, 1.0)
}

fn centsible_mode(kd: &KeyDecision, tonic: usize, minor: bool) -> &'static str {
    if kd.valid && kd.tonic == tonic % 12 && kd.minor == minor {
        let d = kd.degree;
        let m = DEGREE_MODES[d];
        if m != "ionian" && m != "aeolian" && kd.mode_prob[d] >= CENTSIBLE_MODE_P {
            return m;
        }
    }
    if minor { "minor" } else { "major" }
}

// ------------------------------------------------------------------------------------------------ section keys

fn window_keys(seq: &[[f64; 12]], beat_times: &[f64], downbeats: &[f64]) -> Vec<(f64, f64, usize)> {
    let downs: Vec<f64> = downbeats.iter().cloned().filter(|&d| d >= beat_times[0]).collect();
    if downs.len() < WINDOW_BARS + 2 {
        return vec![];
    }
    let mut wins: Vec<(f64, f64, Vec<f64>)> = Vec::new();
    let mut i = 0;
    while i + WINDOW_BARS < downs.len() {
        let (a, z) = (downs[i], downs[i + WINDOW_BARS]);
        let sel: Vec<[f64; 12]> = seq.iter().zip(beat_times).filter(|(_, &t)| t >= a && t < z).map(|(r, _)| *r).collect();
        if sel.len() >= 8 {
            let g = global_chroma(&sel, Some(&vec![1.0; sel.len()]));
            let (pt, pa) = (profile_probs(&g, "temperley"), profile_probs(&g, "albrecht_shanahan"));
            let lp: Vec<f64> = (0..24).map(|k| 0.5 * ((pt[k] + 1e-6).ln() + (pa[k] + 1e-6).ln())).collect();
            wins.push((a, z, lp));
        }
        i += HOP_BARS;
    }
    if wins.is_empty() {
        return vec![];
    }
    let mut delta = wins[0].2.clone();
    let mut back: Vec<Vec<usize>> = Vec::new();
    for (_, _, lp) in &wins[1..] {
        let best = np::argmax(&delta);
        let mv = delta[best] - CHANGE_PENALTY;
        let mut b = vec![0usize; 24];
        let mut nd = vec![0f64; 24];
        for k in 0..24 {
            if mv > delta[k] {
                b[k] = best;
                nd[k] = mv + lp[k];
            } else {
                b[k] = k;
                nd[k] = delta[k] + lp[k];
            }
        }
        back.push(b);
        delta = nd;
    }
    let mut path = vec![np::argmax(&delta)];
    for b in back.iter().rev() {
        let last = *path.last().unwrap();
        path.push(b[last]);
    }
    path.reverse();
    wins.iter().zip(path).map(|(w, k)| (w.0, w.1, k)).collect()
}

struct Sec {
    start: f64,
    end: f64,
    k: usize,
}

fn sections_from_windows(wk: &[(f64, f64, usize)], duration: f64, bar_len: f64) -> Vec<Sec> {
    if wk.is_empty() {
        return vec![];
    }
    let mut secs: Vec<Sec> = Vec::new();
    for &(a, z, k) in wk {
        if let Some(last) = secs.last_mut() {
            if last.k == k {
                last.end = z;
                continue;
            }
            if last.end > a {
                last.end = a + (last.end - a) / 2.0;
            }
        }
        secs.push(Sec { start: a, end: z, k });
    }
    secs[0].start = 0.0;
    let n = secs.len();
    secs[n - 1].end = duration;
    for i in 1..secs.len() {
        secs[i].start = secs[i - 1].end;
    }
    let mut changed = true;
    while changed && secs.len() > 1 {
        changed = false;
        for i in 0..secs.len() {
            if secs[i].end - secs[i].start < MIN_SECTION_BARS * bar_len {
                let j = if i > 0 { i - 1 } else { i + 1 };
                let (s0, s1) = (secs[i].start, secs[i].end);
                secs[j].start = secs[j].start.min(s0);
                secs[j].end = secs[j].end.max(s1);
                secs.remove(i);
                changed = true;
                break;
            }
        }
        let mut merged: Vec<Sec> = Vec::new();
        for s in secs.drain(..) {
            if let Some(m) = merged.last_mut() {
                if m.k == s.k {
                    m.end = s.end;
                    continue;
                }
            }
            merged.push(s);
        }
        secs = merged;
    }
    secs
}

fn modulation_kind(k1: usize, k2: usize) -> &'static str {
    if (k1 >= 12) != (k2 >= 12) {
        return if k1 % 12 == k2 % 12 { "mode change" } else { "key change" };
    }
    match (k2 + 12 - k1 % 12) % 12 {
        1 => "half-step lift",
        2 => "whole-step lift",
        11 => "half-step drop",
        10 => "whole-step drop",
        _ => "key change",
    }
}

// ------------------------------------------------------------------------------------------------ borrows (key/borrow.py)

const SOURCES: [(&str, usize, &str, f64); 14] = [
    ("ionian", 0, "ionian", -1.0),
    ("aeolian", 0, "aeolian", -1.0),
    ("mixolydian", 0, "mixolydian", 0.0),
    ("lydian", 0, "lydian", 0.0),
    ("dorian", 0, "dorian", 0.0),
    ("phrygian", 0, "phrygian", 0.0),
    ("harmonic_minor", 0, "harmonic_minor", 0.0),
    ("relative_harmonic_minor", 9, "harmonic_minor", 1.0),
    ("locrian", 0, "locrian", 0.5),
    ("melodic_minor", 0, "melodic_minor", 0.5),
    ("phrygian_dominant", 0, "phrygian_dominant", 1.0),
    ("harmonic_major", 0, "harmonic_major", 1.0),
    ("lydian_dominant", 0, "lydian_dominant", 0.5),
    ("hungarian_minor", 0, "hungarian_minor", 1.0),
];
const CHORD_MIN_SECONDS: f64 = 5.0;
const MIN_SEGMENTS: usize = 2;
const MIN_SHARE: f64 = 0.10;
const MIN_SECONDS: f64 = 6.0;
const MAX_MODES: usize = 3;
const MERGE_SLACK: f64 = 1.0;
const PICARDY_MINOR_RATIO: f64 = 0.5;
const PICARDY_BII_RATIO: f64 = 0.25;
const MAJOR_TRIADS: [&str; 6] = ["", "7", "maj7", "6", "9", "add9"];
const MINOR_TRIADS: [&str; 4] = ["m", "m7", "m6", "m9"];

type PcSet = u16;

fn scale_set(mode: &str, tonic: usize) -> PcSet {
    nt::mode_info(mode).3.iter().fold(0, |s, i| s | 1 << ((tonic + i) % 12))
}

fn chord_set(root: usize, q: &str) -> Option<PcSet> {
    chords::quality(q).map(|(_, iv, _, _)| iv.iter().fold(0, |s, i| s | 1 << ((root + i) % 12)))
}

fn subset(a: PcSet, b: PcSet) -> bool {
    a & !b == 0
}

#[derive(Debug, Clone, Serialize)]
pub struct Borrow {
    pub from: String,
    pub tonic: String,
    pub mode: String,
    pub chords: Vec<String>,
    pub seconds: f64,
    pub share: f64,
}

/// "May borrow from": each outside chord credited to the closest source scale on the key's tonic (see key/borrow.py).
pub fn borrowed(segs: &[Segment], tonic: usize, mode: &str) -> Vec<Borrow> {
    let own = match nt::norm_mode(mode).unwrap_or("major") {
        "major" => "ionian",
        "minor" => "aeolian",
        m => m,
    };
    let home = scale_set(own, tonic);
    let src_set = |sid: &str| {
        let (_, off, sc, _) = SOURCES.iter().find(|s| s.0 == sid).unwrap();
        scale_set(sc, tonic + off)
    };
    let cands: Vec<&str> = SOURCES.iter().filter(|s| src_set(s.0) != home && (s.1 == 0 || own == "ionian")).map(|s| s.0).collect();
    let mut cost: Vec<f64> = cands
        .iter()
        .map(|sid| {
            let pen = SOURCES.iter().find(|s| s.0 == *sid).unwrap().3;
            (src_set(sid) & !home).count_ones() as f64 + pen
        })
        .collect();
    if own != "ionian" && own != "aeolian" {
        for (i, sid) in cands.iter().enumerate() {
            if *sid == "ionian" || *sid == "aeolian" {
                cost[i] += 1.0;
            }
        }
    }
    let secs_of = |root: usize, quals: &[&str]| -> f64 {
        segs.iter().filter(|s| s.root == Some(root % 12) && quals.contains(&s.quality.as_str())).map(|s| s.end - s.start).sum()
    };
    let (_, parent, _, ivs) = nt::mode_info(own);
    let mut picardy = false;
    if parent == "Minor" && ivs.contains(&3) {
        let major_i = secs_of(tonic, &MAJOR_TRIADS);
        let mut q2: Vec<&str> = MAJOR_TRIADS.to_vec();
        q2.extend(MINOR_TRIADS);
        picardy = major_i > 0.0 && secs_of(tonic, &MINOR_TRIADS) >= PICARDY_MINOR_RATIO * major_i && secs_of(tonic + 1, &q2) < PICARDY_BII_RATIO * major_i;
    }
    let mut total = 0.0;
    // label (without bass) -> (notes, seconds, segments), in first-seen order
    let mut held: Vec<(String, PcSet, f64, usize)> = Vec::new();
    for s in segs {
        let (Some(root), Some(ns)) = (s.root, chord_set(s.root.unwrap_or(0), &s.quality)) else { continue };
        let d = s.end - s.start;
        total += d;
        let on_tonic = root == tonic % 12;
        let counts = !on_tonic || (picardy && MAJOR_TRIADS.contains(&s.quality.as_str()));
        if !subset(ns, home) && counts {
            let label = s.chord.split('/').next().unwrap().to_string();
            match held.iter_mut().find(|h| h.0 == label) {
                Some(h) => {
                    h.2 += d;
                    h.3 += 1;
                }
                None => held.push((label, ns, d, 1)),
            }
        }
    }
    if total <= 0.0 {
        return vec![];
    }
    // each outside chord -> its closest mode; chosen keeps insertion order like the Python dict
    let mut chosen: Vec<(usize, Vec<(String, PcSet, f64, usize)>)> = Vec::new();
    for h in &held {
        if h.2 < CHORD_MIN_SECONDS || h.3 < MIN_SEGMENTS {
            continue;
        }
        let fits: Vec<usize> = (0..cands.len()).filter(|&i| subset(h.1, src_set(cands[i]))).collect();
        let Some(&m) = fits.iter().min_by(|&&a, &&b| cost[a].partial_cmp(&cost[b]).unwrap().then(a.cmp(&b))) else { continue };
        match chosen.iter_mut().find(|c| c.0 == m) {
            Some(c) => c.1.push(h.clone()),
            None => chosen.push((m, vec![h.clone()])),
        }
    }
    while chosen.len() >= 2 {
        let mut best: Option<((isize, f64, usize), usize, Vec<usize>)> = None;
        for m in 0..cands.len() {
            let sc = src_set(cands[m]);
            let group: Vec<usize> = chosen
                .iter()
                .filter(|(k, v)| v.iter().all(|c| subset(c.1, sc)) && !(cands[*k] == "harmonic_minor" && parent == "Minor"))
                .map(|(k, _)| *k)
                .collect();
            if group.len() >= 2 {
                let alt = sc & !home;
                let union = group.iter().fold(0u16, |u, &k| u | (src_set(cands[k]) & !home));
                let min_cost = group.iter().map(|&k| cost[k]).fold(f64::MAX, f64::min);
                if subset(alt, union) && cost[m] <= min_cost + MERGE_SLACK {
                    let key = (-(group.len() as isize), cost[m], m);
                    let better = match &best {
                        None => true,
                        Some((bk, _, _)) => key.0 < bk.0 || (key.0 == bk.0 && (key.1 < bk.1 || (key.1 == bk.1 && key.2 < bk.2))),
                    };
                    if better {
                        best = Some((key, m, group));
                    }
                }
            }
        }
        let Some((_, m, group)) = best else { break };
        let mut merged = Vec::new();
        for g in &group {
            let pos = chosen.iter().position(|c| c.0 == *g).unwrap();
            merged.extend(chosen.remove(pos).1);
        }
        match chosen.iter_mut().find(|c| c.0 == m) {
            Some(c) => c.1.extend(merged),
            None => chosen.push((m, merged)),
        }
    }
    let mut found = Vec::new();
    for (m, cs) in &chosen {
        let sec: f64 = cs.iter().map(|c| c.2).sum();
        if sec < MIN_SECONDS.max(MIN_SHARE * total) {
            continue;
        }
        let sid = cands[*m];
        let (_, off, sc, _) = *SOURCES.iter().find(|s| s.0 == sid).unwrap();
        let named = match sc {
            "ionian" => "major",
            "aeolian" => "minor",
            x => x,
        };
        let pc = (tonic + off) % 12;
        let mut ordered = cs.clone();
        ordered.sort_by(|a, b| b.2.partial_cmp(&a.2).unwrap());
        found.push(Borrow {
            from: nt::mode_name(pc, named),
            tonic: nt::mode_tonic(pc, named).to_string(),
            mode: sc.to_string(),
            chords: ordered.iter().map(|c| c.0.clone()).collect(),
            seconds: np::py_round(sec, 1),
            share: np::py_round(sec / total, 3),
        });
    }
    found.sort_by(|a, b| b.seconds.partial_cmp(&a.seconds).unwrap());
    found.truncate(MAX_MODES);
    found
}

pub fn borrow_note(found: &[Borrow]) -> Option<String> {
    if found.is_empty() {
        None
    } else {
        Some(format!("May borrow from {}", found.iter().map(|f| f.from.as_str()).collect::<Vec<_>>().join(", ")))
    }
}

// ------------------------------------------------------------------------------------------------ the key section

#[derive(Debug, Clone, Serialize)]
pub struct Candidate {
    pub key: String,
    pub p: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct EstimatorVote {
    pub name: String,
    pub key: String,
    pub p: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct KeySection {
    pub start: f64,
    pub end: f64,
    pub key: String,
    pub display: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Modulation {
    pub t: f64,
    pub from: String,
    pub to: String,
    pub kind: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct KeyDisplay {
    pub key: String,
    pub note: Option<String>,
    pub ambiguity_text: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Key {
    pub tonic: String,
    pub scale: String,
    pub mode: Option<String>,
    pub confidence: f64,
    pub ambiguous: bool,
    pub camelot: String,
    pub open_key: String,
    pub candidates: Vec<Candidate>,
    pub sections: Vec<KeySection>,
    pub modulations: Vec<Modulation>,
    pub borrows: Vec<Borrow>,
    pub estimators: Vec<EstimatorVote>,
    pub source: String,
    pub display: KeyDisplay,
    #[serde(skip)]
    pub tonic_pc: usize,
    #[serde(skip)]
    pub mode_id: String,
}

fn title(m: &str) -> String {
    m.split('_').map(|w| {
        let mut c = w.chars();
        match c.next() {
            Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
            None => String::new(),
        }
    }).collect::<Vec<_>>().join(" ")
}

pub struct KeyInputs<'a> {
    pub centsible: &'a KeyDecision,
    pub skey: Option<Vec<f64>>,
    pub beat_chroma: &'a [[f64; 12]],
    pub beat_energy: &'a [f64],
    pub beats: &'a [f64],
    pub downbeats: &'a [f64],
    pub duration: f64,
}

/// key/stage.py: fuse, mode, sections, display. Chords get their roman numerals / spelling and the borrows afterwards (annotate).
pub fn fused_key(inp: &KeyInputs) -> Key {
    let g = global_chroma(inp.beat_chroma, Some(inp.beat_energy));
    let as_scale = profile_probs(&inp.centsible.chroma_scale, "albrecht_shanahan");
    let as_beat = profile_probs(&g, "albrecht_shanahan");
    let cent = inp.centsible.key_prob.to_vec();
    let (p, source) = match &inp.skey {
        Some(sk) => {
            let est = [("centsible", cent.clone()), ("skey", sk.clone()), ("albrecht_shanahan@scale", as_scale.clone()), ("albrecht_shanahan@beat", as_beat.clone())];
            (fuse(&est), "fusion")
        }
        None => (cent.clone(), "centsible (S-KEY not available)"),
    };
    let mut order: Vec<usize> = (0..24).collect();
    order.sort_by(|a, b| p[*b].partial_cmp(&p[*a]).unwrap().then(a.cmp(b)));
    let top = order[0];
    let (tonic, minor) = (top % 12, top >= 12);
    let mode = centsible_mode(inp.centsible, tonic, minor);
    let cands: Vec<Candidate> = order[..3].iter().map(|&i| Candidate { key: nt::key_name(i), p: np::py_round(p[i], 3) }).collect();
    let ambiguous = p[top] < AMBIGUOUS_P;
    let (kd_key, kd_note) = nt::key_display(tonic, mode);
    let amb = if ambiguous { Some(format!("ambiguous between {} and {}", cands[0].key, cands[1].key)) } else { None };
    let mut display = KeyDisplay { key: kd_key, note: kd_note, ambiguity_text: amb };
    let mut sections = Vec::new();
    let mut modulations = Vec::new();
    if inp.downbeats.len() > WINDOW_BARS + 2 && !inp.beats.is_empty() {
        let bar = median(&np::diff(inp.downbeats));
        let n = inp.beats.len().min(inp.beat_chroma.len());
        let wk = window_keys(&inp.beat_chroma[..n], &inp.beats[..n], inp.downbeats);
        let secs = sections_from_windows(&wk, inp.duration, bar);
        if secs.len() >= 2 {
            // each section shows plain major / minor (the chroma templates never clear MODE_MARGIN, eval/REPORT.md 3.3)
            let modes: Vec<&str> = secs.iter().map(|s| if s.k >= 12 { "minor" } else { "major" }).collect();
            let tuples: Vec<(f64, f64, usize, &str)> = secs.iter().zip(&modes).map(|(s, m)| (s.start, s.end, s.k % 12, *m)).collect();
            let (k, n2) = nt::sections_display(&tuples);
            display.key = k.unwrap_or(display.key);
            display.note = n2;
            for (i, w) in secs.windows(2).enumerate() {
                modulations.push(Modulation {
                    t: np::py_round(w[1].start, 2),
                    from: nt::house_key(w[0].k % 12, modes[i]),
                    to: nt::house_key(w[1].k % 12, modes[i + 1]),
                    kind: modulation_kind(w[0].k, w[1].k).into(),
                });
            }
            sections = secs
                .iter()
                .zip(&modes)
                .map(|(s, m)| KeySection { start: np::py_round(s.start, 2), end: np::py_round(s.end, 2), key: nt::mode_name(s.k % 12, m), display: nt::house_key(s.k % 12, m) })
                .collect();
        }
    }
    let arg = |v: &[f64]| { let i = np::argmax(v); (i, v[i]) };
    let mut estimators = Vec::new();
    let mut push = |name: &str, v: &[f64]| {
        let (i, pp) = arg(v);
        estimators.push(EstimatorVote { name: name.into(), key: nt::key_name(i), p: np::py_round(pp, 3) });
    };
    push("centsible", &cent);
    if let Some(sk) = &inp.skey {
        push("skey", sk);
    }
    push("albrecht_shanahan@scale", &as_scale);
    push("albrecht_shanahan@beat", &as_beat);
    push("temperley@scale", &profile_probs(&inp.centsible.chroma_scale, "temperley"));
    push("fusion", &p);
    Key {
        tonic: nt::key_tonic(tonic, mode).into(),
        scale: if minor { "minor".into() } else { "major".into() },
        mode: if nt::is_plain(mode) { None } else { Some(title(mode)) },
        confidence: np::py_round(p[top], 3),
        ambiguous,
        camelot: nt::camelot(tonic, mode),
        open_key: nt::open_key(tonic, mode),
        candidates: cands,
        sections,
        modulations,
        borrows: vec![],
        estimators,
        source: source.into(),
        display,
        tonic_pc: tonic,
        mode_id: mode.into(),
    }
}

// ------------------------------------------------------------------------------------------- tonic and mode from the chords
// eval/key_chord_mode.py, REPORT.md 3.5 (catalog: key exact 47.3 -> 49.1 %, mode 38.3 -> 44.5 %; nested CV 48.5 / 42.2 %)

/// The chord the song lives on becomes the tonic when it holds HOME_MIN of the chord time and HOME_RATIO times the fused tonic's,
/// and at least HOME_VOTES of the four plain key estimators name it as the tonic too. (Beautiful Liar: G 62 % of the chord time,
/// C 4 %, four estimators say G and S-KEY's C Minor carried the fusion. MANNEQUIN has the same chords, one vote, and stays C.)
const HOME_MIN: f64 = 0.3;
const HOME_RATIO: f64 = 2.0;
const HOME_VOTES: usize = 3;
const HOME_ESTIMATORS: [&str; 4] = ["centsible", "albrecht_shanahan@scale", "albrecht_shanahan@beat", "temperley@scale"];
/// A b2 on top of the tonic names the mode (Phrygian, Locrian, Phrygian Dominant) when it carries ALT_MIN of the chord tones and
/// ALT_FRAC of it and the natural 2 together. The other modes are left to the borrow note: a natural 7 / 6 in minor or a b7 in
/// major is usually one borrowed chord, and naming a mode from it was wrong on the catalog.
const ALT_MIN: f64 = 0.04;
const ALT_FRAC: f64 = 0.7;

struct ChordEvidence {
    root: [f64; 12],
    root_major: [f64; 12],
    root_minor: [f64; 12],
    tones: [f64; 12],
}

fn chord_evidence(segs: &[Segment]) -> Option<ChordEvidence> {
    let mut e = ChordEvidence { root: [0.0; 12], root_major: [0.0; 12], root_minor: [0.0; 12], tones: [0.0; 12] };
    for s in segs {
        let Some((r, q, _)) = nt::split_chord(&s.chord) else { continue };
        let d = s.end - s.start;
        e.root[r] += d;
        match q.as_str() {
            "" | "7" | "maj7" | "6" | "9" | "add9" => e.root_major[r] += d,
            "m" | "m7" | "m6" => e.root_minor[r] += d,
            _ => {}
        }
        let ivs: &[usize] = chords::quality(&q).map(|x| x.1).unwrap_or(&[0, 4, 7]);
        for (j, i) in ivs.iter().enumerate() {
            e.tones[(r + i) % 12] += d * if j < 3 { 1.0 } else { 0.5 };
        }
    }
    let tot: f64 = e.root.iter().sum();
    let tt: f64 = e.tones.iter().sum();
    if tot <= 0.0 || tt <= 0.0 {
        return None;
    }
    for i in 0..12 {
        e.root[i] /= tot;
        e.root_major[i] /= tot;
        e.root_minor[i] /= tot;
        e.tones[i] /= tt;
    }
    Some(e)
}

/// (tonic, mode id) from the chords, starting from the fused tonic and major / minor; `votes(pc)`: plain estimators naming pc.
fn chord_tonic_mode(e: &ChordEvidence, tonic: usize, minor: bool, votes: &dyn Fn(usize) -> usize) -> (usize, &'static str) {
    let (mut t, mut minor) = (tonic, minor);
    let c = (0..12).fold(0, |b, i| if e.root[i] > e.root[b] { i } else { b });
    if c != t && e.root[c] >= HOME_MIN && e.root[c] >= HOME_RATIO * e.root[t] && votes(c) >= HOME_VOTES {
        t = c;
        minor = e.root_minor[c] > e.root_major[c];
    }
    let h = &e.tones;
    let alt = |nat: usize, alt: usize| {
        let (a, n) = (h[(t + alt) % 12], h[(t + nat) % 12]);
        a >= ALT_MIN && a / (a + n + 1e-9) >= ALT_FRAC
    };
    let mode = if minor {
        if alt(2, 1) {
            if alt(7, 6) { "locrian" } else { "phrygian" }
        } else {
            "minor"
        }
    } else if alt(2, 1) && alt(9, 8) {
        "phrygian_dominant"
    } else {
        "major"
    };
    (t, mode)
}

/// Move the key to the tonic and mode the chords say (one-key songs only: a song with key sections keeps the sections' reading).
pub fn refine_with_chords(key: &mut Key, segs: &[Segment]) {
    if !key.sections.is_empty() {
        return;
    }
    let Some(e) = chord_evidence(segs) else { return };
    let votes = |pc: usize| {
        key.estimators.iter().filter(|v| HOME_ESTIMATORS.contains(&v.name.as_str()) && nt::parse_key(&v.key).map(|k| k.0) == Some(pc)).count()
    };
    let (t, mode) = chord_tonic_mode(&e, key.tonic_pc, key.scale == "minor", &votes);
    if t == key.tonic_pc && nt::is_plain(mode) {
        return; // nothing new from the chords: keep the fused key (and Centsible's mode, if it named one)
    }
    let minor = nt::is_minor(mode);
    key.tonic_pc = t;
    key.mode_id = mode.into();
    key.tonic = nt::key_tonic(t, mode).into();
    key.scale = if minor { "minor".into() } else { "major".into() };
    key.mode = if nt::is_plain(mode) { None } else { Some(title(mode)) };
    key.camelot = nt::camelot(t, mode);
    key.open_key = nt::open_key(t, mode);
    let (k, n) = nt::key_display(t, mode);
    key.display.key = k;
    key.display.note = n;
    // its "probability": the share of the chord time spent on the tonic
    key.estimators.push(EstimatorVote { name: "chords".into(), key: nt::mode_name(t, mode), p: np::py_round(e.root[t], 3) });
}

/// Chords in the key's main sections (borrowing is judged against the key a chord is played in).
fn in_key_sections(s: &Segment, key: &Key) -> bool {
    if key.sections.is_empty() {
        return true;
    }
    let mid = 0.5 * (s.start + s.end);
    for sec in &key.sections {
        if sec.start <= mid && mid < sec.end {
            let pc = nt::parse_pitch(sec.key.split(' ').next().unwrap()).unwrap_or(99);
            let minor = sec.display.split(' ').nth(1).map(|x| x.trim_end_matches('*') == "Minor").unwrap_or(false);
            return pc == key.tonic_pc && minor == (key.scale == "minor");
        }
    }
    false
}

/// Spell / number the chords for the key and fill the key's borrows (and the note's "May borrow from ...").
pub fn annotate_with_chords(key: &mut Key, segs: &mut [Segment]) {
    let mode = key.mode_id.clone();
    chords::annotate(segs, key.tonic_pc, &mode);
    let main: Vec<Segment> = segs.iter().filter(|s| in_key_sections(s, key)).cloned().collect();
    key.borrows = borrowed(&main, key.tonic_pc, &mode);
    if let Some(bn) = borrow_note(&key.borrows) {
        key.display.note = Some(match &key.display.note {
            Some(n) => format!("{n} - {bn}"),
            None => bn,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn segs(prog: &[&str]) -> Vec<Segment> {
        prog.iter()
            .enumerate()
            .map(|(i, c)| {
                let (r, q, _) = nt::split_chord(c).unwrap();
                Segment { start: i as f64 * 2.0, end: i as f64 * 2.0 + 2.0, chord: c.to_string(), triad: String::new(), root: Some(r), quality: q, bass: None, roman: None, conf: 1.0, on_downbeat: true }
            })
            .collect()
    }

    fn rep(p: &[&'static str], n: usize) -> Vec<&'static str> {
        p.iter().cycle().take(p.len() * n).cloned().collect()
    }

    /// the same cases as service/tests/test_key.py::test_borrowed_chords_name_the_nearest_mode
    #[test]
    fn borrows_match_the_service() {
        let cases: Vec<(Vec<&str>, usize, &str, Vec<&str>)> = vec![
            (rep(&["C", "F", "G", "C", "Bb", "F"], 4), 0, "major", vec!["C Mixolydian"]),
            (rep(&["C", "D", "G", "C", "D", "F"], 4), 0, "major", vec!["C Lydian"]),
            (rep(&["C", "Db", "G", "C", "Db", "F"], 4), 0, "major", vec!["C Phrygian"]),
            (rep(&["Am", "D", "Am", "D", "Em", "G"], 4), 9, "minor", vec!["A Dorian"]),
            (rep(&["Am", "Bb", "Am", "Bb", "Dm", "Em"], 4), 9, "minor", vec!["A Phrygian"]),
            (rep(&["Am", "A", "Dm", "Am", "A", "Dm"], 4), 9, "minor", vec!["A Major"]),
            (rep(&["Am", "Dm", "E", "Am", "Dm", "A"], 4), 9, "minor", vec!["A Harmonic Minor", "A Major"]),
            (rep(&["Am", "D", "Am", "D", "Am", "A"], 4), 9, "minor", vec!["A Major"]),
            (rep(&["A", "Dm", "A", "G", "A", "Dm"], 4), 9, "minor", vec![]),
            (rep(&["Am", "A", "Bb", "Bb", "Am", "A"], 4), 9, "minor", vec!["A Phrygian"]),
            (rep(&["C", "Cm", "F", "C", "Cm", "G"], 4), 0, "major", vec![]),
            (rep(&["Am", "D", "Am", "Dbm", "Am", "D"], 4), 9, "minor", vec!["A Major"]),
            (rep(&["C", "Fm", "G", "C", "Fm", "G"], 4), 0, "major", vec!["C Minor"]),
            (rep(&["C", "Bb", "Fm", "Ab", "C", "G"], 4), 0, "major", vec!["C Minor"]),
            (rep(&["C", "E7", "Am", "F", "E7", "Am"], 4), 0, "major", vec!["A Harmonic Minor"]),
            (rep(&["Bdim", "C", "Bdim", "F", "Bdim", "C"], 4), 11, "locrian", vec![]),
            (rep(&["Bdim", "C", "Bdim", "D", "Bdim", "C"], 4), 11, "locrian", vec!["B Phrygian"]),
            (rep(&["Dm", "G", "Dm", "G", "C", "Dm"], 4), 2, "dorian", vec![]),
            (rep(&["Am", "F", "C", "E"], 6), 9, "minor", vec!["A Harmonic Minor"]),
            (rep(&["Am", "E", "Am", "D", "Am", "E"], 4), 9, "minor", vec!["A Harmonic Minor", "A Dorian"]),
        ];
        for (prog, tonic, mode, expect) in cases {
            let got: Vec<String> = borrowed(&segs(&prog), tonic, mode).iter().map(|b| b.from.clone()).collect();
            assert_eq!(got, expect, "{prog:?} in {tonic} {mode}");
        }
        let mut one = rep(&["C", "Am", "F", "G"], 6);
        one.push("Eb");
        assert!(borrowed(&segs(&one), 0, "major").is_empty());
    }
}
