#![windows_subsystem = "windows"]
//! SongSpec Analyzer desktop app: the website's test page as a native app. Songs (files, or Spotify / Apple Music / YouTube links)
//! are analysed one after another by the engine (songspec-core) on a worker thread; each song shows the same card as the website:
//! Key / BPM / Detune / Time signature tiles, the player with the chord / beat / key / tempo / detune timeline, and the exports.
//! Memory: the engine drops its compiled networks whenever the queue runs empty; playback streams from the file.

mod exports;
mod fetch;
mod player;

use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use eframe::egui::{self, Align2, Color32, FontId, Pos2, Rect, RichText, Sense, Stroke, Vec2};
use songspec_core::analysis::{Analysis, Engine, Options, Stage};

// the website's dark theme (demo/index.html)
const FG: Color32 = Color32::from_rgb(0xec, 0xec, 0xee);
const MUTED: Color32 = Color32::from_rgb(0x9a, 0x9a, 0xa3);
const LINE: Color32 = Color32::from_rgb(0x2c, 0x2d, 0x31);
const ACCENT: Color32 = Color32::from_rgb(0x7a, 0xa2, 0xff);
const CURVE: Color32 = Color32::from_rgb(0x6e, 0xa1, 0xff);
const TARGET: Color32 = Color32::from_rgb(0x5f, 0xd0, 0x7a);
const PLAY: Color32 = Color32::from_rgb(0xff, 0x7a, 0x5c);
const WARN: Color32 = Color32::from_rgb(0xf0, 0xa6, 0x3a);
const BAD: Color32 = Color32::from_rgb(0xef, 0x53, 0x50);

#[derive(Clone)]
enum Status {
    Queued,
    Running(&'static str),
    Done,
    Failed(String),
}

#[derive(Clone)]
enum Source {
    File,
    /// a Spotify / Apple Music song: found on YouTube by title, artist and length
    Song(fetch::Track),
    /// a YouTube video
    Video(String),
}

struct Song {
    source: Source,
    path: Option<PathBuf>,
    name: String,
    status: Status,
    result: Option<Arc<Analysis>>,
}

struct Shared {
    songs: Vec<Song>,
    cache: PathBuf,
    /// links still being read (albums / playlists are expanded into songs first) and the errors they gave
    reading_links: usize,
    link_errors: Vec<String>,
}

/// Add a song to the queue and hand it to the worker.
fn enqueue(shared: &Arc<Mutex<Shared>>, tx: &Sender<usize>, song: Song) {
    let mut g = shared.lock().unwrap();
    g.songs.push(song);
    let _ = tx.send(g.songs.len() - 1);
}

#[derive(Clone, PartialEq)]
enum Ytdlp {
    Missing,
    Installing,
    Ready,
    Failed(String),
}

struct AddDialog {
    links: String,
    error: Option<String>,
}

struct App {
    shared: Arc<Mutex<Shared>>,
    tx: Sender<usize>,
    selected: Option<usize>,
    player: Option<player::Player>,
    play_error: Option<String>,
    add: Option<AddDialog>,
    ytdlp: Arc<Mutex<Ytdlp>>,
    transpose: i32,
    simple: bool,
    tl_view_w: f32,
    /// the timeline's scroll offset last frame, and whether it follows the playhead (off after you scroll it yourself,
    /// back on once the playhead is in view again: otherwise following and your scrolling fight every frame)
    tl_off: f32,
    tl_follow: bool,
    /// playback volume (0..1), kept when switching songs
    volume: f32,
    muted_from: f32,
    flash: Option<(String, Instant)>,
    /// developer flag --screenshot PATH: once every queued song is done, save the window to PATH and quit
    screenshot: Option<PathBuf>,
    shot_requested: bool,
}

fn worker(shared: Arc<Mutex<Shared>>, rx: std::sync::mpsc::Receiver<usize>, ctx: egui::Context) {
    let mut engine: Option<Engine> = None;
    loop {
        let idx = match rx.try_recv() {
            Ok(i) => i,
            Err(_) => {
                if let Some(e) = engine.as_ref() {
                    e.release(); // nothing waiting: free the networks
                }
                match rx.recv() {
                    Ok(i) => i,
                    Err(_) => return,
                }
            }
        };
        let set = |s: Status| {
            shared.lock().unwrap().songs[idx].status = s;
            ctx.request_repaint();
        };
        let (source, cache) = {
            let g = shared.lock().unwrap();
            (g.songs[idx].source.clone(), g.cache.clone())
        };
        let mut meta: Option<fetch::Track> = None;
        if !matches!(source, Source::File) {
            match fetch_audio(&source, &cache, &set, &shared, idx) {
                Ok(t) => meta = t,
                Err(e) => {
                    set(Status::Failed(e));
                    continue;
                }
            }
        }
        let path = shared.lock().unwrap().songs[idx].path.clone().unwrap();
        set(Status::Running("Reading the file..."));
        let audio = match songspec_core::audio::decode_file(&path) {
            Ok(a) => a,
            Err(e) => {
                set(Status::Failed(e.to_string()));
                continue;
            }
        };
        let eng = engine.get_or_insert_with(Engine::embedded);
        let progress = |st: Stage| set(Status::Running(st.label()));
        let r = eng.analyze(&audio, &Options::default(), &progress);
        drop(audio); // the decoded samples are freed as soon as the analysis is done
        let mut g = shared.lock().unwrap();
        match r {
            Ok(mut a) => {
                if let Some(m) = meta {
                    a.track.title = a.track.title.or(Some(m.title));
                    a.track.artist = a.track.artist.or(m.artist);
                }
                g.songs[idx].result = Some(Arc::new(a));
                g.songs[idx].status = Status::Done;
            }
            Err(e) => g.songs[idx].status = Status::Failed(e),
        }
        drop(g);
        ctx.request_repaint();
    }
}

/// A song from a link -> its audio file in the cache (song.path set), and the streaming service's title / artist when known.
fn fetch_audio(source: &Source, cache: &Path, set: &dyn Fn(Status), shared: &Arc<Mutex<Shared>>, idx: usize) -> Result<Option<fetch::Track>, String> {
    let exe = fetch::ytdlp().ok_or("yt-dlp isn't installed (Add songs → Get yt-dlp).")?;
    let (url, mut meta) = match source {
        Source::Song(t) => {
            set(Status::Running("Finding it on YouTube..."));
            (fetch::find_on_youtube(&exe, t)?, Some(t.clone()))
        }
        Source::Video(u) => (u.clone(), None),
        Source::File => unreachable!(),
    };
    set(Status::Running("Downloading the audio..."));
    let (path, title) = fetch::download(&exe, &url, cache)?;
    let mut g = shared.lock().unwrap();
    if meta.is_none() {
        g.songs[idx].name = title.clone();
        meta = Some(fetch::Track { title, artist: None, duration: None });
    }
    g.songs[idx].path = Some(path);
    Ok(meta)
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        install_fonts(&cc.egui_ctx);
        cc.egui_ctx.set_theme(egui::ThemePreference::Dark); // the website's dark look, whatever the system theme (its colours are built for a dark background)
        let shared = Arc::new(Mutex::new(Shared { songs: vec![], cache: fetch::session_cache(), reading_links: 0, link_errors: vec![] }));
        let (tx, rx) = channel();
        let s2 = shared.clone();
        let ctx = cc.egui_ctx.clone();
        std::thread::Builder::new().name("analysis".into()).spawn(move || worker(s2, rx, ctx)).unwrap();
        let raw: Vec<String> = std::env::args().skip(1).collect();
        let screenshot = raw.iter().position(|a| a == "--screenshot").and_then(|i| raw.get(i + 1)).map(PathBuf::from);
        let ytdlp = Arc::new(Mutex::new(if fetch::ytdlp().is_some() { Ytdlp::Ready } else { Ytdlp::Missing }));
        let mut app = App {
            shared, tx, selected: None, player: None, play_error: None, add: None, ytdlp, transpose: 0, simple: false,
            tl_view_w: 800.0, tl_off: 0.0, tl_follow: true, volume: 1.0, muted_from: 1.0, flash: None, screenshot, shot_requested: false,
        };
        // files and links given on the command line (or dropped onto the .exe) start in the queue
        let files: Vec<PathBuf> = raw.iter().map(PathBuf::from).filter(|p| p.is_file() && Some(p) != app.screenshot.as_ref()).collect();
        app.add_files(files);
        let links: Vec<String> = raw.iter().filter(|a| fetch::parse(a).is_some()).cloned().collect();
        app.add_links(&links);
        app
    }

    fn push(&mut self, song: Song) {
        enqueue(&self.shared, &self.tx, song);
    }

    fn add_files(&mut self, files: Vec<PathBuf>) {
        for p in files {
            let ext = p.extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase();
            if !songspec_core::audio::AUDIO_EXTS.contains(&ext.as_str()) {
                continue;
            }
            let name = p.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            self.push(Song { source: Source::File, path: Some(p), name, status: Status::Queued, result: None });
        }
    }

    /// Links are read in the background: an album or playlist becomes one queued song per track.
    fn add_links(&mut self, links: &[String]) {
        if links.is_empty() {
            return;
        }
        self.shared.lock().unwrap().reading_links += links.len();
        let (shared, tx, links) = (self.shared.clone(), self.tx.clone(), links.to_vec());
        std::thread::spawn(move || {
            let exe = fetch::ytdlp();
            for l in links {
                let r = fetch::parse(&l).ok_or_else(|| format!("not a song, album or playlist link: {l}")).and_then(|k| fetch::expand(&k, exe.as_deref()));
                match r {
                    Ok(items) => {
                        for it in items {
                            let (source, name) = match it {
                                fetch::Item::Song(t) => (Source::Song(t.clone()), t.label()),
                                fetch::Item::Video(u) => (Source::Video(u.clone()), u),
                            };
                            enqueue(&shared, &tx, Song { source, path: None, name, status: Status::Queued, result: None });
                        }
                    }
                    Err(e) => shared.lock().unwrap().link_errors.push(e),
                }
                shared.lock().unwrap().reading_links -= 1;
            }
        });
    }

    fn stop(&mut self) {
        self.player = None;
    }
}

fn install_fonts(ctx: &egui::Context) {
    // egui's built-in fonts lack ♭ ♯ ¢ −: use the system's UI font and a symbol font when present (read at start, not bundled).
    // Windows: Segoe UI / Segoe UI Symbol. macOS: SF (or Arial) / Arial Unicode (or Apple Symbols).
    let mut fonts = egui::FontDefinitions::default();
    let sets: [(&str, &[&str]); 2] = [
        ("ui", &[r"C:\Windows\Fonts\segoeui.ttf", "/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]),
        ("symbols", &[r"C:\Windows\Fonts\seguisym.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Apple Symbols.ttf"]),
    ];
    for (i, (name, paths)) in sets.iter().enumerate() {
        if let Some(bytes) = paths.iter().find_map(|p| std::fs::read(p).ok()) {
            fonts.font_data.insert(name.to_string(), Arc::new(egui::FontData::from_owned(bytes)));
            fonts.families.entry(egui::FontFamily::Proportional).or_default().insert(i, name.to_string());
            fonts.families.entry(egui::FontFamily::Monospace).or_default().push(name.to_string());
        }
    }
    ctx.set_fonts(fonts);
}

fn fmt_t(t: f64) -> String {
    let t = t.max(0.0) as u64;
    format!("{}:{:02}", t / 60, t % 60)
}

/// CSS hsl(h s% l% / a) -> Color32
fn hsla(h: f32, s: f32, l: f32, a: f32) -> Color32 {
    let c = (1.0 - (2.0 * l - 1.0).abs()) * s;
    let hp = (h.rem_euclid(360.0)) / 60.0;
    let x = c * (1.0 - (hp % 2.0 - 1.0).abs());
    let (r, g, b) = match hp as i32 {
        0 => (c, x, 0.0),
        1 => (x, c, 0.0),
        2 => (0.0, c, x),
        3 => (0.0, x, c),
        4 => (x, 0.0, c),
        _ => (c, 0.0, x),
    };
    let m = l - c / 2.0;
    let u = |v: f32| ((v + m) * 255.0).round() as u8;
    Color32::from_rgba_unmultiplied(u(r), u(g), u(b), (a * 255.0) as u8)
}

fn tile(ui: &mut egui::Ui, label: &str, body: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::new().stroke(Stroke::new(1.0, LINE)).corner_radius(10.0).inner_margin(egui::Margin::symmetric(12, 10)).show(ui, |ui| {
        ui.set_width(ui.available_width());
        ui.set_min_height(92.0);
        ui.vertical(|ui| {
            ui.label(RichText::new(label.to_uppercase()).size(12.0).color(MUTED));
            body(ui);
        });
    });
}

fn value(ui: &mut egui::Ui, v: &str) {
    ui.label(RichText::new(v).size(26.0).strong().color(FG));
}

fn sub(ui: &mut egui::Ui, v: &str) {
    ui.label(RichText::new(v).size(13.0).color(MUTED));
}

fn chips(ui: &mut egui::Ui, items: &[(String, bool)]) {
    ui.horizontal_wrapped(|ui| {
        for (t, on) in items {
            let c = if *on { ACCENT } else { MUTED };
            // a button-shaped widget knows its size up front, so the row wraps between chips (a Frame would not)
            ui.add(
                egui::Button::new(RichText::new(t).size(12.0).color(c))
                    .fill(Color32::TRANSPARENT)
                    .stroke(Stroke::new(1.0, if *on { ACCENT } else { LINE }))
                    .corner_radius(99.0)
                    .sense(Sense::hover()),
            );
        }
    });
}

fn details(ui: &mut egui::Ui, id: &str, title: &str, lines: &[String]) {
    egui::CollapsingHeader::new(RichText::new(title).size(12.0).color(MUTED)).id_salt(id).default_open(false).show(ui, |ui| {
        for l in lines {
            ui.label(RichText::new(l).size(12.0).color(MUTED));
        }
    });
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        let dropped: Vec<PathBuf> = ctx.input(|i| i.raw.dropped_files.iter().filter_map(|f| f.path.clone()).collect());
        if !dropped.is_empty() {
            self.add_files(dropped);
        }
        let hovering = ctx.input(|i| !i.raw.hovered_files.is_empty());

        egui::TopBottomPanel::top("top").show(ctx, |ui| {
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                ui.heading(RichText::new("SongSpec Analyzer").strong());
                ui.label(RichText::new(format!("v{}", songspec_core::ENGINE_VERSION)).small().color(MUTED));
                ui.separator();
                if ui.add(egui::Button::new(RichText::new("➕ Add songs…").color(Color32::WHITE)).fill(ACCENT)).clicked() {
                    self.add = Some(AddDialog { links: String::new(), error: None });
                }
                ui.label(RichText::new("or drop audio files anywhere in this window").color(MUTED));
            });
            ui.add_space(4.0);
        });

        let (snapshot, reading, link_errors): (Vec<(String, Status, Option<Arc<Analysis>>, Option<PathBuf>)>, usize, Vec<String>) = {
            let g = self.shared.lock().unwrap();
            (g.songs.iter().map(|s| (s.name.clone(), s.status.clone(), s.result.clone(), s.path.clone())).collect(), g.reading_links, g.link_errors.clone())
        };
        if self.selected.is_none() && !snapshot.is_empty() {
            self.selected = Some(0);
        }
        if reading > 0 {
            ctx.request_repaint_after(Duration::from_millis(300));
        }

        egui::SidePanel::left("queue").resizable(true).default_width(290.0).show(ctx, |ui| {
            ui.add_space(4.0);
            ui.label(RichText::new(format!("Queue ({})", snapshot.len())).strong());
            if reading > 0 {
                ui.horizontal(|ui| {
                    ui.spinner();
                    ui.label(RichText::new(format!("reading {reading} link{}…", if reading == 1 { "" } else { "s" })).small().color(ACCENT));
                });
            }
            for e in &link_errors {
                ui.label(RichText::new(e).small().color(BAD));
            }
            ui.separator();
            egui::ScrollArea::vertical().show(ui, |ui| {
                for (i, (name, st, res, _)) in snapshot.iter().enumerate() {
                    let sel = self.selected == Some(i);
                    let sub_t = match st {
                        Status::Queued => "waiting".to_string(),
                        Status::Running(s) => s.to_string(),
                        Status::Failed(e) => format!("failed: {e}"),
                        Status::Done => res
                            .as_ref()
                            .map(|a| format!("{} · {} BPM", a.key.as_ref().map(|k| k.display.key.clone()).unwrap_or("no key".into()), a.tempo.as_ref().and_then(|t| t.bpm_exact).map(|b| format!("{b:.3}")).unwrap_or("–".into())))
                            .unwrap_or_default(),
                    };
                    if ui.add(egui::Button::selectable(sel, RichText::new(name).strong()).wrap_mode(egui::TextWrapMode::Truncate)).clicked() {
                        self.selected = Some(i);
                    }
                    ui.horizontal(|ui| {
                        if matches!(st, Status::Running(_)) {
                            ui.spinner();
                        }
                        let c = match st {
                            Status::Failed(_) => BAD,
                            Status::Running(_) => ACCENT,
                            _ => MUTED,
                        };
                        ui.label(RichText::new(sub_t).small().color(c));
                    });
                    ui.add_space(4.0);
                }
            });
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            if hovering {
                ui.centered_and_justified(|ui| ui.heading("Drop to add to the queue"));
                return;
            }
            let Some(i) = self.selected.filter(|&i| i < snapshot.len()) else {
                ui.centered_and_justified(|ui| ui.heading(RichText::new("Add songs to analyse them").color(MUTED)));
                return;
            };
            let (name, st, res, path) = snapshot[i].clone();
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.label(RichText::new(&name).size(16.0).strong());
                let status = match (&st, &res) {
                    (Status::Done, Some(a)) => format!("done · {:.0} s", a.seconds),
                    (Status::Running(s), _) => s.to_string(),
                    (Status::Failed(_), _) => "error".into(),
                    _ => "queued".into(),
                };
                ui.label(RichText::new(status).size(13.0).color(MUTED));
                if let Status::Failed(e) = &st {
                    ui.colored_label(BAD, e);
                }
                ui.add_space(6.0);
                self.tiles(ui, res.as_deref());
                if let Some(a) = &res {
                    if !a.warnings.is_empty() {
                        ui.label(RichText::new(a.warnings.join(" · ")).size(13.0).color(WARN));
                    }
                    if let Some(p) = &path {
                        ui.add_space(10.0);
                        ui.separator();
                        self.player_section(ui, i, p, a);
                        ui.add_space(8.0);
                        self.actions(ui, a, &name);
                    }
                }
            });
        });

        self.add_dialog(ctx);
        if self.player.as_ref().is_some_and(|p| p.is_playing()) {
            ctx.request_repaint_after(Duration::from_millis(33));
        }
        if self.flash.as_ref().is_some_and(|f| f.1.elapsed() > Duration::from_millis(1200)) {
            self.flash = None;
        }
        self.screenshot_step(ctx, &snapshot);
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.player = None;
        let cache = self.shared.lock().unwrap().cache.clone();
        fetch::clear_cache(&cache); // downloaded songs live only as long as the app
    }
}

impl App {
    fn tiles(&mut self, ui: &mut egui::Ui, a: Option<&Analysis>) {
        let none = |ui: &mut egui::Ui, why: &str| {
            if a.is_some() {
                value(ui, "–");
                sub(ui, why);
            } else {
                ui.label(RichText::new("…").color(MUTED));
            }
        };
        let key = |ui: &mut egui::Ui| match a.and_then(|a| a.key.as_ref()) {
            Some(k) => {
                value(ui, &k.display.key);
                if let Some(n) = &k.display.note {
                    sub(ui, n);
                }
                sub(ui, &format!("{} · {} · {:.0} %", k.camelot, k.open_key, 100.0 * k.confidence));
                if let Some(t) = &k.display.ambiguity_text {
                    sub(ui, t);
                }
                chips(ui, &k.candidates.iter().enumerate().map(|(i, c)| (format!("{} {:.0} %", c.key, 100.0 * c.p), i == 0)).collect::<Vec<_>>());
                if !k.borrows.is_empty() {
                    details(ui, "borrowed", "borrowed chords", &k.borrows.iter().map(|b| format!("{} → {}", b.chords.join(", "), b.from)).collect::<Vec<_>>());
                }
                details(ui, "voted", "what voted", &k.estimators.iter().map(|e| format!("{}: {} ({:.0} %)", e.name, e.key, 100.0 * e.p)).collect::<Vec<_>>());
            }
            None => none(ui, "not measurable on this file"),
        };
        let bpm = |ui: &mut egui::Ui| match a.and_then(|a| a.tempo.as_ref()) {
            Some(t) => {
                // the measured tempo to 3 decimals; the tracker's rounded reading below it
                value(ui, &t.bpm_exact.map(|b| format!("{b:.3}")).unwrap_or("–".into()));
                if let Some(d) = &t.display.bpm {
                    sub(ui, &format!("≈ {d} rounded"));
                }
                if let Some(n) = &t.display.note {
                    sub(ui, n);
                }
                if t.feel == "live" {
                    sub(ui, "played live / drifting");
                }
                let alts: Vec<(String, bool)> = if t.compound {
                    t.display.alternates.iter().map(|x| (x.clone(), false)).collect()
                } else {
                    t.alternates_exact.iter().map(|x| (format!("{x:.3}"), false)).collect()
                };
                chips(ui, &alts);
            }
            None => none(ui, "not measurable on this file"),
        };
        let detune = |ui: &mut egui::Ui| match a.and_then(|a| a.detune.as_ref()) {
            Some(d) => {
                value(ui, &d.display.display);
                let arrow = if d.cents > 0.05 { "↑ sharp" } else if d.cents < -0.05 { "↓ flat" } else { "on pitch" };
                sub(ui, &format!("{arrow} · A4 = {:.2} Hz", d.a4_hz));
                sub(ui, &d.display.fix);
                if d.drift_flag {
                    sub(ui, &format!("tuning moves {} ¢ over the song", d.drift_range_cents.map(|r| format!("{r:.1}")).unwrap_or_default()));
                }
                details(ui, "est", "estimators", &d.estimators.iter().map(|(n, v)| format!("{n}: {}", v.map(|c| format!("{c} ¢")).unwrap_or("–".into()))).collect::<Vec<_>>());
            }
            None => none(ui, "not measurable on this file"),
        };
        let sig = |ui: &mut egui::Ui| match a.and_then(|a| a.tempo.as_ref()) {
            Some(t) => {
                value(ui, &t.signature);
                if let Some(sc) = t.signature_conf {
                    sub(ui, &format!("{:.0} % of bars agree", 100.0 * sc));
                }
            }
            None => none(ui, "no steady beat"),
        };
        if ui.available_width() > 860.0 {
            ui.columns(4, |c| {
                tile(&mut c[0], "Key", key);
                tile(&mut c[1], "BPM", bpm);
                tile(&mut c[2], "Detune", detune);
                tile(&mut c[3], "Time signature", sig);
            });
        } else {
            ui.columns(2, |c| {
                tile(&mut c[0], "Key", key);
                tile(&mut c[1], "BPM", bpm);
            });
            ui.add_space(6.0);
            ui.columns(2, |c| {
                tile(&mut c[0], "Detune", detune);
                tile(&mut c[1], "Time signature", sig);
            });
        }
    }

    fn player_section(&mut self, ui: &mut egui::Ui, idx: usize, path: &Path, a: &Analysis) {
        let dur = a.track.duration;
        let key = exports::song_key(a);
        let key = key.as_ref().map(|(p, m)| (*p, m.as_str()));
        let mine = self.player.as_ref().is_some_and(|p| p.index == idx);
        let pos = if mine { self.player.as_ref().unwrap().pos() } else { 0.0 };
        if mine && self.player.as_ref().unwrap().finished() {
            self.stop();
        }
        let segs = a.chords.as_ref().map(|c| c.segments.as_slice()).unwrap_or(&[]);
        let now = segs.iter().find(|s| s.start <= pos && pos < s.end);
        // now playing
        ui.horizontal(|ui| {
            let chord = now.filter(|s| s.chord != "N").map(|s| exports::transform(&s.chord, self.transpose, self.simple, key)).unwrap_or("–".into());
            ui.add_sized([110.0, 44.0], egui::Label::new(RichText::new(chord).size(34.0).strong().color(FG)));
            let roman = now.and_then(|s| s.roman.clone()).filter(|_| self.transpose == 0).unwrap_or_default();
            ui.add_sized([70.0, 30.0], egui::Label::new(RichText::new(roman).size(18.0).color(MUTED)));
            ui.add_space(12.0);
            ui.label(RichText::new(format!("{} / {}", fmt_t(pos), fmt_t(dur))).size(15.0).color(MUTED));
        });
        // timeline
        if let Some(t) = self.timeline(ui, a, pos, mine && self.player.as_ref().unwrap().is_playing(), key) {
            if !mine {
                self.open_player(idx, path, a);
            }
            if let Some(p) = self.player.as_mut() {
                p.seek(t);
            }
        }
        // controls
        ui.add_space(6.0);
        ui.horizontal_wrapped(|ui| {
            let playing = mine && self.player.as_ref().unwrap().is_playing();
            if ui.add(egui::Button::new(RichText::new(if playing { "⏸ Pause" } else { "▶ Play" }).color(Color32::WHITE)).fill(ACCENT)).clicked() {
                if !mine {
                    self.open_player(idx, path, a);
                }
                if let Some(p) = self.player.as_mut() {
                    p.toggle();
                }
            }
            let click_on = mine && self.player.as_ref().unwrap().click_on;
            if ui.add(egui::Button::new("Click track").selected(click_on)).clicked() {
                if !mine {
                    self.open_player(idx, path, a);
                }
                if let Some(p) = self.player.as_mut() {
                    p.set_click(!click_on);
                }
            }
            let ret_on = mine && self.player.as_ref().unwrap().retune_on;
            let ret_label = match (&a.detune, ret_on) {
                (Some(d), true) => format!("At A440 ({}{:.1} ¢)", if -d.cents >= 0.0 { "+" } else { "" }, -d.cents),
                _ => "Hear at A440".to_string(),
            };
            if ui.add_enabled(a.detune.is_some(), egui::Button::new(ret_label).selected(ret_on)).clicked() {
                if !mine {
                    self.open_player(idx, path, a);
                }
                if let Some(p) = self.player.as_mut() {
                    p.set_retune(!ret_on);
                }
            }
            // volume: click the speaker to mute / unmute
            let icon = if self.volume <= 0.001 { "🔇" } else if self.volume < 0.5 { "🔉" } else { "🔊" };
            if ui.add(egui::Button::new(icon).frame(false)).on_hover_text("Mute / unmute").clicked() {
                self.volume = if self.volume <= 0.001 { self.muted_from.max(0.1) } else { self.muted_from = self.volume; 0.0 };
                if let Some(p) = self.player.as_mut() {
                    p.set_volume(self.volume);
                }
            }
            let r = ui.add(egui::Slider::new(&mut self.volume, 0.0..=1.0).show_value(false)).on_hover_text(format!("Volume {:.0} %", 100.0 * self.volume));
            if r.changed() {
                if let Some(p) = self.player.as_mut() {
                    p.set_volume(self.volume);
                }
            }
            ui.add_space(8.0);
            ui.label(RichText::new("transpose chords").size(13.0).color(MUTED));
            egui::ComboBox::from_id_salt("tr").width(56.0).selected_text(if self.transpose > 0 { format!("+{}", self.transpose) } else { self.transpose.to_string() }).show_ui(ui, |ui| {
                for n in -6..=6 {
                    ui.selectable_value(&mut self.transpose, n, if n > 0 { format!("+{n}") } else { n.to_string() });
                }
            });
            ui.checkbox(&mut self.simple, RichText::new("triads only").size(13.0).color(MUTED));
            if let Some(e) = &self.play_error {
                ui.colored_label(BAD, e);
            }
        });
    }

    fn open_player(&mut self, idx: usize, path: &Path, a: &Analysis) {
        self.player = None;
        let (beats, downs) = a.tempo.as_ref().map(|t| (t.beats.clone(), t.downbeats.clone())).unwrap_or_default();
        match player::Player::open(idx, path, a.track.duration, &beats, &downs, a.detune.as_ref().map(|d| d.cents)) {
            Ok(mut p) => {
                p.set_volume(self.volume);
                self.player = Some(p);
                self.play_error = None;
            }
            Err(e) => self.play_error = Some(e),
        }
    }

    /// The website's timeline: beats, chords (coloured by root), key, tempo curve, detune drift, time marks, the playhead.
    /// Returns a time to seek to when it was clicked.
    fn timeline(&mut self, ui: &mut egui::Ui, a: &Analysis, pos: f64, playing: bool, key: Option<(usize, &str)>) -> Option<f64> {
        let dur = a.track.duration;
        let pxs = (1600.0 / dur).clamp(6.0, 24.0) as f32;
        let w = (900.0f32).max((dur as f32 * pxs).round() + 60.0);
        let h = 182.0f32;
        let x = |t: f64| 50.0 + t as f32 * pxs;
        let mut seek = None;
        let mut sa = egui::ScrollArea::horizontal().id_salt("timeline").auto_shrink([false, true]);
        let px = x(pos);
        let in_view = |off: f32, view: f32| px >= off + 40.0 && px <= off + view - 40.0;
        let mut set_to = None;
        if playing && self.tl_follow && !in_view(self.tl_off, self.tl_view_w) {
            let o = (px - 80.0).max(0.0);
            sa = sa.horizontal_scroll_offset(o);
            set_to = Some(o);
        }
        let frame = egui::Frame::new().stroke(Stroke::new(1.0, LINE)).corner_radius(10.0);
        frame.show(ui, |ui| {
            let out = sa.show(ui, |ui| {
                let (rect, resp) = ui.allocate_exact_size(Vec2::new(w, h), Sense::click());
                let p = ui.painter_at(rect);
                let o = rect.min;
                let at = |xx: f32, yy: f32| Pos2::new(o.x + xx, o.y + yy);
                let small = FontId::proportional(10.0);
                let text = FontId::proportional(11.0);
                let lane = |y: f32, s: &str| {
                    p.text(at(4.0, y), Align2::LEFT_TOP, s, small.clone(), MUTED);
                };
                if let Some(t) = &a.tempo {
                    if !t.beats.is_empty() {
                        lane(0.0, "beats");
                        for &b in &t.beats {
                            let d = t.downbeats.iter().any(|q| (q - b).abs() < 0.0005);
                            p.line_segment([at(x(b), if d { 2.0 } else { 9.0 }), at(x(b), 16.0)], Stroke::new(if d { 1.2 } else { 0.5 }, MUTED));
                        }
                    }
                }
                if let Some(c) = &a.chords {
                    lane(24.0, "chords");
                    for s in c.segments.iter().filter(|s| s.chord != "N") {
                        let name = exports::transform(&s.chord, self.transpose, self.simple, key);
                        let r = songspec_core::notation::split_chord(&name).map(|x| x.0).unwrap_or(0);
                        let ww = (x(s.end) - x(s.start)).max(1.0);
                        p.rect_filled(Rect::from_min_size(at(x(s.start), 22.0), Vec2::new((ww - 1.0).max(1.0), 30.0)), 3.0, hsla(r as f32 * 30.0, 0.6, 0.55, 0.35));
                        if ww > 24.0 {
                            p.text(at(x(s.start) + 3.0, 24.0), Align2::LEFT_TOP, &name, text.clone(), FG);
                            if self.transpose == 0 {
                                p.text(at(x(s.start) + 3.0, 38.0), Align2::LEFT_TOP, s.roman.as_deref().unwrap_or(""), small.clone(), MUTED);
                            }
                        }
                    }
                }
                if let Some(k) = &a.key {
                    lane(58.0, "key");
                    let secs: Vec<(f64, f64, String)> = if k.sections.is_empty() {
                        vec![(0.0, dur, k.display.key.clone())]
                    } else {
                        k.sections.iter().map(|s| (s.start, s.end, s.display.clone())).collect()
                    };
                    for (i, (s0, s1, d)) in secs.iter().enumerate() {
                        p.rect_filled(Rect::from_min_max(at(x(*s0), 58.0), at(x(*s1), 76.0)), 3.0, hsla(210.0 + i as f32 * 70.0, 0.5, 0.5, 0.25));
                        p.text(at(x(*s0) + 4.0, 60.0), Align2::LEFT_TOP, d, text.clone(), FG);
                    }
                }
                if let Some(t) = &a.tempo {
                    if t.curve.t.len() > 2 {
                        lane(84.0, "tempo");
                        let mut v = t.curve.bpm.clone();
                        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
                        let (lo, hi) = (v[(v.len() as f64 * 0.02) as usize], v[((v.len() as f64 * 0.98) as usize).min(v.len() - 1)]);
                        let y = |b: f64| 136.0 - (((b.clamp(lo, hi) - lo) / (hi - lo).max(1e-6)) * 46.0) as f32;
                        p.line_segment([at(x(0.0), y(t.bpm_raw)), at(x(dur), y(t.bpm_raw))], Stroke::new(1.5, TARGET));
                        let pts: Vec<Pos2> = t.curve.t.iter().zip(&t.curve.bpm).map(|(tt, b)| at(x(*tt), y(*b))).collect();
                        p.add(egui::Shape::line(pts, Stroke::new(1.0, CURVE)));
                        p.text(at(4.0, 100.0), Align2::LEFT_TOP, format!("{hi:.0}"), small.clone(), MUTED);
                        p.text(at(4.0, 126.0), Align2::LEFT_TOP, format!("{lo:.0}"), small.clone(), MUTED);
                    }
                }
                if let Some(d) = &a.detune {
                    if d.drift.len() > 1 {
                        lane(146.0, "detune");
                        let y = |c: f64| 164.0 - (c - d.cents).clamp(-14.0, 14.0) as f32;
                        p.line_segment([at(x(0.0), 164.0), at(x(dur), 164.0)], Stroke::new(1.0, LINE));
                        let pts: Vec<Pos2> = d.drift.iter().map(|q| at(x(q.t), y(q.cents))).collect();
                        p.add(egui::Shape::line(pts, Stroke::new(1.5, ACCENT)));
                    }
                }
                let mut t = 0.0;
                while t <= dur {
                    p.text(at(x(t) - 8.0, h - 12.0), Align2::LEFT_TOP, fmt_t(t), small.clone(), MUTED);
                    t += 30.0;
                }
                p.line_segment([at(px, 0.0), at(px, h - 12.0)], Stroke::new(2.0, PLAY));
                if resp.clicked() {
                    if let Some(c) = resp.interact_pointer_pos() {
                        seek = Some((((c.x - o.x - 50.0) / pxs) as f64).max(0.0));
                    }
                }
                if resp.hovered() {
                    ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
                }
            });
            self.tl_view_w = out.inner_rect.width();
            let off = out.state.offset.x;
            if set_to.is_none() && (off - self.tl_off).abs() > 0.5 {
                self.tl_follow = false; // you scrolled it
            } else if in_view(off, self.tl_view_w) {
                self.tl_follow = true;
            }
            self.tl_off = off;
        });
        seek
    }

    fn actions(&mut self, ui: &mut egui::Ui, a: &Analysis, name: &str) {
        ui.horizontal_wrapped(|ui| {
            let flash = |s: &mut Self, t: &str| s.flash = Some((t.to_string(), Instant::now()));
            if ui.button("Copy tracker row").clicked() {
                ui.ctx().copy_text(exports::tracker_row(a, name));
                flash(self, "Tracker row copied");
            }
            if ui.button("Copy progression").clicked() {
                ui.ctx().copy_text(exports::progression_text(a, self.transpose, self.simple));
                flash(self, "Progression copied");
            }
            let stem = exports::title_of(a, name);
            if ui.button("MIDI").clicked() {
                if let Some(bytes) = exports::midi(a, self.transpose, self.simple) {
                    if let Some(p) = rfd::FileDialog::new().set_file_name(format!("{stem}.mid")).add_filter("MIDI", &["mid"]).save_file() {
                        let _ = std::fs::write(p, bytes);
                        flash(self, "MIDI saved");
                    }
                }
            }
            if ui.button("ChordPro").clicked() {
                if let Some(p) = rfd::FileDialog::new().set_file_name(format!("{stem}.cho")).add_filter("ChordPro", &["cho"]).save_file() {
                    let _ = std::fs::write(p, exports::chordpro(a, name, self.transpose, self.simple));
                    flash(self, "ChordPro saved");
                }
            }
            if ui.button("JSON").clicked() {
                if let Some(p) = rfd::FileDialog::new().set_file_name(format!("{stem}.json")).add_filter("JSON", &["json"]).save_file() {
                    let _ = std::fs::write(p, serde_json::to_string_pretty(a).unwrap_or_default());
                    flash(self, "JSON saved");
                }
            }
            if let Some((t, _)) = &self.flash {
                ui.label(RichText::new(t).color(TARGET));
            }
        });
    }

    fn add_dialog(&mut self, ctx: &egui::Context) {
        let Some(mut d) = self.add.take() else { return };
        let mut keep = true;
        let mut files: Option<Vec<PathBuf>> = None;
        let mut links: Option<Vec<String>> = None;
        let yt = self.ytdlp.lock().unwrap().clone();
        let modal = egui::Modal::new(egui::Id::new("add_songs")).show(ctx, |ui| {
            ui.set_width(480.0);
            ui.heading("Add songs");
            ui.add_space(8.0);
            ui.label(RichText::new("Upload file").strong());
            if ui.button("📁  Choose audio files…").clicked() {
                if let Some(f) = rfd::FileDialog::new().add_filter("Audio", songspec_core::audio::AUDIO_EXTS).pick_files() {
                    files = Some(f);
                }
            }
            sub(ui, "MP3, WAV, FLAC, M4A, OGG, AIFF. You can also drop files on the window.");
            ui.add_space(12.0);
            ui.label(RichText::new("Spotify, Apple Music or YouTube link (songs, albums, playlists)").strong());
            ui.add(egui::TextEdit::multiline(&mut d.links).hint_text("https://open.spotify.com/track/…\nhttps://www.youtube.com/watch?v=…").desired_rows(3).desired_width(f32::INFINITY));
            sub(ui, "One per line. A whole album or playlist is added song by song. Spotify and Apple Music songs are found on YouTube by title, artist and length.");
            match &yt {
                Ytdlp::Ready => {}
                Ytdlp::Missing | Ytdlp::Failed(_) => {
                    ui.add_space(4.0);
                    ui.label(RichText::new("Links need yt-dlp (free, about 20–40 MB from its official GitHub release), which isn't installed yet.").size(13.0).color(WARN));
                    if ui.button("Get yt-dlp").clicked() {
                        *self.ytdlp.lock().unwrap() = Ytdlp::Installing;
                        let (state, ctx2) = (self.ytdlp.clone(), ctx.clone());
                        std::thread::spawn(move || {
                            let r = fetch::install_ytdlp();
                            *state.lock().unwrap() = match r {
                                Ok(_) => Ytdlp::Ready,
                                Err(e) => Ytdlp::Failed(e),
                            };
                            ctx2.request_repaint();
                        });
                    }
                    if let Ytdlp::Failed(e) = &yt {
                        ui.colored_label(BAD, e);
                    }
                }
                Ytdlp::Installing => {
                    ui.horizontal(|ui| {
                        ui.spinner();
                        sub(ui, "Getting yt-dlp…");
                    });
                }
            }
            if let Some(e) = &d.error {
                ui.colored_label(BAD, e);
            }
            ui.add_space(6.0);
            sub(ui, "Downloaded audio stays in a temporary cache while the app is open and is deleted when you close it.");
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                let can = yt == Ytdlp::Ready && !d.links.trim().is_empty();
                if ui.add_enabled(can, egui::Button::new(RichText::new("Add links").color(Color32::WHITE)).fill(ACCENT)).clicked() {
                    let all: Vec<String> = d.links.lines().map(|l| l.trim().to_string()).filter(|l| !l.is_empty()).collect();
                    let bad: Vec<&String> = all.iter().filter(|l| fetch::parse(l).is_none()).collect();
                    if bad.is_empty() {
                        links = Some(all);
                    } else {
                        d.error = Some(format!("Not a Spotify / Apple Music / YouTube song, album or playlist link: {}", bad[0]));
                    }
                }
                if ui.button("Cancel").clicked() {
                    keep = false;
                }
            });
        });
        if modal.should_close() {
            keep = false;
        }
        if let Some(f) = files {
            self.add_files(f);
            keep = false;
        }
        if let Some(l) = links {
            self.add_links(&l);
            keep = false;
        }
        if keep {
            self.add = Some(d);
        }
        if yt == Ytdlp::Installing {
            ctx.request_repaint_after(Duration::from_millis(200));
        }
    }

    fn screenshot_step(&mut self, ctx: &egui::Context, snapshot: &[(String, Status, Option<Arc<Analysis>>, Option<PathBuf>)]) {
        let Some(path) = self.screenshot.clone() else { return };
        let all_done = !snapshot.is_empty() && snapshot.iter().all(|s| matches!(s.1, Status::Done | Status::Failed(_)));
        let size = ctx.input(|i| i.viewport().inner_rect.map(|r| r.size()).unwrap_or_default());
        if all_done && !self.shot_requested && (size.x < 10.0 || size.y < 10.0) {
            ctx.send_viewport_cmd(egui::ViewportCommand::Minimized(false));
            ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
            ctx.request_repaint_after(Duration::from_millis(300));
            return;
        }
        if all_done && !self.shot_requested {
            self.shot_requested = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(Default::default()));
        }
        let shot = ctx.input(|i| i.raw.events.iter().find_map(|e| match e {
            egui::Event::Screenshot { image, .. } => Some(image.clone()),
            _ => None,
        }));
        if let Some(img) = shot {
            let f = std::fs::File::create(&path).unwrap();
            let mut enc = png::Encoder::new(std::io::BufWriter::new(f), img.size[0] as u32, img.size[1] as u32);
            enc.set_color(png::ColorType::Rgba);
            enc.set_depth(png::BitDepth::Eight);
            let mut w = enc.write_header().unwrap();
            let bytes: Vec<u8> = img.pixels.iter().flat_map(|p| p.to_array()).collect();
            w.write_image_data(&bytes).unwrap();
            drop(w);
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
        ctx.request_repaint_after(Duration::from_millis(200));
    }
}

fn main() -> eframe::Result<()> {
    #[allow(unused_mut)]
    let mut viewport = egui::ViewportBuilder::default().with_inner_size([1240.0, 860.0]).with_min_inner_size([760.0, 520.0]).with_drag_and_drop(true);
    #[cfg(has_icon)]
    if let Ok(icon) = eframe::icon_data::from_png_bytes(include_bytes!("../assets/icon_256.png")) {
        viewport = viewport.with_icon(icon);
    }
    let options = eframe::NativeOptions { viewport, ..Default::default() };
    eframe::run_native("SongSpec Analyzer", options, Box::new(|cc| Ok(Box::new(App::new(cc)))))
}
