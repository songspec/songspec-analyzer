//! Beat This! (CPJKU, ISMIR 2024) as BPM Fixer and the service run it: Audio2Beats(dbn=False).
//! soxr resampling to 22.05 kHz -> torchaudio log-mel (n_fft 1024, hop 441, 30 - 11000 Hz, 128 slaney bands, magnitude,
//! frame-length normalised, log1p(1000 x)) -> the network in 1500-frame chunks (6-frame borders, keep_first) -> the
//! "minimal" postprocessor (max-pool peak picking, adjacent peaks merged, downbeats moved onto beats).

use realfft::RealFftPlanner;
use tract_onnx::prelude::*;

use crate::dsp;
use crate::models::{run1, ModelBytes, PlanCache};

const SR: f64 = 22050.0;
const N_FFT: usize = 1024;
const HOP: usize = 441;
const N_MELS: usize = 128;
const CHUNK: usize = 1500;
const BORDER: usize = 6;
const FPS: f64 = 50.0;

fn hz_to_mel(f: f64) -> f64 {
    let f_sp = 200.0 / 3.0;
    let min_log_mel = 1000.0 / f_sp;
    let logstep = (6.4f64).ln() / 27.0;
    if f >= 1000.0 { min_log_mel + (f / 1000.0).ln() / logstep } else { f / f_sp }
}

/// torchaudio.functional.melscale_fbanks(513, 30, 11000, 128, 22050, norm=None, mel_scale="slaney"), float32: 513 x 128.
fn fbanks() -> Vec<[f32; N_MELS]> {
    let n_freqs = N_FFT / 2 + 1;
    let all: Vec<f32> = (0..n_freqs).map(|i| (11025.0f64 * i as f64 / (n_freqs - 1) as f64) as f32).collect();
    let (m_min, m_max) = (hz_to_mel(30.0) as f32, hz_to_mel(11000.0) as f32);
    let f_sp = 200.0f32 / 3.0;
    let min_log_mel = 1000.0f32 / f_sp;
    let logstep = (6.4f32).ln() / 27.0;
    let f_pts: Vec<f32> = (0..N_MELS + 2)
        .map(|i| {
            let m = m_min + (m_max - m_min) * i as f32 / (N_MELS + 1) as f32;
            if m >= min_log_mel { 1000.0 * (logstep * (m - min_log_mel)).exp() } else { f_sp * m }
        })
        .collect();
    let mut fb = vec![[0f32; N_MELS]; n_freqs];
    for (k, row) in fb.iter_mut().enumerate() {
        for j in 0..N_MELS {
            let down = -(f_pts[j] - all[k]) / (f_pts[j + 1] - f_pts[j]);
            let up = (f_pts[j + 2] - all[k]) / (f_pts[j + 2] - f_pts[j + 1]);
            row[j] = 0f32.max(down.min(up));
        }
    }
    fb
}

/// LogMelSpect: frames x 128.
pub fn log_mel(y22: &[f32]) -> Vec<[f32; N_MELS]> {
    let pad = N_FFT / 2;
    let n = y22.len();
    // torch.stft(center=True, pad_mode="reflect"): reflect without repeating the edge sample
    let get = |i: isize| -> f32 {
        let mut j = i;
        let last = n as isize - 1;
        loop {
            if j < 0 {
                j = -j;
            } else if j > last {
                j = 2 * last - j;
            } else {
                return y22[j as usize];
            }
        }
    };
    let n_frames = 1 + n / HOP;
    let win = dsp::hann_periodic(N_FFT);
    let fb = fbanks();
    let fft = RealFftPlanner::<f32>::new().plan_fft_forward(N_FFT);
    let norm = 1.0 / (N_FFT as f32).sqrt();
    let mut out = vec![[0f32; N_MELS]; n_frames];
    dsp::for_each_chunk(&mut out, 1, |f, row| {
        let mut buf: Vec<f32> = (0..N_FFT).map(|i| get((f * HOP + i) as isize - pad as isize) * win[i]).collect();
        let mut spec = fft.make_output_vec();
        fft.process(&mut buf, &mut spec).unwrap();
        let mag: Vec<f32> = spec.iter().map(|c| c.norm() * norm).collect();
        let mut mel = [0f32; N_MELS];
        for (k, m) in mag.iter().enumerate() {
            for j in 0..N_MELS {
                mel[j] += m * fb[k][j];
            }
        }
        for j in 0..N_MELS {
            row[0][j] = (1000.0 * mel[j]).ln_1p();
        }
    });
    out
}

/// Frame-wise beat and downbeat logits for the whole piece (split_predict_aggregate, keep_first).
pub fn frame_logits(spect: &[[f32; N_MELS]], models: &ModelBytes, cache: &PlanCache) -> TractResult<(Vec<f32>, Vec<f32>)> {
    let len = spect.len();
    let step = CHUNK - 2 * BORDER;
    let mut starts: Vec<isize> = (0..).map(|k| -(BORDER as isize) + (k * step) as isize).take_while(|&s| s < len as isize - BORDER as isize).collect();
    if starts.is_empty() {
        starts.push(-(BORDER as isize));
    }
    if len > step {
        let last = starts.len() - 1;
        starts[last] = len as isize - (CHUNK - BORDER) as isize;
    }
    let chunks: Vec<(isize, Vec<f32>, usize)> = starts
        .iter()
        .map(|&s| {
            let a = s.max(0) as usize;
            let b = ((s + CHUNK as isize) as usize).min(len);
            let left = (-s).max(0) as usize;
            let right = (BORDER as isize).min(s + CHUNK as isize - len as isize).max(0) as usize;
            let t = left + (b - a) + right;
            let mut x = vec![0f32; t * N_MELS];
            for (i, fr) in spect[a..b].iter().enumerate() {
                x[(left + i) * N_MELS..(left + i + 1) * N_MELS].copy_from_slice(fr);
            }
            (s, x, t)
        })
        .collect();
    // run the chunks a few at a time (each holds attention matrices of ~150 MB while it runs)
    let workers = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(2).clamp(1, 4);
    let mut preds: Vec<Option<(Vec<f32>, Vec<f32>)>> = vec![None; chunks.len()];
    let next = std::sync::atomic::AtomicUsize::new(0);
    let results = std::sync::Mutex::new(Vec::new());
    let err: std::sync::Mutex<Option<TractError>> = std::sync::Mutex::new(None);
    std::thread::scope(|sc| {
        for _ in 0..workers.min(chunks.len()) {
            sc.spawn(|| loop {
                let i = next.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                if i >= chunks.len() {
                    break;
                }
                let (_, x, t) = &chunks[i];
                let r = (|| -> TractResult<(Vec<f32>, Vec<f32>)> {
                    let plan = cache.get(0, &models.beat_this, &[1, *t, N_MELS])?;
                    let input = tract_ndarray::Array3::from_shape_vec((1, *t, N_MELS), x.clone())?.into_tensor();
                    let out = run1(&plan, input)?;
                    let b = out[0].to_plain_array_view::<f32>()?.iter().cloned().collect();
                    let d = out[1].to_plain_array_view::<f32>()?.iter().cloned().collect();
                    Ok((b, d))
                })();
                match r {
                    Ok(v) => results.lock().unwrap().push((i, v)),
                    Err(e) => {
                        *err.lock().unwrap() = Some(e);
                        break;
                    }
                }
            });
        }
    });
    if let Some(e) = err.into_inner().unwrap() {
        return Err(e);
    }
    for (i, v) in results.into_inner().unwrap() {
        preds[i] = Some(v);
    }
    let mut beat = vec![-1000f32; len];
    let mut down = vec![-1000f32; len];
    // keep_first: later chunks are written first, earlier ones overwrite them
    for (ci, (s, _, t)) in chunks.iter().enumerate().rev() {
        let (b, d) = preds[ci].as_ref().unwrap();
        for k in BORDER..t - BORDER {
            let pos = *s + k as isize;
            if pos >= 0 && (pos as usize) < len {
                beat[pos as usize] = b[k];
                down[pos as usize] = d[k];
            }
        }
    }
    Ok((beat, down))
}

fn peaks(logits: &[f32]) -> Vec<usize> {
    // max_pool1d(7, stride 1, padding 3) == the value itself, and logit > 0
    let n = logits.len();
    (0..n)
        .filter(|&i| {
            let lo = i.saturating_sub(3);
            let hi = (i + 3).min(n - 1);
            let mx = logits[lo..=hi].iter().cloned().fold(f32::NEG_INFINITY, f32::max);
            logits[i] == mx && logits[i] > 0.0
        })
        .collect()
}

/// deduplicate_peaks(width=1): runs of adjacent peak frames -> their running mean.
fn dedup(p: &[usize]) -> Vec<f64> {
    let mut out = Vec::new();
    let mut it = p.iter();
    let Some(&first) = it.next() else { return out };
    let mut cur = first as f64;
    let mut c = 1.0;
    for &p2 in it {
        if p2 as f64 - cur <= 1.0 {
            c += 1.0;
            cur += (p2 as f64 - cur) / c;
        } else {
            out.push(cur);
            cur = p2 as f64;
            c = 1.0;
        }
    }
    out.push(cur);
    out
}

/// Beat and downbeat times (seconds) from the logits (the "minimal" postprocessor).
pub fn postprocess(beat: &[f32], down: &[f32]) -> (Vec<f64>, Vec<f64>) {
    let bt: Vec<f64> = dedup(&peaks(beat)).iter().map(|f| f / FPS).collect();
    let mut dt: Vec<f64> = dedup(&peaks(down)).iter().map(|f| f / FPS).collect();
    if !bt.is_empty() {
        for d in dt.iter_mut() {
            // np.argmin: the first of equally near beats
            let mut best = 0;
            for (i, b) in bt.iter().enumerate() {
                if (b - *d).abs() < (bt[best] - *d).abs() {
                    best = i;
                }
            }
            *d = bt[best];
        }
    }
    dt.sort_by(|a, b| a.partial_cmp(b).unwrap());
    dt.dedup();
    (bt, dt)
}

/// mono at the file's own rate -> (beats, downbeats) in seconds, like Audio2Beats(dbn=False).
pub fn detect(mono: &[f32], sr: u32, models: &ModelBytes, cache: &PlanCache) -> TractResult<(Vec<f64>, Vec<f64>)> {
    let y22 = if sr as f64 != SR { dsp::resample_hq(mono, sr as f64, SR, false) } else { mono.to_vec() };
    let spect = log_mel(&y22);
    drop(y22);
    let (b, d) = frame_logits(&spect, models, cache)?;
    Ok(postprocess(&b, &d))
}
