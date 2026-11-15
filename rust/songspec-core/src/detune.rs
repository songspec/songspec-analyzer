//! Detune (service/songspec_analyzer/detune/__init__.py): Centsible's reading is the answer whenever it has one; when Centsible
//! has too little steady tonal evidence, the 120-bin CQT chroma answers with a 5-cent interval. Drift: Centsible's detune curve
//! over 10 s windows (hop 5 s), flagged when the valid windows span more than 5 cents.

use serde::Serialize;

use crate::centsible;
use crate::dsp;
use crate::notation;
use crate::np::{self, median, py_round};

const DRIFT_FLAG_CENTS: f64 = 5.0;
const FALLBACK_CI: f64 = 5.0;
const CROSS_CHECK_SECONDS: f64 = 90.0;

pub fn wrap(c: f64) -> f64 {
    c - 100.0 * ((c + 50.0) / 100.0).floor()
}

fn circ_mean(cents: &[f64], w: &[f64]) -> (f64, f64) {
    let (mut re, mut im, mut sw) = (0.0, 0.0, 0.0);
    for (c, ww) in cents.iter().zip(w) {
        let a = 2.0 * std::f64::consts::PI * c / 100.0;
        re += ww * a.cos();
        im += ww * a.sin();
        sw += ww;
    }
    let (re, im) = (re / sw.max(1e-12), im / sw.max(1e-12));
    (wrap(im.atan2(re) * 100.0 / (2.0 * std::f64::consts::PI)), (re * re + im * im).sqrt())
}

/// CQT at 120 bins per octave over C3-C7 on the harmonic part; spectral peaks located by parabolic interpolation, folded onto one
/// semitone, energy-weighted circular mean (estimators.chroma120).
pub fn chroma120(yh: &[f32]) -> Option<f64> {
    let c = dsp::cqt(yh, 22050.0, 2048, 130.8127826502993, 480, 120, 0.0);
    let (mut cents, mut w) = (Vec::new(), Vec::new());
    for f in 0..c.n_frames {
        let col: Vec<f64> = c.mag[f * 480..(f + 1) * 480].iter().map(|v| *v as f64).collect();
        let top = col.iter().cloned().fold(f64::MIN, f64::max);
        if top <= 1e-6 {
            continue;
        }
        let l: Vec<f64> = col.iter().map(|v| (v + 1e-10).ln()).collect();
        for k in 2..col.len() - 2 {
            if col[k] >= col[k - 1] && col[k] >= col[k + 1] && col[k] >= col[k - 2] && col[k] >= col[k + 2] && col[k] > top * 0.03 {
                let (a, b, cc) = (l[k - 1], l[k], l[k + 1]);
                let den = a - 2.0 * b + cc;
                let frac = if den.abs() > 1e-9 { (0.5 * (a - cc) / den).clamp(-0.5, 0.5) } else { 0.0 };
                cents.push(wrap((k as f64 + frac) * 10.0));
                w.push(col[k] * col[k]);
            }
        }
    }
    if cents.len() < 50 {
        return None;
    }
    Some(circ_mean(&cents, &w).0)
}

#[derive(Debug, Clone, Serialize)]
pub struct DriftPoint {
    pub t: f64,
    pub cents: f64,
    pub ci: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct DetunePart {
    pub start: f64,
    pub end: f64,
    pub cents: f64,
}

/// A step in the drift curve: parts of at least PART_MIN_POINTS drift points (about 20 s) whose tuning differs by
/// PART_MIN_SHIFT cents or more (eval/REPORT.md 2.3).
const PART_MIN_POINTS: usize = 4;
const PART_MIN_SHIFT: f64 = 15.0;

fn tuning_parts(drift: &[DriftPoint], main: f64) -> Vec<DetunePart> {
    let n = drift.len();
    if n < 2 * PART_MIN_POINTS {
        return vec![];
    }
    let c: Vec<f64> = drift.iter().map(|p| wrap(p.cents - main) + main).collect();
    fn split(c: &[f64], a: usize, b: usize, out: &mut Vec<(usize, usize)>) {
        let seg = &c[a..b];
        let n = seg.len();
        if n < 2 * PART_MIN_POINTS {
            out.push((a, b));
            return;
        }
        let tot: f64 = seg.iter().sum();
        let (mut best, mut bi) = (0.0, None);
        let mut acc = 0.0;
        for k in 1..n {
            acc += seg[k - 1];
            if k < PART_MIN_POINTS || n - k < PART_MIN_POINTS {
                continue;
            }
            let g = acc * acc / k as f64 + (tot - acc) * (tot - acc) / (n - k) as f64 - tot * tot / n as f64;
            if g > best {
                best = g;
                bi = Some(k);
            }
        }
        match bi {
            Some(k) if (median(&seg[..k]) - median(&seg[k..])).abs() >= PART_MIN_SHIFT => {
                split(c, a, a + k, out);
                split(c, a + k, b, out);
            }
            _ => out.push((a, b)),
        }
    }
    let mut raw = Vec::new();
    split(&c, 0, n, &mut raw);
    let mut merged: Vec<(usize, usize)> = vec![raw[0]];
    for &(a, b) in &raw[1..] {
        let l = *merged.last().unwrap();
        if (median(&c[l.0..l.1]) - median(&c[a..b])).abs() < PART_MIN_SHIFT {
            merged.last_mut().unwrap().1 = b;
        } else {
            merged.push((a, b));
        }
    }
    if merged.len() < 2 {
        return vec![];
    }
    // each part from the end of the previous one, the first from 0 and the last to the last window's end
    let mut out: Vec<DetunePart> = Vec::new();
    for (i, &(a, b)) in merged.iter().enumerate() {
        let start = if i == 0 { 0.0 } else { out[i - 1].end };
        let end = if i + 1 == merged.len() { drift[b - 1].t + 5.0 } else { 0.5 * (drift[b - 1].t + drift[b].t) };
        out.push(DetunePart { start: py_round(start, 1), end: py_round(end, 1), cents: py_round(median(&c[a..b]), 1) });
    }
    out
}

#[derive(Debug, Clone, Serialize)]
pub struct DetuneDisplay {
    pub display: String,
    pub fix: String,
    pub a4_hz: f64,
    pub key_text: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Detune {
    pub cents: f64,
    pub ci_cents: f64,
    pub confidence: f64,
    pub a4_hz: f64,
    pub drift: Vec<DriftPoint>,
    pub drift_flag: bool,
    pub drift_range_cents: Option<f64>,
    /// the tuning by part, when it steps (a part pitched differently: Hood Politics' intro, about -30 cents against +2); [] otherwise
    pub parts: Vec<DetunePart>,
    pub estimators: Vec<(String, Option<f64>)>,
    pub fusion: String,
    pub display: DetuneDisplay,
    #[serde(skip)]
    pub raw_cents: f64,
}

fn middle(y: &[f32], sr: f64, seconds: f64) -> &[f32] {
    let n = (seconds * sr) as usize;
    if y.len() <= n {
        return y;
    }
    let a = (y.len() - n) / 2;
    &y[a..a + n]
}

/// The detune stage on the mono signal at its own rate. None when nothing tonal can be read.
pub fn measure(mono: &[f32], sr: u32) -> Option<Detune> {
    let t = centsible::analyze_tuning(mono, sr as f64);
    let y22 = dsp::resample_hq(middle(mono, sr as f64, CROSS_CHECK_SECONDS), sr as f64, 22050.0, false);
    let yh = dsp::hpss_signal(&y22, 2.0, true);
    drop(y22);
    let c120 = chroma120(&yh);
    let (cents, ci, rule) = if t.valid {
        (py_round(t.cents, 2), t.ci_cents, "centsible")
    } else if let Some(c) = c120 {
        (c, 3f64.max(FALLBACK_CI), "chroma120 (no Centsible reading)")
    } else {
        return None;
    };
    let disp = notation::detune_display(cents, None);
    Some(Detune {
        cents: py_round(cents, 1),
        ci_cents: py_round(ci, 2),
        confidence: py_round(if t.valid { t.confidence } else { 0.2 }, 3),
        a4_hz: disp.a4_hz,
        drift: vec![],
        drift_flag: t.valid && t.has_drift,
        drift_range_cents: None,
        parts: vec![],
        estimators: vec![
            ("centsible".into(), if t.valid { Some(py_round(t.cents, 2)) } else { None }),
            ("chroma120".into(), c120.map(|c| py_round(c, 2))),
        ],
        fusion: rule.into(),
        display: DetuneDisplay { display: disp.display, fix: disp.fix, a4_hz: disp.a4_hz, key_text: None },
        raw_cents: cents,
    })
}

/// Tuning per 10 s window (hop 5 s): drift points, and the flag when the valid windows span more than 5 cents.
pub fn add_drift(d: &mut Detune, mono: &[f32], sr: u32) {
    let pts = centsible::detune_curve(mono, sr as f64, 10.0, 5.0);
    let good: Vec<&centsible::DetunePoint> = pts.iter().filter(|p| p.valid).collect();
    d.drift = good.iter().map(|p| DriftPoint { t: py_round(p.t, 1), cents: py_round(p.cents, 1), ci: py_round(p.ci, 1) }).collect();
    if good.len() >= 3 {
        let rel: Vec<f64> = good.iter().map(|p| wrap(p.cents - d.cents)).collect();
        let (lo, hi) = (np::percentile(&rel, 5.0), np::percentile(&rel, 95.0));
        d.drift_flag = d.drift_flag || (hi - lo) > DRIFT_FLAG_CENTS;
        d.drift_range_cents = Some(py_round(hi - lo, 1));
    }
    d.parts = tuning_parts(&d.drift, d.cents);
}

/// The "Plays in Bb Minor (+38.0 ¢ above Bb) ..." sentence once the key is known.
pub fn with_key_text(d: &mut Detune, tonic: usize, mode: &str) {
    d.display.key_text = notation::detune_display(d.cents, Some((tonic, mode))).key_text;
}
