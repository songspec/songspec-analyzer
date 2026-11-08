#![windows_subsystem = "windows"]
//! SongSpec Analyzer desktop app: drop songs in, they are analysed one after another by the engine (songspec-core) on a worker
//! thread; pick one to see its key, BPM, detune, borrowed chords and what voted, and play it.
//! Memory: the engine drops its compiled networks whenever the queue runs empty, and playback streams from the file.

mod links;

use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use eframe::egui::{self, Color32, RichText};
use songspec_core::analysis::{Analysis, Engine, Options, Stage};

#[derive(Clone)]
enum Status {
    Queued,
    Running(&'static str),
    Done,
    Failed(String),
}

struct Song {
    path: PathBuf,
    name: String,
    status: Status,
    result: Option<Analysis>,
}

#[derive(Default)]
struct Shared {
    songs: Vec<Song>,
}

enum LinkState {
    Busy,
    Done(links::Links),
    Failed(String),
}

struct Playback {
    _device: rodio::MixerDeviceSink,
    player: rodio::Player,
    index: usize,
    length: Option<f64>,
}

struct App {
    shared: Arc<Mutex<Shared>>,
    tx: Sender<usize>,
    selected: Option<usize>,
    playback: Option<Playback>,
    play_error: Option<String>,
    links: Arc<Mutex<std::collections::HashMap<usize, LinkState>>>,
    /// developer flag --screenshot PATH [--expand]: once every queued song is done, save the window to PATH and quit
    screenshot: Option<PathBuf>,
    shot_requested: bool,
    expand: bool,
}

const ACCENT: Color32 = Color32::from_rgb(110, 170, 255);

fn worker(shared: Arc<Mutex<Shared>>, rx: std::sync::mpsc::Receiver<usize>, ctx: egui::Context) {
    let mut engine: Option<Engine> = None;
    loop {
        // block for the next song; when nothing more is waiting, free the engine's networks first
        let idx = match rx.try_recv() {
            Ok(i) => i,
            Err(_) => {
                if let Some(e) = engine.as_ref() {
                    e.release();
                }
                match rx.recv() {
                    Ok(i) => i,
                    Err(_) => return,
                }
            }
        };
        let path = shared.lock().unwrap().songs[idx].path.clone();
        let set = |s: Status| {
            shared.lock().unwrap().songs[idx].status = s;
            ctx.request_repaint();
        };
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
            Ok(a) => {
                g.songs[idx].result = Some(a);
                g.songs[idx].status = Status::Done;
            }
            Err(e) => g.songs[idx].status = Status::Failed(e),
        }
        drop(g);
        ctx.request_repaint();
    }
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        install_fonts(&cc.egui_ctx);
        let shared = Arc::new(Mutex::new(Shared::default()));
        let (tx, rx) = channel();
        let s2 = shared.clone();
        let ctx = cc.egui_ctx.clone();
        std::thread::Builder::new().name("analysis".into()).spawn(move || worker(s2, rx, ctx)).unwrap();
        let raw: Vec<String> = std::env::args().skip(1).collect();
        let screenshot = raw.iter().position(|a| a == "--screenshot").and_then(|i| raw.get(i + 1)).map(PathBuf::from);
        let expand = raw.iter().any(|a| a == "--expand");
        let mut app = App { shared, tx, selected: None, playback: None, play_error: None, links: Default::default(), screenshot, shot_requested: false, expand };
        // files given on the command line (or dropped onto the .exe) start in the queue
        let args: Vec<PathBuf> = raw.iter().map(PathBuf::from).filter(|p| p.is_file() && Some(p) != app.screenshot.as_ref()).collect();
        app.add_files(args);
        app
    }

    fn add_files(&mut self, files: Vec<PathBuf>) {
        let mut g = self.shared.lock().unwrap();
        for p in files {
            let ext = p.extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase();
            if !songspec_core::audio::AUDIO_EXTS.contains(&ext.as_str()) {
                continue;
            }
            let name = p.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            g.songs.push(Song { path: p, name, status: Status::Queued, result: None });
            let i = g.songs.len() - 1;
            let _ = self.tx.send(i);
            if self.selected.is_none() {
                self.selected = Some(i);
            }
        }
    }

    fn play(&mut self, idx: usize, path: &Path, length: Option<f64>) {
        self.stop();
        let r = (|| -> Result<Playback, String> {
            let device = rodio::DeviceSinkBuilder::open_default_sink().map_err(|e| format!("no audio output ({e})"))?;
            let player = rodio::Player::connect_new(device.mixer());
            let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
            let src = rodio::Decoder::try_from(file).map_err(|e| format!("can't play this file ({e})"))?;
            player.append(src);
            Ok(Playback { _device: device, player, index: idx, length })
        })();
        match r {
            Ok(p) => {
                self.playback = Some(p);
                self.play_error = None;
            }
            Err(e) => self.play_error = Some(e),
        }
    }

    fn stop(&mut self) {
        if let Some(p) = self.playback.take() {
            p.player.stop();
        }
    }
}

fn install_fonts(ctx: &egui::Context) {
    // egui's built-in fonts lack ♭ ♯ ¢ −: use Windows' Segoe UI / Segoe UI Symbol when present (read at start, not bundled)
    let mut fonts = egui::FontDefinitions::default();
    for (name, file) in [("segoe", "segoeui.ttf"), ("segoe_symbol", "seguisym.ttf")] {
        let p = Path::new(r"C:\Windows\Fonts").join(file);
        if let Ok(bytes) = std::fs::read(&p) {
            fonts.font_data.insert(name.into(), Arc::new(egui::FontData::from_owned(bytes)));
            fonts.families.entry(egui::FontFamily::Proportional).or_default().insert(if name == "segoe" { 0 } else { 1 }, name.into());
            fonts.families.entry(egui::FontFamily::Monospace).or_default().push(name.into());
        }
    }
    ctx.set_fonts(fonts);
}

fn fmt_time(t: f64) -> String {
    let t = t.max(0.0) as u64;
    format!("{}:{:02}", t / 60, t % 60)
}

fn card(ui: &mut egui::Ui, title: &str, body: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::group(ui.style()).inner_margin(10.0).show(ui, |ui| {
        ui.set_width(ui.available_width());
        ui.vertical(|ui| {
            ui.label(RichText::new(title).small().color(Color32::GRAY));
            body(ui);
        });
    });
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // drag and drop
        let dropped: Vec<PathBuf> = ctx.input(|i| i.raw.dropped_files.iter().filter_map(|f| f.path.clone()).collect());
        if !dropped.is_empty() {
            self.add_files(dropped);
        }
        let hovering = ctx.input(|i| !i.raw.hovered_files.is_empty());

        egui::TopBottomPanel::top("top").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.heading(RichText::new("SongSpec Analyzer").strong());
                ui.label(RichText::new(format!("v{}", songspec_core::ENGINE_VERSION)).small().color(Color32::GRAY));
                ui.separator();
                if ui.button("➕ Add songs…").clicked() {
                    if let Some(files) = rfd::FileDialog::new().add_filter("Audio", songspec_core::audio::AUDIO_EXTS).pick_files() {
                        self.add_files(files);
                    }
                }
                ui.label(RichText::new("or drop audio files anywhere in this window").color(Color32::GRAY));
            });
        });

        let snapshot: Vec<(String, Status, Option<Analysis>, PathBuf)> = {
            let g = self.shared.lock().unwrap();
            g.songs.iter().map(|s| (s.name.clone(), s.status.clone(), s.result.clone(), s.path.clone())).collect()
        };

        egui::SidePanel::left("queue").resizable(true).default_width(300.0).show(ctx, |ui| {
            ui.add_space(4.0);
            ui.label(RichText::new(format!("Queue ({})", snapshot.len())).strong());
            ui.separator();
            egui::ScrollArea::vertical().show(ui, |ui| {
                for (i, (name, st, res, _)) in snapshot.iter().enumerate() {
                    let sel = self.selected == Some(i);
                    let sub = match st {
                        Status::Queued => "waiting".to_string(),
                        Status::Running(s) => s.to_string(),
                        Status::Failed(e) => format!("failed: {e}"),
                        Status::Done => match res {
                            Some(a) => format!(
                                "{} · {} BPM",
                                a.key.as_ref().map(|k| k.display.key.clone()).unwrap_or("no key".into()),
                                a.tempo.as_ref().and_then(|t| t.display.bpm.clone()).unwrap_or("–".into())
                            ),
                            None => String::new(),
                        },
                    };
                    let resp = ui.add(egui::Button::selectable(sel, RichText::new(name).strong()).wrap_mode(egui::TextWrapMode::Truncate));
                    if resp.clicked() {
                        self.selected = Some(i);
                    }
                    let color = match st {
                        Status::Failed(_) => Color32::LIGHT_RED,
                        Status::Running(_) => ACCENT,
                        _ => Color32::GRAY,
                    };
                    ui.horizontal(|ui| {
                        if matches!(st, Status::Running(_)) {
                            ui.spinner();
                        }
                        ui.label(RichText::new(sub).small().color(color));
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
                ui.centered_and_justified(|ui| ui.heading(RichText::new("Add or drop songs to analyse them").color(Color32::GRAY)));
                return;
            };
            let (name, st, res, path) = snapshot[i].clone();
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.heading(&name);
                if let Some(a) = &res {
                    let who = match (&a.track.artist, &a.track.title) {
                        (Some(ar), Some(t)) => format!("{ar} – {t} · "),
                        _ => String::new(),
                    };
                    ui.label(RichText::new(format!("{who}{} · {} Hz · analysed in {:.0} s", fmt_time(a.track.duration), a.track.sample_rate, a.seconds)).color(Color32::GRAY));
                }
                self.player_bar(ui, i, &path, res.as_ref().map(|a| a.track.duration));
                ui.add_space(6.0);
                match (&st, &res) {
                    (Status::Failed(e), _) => {
                        ui.colored_label(Color32::LIGHT_RED, e);
                    }
                    (_, Some(a)) => self.results(ui, i, a),
                    (Status::Running(s), _) => {
                        ui.horizontal(|ui| {
                            ui.spinner();
                            ui.label(*s);
                        });
                    }
                    _ => {
                        ui.label("Waiting in the queue…");
                    }
                }
            });
        });
        if self.playback.is_some() {
            ctx.request_repaint_after(Duration::from_millis(250));
        }
        self.screenshot_step(ctx, &snapshot);
    }
}

impl App {
    fn screenshot_step(&mut self, ctx: &egui::Context, snapshot: &[(String, Status, Option<Analysis>, PathBuf)]) {
        let Some(path) = self.screenshot.clone() else { return };
        let all_done = !snapshot.is_empty() && snapshot.iter().all(|s| matches!(s.1, Status::Done | Status::Failed(_)));
        let size = ctx.input(|i| i.viewport().inner_rect.map(|r| r.size()).unwrap_or_default());
        if all_done && !self.shot_requested && (size.x < 10.0 || size.y < 10.0) {
            // started minimised (e.g. from a background shell): bring the window up before capturing it
            ctx.send_viewport_cmd(egui::ViewportCommand::Minimized(false));
            ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
            ctx.request_repaint_after(Duration::from_millis(300));
            return;
        }
        if all_done && !self.shot_requested {
            self.shot_requested = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(Default::default()));
        }
        let shot = ctx.input(|i| {
            i.raw.events.iter().find_map(|e| match e {
                egui::Event::Screenshot { image, .. } => Some(image.clone()),
                _ => None,
            })
        });
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

impl App {
    fn player_bar(&mut self, ui: &mut egui::Ui, i: usize, path: &Path, length: Option<f64>) {
        ui.horizontal(|ui| {
            let playing_this = self.playback.as_ref().is_some_and(|p| p.index == i);
            if playing_this {
                let p = self.playback.as_ref().unwrap();
                let paused = p.player.is_paused();
                if ui.button(if paused { "▶ Play" } else { "⏸ Pause" }).clicked() {
                    if paused { p.player.play() } else { p.player.pause() }
                }
                if ui.button("⏹ Stop").clicked() {
                    self.stop();
                    return;
                }
                let p = self.playback.as_ref().unwrap();
                let pos = p.player.get_pos().as_secs_f64();
                if let Some(len) = p.length {
                    let mut v = pos;
                    let r = ui.add(egui::Slider::new(&mut v, 0.0..=len).show_value(false));
                    if r.drag_stopped() || r.clicked() {
                        let _ = p.player.try_seek(Duration::from_secs_f64(v));
                    }
                    ui.label(format!("{} / {}", fmt_time(pos), fmt_time(len)));
                } else {
                    ui.label(fmt_time(pos));
                }
                if p.player.empty() {
                    self.stop();
                }
            } else if ui.button("▶ Play").clicked() {
                self.play(i, path, length);
            }
            if let Some(e) = &self.play_error {
                ui.colored_label(Color32::LIGHT_RED, e);
            }
        });
    }

    fn results(&mut self, ui: &mut egui::Ui, i: usize, a: &Analysis) {
        // three equal columns on a wide window, stacked on a narrow one
        let wide = ui.available_width() > 760.0;
        let mut cols: Vec<Box<dyn FnOnce(&mut egui::Ui) + '_>> = Vec::new();
        cols.push(Box::new(|ui: &mut egui::Ui| card(ui, "KEY", |ui| match &a.key {
                Some(k) => {
                    ui.label(RichText::new(&k.display.key).size(30.0).strong().color(ACCENT));
                    if let Some(n) = &k.display.note {
                        ui.label(n);
                    }
                    if let Some(t) = &k.display.ambiguity_text {
                        ui.label(RichText::new(t).color(Color32::YELLOW));
                    }
                    ui.label(format!("confidence {:.0} %", 100.0 * k.confidence));
                    ui.label(RichText::new(format!("Camelot {} · Open Key {}", k.camelot, k.open_key)).color(Color32::GRAY));
                    ui.add_space(4.0);
                    ui.label(RichText::new("Alternates").small().color(Color32::GRAY));
                    for c in k.candidates.iter().skip(1) {
                        ui.label(format!("{}  ({:.0} %)", c.key, 100.0 * c.p));
                    }
                }
                None => {
                    ui.label("No key could be read");
                }
            })));
        cols.push(Box::new(|ui: &mut egui::Ui| card(ui, "BPM", |ui| match &a.tempo {
                Some(t) => {
                    ui.label(RichText::new(t.display.bpm.clone().unwrap_or("–".into())).size(30.0).strong().color(ACCENT));
                    if let Some(n) = &t.display.note {
                        ui.label(n);
                    }
                    ui.label(format!("Half / double: {}", t.display.alternates.join("  ·  ")));
                    let conf = t.signature_conf.map(|c| format!(" ({:.0} % of bars)", 100.0 * c)).unwrap_or_default();
                    ui.label(format!("Time signature {}{conf}", t.signature));
                    ui.label(RichText::new(format!("{} · irregularity {:.1} %", if t.feel == "live" { "played live" } else { "on a grid" }, t.irregularity_pct)).color(Color32::GRAY));
                }
                None => {
                    ui.label("No steady beat found");
                }
            })));
        cols.push(Box::new(|ui: &mut egui::Ui| card(ui, "DETUNE", |ui| match &a.detune {
                Some(d) => {
                    ui.label(RichText::new(&d.display.display).size(30.0).strong().color(ACCENT));
                    ui.label(format!("A4 = {:.2} Hz", d.a4_hz));
                    ui.label(&d.display.fix);
                    if let Some(t) = &d.display.key_text {
                        ui.label(RichText::new(t).color(Color32::GRAY));
                    }
                    if d.drift_flag {
                        ui.label(RichText::new(format!("tuning drifts ({} ¢ range)", d.drift_range_cents.map(|r| format!("{r:.1}")).unwrap_or("?".into()))).color(Color32::YELLOW));
                    }
                }
                None => {
                    ui.label("No steady tonal content");
                }
            })));
        if wide {
            ui.columns(3, |c| {
                for (ui, f) in c.iter_mut().zip(cols) {
                    f(ui);
                }
            });
        } else {
            for f in cols {
                f(ui);
                ui.add_space(6.0);
            }
        }
        ui.add_space(6.0);
        if let Some(k) = &a.key {
            card(ui, "BORROWED CHORDS", |ui| {
                if k.borrows.is_empty() {
                    ui.label("No borrowed chords found");
                }
                for b in &k.borrows {
                    ui.label(RichText::new(format!("May borrow from {}", b.from)).strong());
                    ui.label(format!("{}  ·  {:.0} s, {:.0} % of the chords", b.chords.join(", "), b.seconds, 100.0 * b.share));
                }
            });
        }
        ui.add_space(6.0);
        egui::CollapsingHeader::new("What voted").default_open(self.expand).show(ui, |ui| {
            if let Some(k) = &a.key {
                ui.label(RichText::new(format!("Key ({})", k.source)).strong());
                egui::Grid::new("est").striped(true).show(ui, |ui| {
                    for e in &k.estimators {
                        ui.label(&e.name);
                        ui.label(&e.key);
                        ui.label(format!("{:.0} %", 100.0 * e.p));
                        ui.end_row();
                    }
                });
                if !k.sections.is_empty() {
                    ui.label(RichText::new("Key sections").strong());
                    for s in &k.sections {
                        ui.label(format!("{}–{}  {}", fmt_time(s.start), fmt_time(s.end), s.key));
                    }
                }
            }
            if let Some(d) = &a.detune {
                ui.label(RichText::new(format!("Detune ({})", d.fusion)).strong());
                for (n, v) in &d.estimators {
                    ui.label(format!("{n}: {}", v.map(|c| format!("{c:+.2} ¢")).unwrap_or("no reading".into())));
                }
            }
            if let Some(t) = &a.tempo {
                ui.label(RichText::new("Tempo sections").strong());
                for s in &t.sections {
                    ui.label(format!("{}–{}  {:.2} BPM ({})", fmt_time(s.start), fmt_time(s.end), s.bpm, s.steadiness));
                }
            }
            for w in &a.warnings {
                ui.colored_label(Color32::YELLOW, w);
            }
        });
        if let Some(c) = &a.chords {
            egui::CollapsingHeader::new("Chords").default_open(false).show(ui, |ui| {
                for p in &c.progressions {
                    ui.label(format!("{}   ({})", p.display, p.chords));
                }
                ui.add_space(4.0);
                egui::Grid::new("chords").striped(true).show(ui, |ui| {
                    for s in c.segments.iter().filter(|s| s.chord != "N") {
                        ui.label(fmt_time(s.start));
                        ui.label(RichText::new(&s.chord).strong());
                        ui.label(s.roman.clone().unwrap_or_default());
                        ui.end_row();
                    }
                });
            });
        }
        egui::CollapsingHeader::new("Spotify / Apple Music").default_open(false).show(ui, |ui| {
            let query = links::query(a.track.artist.as_deref(), a.track.title.as_deref(), &self.shared.lock().unwrap().songs[i].name);
            ui.label(RichText::new(format!("Searches for: {query}")).color(Color32::GRAY));
            let state = self.links.lock().unwrap().get(&i).map(|s| match s {
                LinkState::Busy => None,
                LinkState::Done(l) => Some(Ok(l.clone())),
                LinkState::Failed(e) => Some(Err(e.clone())),
            });
            match state {
                Some(None) => {
                    ui.spinner();
                }
                None => {
                    if ui.button("Look up links").clicked() {
                        self.links.lock().unwrap().insert(i, LinkState::Busy);
                        let (store, ctx, q) = (self.links.clone(), ui.ctx().clone(), query.clone());
                        std::thread::spawn(move || {
                            let r = links::lookup(&q);
                            store.lock().unwrap().insert(i, match r {
                                Ok(l) => LinkState::Done(l),
                                Err(e) => LinkState::Failed(e),
                            });
                            ctx.request_repaint();
                        });
                    }
                }
                Some(Some(Ok(l))) => {
                    for (label, url) in &l.apple {
                        ui.hyperlink_to(format!("Apple Music: {label}"), url);
                    }
                    if l.apple.is_empty() {
                        ui.label("No Apple Music match");
                    }
                    ui.hyperlink_to("Search on Spotify", &l.spotify_search);
                }
                Some(Some(Err(e))) => {
                    ui.colored_label(Color32::LIGHT_RED, e);
                }
            }
        });
    }
}

fn main() -> eframe::Result<()> {
    #[allow(unused_mut)]
    let mut viewport = egui::ViewportBuilder::default().with_inner_size([1180.0, 780.0]).with_min_inner_size([720.0, 480.0]).with_drag_and_drop(true);
    #[cfg(has_icon)]
    if let Ok(icon) = eframe::icon_data::from_png_bytes(include_bytes!("../assets/icon_256.png")) {
        viewport = viewport.with_icon(icon);
    }
    let options = eframe::NativeOptions { viewport, ..Default::default() };
    eframe::run_native("SongSpec Analyzer", options, Box::new(|cc| Ok(Box::new(App::new(cc)))))
}
