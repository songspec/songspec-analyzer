//! Tempo: Beat This! + BPM Fixer's cleaning (bpmfix.rs), with the service's additions (service/.../tempo/engine.py): beat
//! switches, a line-fit tempo, sections with steadiness, downbeats on the cleaned grid; and the house BPM display (display/bpm.py).

use serde::Serialize;

use crate::bpmfix::{self as bf, BpmMode, Spec};
use crate::np::{self, diff, median, py_round};

#[derive(Debug, Clone, Serialize)]
pub struct Section {
    pub start: f64,
    pub end: f64,
    pub bpm: f64,
    pub lo: Option<f64>,
    pub hi: Option<f64>,
    pub drift_pct: f64,
    pub steadiness: String,
    pub core_share: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct TempoDisplay {
    pub bpm: Option<String>,
    pub note: Option<String>,
    pub alternates: Vec<String>,
}

/// The contract's `tempo` section.
#[derive(Debug, Clone, Serialize)]
pub struct Tempo {
    pub bpm: Option<f64>,
    pub bpm_raw: f64,
    pub alternates: Vec<f64>,
    pub signature: String,
    pub signature_conf: Option<f64>,
    pub compound: bool,
    pub sections: Vec<Section>,
    pub range: Option<[f64; 2]>,
    pub irregularity_pct: f64,
    pub drift_pct: f64,
    pub feel: String,
    pub beats: Vec<f64>,
    pub downbeats: Vec<f64>,
    pub display: TempoDisplay,
}

pub fn signature_confidence(beats: &[f64], down: Option<&[f64]>, name: &str) -> Option<f64> {
    let down = down?;
    if down.len() < 6 || beats.len() < 12 {
        return None;
    }
    let tol = 0.3 * median(&diff(beats));
    let idx: Vec<i64> = down.iter().map(|d| np::searchsorted(beats, d - tol) as i64).collect();
    let counts: Vec<i64> = idx.windows(2).map(|w| w[1] - w[0]).collect();
    let n: i64 = match name {
        "2/4" | "2/2" => 2,
        "3/4" => 3,
        "4/4" => 4,
        "5/4" | "5/8" => 5,
        "6/8" => 6,
        "7/8" | "7/4" => 7,
        "9/8" => 9,
        "12/8" => 12,
        _ => return None,
    };
    let ok = counts.iter().filter(|&&c| c == n || (c == 8 && n == 4)).count();
    Some(py_round(ok as f64 / counts.len() as f64, 3))
}

/// Beat period from a robust straight-line fit of beat time against beat index (trims outliers twice).
pub fn line_period(t: &[f64]) -> Option<f64> {
    if t.len() < 4 {
        return if t.len() > 1 { Some(median(&diff(t))) } else { None };
    }
    let idx: Vec<f64> = (0..t.len()).map(|i| i as f64).collect();
    let mut keep = vec![true; t.len()];
    let mut a = 0.0;
    for _ in 0..3 {
        let (xi, yi): (Vec<f64>, Vec<f64>) = idx.iter().zip(t).zip(&keep).filter(|(_, k)| **k).map(|((x, y), _)| (*x, *y)).unzip();
        let (aa, bb) = np::polyfit1(&xi, &yi);
        a = aa;
        let r: Vec<f64> = idx.iter().zip(t).map(|(i, y)| y - (aa * i + bb)).collect();
        let rk: Vec<f64> = r.iter().zip(&keep).filter(|(_, k)| **k).map(|(v, _)| *v).collect();
        let mr = median(&rk);
        let mad = median(&rk.iter().map(|v| (v - mr).abs()).collect::<Vec<_>>()) + 1e-6;
        let thr = (4.0 * 1.4826 * mad).max(0.004);
        let new: Vec<bool> = r.iter().map(|v| v.abs() <= thr).collect();
        let cnt = new.iter().filter(|x| **x).count();
        if (cnt as f64) < 4f64.max(0.5 * t.len() as f64) || new == keep {
            break;
        }
        keep = new;
    }
    Some(a)
}

fn segment_intervals(iv: &[f64], min_beats: usize, min_shift: f64) -> Vec<(usize, usize)> {
    if iv.len() < 2 * min_beats {
        return vec![(0, iv.len())];
    }
    let lv: Vec<f64> = bf::rolling_median(iv, 5).iter().map(|x| x.ln()).collect();
    fn split(lv: &[f64], a: usize, b: usize, min_beats: usize, min_shift: f64) -> Vec<(usize, usize)> {
        let seg = &lv[a..b];
        let n = seg.len();
        if n < 2 * min_beats {
            return vec![(a, b)];
        }
        let mut cs = vec![0.0; n];
        let mut acc = 0.0;
        for i in 0..n {
            acc += seg[i];
            cs[i] = acc;
        }
        let tot = cs[n - 1];
        let (mut best, mut bi) = (0.0, None);
        for k in min_beats..n - min_beats {
            let (l_n, r_n) = (k as f64, (n - k) as f64);
            let (l_s, r_s) = (cs[k - 1], tot - cs[k - 1]);
            let gain = l_s * l_s / l_n + r_s * r_s / r_n - tot * tot / n as f64;
            if gain > best {
                best = gain;
                bi = Some(k);
            }
        }
        let Some(bi) = bi else { return vec![(a, b)] };
        let (left, right) = (median(&seg[..bi]), median(&seg[bi..]));
        if (left - right).abs() < min_shift.ln_1p() {
            return vec![(a, b)];
        }
        let mut out = split(lv, a, a + bi, min_beats, min_shift);
        out.extend(split(lv, a + bi, b, min_beats, min_shift));
        out
    }
    let segs = split(&lv, 0, lv.len(), min_beats, min_shift);
    let mut merged: Vec<(usize, usize)> = vec![segs[0]];
    for &(a, b) in &segs[1..] {
        let last = *merged.last().unwrap();
        if (median(&lv[last.0..last.1]) - median(&lv[a..b])).abs() < min_shift.ln_1p() {
            merged.last_mut().unwrap().1 = b;
        } else {
            merged.push((a, b));
        }
    }
    merged
}

const SWITCH_MIN_BEATS: usize = 32;
const SWITCH_MIN_SHIFT: f64 = 0.06;

/// Times where the song switches tempo, confirmed by the bar length. [] for the usual one-tempo song.
fn find_switches(raw: &[f64], down: Option<&[f64]>) -> Vec<f64> {
    let iv = diff(raw);
    let Some(down) = down else { return vec![] };
    if iv.len() < 2 * SWITCH_MIN_BEATS || down.len() < 8 {
        return vec![];
    }
    let r = median(&iv);
    let folded: Vec<f64> = iv.iter().map(|v| v * 2f64.powf((r / v).log2().round_ties_even())).collect();
    let segs = segment_intervals(&folded, SWITCH_MIN_BEATS, SWITCH_MIN_SHIFT);
    if segs.len() < 2 {
        return vec![];
    }
    let mut cuts = Vec::new();
    for w in segs.windows(2) {
        let ((a0, a1), (b0, b1)) = (w[0], w[1]);
        let rr = median(&folded[b0..b1]) / median(&folded[a0..a1]);
        let k = 16;
        let near = median(&folded[b0..(b0 + k).min(b1)]) / median(&folded[a0.max(b0.saturating_sub(k))..b0]);
        if near.ln().abs() < 0.7 * rr.ln().abs() {
            continue;
        }
        let (ta, tb) = (raw[a0], raw[b0]);
        let tend = raw[b1.min(raw.len() - 1)];
        let da: Vec<f64> = down.iter().cloned().filter(|&d| d >= ta && d < tb).collect();
        let db: Vec<f64> = down.iter().cloned().filter(|&d| d >= tb && d < tend).collect();
        if da.len() < 4 || db.len() < 4 {
            continue;
        }
        let rb = median(&diff(&db)) / median(&diff(&da));
        let best = [-1.0f64, 0.0, 1.0].iter().map(|kk| ((rb / rr).ln() - kk * 2f64.ln()).abs()).fold(f64::INFINITY, f64::min);
        if best < 0.05 {
            cuts.push(tb);
        }
    }
    cuts
}

fn pipeline(y: &[f32], sr: f64, raw: &[f64], down: Option<&[f64]>, end: f64, spec: &Spec) -> Result<Vec<f64>, bf::BeatError> {
    let mut prep = bf::prepare_anchors(raw, down, end, spec, 80.0, 160.0)?;
    // level guard, exactly as in process()
    let t_ref = bf::pick_reference_period(&prep.anchors);
    let (fixed, fixes) = bf::enforce_level(&prep.anchors, y, sr, t_ref);
    if !fixes.is_empty() {
        prep.anchors = fixed;
    }
    let (real, mask) = bf::refine_beats(&prep.anchors, y, sr);
    Ok(bf::fill_weak_runs(&real, &mask))
}

const LOCAL_WINDOW: usize = 32;
const LOCAL_HOP: usize = 8;
const MODERATE_PCT: f64 = 3.0;
const LIVE_PCT: f64 = 6.0;
const CORE_BAND: f64 = 0.12;

fn local_tempo(beats: &[f64], spec: &Spec) -> Vec<f64> {
    if beats.len() < LOCAL_WINDOW {
        return match line_period(beats) {
            Some(p) => vec![bf::period_to_bpm(p, spec, BpmMode::Felt)],
            None => vec![],
        };
    }
    let mut out = Vec::new();
    let mut i = 0;
    while i + LOCAL_WINDOW <= beats.len() {
        out.push(bf::period_to_bpm(line_period(&beats[i..i + LOCAL_WINDOW]).unwrap(), spec, BpmMode::Felt));
        i += LOCAL_HOP;
    }
    out
}

fn section_stats(sb: &[f64], spec: &Spec) -> Section {
    let line_bpm = bf::period_to_bpm(line_period(sb).unwrap(), spec, BpmMode::Felt);
    let loc = local_tempo(sb, spec);
    let (start, end) = (py_round(sb[0], 3), py_round(sb[sb.len() - 1], 3));
    if loc.len() < 3 {
        return Section { start, end, bpm: py_round(line_bpm, 2), lo: None, hi: None, drift_pct: 0.0, steadiness: "steady".into(), core_share: 1.0 };
    }
    let mode = bf::log_mode(&loc, &vec![1.0; loc.len()]);
    let core: Vec<f64> = loc.iter().cloned().filter(|v| (v / mode).ln().abs() <= CORE_BAND.ln_1p()).collect();
    let (lo, med, hi) = (np::percentile(&core, 10.0), np::percentile(&core, 50.0), np::percentile(&core, 90.0));
    let drift = 100.0 * (hi - lo) / med;
    let steadiness = if drift > LIVE_PCT { "live" } else if drift > MODERATE_PCT { "moderate" } else { "steady" };
    let whole_ok = (line_bpm / med).ln().abs() <= 0.01f64.ln_1p();
    let bpm = if steadiness == "steady" && whole_ok { line_bpm } else { med };
    Section {
        start,
        end,
        bpm: py_round(bpm, 2),
        lo: Some(py_round(lo, 2)),
        hi: Some(py_round(hi, 2)),
        drift_pct: py_round(drift, 2),
        steadiness: steadiness.into(),
        core_share: py_round(core.len() as f64 / loc.len() as f64, 3),
    }
}

pub struct Analysis {
    pub bpm_raw: f64,
    pub signature: String,
    pub signature_conf: Option<f64>,
    pub sections: Vec<Section>,
    pub irregularity_pct: f64,
    pub drift_pct: f64,
    pub feel: String,
    pub beats: Vec<f64>,
    pub downbeats: Vec<f64>,
}

/// engine.analyze: y mono at the file's own rate, the tracker's raw beats / downbeats. `signature`: "Auto" or a name.
pub fn analyze(y: &[f32], sr: f64, raw: &[f64], down_raw: &[f64], signature: &str) -> Result<Analysis, String> {
    let mut raw: Vec<f64> = raw.to_vec();
    let mut down_v: Vec<f64> = down_raw.to_vec();
    raw.sort_by(|a, b| a.partial_cmp(b).unwrap());
    down_v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    if down_v.len() > raw.len() {
        std::mem::swap(&mut raw, &mut down_v);
    }
    if raw.len() < 8 {
        return Err("No steady beat found in this file.".into());
    }
    let down: Option<&[f64]> = if down_v.len() >= 4 { Some(&down_v) } else { None };
    let duration = y.len() as f64 / sr;
    let sig_auto = signature == "Auto";
    let sig = if sig_auto { bf::infer_signature(&raw, down).map(|g| g.0).unwrap_or("4/4") } else { signature };
    let spec = bf::sig_spec(sig).ok_or_else(|| format!("unknown time signature {sig}"))?;
    let switches = find_switches(&raw, down);
    let beats: Vec<f64> = if switches.is_empty() {
        pipeline(y, sr, &raw, down, duration, &spec).map_err(|e| format!("No steady beat found in this file ({})", e.0))?
    } else {
        let mut edges = vec![0.0];
        edges.extend(switches.iter().cloned());
        edges.push(duration + 1.0);
        let mut all: Vec<f64> = Vec::new();
        for w in edges.windows(2) {
            let (a, b) = (w[0], w[1]);
            let b_ = b.min(duration);
            let ys = &y[(a * sr) as usize..((b_ * sr) as usize).min(y.len())];
            let r_: Vec<f64> = raw.iter().filter(|&&t| t >= a && t < b).map(|t| t - a).collect();
            let d_: Vec<f64> = down_v.iter().filter(|&&t| t >= a && t < b).map(|t| t - a).collect();
            let d_opt = if d_.len() >= 4 { Some(&d_[..]) } else { None };
            let part = match pipeline(ys, sr, &r_, d_opt, b_ - a, &spec) {
                Ok(p) => p,
                Err(_) => {
                    if r_.is_empty() { r_.clone() } else { bf::refine_beats(&r_, ys, sr).0 }
                }
            };
            all.extend(part.iter().map(|t| t + a).filter(|&t| t >= a && t < b));
        }
        let mut out = vec![all[0]];
        for w in all.windows(2) {
            if w[1] - w[0] > 1e-3 {
                out.push(w[1]);
            }
        }
        out
    };
    let iv = diff(&beats);
    if iv.len() < 6 {
        return Err("No steady beat found in this file.".into());
    }
    let mut edges = vec![0.0];
    edges.extend(switches.iter().cloned());
    edges.push(duration + 1.0);
    let mut sections: Vec<Section> = Vec::new();
    for w in edges.windows(2) {
        let (a, b) = (w[0], w[1]);
        let sb: Vec<f64> = beats.iter().cloned().filter(|&t| t >= a && t < b).collect();
        if sb.len() < 8 {
            continue;
        }
        let s = section_stats(&sb, &spec);
        if let Some(last) = sections.last() {
            if (s.bpm / last.bpm).ln().abs() < 1.03f64.ln() {
                let st = last.start;
                let sb2: Vec<f64> = beats.iter().cloned().filter(|&t| t >= st - 1e-6 && t < b).collect();
                let merged = section_stats(&sb2, &spec);
                *sections.last_mut().unwrap() = merged;
                continue;
            }
        }
        sections.push(s);
    }
    if sections.is_empty() {
        return Err("No steady beat found in this file.".into());
    }
    let main = sections.iter().fold(&sections[0], |m, s| if s.end - s.start > m.end - m.start { s } else { m }).clone();
    let irr = bf::irregularity(&beats);
    let live = sections.iter().any(|s| s.steadiness == "live");
    // downbeats on the cleaned grid: the nearest anchor to each tracker downbeat (within a quarter beat)
    let mut dbs: Vec<f64> = Vec::new();
    if let Some(d) = down {
        let miv = median(&iv);
        for &dt in d {
            let j = np::searchsorted(&beats, dt).clamp(1, beats.len() - 1);
            let k = if (beats[j] - dt).abs() < (beats[j - 1] - dt).abs() { j } else { j - 1 };
            if (beats[k] - dt).abs() <= 0.25 * miv && dbs.last().map_or(true, |&l| beats[k] > l) {
                dbs.push(beats[k]);
            }
        }
    }
    Ok(Analysis {
        bpm_raw: py_round(main.bpm, 2),
        signature: sig.to_string(),
        signature_conf: if sig_auto { signature_confidence(&raw, down, sig) } else { Some(1.0) },
        sections,
        irregularity_pct: py_round(irr, 2),
        drift_pct: main.drift_pct,
        feel: if live { "live".into() } else { "grid".into() },
        beats: beats.iter().map(|t| py_round(*t, 3)).collect(),
        downbeats: dbs.iter().map(|t| py_round(*t, 3)).collect(),
    })
}

// ---------------------------------------------------------------------------------------------------- house BPM (display/bpm.py)

pub const COMPOUND: [&str; 3] = ["6/8", "9/8", "12/8"];
const LIVE_NOTE: &str = "BPM inconsistent - played live";
const MODERATE_NOTE: &str = "BPM moderately inconsistent";

/// (bpm, compound). raw = the felt tempo (dotted quarters in compound meters).
pub fn house_bpm(raw: f64, signature: &str) -> (Option<f64>, bool) {
    if !raw.is_finite() || raw <= 0.0 {
        return (None, false);
    }
    if COMPOUND.contains(&signature) {
        return (Some(py_round(raw * 3.0, 2)), true);
    }
    let mut b = raw;
    while py_round(b, 1) < 79.95 {
        b *= 2.0;
    }
    while py_round(b, 1) >= 159.95 {
        b /= 2.0;
    }
    (Some(py_round(b, 2)), false)
}

/// 114.02 -> "114", 82.5 -> "82.5", 127.96 -> "128".
pub fn fmt_bpm(b: f64) -> String {
    let r = b.round_ties_even();
    if (b - r).abs() <= 0.05 + 1e-9 {
        format!("{}", r as i64)
    } else {
        format!("{:.1}", b)
    }
}

fn half(x: f64) -> f64 {
    (x * 2.0).round_ties_even() / 2.0
}

fn section_text(sec: &Section, signature: &str) -> String {
    let (Some(bpm), _) = house_bpm(sec.bpm, signature) else { return "N/A".into() };
    let star = if signature != "4/4" { "*" } else { "" };
    if sec.steadiness == "live" {
        if let (Some(lo), Some(hi)) = (sec.lo, sec.hi) {
            let f = bpm / sec.bpm;
            let (l, h) = (half(lo * f), half(hi * f));
            if fmt_bpm(l) != fmt_bpm(h) {
                return format!("~{} - {}{star}", fmt_bpm(l), fmt_bpm(h));
            }
        }
    }
    if sec.steadiness == "live" || sec.steadiness == "moderate" {
        return format!("~{}{star}", fmt_bpm(bpm));
    }
    format!("{}{star}", fmt_bpm(bpm))
}

fn where_(i: usize, n: usize, sec: &Section) -> String {
    if n == 1 {
        return String::new();
    }
    if i == 0 {
        return "Intro ".into();
    }
    if i == n - 1 {
        return "Outro ".into();
    }
    let m = |t: f64| format!("{}:{:02}", t as i64 / 60, t as i64 % 60);
    format!("{}-{} ", m(sec.start), m(sec.end))
}

/// tempo/__init__.section_from_engine + display/bpm.bpm_display.
pub fn section_from_engine(r: &Analysis) -> Tempo {
    let sig = r.signature.as_str();
    let (bpm, compound) = house_bpm(r.bpm_raw, sig);
    let star = if sig != "4/4" { "*" } else { "" };
    let mut display = bpm.map(|b| fmt_bpm(b) + star);
    let mut notes: Vec<String> = Vec::new();
    let mut range = None;
    if !r.sections.is_empty() {
        display = Some(r.sections.iter().map(|s| section_text(s, sig)).collect::<Vec<_>>().join(" / "));
        let n = r.sections.len();
        for (i, s) in r.sections.iter().enumerate() {
            if s.steadiness == "live" {
                notes.push(where_(i, n, s) + LIVE_NOTE);
            } else if s.steadiness == "moderate" {
                notes.push(where_(i, n, s) + MODERATE_NOTE);
            }
        }
        if n == 1 && r.sections[0].steadiness == "live" {
            if let (Some(b), Some(lo), Some(hi)) = (bpm, r.sections[0].lo, r.sections[0].hi) {
                let f = b / r.sections[0].bpm;
                range = Some([py_round(lo * f, 2), py_round(hi * f, 2)]);
            }
        }
    }
    if sig != "4/4" {
        notes.push(format!("Played in {sig} time signature"));
    }
    let notes: Vec<String> = notes
        .into_iter()
        .map(|n| {
            let mut c = n.chars();
            match c.next() {
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                None => n,
            }
        })
        .collect();
    let (alts, alt_text) = if COMPOUND.contains(&sig) {
        let a0 = house_bpm(r.bpm_raw * 2.0, "4/4").0.unwrap_or(0.0);
        let a1 = py_round(r.bpm_raw * 1.5, 2);
        (vec![a0, a1], vec![format!("{} (in 4/4)", fmt_bpm(a0)), format!("{} (quarter notes)", fmt_bpm(a1))])
    } else {
        let b = bpm.unwrap_or(0.0);
        let a = vec![py_round(b / 2.0, 2), py_round(b * 2.0, 2)];
        let t = vec![fmt_bpm(a[0]), fmt_bpm(a[1])];
        (a, t)
    };
    Tempo {
        bpm,
        bpm_raw: r.bpm_raw,
        alternates: alts,
        signature: sig.to_string(),
        signature_conf: r.signature_conf,
        compound,
        sections: r.sections.clone(),
        range,
        irregularity_pct: r.irregularity_pct,
        drift_pct: r.drift_pct,
        feel: r.feel.clone(),
        beats: r.beats.clone(),
        downbeats: r.downbeats.clone(),
        display: TempoDisplay { bpm: display, note: if notes.is_empty() { None } else { Some(notes.join(". ")) }, alternates: alt_text },
    }
}
