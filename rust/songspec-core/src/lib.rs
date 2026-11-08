//! SongSpec analysis engine: audio in, key / BPM / chords / detune out.
//!
//! A port of the Python service (`service/songspec_analyzer`), stage for stage, so the desktop app and the website agree.
//! No GUI, windowing or OS APIs here: the crate works on decoded samples and bytes, so it can be compiled for WASM later
//! (with the `centsible` feature off or Centsible built for the target).

pub const ENGINE_VERSION: &str = "1.1.0";

pub mod audio;
#[cfg(feature = "centsible")]
pub mod centsible;
pub mod dsp;
pub mod models;
pub mod beatthis;
pub mod np;
pub mod bpmfix;
pub mod tempo;
