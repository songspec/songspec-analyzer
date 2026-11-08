//! The whole analysis, stage for stage as the service's pipeline (pipeline/__init__.py): detune -> Centsible's key evidence ->
//! tempo (Beat This! + BPM Fixer) -> drift -> chords (templates + BTC) -> fused key (+ borrows, roman numerals) -> detune text.
//! Every buffer is local to `Engine::analyze`, so the decoded audio and intermediate arrays are freed when it returns.

use serde::Serialize;

use crate::audio::Audio;
use crate::chords::{self, Progression, Segment, SummaryItem};
use crate::detune::{self, Detune};
use crate::key::{self, Key, KeyInputs};
use crate::models::{ModelBytes, PlanCache};
use crate::tempo::{self, Tempo};
use crate::{beatthis, centsible, dsp, ENGINE_VERSION};

#[derive(Debug, Clone, Serialize)]
pub struct Track {
    pub duration: f64,
    pub sample_rate: u32,
    pub channels: usize,
    pub title: Option<String>,
    pub artist: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Chords {
    pub segments: Vec<Segment>,
    pub summary: Vec<SummaryItem>,
    pub progressions: Vec<Progression>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Analysis {
    pub engine: String,
    pub track: Track,
    pub detune: Option<Detune>,
    pub tempo: Option<Tempo>,
    pub key: Option<Key>,
    pub chords: Option<Chords>,
    pub warnings: Vec<String>,
    pub seconds: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Stage {
    Detune,
    Tempo,
    Chords,
    Key,
    Done,
}

impl Stage {
    pub fn label(&self) -> &'static str {
        match self {
            Stage::Detune => "Measuring detune...",
            Stage::Tempo => "Finding beats...",
            Stage::Chords => "Detecting chords...",
            Stage::Key => "Weighing the key evidence...",
            Stage::Done => "Done",
        }
    }
}

pub struct Options {
    /// "Auto" or a time signature name ("4/4", "6/8" ...)
    pub signature: String,
}

impl Default for Options {
    fn default() -> Self {
        Options { signature: "Auto".into() }
    }
}

pub struct Engine {
    models: ModelBytes,
    cache: PlanCache,
}

impl Engine {
    pub fn new(models: ModelBytes) -> Self {
        Engine { models, cache: PlanCache::default() }
    }

    #[cfg(feature = "embedded-models")]
    pub fn embedded() -> Self {
        Self::new(ModelBytes::embedded())
    }

    /// Drop the compiled network plans (call when the queue is empty: an idle app then holds no model memory).
    pub fn release(&self) {
        self.cache.clear();
    }

    pub fn analyze(&self, audio: &Audio, opts: &Options, progress: &dyn Fn(Stage)) -> Result<Analysis, String> {
        let t0 = std::time::Instant::now();
        let duration = audio.duration();
        if duration < 5.0 {
            return Err("This file is shorter than 5 seconds - too short to analyse.".into());
        }
        let mono = audio.mono();
        let sr = audio.sr;
        let mut warnings = Vec::new();

        progress(Stage::Detune);
        let mut det = detune::measure(&mono, sr);
        if det.is_none() {
            warnings.push("No steady tonal content found - detune can't be measured on this file.".into());
        }
        let cents = det.as_ref().map(|d| d.raw_cents);
        let ck = cents.map(|c| centsible::key_probabilities(&mono, sr as f64, c));

        progress(Stage::Tempo);
        let tempo_r = match beatthis::detect(&mono, sr, &self.models, &self.cache) {
            Ok((b, d)) => tempo::analyze(&mono, sr as f64, &b, &d, &opts.signature),
            Err(e) => Err(format!("Beat tracking failed ({e})")),
        };
        let tempo_r = match tempo_r {
            Ok(r) => Some(r),
            Err(e) => {
                warnings.push(e);
                None
            }
        };
        if let Some(d) = det.as_mut() {
            detune::add_drift(d, &mono, sr);
        }

        progress(Stage::Chords);
        let tuning = cents.unwrap_or(0.0);
        let y22 = dsp::resample_hq(&mono, sr as f64, 22050.0, false);
        let yh = dsp::hpss_signal(&y22, 2.0, true);
        let cqt = chords::tuned_cqt(&yh, tuning);
        drop(yh);
        let mut segs: Option<Vec<Segment>> = None;
        if let Some(t) = &tempo_r {
            if t.beats.len() >= 4 {
                let ev = chords::evidence(&cqt, &t.beats, &t.downbeats, duration);
                let btc = chords::btc_frame_probs(&y22, tuning, &self.models, &self.cache)
                    .map(|(times, p)| chords::btc_beat_logp(&times, &p, &ev.starts, ev.end));
                if let Err(e) = &btc {
                    warnings.push(format!("BTC chord model failed ({e}); templates only"));
                }
                segs = Some(chords::decode(&ev, btc.ok().as_deref()));
            }
        }
        drop(y22);

        progress(Stage::Key);
        let mut key_out: Option<Key> = None;
        if let Some(ck) = &ck {
            if ck.valid {
                let beats: Vec<f64> = tempo_r.as_ref().map(|t| t.beats.clone()).unwrap_or_default();
                let (bc, be) = key::tuned_beat_chroma(&cqt, &beats);
                let skey = match key::skey_probs(&mono, sr, &self.models, &self.cache) {
                    Ok(p) => Some(p),
                    Err(e) => {
                        warnings.push(format!("S-KEY failed ({e}); key from Centsible"));
                        None
                    }
                };
                let downs: Vec<f64> = tempo_r.as_ref().map(|t| t.downbeats.clone()).unwrap_or_default();
                let mut k = key::fused_key(&KeyInputs { centsible: ck, skey, beat_chroma: &bc, beat_energy: &be, beats: &beats, downbeats: &downs, duration });
                if let Some(s) = segs.as_mut() {
                    key::annotate_with_chords(&mut k, s);
                }
                if let Some(d) = det.as_mut() {
                    detune::with_key_text(d, k.tonic_pc, &k.mode_id.clone());
                }
                key_out = Some(k);
            } else {
                warnings.push("No key could be read from this file.".into());
            }
        }
        drop(cqt);
        drop(mono);
        let chords_out = segs.map(|s| {
            let downs: Vec<f64> = tempo_r.as_ref().map(|t| t.downbeats.clone()).unwrap_or_default();
            Chords { summary: chords::summary(&s), progressions: chords::progressions(&s, &downs), segments: s }
        });
        progress(Stage::Done);
        Ok(Analysis {
            engine: ENGINE_VERSION.into(),
            track: Track { duration: crate::np::py_round(duration, 3), sample_rate: sr, channels: audio.channels, title: audio.title.clone(), artist: audio.artist.clone() },
            detune: det,
            tempo: tempo_r.as_ref().map(tempo::section_from_engine),
            key: key_out,
            chords: chords_out,
            warnings,
            seconds: crate::np::py_round(t0.elapsed().as_secs_f64(), 2),
        })
    }
}
