//! Decoding (symphonia, pure Rust): WAV, FLAC, MP3, OGG Vorbis, AAC / M4A, AIFF, ALAC. Same shape as the service's
//! audio/decode.py: samples at the file's own rate, every channel, float32; `mono()` is the channel mean.

use std::io::Cursor;

use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::{DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;

/// Extensions the decoder handles (the file picker's filter).
pub const AUDIO_EXTS: &[&str] = &["wav", "flac", "mp3", "ogg", "oga", "m4a", "aac", "mp4", "aiff", "aif", "aifc", "caf", "wave"];

#[derive(Debug, Clone)]
pub struct Audio {
    /// interleaved samples, `channels` per frame
    pub samples: Vec<f32>,
    pub channels: usize,
    pub sr: u32,
    pub title: Option<String>,
    pub artist: Option<String>,
}

impl Audio {
    pub fn frames(&self) -> usize {
        self.samples.len() / self.channels.max(1)
    }
    pub fn duration(&self) -> f64 {
        self.frames() as f64 / self.sr as f64
    }
    /// Channel mean, like `samples.mean(axis=1)` in the service (accumulated in f32 as numpy does for float32 input).
    pub fn mono(&self) -> Vec<f32> {
        let c = self.channels.max(1);
        if c == 1 {
            return self.samples.clone();
        }
        self.samples.chunks_exact(c).map(|f| f.iter().sum::<f32>() / c as f32).collect()
    }
}

#[derive(Debug)]
pub struct DecodeError(pub String);

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for DecodeError {}

/// Decode a whole file held in memory. `ext` (e.g. "mp3") helps pick the container.
pub fn decode(bytes: Vec<u8>, ext: Option<&str>) -> Result<Audio, DecodeError> {
    let mss = MediaSourceStream::new(Box::new(Cursor::new(bytes)), Default::default());
    let mut hint = Hint::new();
    if let Some(e) = ext {
        hint.with_extension(e);
    }
    let fmt_opts = FormatOptions { enable_gapless: true, ..Default::default() };
    let mut probed = symphonia::default::get_probe()
        .format(&hint, mss, &fmt_opts, &MetadataOptions::default())
        .map_err(|e| DecodeError(format!("Could not read this file as audio ({e}).")))?;
    let (mut title, mut artist) = (None, None);
    let mut read_tags = |rev: &symphonia::core::meta::MetadataRevision| {
        for t in rev.tags() {
            match t.std_key {
                Some(symphonia::core::meta::StandardTagKey::TrackTitle) => title = Some(t.value.to_string()),
                Some(symphonia::core::meta::StandardTagKey::Artist) => artist = Some(t.value.to_string()),
                _ => {}
            }
        }
    };
    if let Some(m) = probed.metadata.get() {
        if let Some(rev) = m.current() {
            read_tags(rev);
        }
    }
    let mut format = probed.format;
    if let Some(rev) = format.metadata().current() {
        read_tags(rev);
    }
    let track = format
        .tracks()
        .iter()
        .find(|t| t.codec_params.codec != CODEC_TYPE_NULL)
        .ok_or_else(|| DecodeError("This file has no audio track.".into()))?;
    let track_id = track.id;
    let mut decoder = symphonia::default::get_codecs()
        .make(&track.codec_params, &DecoderOptions::default())
        .map_err(|e| DecodeError(format!("Unsupported audio codec ({e}).")))?;
    let mut sr = track.codec_params.sample_rate.unwrap_or(0);
    let mut channels = track.codec_params.channels.map(|c| c.count()).unwrap_or(0);
    let mut samples: Vec<f32> = Vec::new();
    let mut buf: Option<SampleBuffer<f32>> = None;
    loop {
        let packet = match format.next_packet() {
            Ok(p) => p,
            Err(SymError::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(SymError::ResetRequired) => break,
            Err(e) => return Err(DecodeError(format!("Could not read this file as audio ({e})."))),
        };
        if packet.track_id() != track_id {
            continue;
        }
        let decoded = match decoder.decode(&packet) {
            Ok(d) => d,
            Err(SymError::DecodeError(_)) => continue, // a damaged frame: skip it, as ffmpeg does
            Err(e) => return Err(DecodeError(format!("Could not decode this file ({e})."))),
        };
        let spec = *decoded.spec();
        sr = spec.rate;
        channels = spec.channels.count();
        if buf.as_ref().map_or(true, |b| b.capacity() < decoded.capacity() * channels) {
            buf = Some(SampleBuffer::new(decoded.capacity() as u64, spec));
        }
        let b = buf.as_mut().unwrap();
        b.copy_interleaved_ref(decoded);
        samples.extend_from_slice(b.samples());
    }
    if samples.is_empty() || sr == 0 || channels == 0 {
        return Err(DecodeError("Could not read this file as audio (no samples).".into()));
    }
    samples.shrink_to_fit();
    Ok(Audio { samples, channels, sr, title, artist })
}

/// Decode a file from disk (the extension picks the container).
pub fn decode_file(path: &std::path::Path) -> Result<Audio, DecodeError> {
    let bytes = std::fs::read(path).map_err(|e| DecodeError(format!("Could not open the file ({e}).")))?;
    let ext = path.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_lowercase());
    decode(bytes, ext.as_deref())
}
