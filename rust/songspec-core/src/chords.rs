//! Chords, beat-synchronous: the service's chords/engine.py + vocab.py + btc.py + the annotation helpers in chords/__init__.py.
//! Template evidence (treble / bass chroma from the tuned CQT) blended with BTC's frame probabilities, Viterbi over beats.

use serde::Serialize;
use tract_onnx::prelude::*;

use crate::dsp;
use crate::models::{run1, ModelBytes, PlanCache};
use crate::notation::{self, FLAT_NAMES as FLAT};
use crate::np::{self, median};

pub const SR: f64 = 22050.0;
pub const HOP: usize = 512;
const BINS: usize = 36;
const TEMP: f64 = 0.07;
const BASS_W: f64 = 0.25;
const CHANGE_DOWNBEAT: f64 = 1.5;
const CHANGE_BEAT: f64 = 4.0;
const N_ENERGY: f64 = 0.08;
const PRIOR_SCALE: f64 = 3.0;
pub const BTC_WEIGHT: f64 = 0.25;
const BTC_SCALE: f64 = 3.0;

/// quality: (intervals, weights, log-prior)
pub const QUALITIES: [(&str, &[usize], &[f64], f64); 15] = [
    ("", &[0, 4, 7], &[1.0, 0.9, 0.8], 0.0),
    ("m", &[0, 3, 7], &[1.0, 0.9, 0.8], 0.0),
    ("7", &[0, 4, 7, 10], &[1.0, 0.9, 0.7, 0.7], -0.6),
    ("maj7", &[0, 4, 7, 11], &[1.0, 0.9, 0.7, 0.7], -0.7),
    ("m7", &[0, 3, 7, 10], &[1.0, 0.9, 0.7, 0.7], -0.6),
    ("dim", &[0, 3, 6], &[1.0, 0.9, 0.8], -0.9),
    ("aug", &[0, 4, 8], &[1.0, 0.9, 0.8], -1.2),
    ("sus2", &[0, 2, 7], &[1.0, 0.8, 0.8], -0.9),
    ("sus4", &[0, 5, 7], &[1.0, 0.8, 0.8], -0.8),
    ("6", &[0, 4, 7, 9], &[1.0, 0.9, 0.7, 0.6], -1.0),
    ("m6", &[0, 3, 7, 9], &[1.0, 0.9, 0.7, 0.6], -1.1),
    ("9", &[0, 4, 7, 10, 2], &[1.0, 0.9, 0.6, 0.6, 0.5], -1.1),
    ("add9", &[0, 4, 7, 2], &[1.0, 0.9, 0.7, 0.5], -1.0),
    ("m7b5", &[0, 3, 6, 10], &[1.0, 0.9, 0.8, 0.7], -1.0),
    ("dim7", &[0, 3, 6, 9], &[1.0, 0.9, 0.8, 0.7], -1.3),
];

pub fn quality(q: &str) -> Option<&'static (&'static str, &'static [usize], &'static [f64], f64)> {
    QUALITIES.iter().find(|x| x.0 == q)
}

pub fn triad_of(q: &str) -> &'static str {
    match q {
        "m" | "m7" | "m6" => "m",
        "dim" | "m7b5" | "dim7" => "dim",
        "aug" => "aug",
        _ => "",
    }
}

/// ["N", "C", "Db", ..., "B", "Cm", ...]: N, then each quality over the 12 roots.
pub fn labels() -> Vec<String> {
    let mut v = vec!["N".to_string()];
    for (q, ..) in QUALITIES.iter() {
        for r in FLAT.iter() {
            v.push(format!("{r}{q}"));
        }
    }
    v
}

/// (root, quality) of a label from `labels()`; None for N.
fn label_rq(k: usize) -> Option<(usize, &'static str)> {
    if k == 0 {
        None
    } else {
        Some(((k - 1) % 12, QUALITIES[(k - 1) / 12].0))
    }
}

fn templates() -> (Vec<[f64; 12]>, Vec<f64>) {
    let mut t = vec![[1.0 / 12f64.sqrt(); 12]];
    let mut pri = vec![-0.5];
    for k in 1..=QUALITIES.len() * 12 {
        let (root, q) = label_rq(k).unwrap();
        let (_, iv, w, lp) = quality(q).unwrap();
        let mut v = [0f64; 12];
        for (i, wi) in iv.iter().zip(w.iter()) {
            v[(root + i) % 12] = *wi;
        }
        let n = v.iter().map(|x| x * x).sum::<f64>().sqrt();
        for x in v.iter_mut() {
            *x /= n;
        }
        t.push(v);
        pri.push(*lp);
    }
    (t, pri)
}

/// The 252-bin tuned CQT (36 / octave from C1) of the harmonic signal: shared by the chords and the key's beat chroma.
pub fn tuned_cqt(yh: &[f32], tuning_cents: f64) -> dsp::Cqt {
    dsp::cqt(yh, SR, HOP, 32.70319566257483, BINS * 7, BINS, tuning_cents / 100.0)
}

/// (treble 12 x T, bass 12 x T, energy T) as cqt_chromas in engine.py. Column-major per frame.
fn cqt_chromas(c: &dsp::Cqt) -> (Vec<[f64; 12]>, Vec<[f64; 12]>, Vec<f64>) {
    let mx = c.mag.iter().cloned().fold(0f32, f32::max);
    let mut tr = vec![[0f64; 12]; c.n_frames];
    let mut bs = vec![[0f64; 12]; c.n_frames];
    let mut en = vec![0f64; c.n_frames];
    for f in 0..c.n_frames {
        let row = &c.mag[f * c.n_bins..(f + 1) * c.n_bins];
        let mut e = 0f32;
        for (b, &v) in row.iter().enumerate() {
            let l = (100.0 * v / (mx + 1e-12)).ln_1p() as f64;
            let pc = ((b + 1) / 3) % 12;
            if b < BINS * 2 { bs[f][pc] += l } else { tr[f][pc] += l }
            e += v;
        }
        en[f] = e as f64;
    }
    (tr, bs, en)
}

fn unique_frames(times: &[f64], n: usize) -> Vec<usize> {
    let mut fr: Vec<usize> = times
        .iter()
        .map(|&t| dsp::time_to_frames(t, SR, HOP))
        .filter(|&f| f >= 0 && (f as usize) < n)
        .map(|f| f as usize)
        .collect();
    fr.sort();
    fr.dedup();
    fr
}

/// librosa.util.sync(X, fr, aggregate=np.median, pad=False): one column per interval between consecutive frames.
fn sync_median(x: &[[f64; 12]], fr: &[usize]) -> Vec<[f64; 12]> {
    fr.windows(2)
        .map(|w| {
            let mut out = [0f64; 12];
            for (k, o) in out.iter_mut().enumerate() {
                let v: Vec<f64> = x[w[0]..w[1]].iter().map(|r| r[k]).collect();
                *o = median(&v);
            }
            out
        })
        .collect()
}

pub struct Evidence {
    pub logp: Vec<Vec<f64>>, // K x B
    pub bb: Vec<[f64; 12]>,  // bass chroma per beat
    pub starts: Vec<f64>,
    pub end: f64,
    pub is_down: Vec<bool>,
}

fn discount_bass_harmonics(tb: &mut [[f64; 12]], bb: &[[f64; 12]]) {
    for (t, b) in tb.iter_mut().zip(bb) {
        let bmax = b.iter().cloned().fold(f64::MIN, f64::max) + 1e-12;
        let bn: Vec<f64> = b.iter().map(|v| v / bmax).collect();
        let pb = np::argmax(&bn);
        let clear = ((1.0 - median(&bn)) * 1.5).clamp(0.0, 1.0);
        t[(pb + 7) % 12] *= 1.0 - 0.5 * clear;
        t[pb] *= 1.0 - 0.3 * clear;
    }
}

pub fn evidence(c: &dsp::Cqt, beats: &[f64], downbeats: &[f64], duration: f64) -> Evidence {
    let (treble, bass, energy) = cqt_chromas(c);
    let iv = np::diff(beats);
    let mut edges = beats.to_vec();
    edges.push(duration.min(beats[beats.len() - 1] + median(&iv)));
    let fr = unique_frames(&edges, treble.len());
    let mut tb = sync_median(&treble, &fr);
    let bb = sync_median(&bass, &fr);
    let eb: Vec<f64> = fr.windows(2).map(|w| median(&energy[w[0]..w[1]])).collect();
    let t_frames: Vec<f64> = fr.iter().map(|&f| dsp::frames_to_time(f as i64, SR, HOP)).collect();
    let starts: Vec<f64> = if t_frames.len() > tb.len() { t_frames[..t_frames.len() - 1].to_vec() } else { t_frames };
    let nb = starts.len();
    tb.truncate(nb);
    let bb: Vec<[f64; 12]> = bb.into_iter().take(nb).collect();
    let eb: Vec<f64> = eb.into_iter().take(nb).collect();
    // emissions
    let (tmpl, prior) = templates();
    discount_bass_harmonics(&mut tb, &bb);
    let k = tmpl.len();
    let pos: Vec<f64> = eb.iter().cloned().filter(|&e| e > 0.0).collect();
    let med_e = if pos.is_empty() { 0.0 } else { median(&pos) };
    let mut logp = vec![vec![0f64; nb]; k];
    for b in 0..nb {
        let n = tb[b].iter().map(|v| v * v).sum::<f64>().sqrt() + 1e-12;
        let tn: Vec<f64> = tb[b].iter().map(|v| v / n).collect();
        let bmax = bb[b].iter().cloned().fold(f64::MIN, f64::max) + 1e-12;
        let bn: Vec<f64> = bb[b].iter().map(|v| v / bmax).collect();
        let quiet = if pos.is_empty() { true } else { eb[b] < N_ENERGY * med_e };
        for kk in 0..k {
            let cos: f64 = (0..12).map(|i| tmpl[kk][i] * tn[i]).sum();
            let score = if kk == 0 {
                cos - 0.15 + if quiet { 1.0 } else { 0.0 }
            } else {
                let (root, q) = label_rq(kk).unwrap();
                let tones = quality(q).unwrap().1;
                let tmax = tones.iter().map(|i| bn[(root + i) % 12]).fold(f64::MIN, f64::max);
                cos + BASS_W * (0.6 * bn[root] + 0.4 * tmax)
            };
            logp[kk][b] = score / TEMP + prior[kk] * PRIOR_SCALE;
        }
    }
    let is_down: Vec<bool> = starts.iter().map(|s| downbeats.iter().any(|d| (d - s).abs() < 0.1)).collect();
    Evidence { logp, bb, starts, end: edges[edges.len() - 1], is_down }
}

fn viterbi(logp: &[Vec<f64>], down: &[bool]) -> Vec<usize> {
    let k = logp.len();
    let b = logp[0].len();
    let mut delta: Vec<f64> = (0..k).map(|i| logp[i][0]).collect();
    let mut back = vec![vec![0usize; b]; k];
    for t in 1..b {
        let cost = if down[t] { CHANGE_DOWNBEAT } else { CHANGE_BEAT };
        let best = np::argmax(&delta);
        let mv = delta[best] - cost;
        let mut nd = vec![0f64; k];
        for i in 0..k {
            if mv > delta[i] {
                back[i][t] = best;
                nd[i] = mv + logp[i][t];
            } else {
                back[i][t] = i;
                nd[i] = delta[i] + logp[i][t];
            }
        }
        delta = nd;
    }
    let mut path = vec![0usize; b];
    path[b - 1] = np::argmax(&delta);
    for t in (1..b).rev() {
        path[t - 1] = back[path[t]][t];
    }
    path
}

fn slash(root: usize, q: &str, bass: &[f64; 12]) -> Option<usize> {
    let b = np::argmax(&bass.to_vec());
    let tones: Vec<usize> = quality(q).unwrap().1.iter().map(|i| (root + i) % 12).collect();
    if b != root && tones.contains(&b) && bass[b] > 1.3 * bass[root] { Some(b) } else { None }
}

fn smooth_bass(beat_bass: &[Option<usize>], path: &[usize]) -> Vec<Option<usize>> {
    let mut out = beat_bass.to_vec();
    let mut i = 0;
    while i < path.len() {
        let mut j = i;
        while j + 1 < path.len() && path[j + 1] == path[i] {
            j += 1;
        }
        let mut k = i;
        while k <= j {
            let mut m = k;
            while m + 1 <= j && out[m + 1] == out[k] {
                m += 1;
            }
            if out[k].is_some() && m - k + 1 < 2 {
                for x in out.iter_mut().take(m + 1).skip(k) {
                    *x = None;
                }
            }
            k = m + 1;
        }
        i = j + 1;
    }
    out
}

#[derive(Debug, Clone, Serialize)]
pub struct Segment {
    pub start: f64,
    pub end: f64,
    pub chord: String,
    pub triad: String,
    #[serde(skip)]
    pub root: Option<usize>,
    #[serde(skip)]
    pub quality: String,
    pub bass: Option<String>,
    pub roman: Option<String>,
    pub conf: f64,
    pub on_downbeat: bool,
}

pub fn decode(ev: &Evidence, btc_logp: Option<&[Vec<f64>]>) -> Vec<Segment> {
    let w = BTC_WEIGHT;
    let logp: Vec<Vec<f64>> = match btc_logp {
        Some(bl) if w > 0.0 => ev.logp.iter().zip(bl).map(|(a, b)| a.iter().zip(b).map(|(x, y)| (1.0 - w) * x + w * BTC_SCALE * y).collect()).collect(),
        _ => ev.logp.clone(),
    };
    let k = logp.len();
    let nb = logp[0].len();
    let mut post = vec![vec![0f64; nb]; k];
    for b in 0..nb {
        let mx = (0..k).map(|i| logp[i][b]).fold(f64::MIN, f64::max);
        let s: f64 = (0..k).map(|i| (logp[i][b] - mx).exp()).sum();
        for i in 0..k {
            post[i][b] = (logp[i][b] - mx).exp() / s;
        }
    }
    let path = viterbi(&logp, &ev.is_down);
    let beat_bass: Vec<Option<usize>> = path.iter().enumerate().map(|(b, &kk)| label_rq(kk).and_then(|(r, q)| slash(r, q, &ev.bb[b]))).collect();
    let beat_bass = smooth_bass(&beat_bass, &path);
    let mut segs = Vec::new();
    let mut i = 0;
    while i < path.len() {
        let mut j = i;
        while j + 1 < path.len() && path[j + 1] == path[i] && beat_bass[j + 1] == beat_bass[i] {
            j += 1;
        }
        let rq = label_rq(path[i]);
        let lab = match rq {
            None => "N".to_string(),
            Some((r, q)) => format!("{}{}", FLAT[r], q),
        };
        let name = match beat_bass[i] {
            Some(b) => format!("{lab}/{}", FLAT[b]),
            None => lab.clone(),
        };
        let end = if j + 1 < ev.starts.len() { ev.starts[j + 1] } else { ev.end };
        let conf = (i..=j).map(|b| post[path[i]][b]).sum::<f64>() / (j - i + 1) as f64;
        segs.push(Segment {
            start: np::py_round(ev.starts[i], 3),
            end: np::py_round(end, 3),
            chord: name,
            triad: match rq {
                None => "N".into(),
                Some((r, q)) => format!("{}{}", FLAT[r], triad_of(q)),
            },
            root: rq.map(|x| x.0),
            quality: rq.map(|x| x.1.to_string()).unwrap_or_default(),
            bass: rq.map(|(r, _)| FLAT[beat_bass[i].unwrap_or(r)].to_string()),
            roman: None,
            conf: np::py_round(conf, 3),
            on_downbeat: ev.is_down[i],
        });
        i = j + 1;
    }
    segs
}

// ------------------------------------------------------------------------------------------------ BTC

const BTC_BINS: usize = 144;
const BTC_BPO: usize = 24;
const BTC_HOP: usize = 2048;
const TIMESTEP: usize = 108;
const FRAME_S: f64 = 10.0 / TIMESTEP as f64;

fn btc_mapping() -> Vec<usize> {
    const Q: [&str; 14] = ["m", "", "dim", "aug", "m6", "6", "m7", "m", "maj7", "7", "dim7", "m7b5", "sus2", "sus4"];
    let labs = labels();
    let idx = |l: &str| labs.iter().position(|x| x == l).unwrap();
    let mut m = vec![0usize; 170];
    for i in 0..168 {
        m[i] = idx(&format!("{}{}", FLAT[i / 14], Q[i % 14]));
    }
    m[168] = 0;
    m[169] = 0;
    m
}

/// BTC frame probabilities over our vocabulary: (frame times, frames x K).
pub fn btc_frame_probs(y22: &[f32], tuning_cents: f64, models: &ModelBytes, cache: &PlanCache) -> TractResult<(Vec<f64>, Vec<Vec<f64>>)> {
    let step = (SR * 10.0) as usize;
    let mut feat: Vec<[f32; BTC_BINS]> = Vec::new();
    let mut a = 0;
    while a < y22.len() {
        let seg = &y22[a..(a + step).min(y22.len())];
        if seg.len() < BTC_HOP {
            break;
        }
        let c = dsp::cqt(seg, SR, BTC_HOP, 32.70319566257483, BTC_BINS, BTC_BPO, tuning_cents / 100.0);
        for f in 0..c.n_frames {
            let mut row = [0f32; BTC_BINS];
            for b in 0..BTC_BINS {
                row[b] = ((c.mag[f * BTC_BINS + b] + 1e-6).ln() - models.btc_mean) / models.btc_std;
            }
            feat.push(row);
        }
        a += step;
    }
    let n = feat.len();
    let windows = (n + TIMESTEP - 1) / TIMESTEP;
    let plan = cache.get(2, &models.btc, &[1, TIMESTEP, BTC_BINS])?;
    let map = btc_mapping();
    let k = labels().len();
    let mut out: Vec<Vec<f64>> = Vec::with_capacity(n);
    for w in 0..windows {
        let mut x = vec![0f32; TIMESTEP * BTC_BINS];
        for t in 0..TIMESTEP {
            if let Some(r) = feat.get(w * TIMESTEP + t) {
                x[t * BTC_BINS..(t + 1) * BTC_BINS].copy_from_slice(r);
            }
        }
        let input = tract_ndarray::Array3::from_shape_vec((1, TIMESTEP, BTC_BINS), x)?.into_tensor();
        let o = run1(&plan, input)?;
        let p = o[0].to_plain_array_view::<f32>()?;
        for t in 0..TIMESTEP {
            if out.len() >= n {
                break;
            }
            let mut row = vec![0f64; k];
            for c in 0..170 {
                row[map[c]] += p[[0, t, c]] as f64;
            }
            out.push(row);
        }
    }
    Ok(((0..n).map(|i| i as f64 * FRAME_S).collect(), out))
}

/// BTC's frame probabilities averaged per beat -> K x B log-probabilities.
pub fn btc_beat_logp(times: &[f64], p: &[Vec<f64>], starts: &[f64], end: f64) -> Vec<Vec<f64>> {
    let k = p[0].len();
    let mut edges = starts.to_vec();
    edges.push(end);
    let mut out = vec![vec![0f64; starts.len()]; k];
    for b in 0..starts.len() {
        let sel: Vec<usize> = (0..times.len()).filter(|&i| times[i] >= edges[b] && times[i] < edges[b + 1]).collect();
        let q: Vec<f64> = if !sel.is_empty() {
            (0..k).map(|c| sel.iter().map(|&i| p[i][c]).sum::<f64>() / sel.len() as f64).collect()
        } else {
            p[np::searchsorted(times, edges[b]).min(p.len() - 1)].clone()
        };
        for c in 0..k {
            out[c][b] = (q[c] + 1e-4).ln();
        }
    }
    out
}

// ------------------------------------------------------------------------------------------------ annotation

/// Roman numerals for the key, and chord names in the key's spelling (sharps in sharp keys).
pub fn annotate(segs: &mut [Segment], tonic: usize, mode: &str) {
    for s in segs.iter_mut() {
        if let Some(r) = s.root {
            s.roman = Some(notation::roman(r, &s.quality, tonic, mode));
            s.chord = notation::spell_chord(&s.chord, tonic, mode);
            s.triad = notation::spell_chord(&s.triad, tonic, mode);
            s.bass = s.bass.as_ref().map(|b| notation::spell_chord(b, tonic, mode));
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct SummaryItem {
    pub chord: String,
    pub seconds: f64,
    pub count: usize,
}

pub fn summary(segs: &[Segment]) -> Vec<SummaryItem> {
    let mut items: Vec<(String, f64, usize)> = Vec::new();
    for s in segs.iter().filter(|s| s.chord != "N") {
        match items.iter_mut().find(|x| x.0 == s.chord) {
            Some(x) => {
                x.1 += s.end - s.start;
                x.2 += 1;
            }
            None => items.push((s.chord.clone(), s.end - s.start, 1)),
        }
    }
    // Counter.most_common: by time, ties in first-seen order
    let mut idx: Vec<usize> = (0..items.len()).collect();
    idx.sort_by(|a, b| items[*b].1.partial_cmp(&items[*a].1).unwrap().then(a.cmp(b)));
    idx.into_iter().map(|i| SummaryItem { chord: items[i].0.clone(), seconds: np::py_round(items[i].1, 1), count: items[i].2 }).collect()
}

#[derive(Debug, Clone, Serialize)]
pub struct Progression {
    pub roman: String,
    pub chords: String,
    pub count: usize,
    pub display: String,
}

type Item = (String, String);

fn bar_contents(segs: &[Segment], downbeats: &[f64]) -> Vec<Vec<Item>> {
    let mut downs = downbeats.to_vec();
    if downs.len() >= 2 && !segs.is_empty() {
        let n = downs.len();
        let last_end = segs.iter().map(|s| s.end).fold(f64::MIN, f64::max);
        downs.push((downs[n - 1] + (downs[n - 1] - downs[n - 2])).min(last_end));
    }
    let mut bars = Vec::new();
    for w in downs.windows(2) {
        let (d, e) = (w[0], w[1]);
        let mut items: Vec<Item> = Vec::new();
        for s in segs {
            let ov = s.end.min(e) - s.start.max(d);
            if ov >= 0.25 * (e - d) && s.triad != "N" {
                let it = (s.triad.clone(), s.roman.clone().unwrap_or_else(|| s.triad.clone()));
                if items.last() != Some(&it) {
                    items.push(it);
                }
            }
        }
        bars.push(items);
    }
    bars
}

fn flat(bars: &[Vec<Item>]) -> Vec<Item> {
    let mut out: Vec<Item> = Vec::new();
    for b in bars {
        for it in b {
            if out.last() != Some(it) {
                out.push(it.clone());
            }
        }
    }
    out
}

fn inside(names: &[String], longer: &[String]) -> bool {
    let s = format!("|{}|", names.join("|"));
    let l: Vec<String> = longer.iter().chain(longer.iter()).cloned().collect();
    format!("|{}|", l.join("|")).contains(&s)
}

/// Repeating chord loops over 4, 2 or 1 bars (chords/__init__.progressions).
pub fn progressions(segs: &[Segment], downbeats: &[f64]) -> Vec<Progression> {
    let bars = bar_contents(segs, downbeats);
    let mut found = Vec::new();
    let mut covered: Vec<Vec<String>> = Vec::new();
    for l in [4usize, 2, 1] {
        if bars.len() < l {
            continue;
        }
        // Counter of windows, most_common order (count desc, first-seen asc)
        let mut counts: Vec<(Vec<Vec<Item>>, usize, usize)> = Vec::new();
        for i in 0..=bars.len() - l {
            let w: Vec<Vec<Item>> = bars[i..i + l].to_vec();
            if w.iter().any(|b| b.is_empty()) {
                continue;
            }
            let names: std::collections::HashSet<String> = flat(&w).into_iter().map(|x| x.0).collect();
            if names.len() < 2 {
                continue;
            }
            match counts.iter_mut().find(|x| x.0 == w) {
                Some(x) => x.1 += 1,
                None => counts.push((w, 1, i)),
            }
        }
        counts.sort_by(|a, b| b.1.cmp(&a.1).then(a.2.cmp(&b.2)));
        for (w, _, _) in counts {
            let (mut k, mut c) = (0, 0);
            while k + l <= bars.len() {
                if bars[k..k + l] == w[..] {
                    c += 1;
                    k += l;
                } else {
                    k += 1;
                }
            }
            let fl = flat(&w);
            let names: Vec<String> = fl.iter().map(|x| x.0.clone()).collect();
            if c < 3 || covered.iter().any(|f| inside(&names, f)) {
                continue;
            }
            covered.push(names.clone());
            let r: Vec<String> = fl.iter().map(|x| x.1.clone()).collect();
            found.push(Progression { roman: r.join("-"), chords: names.join("-"), count: c, display: format!("{} ×{}", r.join("–"), c) });
        }
    }
    found.truncate(5);
    found
}
