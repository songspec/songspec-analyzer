//! Centsible's analysis core (C++, compiled by build.rs from service/native/centsible/core) through a small C shim
//! (csrc/centsible_shim.cpp). Same entry points and options as the Python binding (service/native/centsible/binding.cpp).

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct SsTuning {
    valid: i32, locked: i32, settled: i32, competing: i32, contested: i32, song_changes: i32, notes: i32,
    has_secondary: i32, has_alt: i32, has_drift: i32,
    cents: f64, ref_hz: f64, ci_cents: f64, confidence: f64, seconds: f64, dominance: f64,
    secondary_cents: f64, secondary_share: f64, alt_cents: f64, drift_cents: f64,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct SsKey {
    valid: i32, tonic: i32, minor: i32, scale: i32, degree: i32, frames: i32,
    scale_confidence: f64, tonic_confidence: f64,
    key_prob: [f64; 24], mode_prob: [f64; 7], profile_scores: [f64; 24],
    chroma_scale: [f64; 12], chroma_bass: [f64; 12], chroma_notes: [f64; 12],
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct SsDetunePoint { t: f64, cents: f64, ci: f64, mass: f64, sharpness: f64, window: f64, valid: i32 }

extern "C" {
    fn ss_analyze_tuning(x: *const f32, n: usize, sr: f64, out: *mut SsTuning);
    fn ss_key_probabilities(x: *const f32, n: usize, sr: f64, tuning_cents: f64, out: *mut SsKey);
    fn ss_detune_curve(x: *const f32, n: usize, sr: f64, window: f64, hop: f64, out: *mut SsDetunePoint, cap: usize) -> usize;
}

/// The plugin's tuning reading over the whole track.
#[derive(Debug, Clone, Default)]
pub struct Tuning {
    pub valid: bool, pub cents: f64, pub ref_hz: f64, pub ci_cents: f64, pub confidence: f64,
    pub locked: bool, pub settled: bool, pub seconds: f64, pub competing: bool, pub contested: bool,
    pub song_changes: i32, pub dominance: f64, pub notes: i32,
    pub has_secondary: bool, pub secondary_cents: f64, pub secondary_share: f64,
    pub has_alt: bool, pub alt_cents: f64, pub has_drift: bool, pub drift_cents: f64,
}

/// Centsible's key decision: 24 key probabilities (index tonic + 12 * minor), the 7 mode probabilities and its chromas.
#[derive(Debug, Clone)]
pub struct KeyDecision {
    pub valid: bool, pub tonic: usize, pub minor: bool, pub scale: usize, pub degree: usize, pub frames: i32,
    pub scale_confidence: f64, pub tonic_confidence: f64,
    pub key_prob: [f64; 24], pub mode_prob: [f64; 7], pub profile_scores: [f64; 24],
    pub chroma_scale: [f64; 12], pub chroma_bass: [f64; 12], pub chroma_notes: [f64; 12],
}

#[derive(Debug, Clone, Default)]
pub struct DetunePoint { pub t: f64, pub cents: f64, pub ci: f64, pub mass: f64, pub sharpness: f64, pub window: f64, pub valid: bool }

pub fn analyze_tuning(mono: &[f32], sr: f64) -> Tuning {
    let mut o = SsTuning::default();
    unsafe { ss_analyze_tuning(mono.as_ptr(), mono.len(), sr, &mut o) };
    Tuning {
        valid: o.valid != 0, cents: o.cents, ref_hz: o.ref_hz, ci_cents: o.ci_cents, confidence: o.confidence,
        locked: o.locked != 0, settled: o.settled != 0, seconds: o.seconds, competing: o.competing != 0,
        contested: o.contested != 0, song_changes: o.song_changes, dominance: o.dominance, notes: o.notes,
        has_secondary: o.has_secondary != 0, secondary_cents: o.secondary_cents, secondary_share: o.secondary_share,
        has_alt: o.has_alt != 0, alt_cents: o.alt_cents, has_drift: o.has_drift != 0, drift_cents: o.drift_cents,
    }
}

pub fn key_probabilities(mono: &[f32], sr: f64, tuning_cents: f64) -> KeyDecision {
    let mut o = SsKey {
        valid: 0, tonic: 0, minor: 0, scale: 0, degree: 0, frames: 0, scale_confidence: 0.0, tonic_confidence: 0.0,
        key_prob: [0.0; 24], mode_prob: [0.0; 7], profile_scores: [0.0; 24],
        chroma_scale: [0.0; 12], chroma_bass: [0.0; 12], chroma_notes: [0.0; 12],
    };
    unsafe { ss_key_probabilities(mono.as_ptr(), mono.len(), sr, tuning_cents, &mut o) };
    KeyDecision {
        valid: o.valid != 0, tonic: o.tonic as usize, minor: o.minor != 0, scale: o.scale as usize, degree: o.degree as usize,
        frames: o.frames, scale_confidence: o.scale_confidence, tonic_confidence: o.tonic_confidence,
        key_prob: o.key_prob, mode_prob: o.mode_prob, profile_scores: o.profile_scores,
        chroma_scale: o.chroma_scale, chroma_bass: o.chroma_bass, chroma_notes: o.chroma_notes,
    }
}

pub fn detune_curve(mono: &[f32], sr: f64, window: f64, hop: f64) -> Vec<DetunePoint> {
    // the curve is computed once: ask for room for one point per hop of the track, plus margin
    let cap = (mono.len() as f64 / sr / hop.max(0.1)) as usize + 16;
    let mut buf = vec![SsDetunePoint::default(); cap];
    let n = unsafe { ss_detune_curve(mono.as_ptr(), mono.len(), sr, window, hop, buf.as_mut_ptr(), cap) };
    buf.truncate(n);
    buf.into_iter()
        .map(|p| DetunePoint { t: p.t, cents: p.cents, ci: p.ci, mass: p.mass, sharpness: p.sharpness, window: p.window, valid: p.valid != 0 })
        .collect()
}
