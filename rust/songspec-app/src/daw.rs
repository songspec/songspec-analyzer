//! "Drag into your DAW": the song as a file a DAW can open, dragged out of the window.
//!
//! A downloaded song is AAC (.m4a), which not every DAW opens (FL Studio doesn't), and it lives in the session cache, which is
//! deleted when the app closes; DAWs such as Ableton and FL reference a dropped file where it is. So a downloaded song is decoded to
//! a 16-bit WAV in the background, and on drag it is copied to Music/SongSpec Analyzer (named with its BPM and key) and that copy
//! is what the DAW gets. Your own files are dragged as they are.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

#[derive(Clone)]
pub enum Prep {
    Working,
    Ready(PathBuf),
    Failed(String),
}

/// Music/SongSpec Analyzer (made on first use).
pub fn export_dir() -> PathBuf {
    let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
    home.join("Music").join("SongSpec Analyzer")
}

/// "Artist - Title (92 BPM, G Phrygian Dominant).wav", with the characters file systems refuse taken out.
pub fn file_name(name: &str, bpm: Option<&str>, key: Option<&str>) -> String {
    // a file's own name loses its audio extension; a video title ("Song ft. X") keeps its dots
    let stem = match name.rsplit_once('.') {
        Some((s, ext)) if songspec_core::audio::AUDIO_EXTS.contains(&ext.to_ascii_lowercase().as_str()) => s.to_string(),
        _ => name.to_string(),
    };
    let tags: Vec<String> = [bpm.map(|b| format!("{b} BPM")), key.map(str::to_string)].into_iter().flatten().collect();
    let full = if tags.is_empty() { stem } else { format!("{stem} ({})", tags.join(", ")) };
    let mut clean: String = full.chars().map(|c| if "<>:\"/\\|?*".contains(c) || c.is_control() { ' ' } else { c }).collect();
    clean = clean.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut clean: String = clean.chars().take(150).collect();
    while clean.ends_with('.') || clean.ends_with(' ') {
        clean.pop();
    }
    if clean.is_empty() {
        clean = "song".into();
    }
    format!("{clean}.wav")
}

/// Decode `src` and write it to `dst` as 16-bit PCM WAV (written to a temporary name first, so a half-written file is never used).
pub fn write_wav(src: &Path, dst: &Path) -> Result<(), String> {
    use std::io::Write;
    let a = songspec_core::audio::decode_file(src).map_err(|e| e.to_string())?;
    let ch = a.channels.max(1) as u16;
    let data_len = (a.samples.len() * 2) as u32;
    if let Some(d) = dst.parent() {
        std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    let tmp = dst.with_extension("wav.part");
    let f = std::fs::File::create(&tmp).map_err(|e| e.to_string())?;
    let mut w = std::io::BufWriter::new(f);
    let mut put = |b: &[u8]| w.write_all(b).map_err(|e| e.to_string());
    put(b"RIFF")?;
    put(&(36 + data_len).to_le_bytes())?;
    put(b"WAVEfmt ")?;
    put(&16u32.to_le_bytes())?;
    put(&1u16.to_le_bytes())?; // PCM
    put(&ch.to_le_bytes())?;
    put(&a.sr.to_le_bytes())?;
    put(&(a.sr * ch as u32 * 2).to_le_bytes())?;
    put(&(ch * 2).to_le_bytes())?;
    put(&16u16.to_le_bytes())?;
    put(b"data")?;
    put(&data_len.to_le_bytes())?;
    let mut buf = Vec::with_capacity(1 << 16);
    for chunk in a.samples.chunks(1 << 15) {
        buf.clear();
        for &x in chunk {
            buf.extend_from_slice(&((x.clamp(-1.0, 1.0) * 32767.0).round() as i16).to_le_bytes());
        }
        put(&buf)?;
    }
    w.flush().map_err(|e| e.to_string())?;
    drop(w);
    std::fs::rename(&tmp, dst).map_err(|e| e.to_string())
}

/// Start decoding a downloaded song to a WAV in the session cache.
pub fn prepare(src: PathBuf, cache: &Path, ctx: eframe::egui::Context) -> Arc<Mutex<Prep>> {
    let state = Arc::new(Mutex::new(Prep::Working));
    let (st, out) = (state.clone(), cache.join("daw").join(format!("{}.wav", src.file_stem().unwrap_or_default().to_string_lossy())));
    std::thread::spawn(move || {
        let r = if out.is_file() { Ok(()) } else { write_wav(&src, &out) };
        *st.lock().unwrap() = match r {
            Ok(()) => Prep::Ready(out),
            Err(e) => Prep::Failed(e),
        };
        ctx.request_repaint();
    });
    state
}

/// The file to hand to the DAW: a prepared WAV is copied into Music/SongSpec Analyzer under its nice name (once); a file of your
/// own is used as it is.
pub fn deliver(prepared: Option<&Path>, original: &Path, nice_name: &str) -> Result<PathBuf, String> {
    let Some(wav) = prepared else { return Ok(original.to_path_buf()) };
    let dst = export_dir().join(nice_name);
    let same = std::fs::metadata(&dst).ok().zip(std::fs::metadata(wav).ok()).is_some_and(|(a, b)| a.len() == b.len());
    if !same {
        std::fs::create_dir_all(export_dir()).map_err(|e| e.to_string())?;
        std::fs::copy(wav, &dst).map_err(|e| e.to_string())?;
    }
    Ok(dst)
}

/// Drag `file` out of the window. `done` is called with true when it was dropped somewhere.
#[cfg(any(windows, target_os = "macos"))]
pub fn start_drag(frame: &eframe::Frame, file: &Path, done: impl Fn(bool) + Send + 'static) -> Result<(), String> {
    let path = std::fs::canonicalize(file).map_err(|e| e.to_string())?;
    #[cfg(windows)]
    let path = PathBuf::from(path.to_string_lossy().trim_start_matches(r"\\?\")); // DAWs don't all take \\?\ paths
    let image = drag::Image::Raw(include_bytes!("../assets/drag.png").to_vec());
    drag::start_drag(frame, drag::DragItem::Files(vec![path]), image, move |r, _| done(matches!(r, drag::DragResult::Dropped)), drag::Options::default())
        .map_err(|e| e.to_string())
}

#[cfg(not(any(windows, target_os = "macos")))]
pub fn start_drag(_frame: &eframe::Frame, _file: &Path, _done: impl Fn(bool) + Send + 'static) -> Result<(), String> {
    Err("dragging out of the window isn't available on this system; use Show in folder".into())
}

/// Show the file in Explorer / the Finder.
pub fn reveal(file: &Path) {
    #[cfg(windows)]
    let _ = std::process::Command::new("explorer").arg(format!("/select,{}", file.display())).spawn();
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("open").arg("-R").arg(file).spawn();
    #[cfg(not(any(windows, target_os = "macos")))]
    let _ = std::process::Command::new("xdg-open").arg(file.parent().unwrap_or(file)).spawn();
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A WAV written from a decoded file decodes back to the same samples (16-bit), rate and channels.
    #[test]
    fn wav_round_trip() {
        let dir = std::env::temp_dir().join(format!("songspec-daw-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        // a 0.5 s stereo source: a 440 Hz tone left, 660 Hz right, at 48 kHz
        let (sr, n) = (48000u32, 24000usize);
        let src_samples: Vec<f32> = (0..n).flat_map(|i| {
            let t = i as f32 / sr as f32;
            [0.5 * (std::f32::consts::TAU * 440.0 * t).sin(), 0.25 * (std::f32::consts::TAU * 660.0 * t).sin()]
        }).collect();
        let src = dir.join("src.wav");
        {
            use std::io::Write;
            let mut f = std::fs::File::create(&src).unwrap();
            let len = (src_samples.len() * 2) as u32;
            let mut h = Vec::new();
            h.extend(b"RIFF"); h.extend((36 + len).to_le_bytes()); h.extend(b"WAVEfmt "); h.extend(16u32.to_le_bytes());
            h.extend(1u16.to_le_bytes()); h.extend(2u16.to_le_bytes()); h.extend(sr.to_le_bytes()); h.extend((sr * 4).to_le_bytes());
            h.extend(4u16.to_le_bytes()); h.extend(16u16.to_le_bytes()); h.extend(b"data"); h.extend(len.to_le_bytes());
            for x in &src_samples { h.extend(((x * 32767.0).round() as i16).to_le_bytes()); }
            f.write_all(&h).unwrap();
        }
        let out = dir.join("sub").join("out.wav");
        write_wav(&src, &out).unwrap();
        assert!(!out.with_extension("wav.part").exists());
        let a = songspec_core::audio::decode_file(&src).unwrap();
        let b = songspec_core::audio::decode_file(&out).unwrap();
        assert_eq!((b.sr, b.channels, b.samples.len()), (sr, 2, src_samples.len()));
        let worst = a.samples.iter().zip(&b.samples).map(|(x, y)| (x - y).abs()).fold(0f32, f32::max);
        assert!(worst < 1.0 / 16000.0, "{worst}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn names_are_safe() {
        assert_eq!(file_name("Beyoncé - Beautiful Liar.m4a", Some("92"), Some("G Phrygian Dominant")), "Beyoncé - Beautiful Liar (92 BPM, G Phrygian Dominant).wav");
        assert_eq!(file_name("ARE WE STILL FRIENDS?", Some("185.4"), None), "ARE WE STILL FRIENDS (185.4 BPM).wav");
        assert_eq!(file_name("a/b:c*", None, None), "a b c.wav");
        assert_eq!(file_name("...", None, None), "song.wav");
    }
}
