//! Signal processing: Rust ports of the librosa 1.0.0 / torchaudio functions the Python service calls, written to give the same
//! numbers (same windows, padding, filter design, rounding and float32 storage where librosa uses it).
//!
//! Exact ports: stft, istft, hpss (median filters, soft masks), mel filters, melspectrogram, power_to_db, onset_strength,
//! beat_track (Ellis DP), torchaudio's sinc resampler, the CQT filter bank and chroma folding.
//! Close, not bit-exact: `resample_hq` stands in for soxr's "HQ" resampler (librosa.resample's default and Beat This!'s
//! front end): a linear-phase Kaiser-windowed sinc with the same pass band (91.3 % of the lower Nyquist) and stop band.

use std::sync::Arc;

use realfft::{ComplexToReal, RealFftPlanner, RealToComplex};
use rustfft::num_complex::Complex32;

pub type C32 = Complex32;

// ------------------------------------------------------------------------------------------------------------- windows, FFT

/// scipy.signal.get_window("hann", n, fftbins=True): the periodic Hann window.
pub fn hann_periodic(n: usize) -> Vec<f32> {
    (0..n).map(|k| (0.5 - 0.5 * (2.0 * std::f64::consts::PI * k as f64 / n as f64).cos()) as f32).collect()
}

pub fn ones(n: usize) -> Vec<f32> {
    vec![1.0; n]
}

struct Fft {
    fwd: Arc<dyn RealToComplex<f32>>,
    inv: Arc<dyn ComplexToReal<f32>>,
}

fn plan(n: usize) -> Fft {
    let mut p = RealFftPlanner::<f32>::new();
    Fft { fwd: p.plan_fft_forward(n), inv: p.plan_fft_inverse(n) }
}

// ------------------------------------------------------------------------------------------------------------- STFT

/// A complex spectrogram, frame-major: `data[f * n_bins + k]`.
#[derive(Clone)]
pub struct Spec {
    pub n_fft: usize,
    pub hop: usize,
    pub n_frames: usize,
    pub n_bins: usize,
    pub data: Vec<C32>,
}

impl Spec {
    pub fn frame(&self, f: usize) -> &[C32] {
        &self.data[f * self.n_bins..(f + 1) * self.n_bins]
    }
}

/// librosa.stft(y, n_fft, hop_length, window, center=True, pad_mode="constant"): zero padding of n_fft/2 on both sides,
/// 1 + len(y) // hop frames.
pub fn stft(y: &[f32], n_fft: usize, hop: usize, window: &[f32]) -> Spec {
    let pad = n_fft / 2;
    let n_frames = if y.len() + 2 * pad >= n_fft { 1 + (y.len() + 2 * pad - n_fft) / hop } else { 0 };
    let n_bins = n_fft / 2 + 1;
    let fft = plan(n_fft);
    let mut data = vec![C32::new(0.0, 0.0); n_frames * n_bins];
    let work = |f: usize, out: &mut [C32]| {
        let mut buf = vec![0f32; n_fft];
        let start = (f * hop) as isize - pad as isize;
        for i in 0..n_fft {
            let j = start + i as isize;
            if j >= 0 && (j as usize) < y.len() {
                buf[i] = y[j as usize] * window[i];
            }
        }
        let mut scratch = fft.fwd.make_scratch_vec();
        fft.fwd.process_with_scratch(&mut buf, out, &mut scratch).unwrap();
    };
    for_each_chunk(&mut data, n_bins, work);
    Spec { n_fft, hop, n_frames, n_bins, data }
}

/// Run `f(index, chunk)` over equal chunks of `data`, in parallel when the `rayon` feature is on.
pub fn for_each_chunk<T: Send, F: Fn(usize, &mut [T]) + Sync + Send>(data: &mut [T], size: usize, f: F) {
    #[cfg(feature = "rayon")]
    {
        use rayon::prelude::*;
        data.par_chunks_mut(size).enumerate().for_each(|(i, c)| f(i, c));
    }
    #[cfg(not(feature = "rayon"))]
    {
        data.chunks_mut(size).enumerate().for_each(|(i, c)| f(i, c));
    }
}

/// librosa.istft(S, hop_length, window="hann", center=True, length=length).
pub fn istft(s: &Spec, window: &[f32], length: usize) -> Vec<f32> {
    let n_fft = s.n_fft;
    let hop = s.hop;
    let padded = length + 2 * (n_fft / 2);
    let n_frames = s.n_frames.min((padded + hop - 1) / hop);
    let total = n_fft + hop * n_frames.saturating_sub(1);
    let fft = plan(n_fft);
    let mut out = vec![0f32; total];
    let mut wss = vec![0f32; total];
    let mut spec = vec![C32::new(0.0, 0.0); s.n_bins];
    let mut buf = vec![0f32; n_fft];
    let mut scratch = fft.inv.make_scratch_vec();
    let sq: Vec<f32> = window.iter().map(|w| w * w).collect();
    for f in 0..n_frames {
        spec.copy_from_slice(s.frame(f));
        spec[0].im = 0.0;
        let last = s.n_bins - 1;
        spec[last].im = 0.0;
        fft.inv.process_with_scratch(&mut spec, &mut buf, &mut scratch).unwrap();
        let o = f * hop;
        for i in 0..n_fft {
            out[o + i] += window[i] * buf[i] / n_fft as f32;
            wss[o + i] += sq[i];
        }
    }
    let tiny = f32::MIN_POSITIVE;
    let start = n_fft / 2;
    (0..length)
        .map(|i| {
            let j = start + i;
            if j >= total {
                return 0.0;
            }
            if wss[j] > tiny { out[j] / wss[j] } else { out[j] }
        })
        .collect()
}

// ------------------------------------------------------------------------------------------------------------- HPSS

fn reflect(i: isize, n: usize) -> usize {
    // scipy.ndimage mode="reflect": (d c b a | a b c d | d c b a)
    let n = n as isize;
    let mut i = i;
    loop {
        if i < 0 {
            i = -i - 1;
        } else if i >= n {
            i = 2 * n - i - 1;
        } else {
            return i as usize;
        }
    }
}

fn median_in_place(v: &mut [f32]) -> f32 {
    let m = v.len() / 2;
    let (_, x, _) = v.select_nth_unstable_by(m, |a, b| a.partial_cmp(b).unwrap());
    *x
}

/// librosa.decompose.hpss on a complex STFT with kernel 31, power 2 and the given margin: (harmonic, percussive) spectrograms.
pub fn hpss(s: &Spec, kernel: usize, margin: f32, want_harm: bool) -> Spec {
    let (nf, nb) = (s.n_frames, s.n_bins);
    let mag: Vec<f32> = s.data.iter().map(|c| c.norm()).collect();
    let half = (kernel / 2) as isize;
    // harmonic: median over time (per bin); percussive: median over frequency (per frame)
    let mut harm = vec![0f32; nf * nb];
    for_each_chunk(&mut harm, nb, |f, row| {
        let mut w = vec![0f32; kernel];
        for k in 0..nb {
            for (j, d) in (-half..=half).enumerate() {
                w[j] = mag[reflect(f as isize + d, nf) * nb + k];
            }
            row[k] = median_in_place(&mut w);
        }
    });
    let mut perc = vec![0f32; nf * nb];
    for_each_chunk(&mut perc, nb, |f, row| {
        let mut w = vec![0f32; kernel];
        let m = &mag[f * nb..(f + 1) * nb];
        for k in 0..nb {
            for (j, d) in (-half..=half).enumerate() {
                w[j] = m[reflect(k as isize + d, nb)];
            }
            row[k] = median_in_place(&mut w);
        }
    });
    let tiny = f32::MIN_POSITIVE;
    let mut data = s.data.clone();
    for i in 0..nf * nb {
        let (x, xr) = if want_harm { (harm[i], perc[i] * margin) } else { (perc[i], harm[i] * margin) };
        let z = x.max(xr);
        let m = if z < tiny {
            0.0
        } else {
            let a = (x / z).powi(2);
            let b = (xr / z).powi(2);
            a / (a + b)
        };
        // S * mask * phase == the complex value scaled by the mask
        data[i] = s.data[i] * m;
    }
    Spec { data, ..s.clone() }
}

/// librosa.effects.harmonic(y, margin) / percussive(y, margin): n_fft 2048, hop 512, Hann, kernel 31.
pub fn hpss_signal(y: &[f32], margin: f32, harmonic: bool) -> Vec<f32> {
    let w = hann_periodic(2048);
    let s = stft(y, 2048, 512, &w);
    let h = hpss(&s, 31, margin, harmonic);
    istft(&h, &w, y.len())
}

// ------------------------------------------------------------------------------------------------------------- mel, onsets

fn hz_to_mel(f: f64) -> f64 {
    let f_sp = 200.0 / 3.0;
    let min_log_hz = 1000.0;
    let min_log_mel = min_log_hz / f_sp;
    let logstep = (6.4f64).ln() / 27.0;
    if f >= min_log_hz { min_log_mel + (f / min_log_hz).ln() / logstep } else { f / f_sp }
}

fn mel_to_hz(m: f64) -> f64 {
    let f_sp = 200.0 / 3.0;
    let min_log_hz = 1000.0;
    let min_log_mel = min_log_hz / f_sp;
    let logstep = (6.4f64).ln() / 27.0;
    if m >= min_log_mel { min_log_hz * (logstep * (m - min_log_mel)).exp() } else { f_sp * m }
}

/// librosa.filters.mel(sr, n_fft, n_mels, fmin, fmax, htk=False, norm="slaney"), float32 weights: n_mels x (1 + n_fft/2).
pub fn mel_filters(sr: f64, n_fft: usize, n_mels: usize, fmin: f64, fmax: f64) -> Vec<Vec<f32>> {
    let n_bins = n_fft / 2 + 1;
    let fftfreqs: Vec<f64> = (0..n_bins).map(|k| k as f64 * sr / n_fft as f64).collect();
    let (lo, hi) = (hz_to_mel(fmin), hz_to_mel(fmax));
    let mel_f: Vec<f64> = (0..n_mels + 2)
        .map(|i| mel_to_hz(lo + (hi - lo) * i as f64 / (n_mels + 1) as f64))
        .collect();
    let mut w = vec![vec![0f32; n_bins]; n_mels];
    for i in 0..n_mels {
        let (d0, d1) = (mel_f[i + 1] - mel_f[i], mel_f[i + 2] - mel_f[i + 1]);
        let enorm = 2.0 / (mel_f[i + 2] - mel_f[i]);
        for k in 0..n_bins {
            let lower = -(mel_f[i] - fftfreqs[k]) / d0;
            let upper = (mel_f[i + 2] - fftfreqs[k]) / d1;
            let v = 0f64.max(lower.min(upper)) as f32;
            w[i][k] = v * enorm as f32;
        }
    }
    w
}

/// librosa.feature.melspectrogram(y, sr, n_fft, hop, n_mels, fmax, power=2): n_frames x n_mels (frame-major).
pub fn melspectrogram(y: &[f32], sr: f64, n_fft: usize, hop: usize, n_mels: usize, fmax: f64) -> (Vec<f32>, usize) {
    let s = stft(y, n_fft, hop, &hann_periodic(n_fft));
    let fb = mel_filters(sr, n_fft, n_mels, 0.0, fmax);
    let mut out = vec![0f32; s.n_frames * n_mels];
    for_each_chunk(&mut out, n_mels, |f, row| {
        let fr = s.frame(f);
        let p: Vec<f32> = fr.iter().map(|c| c.norm_sqr()).collect();
        for (m, filt) in fb.iter().enumerate() {
            let mut acc = 0f32;
            for k in 0..p.len() {
                acc += filt[k] * p[k];
            }
            row[m] = acc;
        }
    });
    (out, s.n_frames)
}

/// librosa.power_to_db(S, ref=1, amin=1e-10, top_db=80) in place.
pub fn power_to_db(s: &mut [f32]) {
    let mut mx = f32::NEG_INFINITY;
    for v in s.iter_mut() {
        *v = 10.0 * v.max(1e-10).log10();
        mx = mx.max(*v);
    }
    for v in s.iter_mut() {
        *v = v.max(mx - 80.0);
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Aggregate {
    Mean,
    Median,
}

/// librosa.onset.onset_strength(y, sr, n_fft, hop_length, lag, max_size, aggregate, n_mels, center=True).
pub fn onset_strength(y: &[f32], sr: f64, n_fft: usize, hop: usize, lag: usize, max_size: usize, agg: Aggregate, n_mels: usize) -> Vec<f32> {
    let (mut s, nf) = melspectrogram(y, sr, n_fft, hop, n_mels, 0.5 * sr);
    power_to_db(&mut s);
    let half = (max_size / 2) as isize;
    let refm: Vec<f32> = if max_size == 1 {
        s.clone()
    } else {
        // scipy.ndimage.maximum_filter1d(S, max_size, axis=freq), mode reflect
        let mut r = vec![0f32; s.len()];
        for f in 0..nf {
            for m in 0..n_mels {
                let mut mx = f32::NEG_INFINITY;
                for d in -half..=(max_size as isize - 1 - half) {
                    mx = mx.max(s[f * n_mels + reflect(m as isize + d, n_mels)]);
                }
                r[f * n_mels + m] = mx;
            }
        }
        r
    };
    let mut env = Vec::with_capacity(nf);
    let mut col = vec![0f32; n_mels];
    for f in lag..nf {
        for m in 0..n_mels {
            col[m] = (s[f * n_mels + m] - refm[(f - lag) * n_mels + m]).max(0.0);
        }
        env.push(match agg {
            Aggregate::Mean => col.iter().sum::<f32>() / n_mels as f32,
            Aggregate::Median => median_f32(&mut col.clone()),
        });
    }
    let pad = lag + n_fft / (2 * hop);
    let mut out = vec![0f32; pad];
    out.extend(env);
    out.truncate(nf);
    out
}

/// numpy's pairwise summation for float32 (umath loops: 8 accumulators below 128 elements, halves above), so sums, means and
/// standard deviations round exactly like numpy's.
pub fn pairwise_sum_f32(a: &[f32]) -> f32 {
    let n = a.len();
    if n < 8 {
        let mut r = 0f32;
        for &v in a {
            r += v;
        }
        r
    } else if n <= 128 {
        let mut r = [0f32; 8];
        r.copy_from_slice(&a[..8]);
        let mut i = 8;
        while i < n - (n % 8) {
            for k in 0..8 {
                r[k] += a[i + k];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += a[i];
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        pairwise_sum_f32(&a[..n2]) + pairwise_sum_f32(&a[n2..])
    }
}

/// numpy median (the mean of the two middle values for an even count).
pub fn median_f32(v: &mut [f32]) -> f32 {
    let n = v.len();
    if n == 0 {
        return f32::NAN;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    if n % 2 == 1 { v[n / 2] } else { 0.5 * (v[n / 2 - 1] + v[n / 2]) }
}

pub fn median_f64(v: &mut [f64]) -> f64 {
    let n = v.len();
    if n == 0 {
        return f64::NAN;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    if n % 2 == 1 { v[n / 2] } else { 0.5 * (v[n / 2 - 1] + v[n / 2]) }
}

/// librosa.beat.beat_track(onset_envelope, sr, hop_length, bpm, tightness, trim=False) -> beat frames.
pub fn beat_track(env: &[f32], sr: f64, hop: usize, bpm: f64, tightness: f32) -> Vec<usize> {
    beat_track_debug(env, sr, hop, bpm, tightness).0
}

/// beat_track plus its internals (normalised onsets, local score, cumulative score, backlinks) for checking against librosa.
pub fn beat_track_debug(env: &[f32], sr: f64, hop: usize, bpm: f64, tightness: f32) -> (Vec<usize>, Vec<f32>, Vec<f32>, Vec<f64>, Vec<i64>) {
    let n = env.len();
    if n == 0 || !env.iter().any(|&v| v != 0.0) {
        return (vec![], vec![], vec![], vec![], vec![]);
    }
    let fpb = (sr / hop as f64 * 60.0 / bpm).round_ties_even();
    // __normalize_onsets: divide by the standard deviation (ddof=1), computed the way numpy does for float32 (pairwise sums)
    let mean = pairwise_sum_f32(env) / n as f32;
    let sq: Vec<f32> = env.iter().map(|&v| (v - mean) * (v - mean)).collect();
    let var = pairwise_sum_f32(&sq) / (n as f32 - 1.0).max(1.0);
    let norm = var.sqrt() + f32::MIN_POSITIVE;
    let on: Vec<f32> = env.iter().map(|&v| v / norm).collect();
    // __beat_local_score
    let fpbi = fpb as i64;
    let window: Vec<f64> = (-fpbi..=fpbi).map(|i| (-0.5 * (i as f64 * 32.0 / fpb).powi(2)).exp()).collect();
    let k = window.len();
    let mut local = vec![0f32; n];
    for i in 0..n {
        let lo = (i + k / 2).saturating_sub(n - 1).max(0);
        let hi = (i + k / 2).min(k);
        let mut acc = 0f32;
        for kk in lo..hi {
            acc = (acc as f64 + window[kk] * on[i + k / 2 - kk] as f64) as f32;
        }
        local[i] = acc;
    }
    // __beat_track_dp: numba picks its float64 signature (frames_per_beat is float64), so the DP runs in float64
    let score_thresh = 0.01 * local.iter().cloned().fold(f32::NEG_INFINITY, f32::max) as f64;
    let mut backlink = vec![-1i64; n];
    let mut cum = vec![0f64; n];
    cum[0] = local[0] as f64;
    let mut first_beat = true;
    let log_fpb = fpb.ln();
    for i in 0..n {
        let mut best = f64::NEG_INFINITY;
        let mut loc_best: i64 = -1;
        let start = i as i64 - (fpb / 2.0).round_ties_even() as i64;
        let stop = (i as f64 - 2.0 * fpb - 1.0) as i64;
        let mut loc = start;
        while loc > stop {
            if loc < 0 {
                break;
            }
            let d = ((i as i64 - loc) as f64).ln() - log_fpb;
            let score = cum[loc as usize] - tightness as f64 * d * d;
            if score > best {
                best = score;
                loc_best = loc;
            }
            loc -= 1;
        }
        cum[i] = if loc_best >= 0 { local[i] as f64 + best } else { local[i] as f64 };
        if first_beat && (local[i] as f64) < score_thresh {
            backlink[i] = -1;
        } else {
            backlink[i] = loc_best;
            first_beat = false;
        }
    }
    // __last_beat: the last local maximum of the cumulative score above half the median of all local maxima
    let mut is_max = vec![false; n];
    if n >= 2 {
        is_max[0] = cum[0] > cum[1];
        for i in 1..n - 1 {
            is_max[i] = cum[i] > cum[i - 1] && cum[i] >= cum[i + 1];
        }
        is_max[n - 1] = cum[n - 1] > cum[n - 2];
    }
    let mut maxima: Vec<f64> = (0..n).filter(|&i| is_max[i]).map(|i| cum[i]).collect();
    let thr = if maxima.is_empty() { 0.0 } else { 0.5 * median_f64(&mut maxima) };
    let mut tail = n - 1;
    let mut j = n as i64 - 1;
    while j >= 0 {
        let u = j as usize;
        if is_max[u] && cum[u] >= thr {
            tail = u;
            break;
        }
        j -= 1;
    }
    let mut beats = vec![false; n];
    let mut b = tail as i64;
    while b >= 0 {
        beats[b as usize] = true;
        b = backlink[b as usize];
    }
    // __trim_beats with trim=False: threshold 0 at both ends
    let mut i = 0;
    while i < n && local[i] <= 0.0 {
        beats[i] = false;
        i += 1;
    }
    let mut i = n as i64 - 1;
    while i >= 0 && local[i as usize] <= 0.0 {
        beats[i as usize] = false;
        i -= 1;
    }
    ((0..n).filter(|&i| beats[i]).collect(), on, local, cum, backlink)
}

// ------------------------------------------------------------------------------------------------------------- resampling

fn gcd(a: u64, b: u64) -> u64 {
    if b == 0 { a } else { gcd(b, a % b) }
}

/// torchaudio.functional.resample(waveform, orig, new) with its defaults (sinc_interp_hann, lowpass_filter_width 6,
/// rolloff 0.99): the exact kernel and padding, so S-KEY sees the same input as in the service.
pub fn resample_torch(y: &[f32], orig: u32, new: u32) -> Vec<f32> {
    if orig == new {
        return y.to_vec();
    }
    let g = gcd(orig as u64, new as u64);
    let (of, nf) = ((orig as u64 / g) as usize, (new as u64 / g) as usize);
    let width_lp = 6.0f64;
    let base = (of.min(nf) as f64) * 0.99;
    let width = (width_lp * of as f64 / base).ceil() as usize;
    let klen = 2 * width + of;
    // kernel[phase][k], computed in float32 like torchaudio for float32 input
    let mut kern = vec![vec![0f32; klen]; nf];
    for (i, row) in kern.iter_mut().enumerate() {
        for (k, v) in row.iter_mut().enumerate() {
            let idx = (k as f32 - width as f32) / of as f32;
            let mut t = (-(i as f32) / nf as f32 + idx) * base as f32;
            t = t.clamp(-width_lp as f32, width_lp as f32);
            let w = (t * std::f32::consts::PI / width_lp as f32 / 2.0).cos().powi(2);
            let tp = t * std::f32::consts::PI;
            let s = if tp == 0.0 { 1.0 } else { tp.sin() / tp };
            *v = s * w * (base as f32 / of as f32);
        }
    }
    let n = y.len();
    let target = ((nf as f64 * n as f64) / of as f64).ceil() as usize;
    let padded_len = n + width + width + of;
    let get = |j: usize| -> f32 { if j >= width && j - width < n { y[j - width] } else { 0.0 } };
    let n_steps = (padded_len - klen) / of + 1;
    let mut out = Vec::with_capacity(target);
    'outer: for s in 0..n_steps {
        for row in kern.iter() {
            if out.len() >= target {
                break 'outer;
            }
            let base_i = s * of;
            let mut acc = 0f32;
            for (k, &kv) in row.iter().enumerate() {
                acc += kv * get(base_i + k);
            }
            out.push(acc);
        }
    }
    out
}

fn bessel_i0(x: f64) -> f64 {
    let mut sum = 1.0;
    let mut term = 1.0;
    let q = x * x / 4.0;
    for k in 1..200 {
        term *= q / (k as f64 * k as f64);
        sum += term;
        if term < sum * 1e-17 {
            break;
        }
    }
    sum
}

/// High-quality band-limited resampling in the place of soxr "HQ" (librosa.resample's default; Beat This!'s front end).
/// Linear phase, pass band to 91.3 % of the lower Nyquist, stop band from 100 %, about 125 dB rejection. Output length
/// ceil(len * out / in), aligned like soxr (no added delay). `scale` = librosa's scale=True (divide by sqrt(ratio)).
pub fn resample_hq(y: &[f32], sr_in: f64, sr_out: f64, scale: bool) -> Vec<f32> {
    if sr_in == sr_out {
        return y.to_vec();
    }
    let ratio = sr_out / sr_in;
    let n_out = (y.len() as f64 * ratio).ceil() as usize;
    let lower = ratio.min(1.0);                         // lower Nyquist relative to the input Nyquist
    let pass = 0.913;
    let cutoff = 0.5 * (pass + 1.0) * lower;            // fraction of the input Nyquist
    let trans = (1.0 - pass) * lower;                   // transition width, fraction of the input Nyquist
    let atten = 125.0;
    let beta = 0.1102 * (atten - 8.7);
    // taps on each side, in input samples: Kaiser's formula for the full length
    let half = (((atten - 7.95) / (2.285 * std::f64::consts::PI * trans)) / 2.0).ceil() as i64 + 1;
    let i0b = bessel_i0(beta);
    let kernel = |tau: f64| -> f64 {
        let r = tau / (half as f64);
        if r.abs() >= 1.0 {
            return 0.0;
        }
        let w = bessel_i0(beta * (1.0 - r * r).sqrt()) / i0b;
        let x = std::f64::consts::PI * cutoff * tau;
        let s = if x.abs() < 1e-12 { 1.0 } else { x.sin() / x };
        cutoff * s * w
    };
    // polyphase table when the ratio is a modest fraction, else evaluate the kernel per output sample
    let (num, den) = {
        let a = (sr_out.round() as u64, sr_in.round() as u64);
        let g = gcd(a.0, a.1);
        (a.0 / g, a.1 / g)
    };
    let gain = if scale { 1.0 / ratio.sqrt() } else { 1.0 };
    let taps = (2 * half + 1) as usize;
    let mut out = vec![0f32; n_out];
    if num <= 4096 && (num as f64 / den as f64 - ratio).abs() < 1e-12 {
        // output sample m sits at input position m * den / num = q + p / num
        let table: Vec<Vec<f32>> = (0..num)
            .map(|p| {
                let frac = p as f64 / num as f64;
                (0..taps).map(|t| kernel((t as i64 - half) as f64 - frac) as f32).collect()
            })
            .collect();
        for_each_chunk(&mut out, 4096, |c, chunk| {
            for (i, o) in chunk.iter_mut().enumerate() {
                let m = (c * 4096 + i) as u64;
                let pos = m * den;
                let q = (pos / num) as i64;
                let p = (pos % num) as usize;
                let row = &table[p];
                let mut acc = 0f64;
                for t in 0..taps {
                    let j = q + t as i64 - half;
                    if j >= 0 && (j as usize) < y.len() {
                        acc += row[t] as f64 * y[j as usize] as f64;
                    }
                }
                *o = (acc * gain) as f32;
            }
        });
    } else {
        for_each_chunk(&mut out, 4096, |c, chunk| {
            for (i, o) in chunk.iter_mut().enumerate() {
                let m = c * 4096 + i;
                let x = m as f64 / ratio;
                let q = x.floor() as i64;
                let mut acc = 0f64;
                for j in (q - half)..=(q + half + 1) {
                    if j >= 0 && (j as usize) < y.len() {
                        acc += kernel(x - j as f64) * y[j as usize] as f64;
                    }
                }
                *o = (acc * gain) as f32;
            }
        });
    }
    out
}

// ------------------------------------------------------------------------------------------------------------- CQT

/// librosa.cqt(y, sr, hop_length, fmin, n_bins, bins_per_octave, tuning, filter_scale=1, norm=1, sparsity=0.01, window="hann",
/// scale=True): complex, bins x frames returned as frames x bins (frame-major) magnitudes. `tuning` is in fractions of a bin.
pub struct Cqt {
    pub n_bins: usize,
    pub n_frames: usize,
    /// |CQT|, frame-major: mag[f * n_bins + b]
    pub mag: Vec<f32>,
}

fn relative_bandwidth(freqs: &[f64]) -> Vec<f64> {
    let n = freqs.len();
    let logf: Vec<f64> = freqs.iter().map(|f| f.log2()).collect();
    let mut bpo = vec![0f64; n];
    bpo[0] = 1.0 / (logf[1] - logf[0]);
    bpo[n - 1] = 1.0 / (logf[n - 1] - logf[n - 2]);
    for i in 1..n - 1 {
        bpo[i] = 2.0 / (logf[i + 1] - logf[i - 1]);
    }
    bpo.iter().map(|b| ((2f64).powf(2.0 / b) - 1.0) / ((2f64).powf(2.0 / b) + 1.0)).collect()
}

struct SparseFilter {
    idx: Vec<u32>,
    val: Vec<C32>,
}

fn num_two_factors(mut x: usize) -> usize {
    if x == 0 {
        return 0;
    }
    let mut n = 0;
    while x % 2 == 0 {
        n += 1;
        x /= 2;
    }
    n
}

pub fn cqt(y: &[f32], sr: f64, hop: usize, fmin: f64, n_bins: usize, bpo: usize, tuning: f64) -> Cqt {
    let fmin_t = fmin * (2f64).powf(tuning / bpo as f64);
    let freqs: Vec<f64> = (0..n_bins).map(|k| fmin_t * (2f64).powf(k as f64 / bpo as f64)).collect();
    let alpha = relative_bandwidth(&freqs);
    let win_bw = 1.50018310546875; // filters.window_bandwidth("hann")
    let q: Vec<f64> = alpha.iter().map(|a| 1.0 / a).collect();
    let filter_cutoff = freqs.iter().zip(&q).map(|(f, qq)| f * (1.0 + 0.5 * win_bw / qq)).fold(f64::MIN, f64::max);
    let nyquist = sr / 2.0;
    let n_octaves = (n_bins + bpo - 1) / bpo;
    let n_filters = bpo.min(n_bins);
    // __early_downsample
    let count1 = 0i64.max(((nyquist / filter_cutoff).log2().ceil() as i64 - 1) - 1);
    let count2 = 0i64.max(num_two_factors(hop) as i64 - n_octaves as i64 + 1);
    let count = count1.min(count2).max(0) as u32;
    let (mut my_y, mut my_sr, mut my_hop) = (y.to_vec(), sr, hop);
    if count > 0 {
        let factor = 2usize.pow(count);
        my_hop /= factor;
        my_y = resample_hq(&my_y, factor as f64, 1.0, true);
        my_sr = sr / factor as f64;
    }
    let base_sr = my_sr;
    let lengths: Vec<f64> = freqs.iter().zip(&q).map(|(f, qq)| qq * base_sr / f).collect();
    let mut resp: Vec<(Vec<C32>, usize, usize)> = Vec::new(); // (frame-major values, n_filters_in_octave, n_frames)
    for i in 0..n_octaves {
        let hi = n_bins - n_filters * i;
        let lo = hi.saturating_sub(n_filters);
        let fo = &freqs[lo..hi];
        let qo = &q[lo..hi];
        // filters.wavelet at my_sr
        let lens: Vec<f64> = fo.iter().zip(qo).map(|(f, qq)| qq * my_sr / f).collect();
        let max_len = lens.iter().cloned().fold(0.0, f64::max);
        let n_fft = (2f64).powf(max_len.log2().ceil()) as usize;
        let mut planner = rustfft::FftPlanner::<f32>::new();
        let fft = planner.plan_fft_forward(n_fft);
        let mut filters: Vec<SparseFilter> = Vec::with_capacity(fo.len());
        for (j, &f) in fo.iter().enumerate() {
            let ilen = lens[j];
            let start = (-ilen / 2.0).floor();
            let stop = (ilen / 2.0).floor();
            let n = (stop - start).ceil() as usize;
            let win = hann_periodic(n);
            let mut sig: Vec<rustfft::num_complex::Complex<f64>> = (0..n)
                .map(|k| {
                    let ph = (start + k as f64) * 2.0 * std::f64::consts::PI * f / my_sr;
                    rustfft::num_complex::Complex::new(ph.cos(), ph.sin()) * win[k] as f64
                })
                .collect();
            let l1: f64 = sig.iter().map(|c| c.norm()).sum();
            for c in sig.iter_mut() {
                *c /= l1;
            }
            // pad_center to n_fft, scale by length / n_fft, FFT (complex64 like librosa)
            let lpad = (n_fft - n) / 2;
            let mut buf = vec![C32::new(0.0, 0.0); n_fft];
            for k in 0..n {
                let v = sig[k] * (ilen / n_fft as f64);
                buf[lpad + k] = C32::new(v.re as f32, v.im as f32);
            }
            fft.process(&mut buf);
            let half = n_fft / 2 + 1;
            let row = &buf[..half];
            // util.sparsify_rows(quantile=0.01)
            let mags: Vec<f32> = row.iter().map(|c| c.norm()).collect();
            let norm: f32 = mags.iter().sum();
            let mut sorted = mags.clone();
            sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let mut cum = 0f32;
            let mut thr_idx = 0;
            for (k, m) in sorted.iter().enumerate() {
                cum += m / norm;
                if !(cum < 0.01) {
                    thr_idx = k;
                    break;
                }
            }
            let thresh = sorted[thr_idx];
            let scale = (base_sr / my_sr).sqrt() as f32;
            let mut sf = SparseFilter { idx: vec![], val: vec![] };
            for k in 0..half {
                if mags[k] >= thresh {
                    sf.idx.push(k as u32);
                    sf.val.push(row[k] * scale);
                }
            }
            filters.push(sf);
        }
        // __cqt_response: STFT with a rectangular window, then the filter bank
        let d = stft(&my_y, n_fft, my_hop, &ones(n_fft));
        let nfo = filters.len();
        let mut out = vec![C32::new(0.0, 0.0); d.n_frames * nfo];
        for_each_chunk(&mut out, nfo, |fr, row| {
            let col = d.frame(fr);
            for (j, filt) in filters.iter().enumerate() {
                let mut acc = C32::new(0.0, 0.0);
                for (k, v) in filt.idx.iter().zip(&filt.val) {
                    acc += v * col[*k as usize];
                }
                row[j] = acc;
            }
        });
        resp.push((out, nfo, d.n_frames));
        if i < n_octaves - 1 {
            let f_max_next = freqs[lo - 1];
            if my_hop % 2 == 0 && f_max_next <= my_sr / 5.0 {
                my_hop /= 2;
                my_sr /= 2.0;
                my_y = resample_hq(&my_y, 2.0, 1.0, true);
            }
        }
    }
    // __trim_stack + scale by 1/sqrt(length)
    let n_frames = resp.iter().map(|r| r.2).min().unwrap_or(0);
    let mut mag = vec![0f32; n_frames * n_bins];
    let mut end = n_bins;
    for (vals, nfo, _) in &resp {
        let lo = end.saturating_sub(*nfo);
        let skip = nfo - (end - lo); // rows to drop from the bottom when fewer bins remain
        for f in 0..n_frames {
            for b in lo..end {
                let v = vals[f * nfo + skip + (b - lo)];
                mag[f * n_bins + b] = v.norm() / (lengths[b].sqrt() as f32);
            }
        }
        end = lo;
    }
    Cqt { n_bins, n_frames, mag }
}

/// librosa.feature.chroma_cqt(C=|CQT|, bins_per_octave, fmin) folding (norm=None): frames x 12. The CQT must start at C.
pub fn chroma_from_cqt(c: &Cqt, bpo: usize) -> Vec<[f32; 12]> {
    let n_merge = bpo / 12;
    let shift = n_merge / 2; // np.roll(..., -int(n_merge // 2), axis=1)
    // column b of the folding matrix belongs to chroma ((b + shift) / n_merge) % 12
    let pc: Vec<usize> = (0..c.n_bins).map(|b| ((b + shift) / n_merge) % 12).collect();
    (0..c.n_frames)
        .map(|f| {
            let mut ch = [0f32; 12];
            for b in 0..c.n_bins {
                ch[pc[b]] += c.mag[f * c.n_bins + b];
            }
            ch
        })
        .collect()
}

pub fn time_to_frames(t: f64, sr: f64, hop: usize) -> i64 {
    // librosa: samples = (t * sr).astype(int) (truncation), frames = samples // hop (floor)
    let s = (t * sr) as i64;
    s.div_euclid(hop as i64)
}

pub fn frames_to_time(f: i64, sr: f64, hop: usize) -> f64 {
    (f * hop as i64) as f64 / sr
}
