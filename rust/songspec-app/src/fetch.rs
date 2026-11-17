//! Songs from links. Spotify tracks, albums and playlists (from Spotify's public embed pages) and Apple Music songs and albums
//! (Apple's iTunes Lookup) give title, artist and length, and each song is then found on YouTube; YouTube videos and playlists are
//! used directly. Audio comes from YouTube through yt-dlp, which is not bundled: it is downloaded from its official GitHub release
//! on first use, after the user confirms, and checked against the release's SHA-256 list.
//! Downloads are kept by video ID in the app's folder (2 GB at most, least recently used removed first), and which video a
//! Spotify / Apple song was found as is remembered, so adding a song again doesn't ask YouTube again.
//! YouTube requests go one at a time with a pause between them, back off on rate limits, and only use a sign-in (cookies) when
//! YouTube asks for one; failures are written to yt-dlp.log in the app's folder.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use sha2::{Digest, Sha256};

const UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
// yt-dlp's release file for this OS, and the name it gets on disk
#[cfg(windows)]
const YTDLP_ASSET: &str = "yt-dlp.exe";
#[cfg(target_os = "macos")]
const YTDLP_ASSET: &str = "yt-dlp_macos";
#[cfg(not(any(windows, target_os = "macos")))]
const YTDLP_ASSET: &str = "yt-dlp_linux";
const YTDLP_NAME: &str = if cfg!(windows) { "yt-dlp.exe" } else { "yt-dlp" };
const YTDLP_RELEASE: &str = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/";
const YTDLP_SUMS: &str = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS";

#[derive(Debug, Clone, PartialEq)]
pub enum Link {
    /// kind: "track", "album" or "playlist"
    Spotify { kind: String, id: String },
    AppleTrack(String),
    AppleAlbum(String),
    YouTube(String),
    YouTubePlaylist(String),
}

/// One song to fetch: known metadata (searched on YouTube) or a YouTube video.
#[derive(Debug, Clone)]
pub enum Item {
    Song(Track),
    Video(String),
}

#[derive(Debug, Clone)]
pub struct Track {
    pub title: String,
    pub artist: Option<String>,
    pub duration: Option<f64>,
}

impl Track {
    pub fn label(&self) -> String {
        match &self.artist {
            Some(a) => format!("{a} - {}", self.title),
            None => self.title.clone(),
        }
    }
}

fn segment_after(u: &str, pat: &str) -> Option<String> {
    let i = u.find(pat)? + pat.len();
    Some(u[i..].split(['?', '&', '/', '#']).next().unwrap_or("").to_string()).filter(|s| !s.is_empty())
}

fn query_param(u: &str, name: &str) -> Option<String> {
    let q = u.split_once('?')?.1;
    let pre = format!("{name}=");
    q.split('&').find_map(|kv| kv.strip_prefix(pre.as_str())).map(|v| v.split('#').next().unwrap().to_string()).filter(|v| !v.is_empty())
}

/// What kind of link is this? None for anything else.
pub fn parse(url: &str) -> Option<Link> {
    let u = url.trim();
    for kind in ["track", "album", "playlist"] {
        if let Some(id) = u.strip_prefix(&format!("spotify:{kind}:")) {
            return Some(Link::Spotify { kind: kind.into(), id: id.into() });
        }
    }
    let lower = u.to_lowercase();
    if lower.contains("open.spotify.com") {
        for kind in ["track", "album", "playlist"] {
            if let Some(id) = segment_after(u, &format!("/{kind}/")) {
                return Some(Link::Spotify { kind: kind.into(), id });
            }
        }
        return None;
    }
    if lower.contains("music.apple.com") || lower.contains("itunes.apple.com") {
        if let Some(i) = query_param(u, "i") {
            return Some(Link::AppleTrack(i));
        }
        let path = u.split('?').next().unwrap().trim_end_matches('/');
        let last = path.rsplit('/').next().unwrap_or("").trim_start_matches("id");
        if !last.is_empty() && last.chars().all(|c| c.is_ascii_digit()) {
            if path.contains("/song/") {
                return Some(Link::AppleTrack(last.into()));
            }
            if path.contains("/album/") {
                return Some(Link::AppleAlbum(last.into()));
            }
        }
        return None;
    }
    if lower.contains("youtu.be/") {
        return segment_after(u, "youtu.be/").map(|id| Link::YouTube(format!("https://www.youtube.com/watch?v={id}")));
    }
    if lower.contains("youtube.com/") {
        if let Some(id) = query_param(u, "v").or_else(|| segment_after(u, "/shorts/")) {
            return Some(Link::YouTube(format!("https://www.youtube.com/watch?v={id}")));
        }
        if let Some(list) = query_param(u, "list") {
            return Some(Link::YouTubePlaylist(format!("https://www.youtube.com/playlist?list={list}")));
        }
    }
    None
}

fn get(url: &str) -> Result<String, String> {
    ureq::get(url)
        .header("User-Agent", UA)
        .header("Accept-Language", "en")
        .call()
        .map_err(|e| e.to_string())?
        .into_body()
        .read_to_string()
        .map_err(|e| e.to_string())
}

/// Spotify's embed page (open.spotify.com/embed/...) carries the track / album / playlist data as JSON (__NEXT_DATA__).
fn spotify_entity(kind: &str, id: &str) -> Result<serde_json::Value, String> {
    let html = get(&format!("https://open.spotify.com/embed/{kind}/{id}")).map_err(|e| format!("Spotify: {e}"))?;
    let tag = "<script id=\"__NEXT_DATA__\" type=\"application/json\">";
    let i = html.find(tag).ok_or("Spotify's page didn't include the song data")? + tag.len();
    let j = html[i..].find("</script>").ok_or("Spotify's page was cut off")? + i;
    let v: serde_json::Value = serde_json::from_str(&html[i..j]).map_err(|e| e.to_string())?;
    let e = v["props"]["pageProps"]["state"]["data"]["entity"].clone();
    if e.is_null() {
        return Err("Spotify didn't find that link (a private playlist?)".into());
    }
    Ok(e)
}

fn ms(v: &serde_json::Value) -> Option<f64> {
    v.as_f64().filter(|d| *d > 0.0).map(|d| d / 1000.0)
}

/// Every song a link stands for: one for a track or video, all of them for an album or playlist.
pub fn expand(link: &Link, ytdlp: Option<&Path>) -> Result<Vec<Item>, String> {
    match link {
        Link::Spotify { kind, id } if kind == "track" => {
            let e = spotify_entity("track", id)?;
            let artist = e["artists"].as_array().map(|a| a.iter().filter_map(|x| x["name"].as_str()).collect::<Vec<_>>().join(", ")).filter(|s| !s.is_empty());
            Ok(vec![Item::Song(Track { title: e["name"].as_str().unwrap_or("?").into(), artist, duration: ms(&e["duration"]) })])
        }
        Link::Spotify { kind, id } => {
            let e = spotify_entity(kind, id)?;
            let list = e["trackList"].as_array().ok_or("Spotify didn't list the tracks")?;
            Ok(list
                .iter()
                .map(|t| {
                    Item::Song(Track {
                        title: t["title"].as_str().unwrap_or("?").into(),
                        artist: t["subtitle"].as_str().map(|s| s.replace('\u{a0}', " ")).filter(|s| !s.is_empty()),
                        duration: ms(&t["duration"]),
                    })
                })
                .collect())
        }
        Link::AppleTrack(id) | Link::AppleAlbum(id) => {
            let body = get(&format!("https://itunes.apple.com/lookup?id={id}&entity=song")).map_err(|e| format!("Apple Music: {e}"))?;
            let v: serde_json::Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
            let album = matches!(link, Link::AppleAlbum(_));
            let songs: Vec<Item> = v["results"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter(|x| x["kind"] == "song" && (album || x["trackId"].to_string() == *id))
                        .map(|x| {
                            Item::Song(Track {
                                title: x["trackName"].as_str().unwrap_or("?").into(),
                                artist: x["artistName"].as_str().map(|s| s.into()),
                                duration: x["trackTimeMillis"].as_f64().map(|v| v / 1000.0),
                            })
                        })
                        .collect()
                })
                .unwrap_or_default();
            if songs.is_empty() {
                Err("Apple Music didn't find that song or album".into())
            } else {
                Ok(songs)
            }
        }
        Link::YouTube(u) => Ok(vec![Item::Video(u.clone())]),
        Link::YouTubePlaylist(u) => {
            let exe = ytdlp.ok_or("yt-dlp isn't installed")?;
            let out = request(exe, &["--flat-playlist", "--print", "%(id)s", u], &Progress::none()).map_err(|f| f.msg)?;
            let v: Vec<Item> = out
                .lines()
                .map(|l| l.trim())
                .filter(|l| !l.is_empty())
                .map(|id| Item::Video(format!("https://www.youtube.com/watch?v={id}")))
                .collect();
            if v.is_empty() {
                Err("that YouTube playlist is empty or private".into())
            } else {
                Ok(v)
            }
        }
    }
}

// ------------------------------------------------------------------------------------------------ yt-dlp

/// Where the app keeps yt-dlp: %LOCALAPPDATA%\SongSpecAnalyzer on Windows, ~/Library/Application Support/SongSpecAnalyzer on macOS
/// (never inside the .app bundle, which would break its signature).
fn app_dir() -> PathBuf {
    if cfg!(test) {
        return std::env::temp_dir().join("SongSpecAnalyzer-test"); // tests never touch the real settings, log or downloads
    }
    let base = if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA").map(PathBuf::from)
    } else if cfg!(target_os = "macos") {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join("Library/Application Support"))
    } else {
        std::env::var_os("XDG_DATA_HOME").map(PathBuf::from).or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share")))
    };
    base.unwrap_or_else(std::env::temp_dir).join("SongSpecAnalyzer")
}

/// yt-dlp next to the exe (Windows), in the app's folder, or on PATH (plus Homebrew's folders: apps opened from the Finder get a
/// bare PATH).
pub fn ytdlp() -> Option<PathBuf> {
    let mut cands = vec![app_dir().join(YTDLP_NAME)];
    if cfg!(windows) {
        if let Some(d) = std::env::current_exe().ok().and_then(|e| e.parent().map(Path::to_path_buf)) {
            cands.insert(0, d.join(YTDLP_NAME));
        }
    }
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH").map(|p| std::env::split_paths(&p).collect()).unwrap_or_default();
    if cfg!(target_os = "macos") {
        dirs.extend(["/opt/homebrew/bin", "/usr/local/bin"].map(PathBuf::from));
    }
    cands.extend(dirs.into_iter().map(|d| d.join(YTDLP_NAME)));
    cands.into_iter().find(|p| p.is_file())
}

/// Download yt-dlp from its official release into the app's folder, checked against the release's SHA2-256SUMS.
pub fn install_ytdlp() -> Result<PathBuf, String> {
    let sums = get(YTDLP_SUMS).map_err(|e| format!("couldn't reach GitHub ({e})"))?;
    let want = sums
        .lines()
        .find_map(|l| {
            let (h, f) = l.split_once(char::is_whitespace)?;
            (f.trim_start_matches([' ', '*']) == YTDLP_ASSET).then(|| h.trim().to_lowercase())
        })
        .ok_or_else(|| format!("yt-dlp's checksum list has no {YTDLP_ASSET}"))?;
    let mut bytes = Vec::new();
    use std::io::Read;
    ureq::get(format!("{YTDLP_RELEASE}{YTDLP_ASSET}"))
        .header("User-Agent", UA)
        .call()
        .map_err(|e| format!("download failed ({e})"))?
        .into_body()
        .into_reader()
        .take(200 * 1024 * 1024)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    let got: String = Sha256::digest(&bytes).iter().map(|b| format!("{b:02x}")).collect();
    if got != want {
        return Err(format!("the downloaded {YTDLP_ASSET} doesn't match its published checksum; not installed"));
    }
    std::fs::create_dir_all(app_dir()).map_err(|e| e.to_string())?;
    let p = app_dir().join(YTDLP_NAME);
    let _one = ONE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner()); // not while a request is running it
    std::fs::write(&p, bytes).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    }
    Ok(p)
}

// ------------------------------------------------------------------------------------------------ settings

/// Where yt-dlp gets a YouTube sign-in from, when YouTube asks for one. None by default: no cookies are ever read.
/// Chrome, Edge and other Chromium browsers aren't offered: on Windows they encrypt their cookies so only the browser itself can
/// read them (yt-dlp fails with "Failed to decrypt with DPAPI", yt-dlp issue 10927).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CookieMode {
    None,
    Firefox,
    /// a cookies.txt (Netscape format) the user exported and picked
    File,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Settings {
    pub cookies: CookieMode,
    pub cookies_file: Option<PathBuf>,
    /// pause between two YouTube requests, in seconds (each one varies by up to ±1 s)
    pub gap_secs: f64,
}

impl Default for Settings {
    fn default() -> Self {
        Settings { cookies: CookieMode::None, cookies_file: None, gap_secs: 4.0 }
    }
}

pub const GAP_RANGE: std::ops::RangeInclusive<f64> = 2.0..=20.0;

static SETTINGS: Mutex<Option<Settings>> = Mutex::new(None);

pub fn settings() -> Settings {
    SETTINGS.lock().unwrap().clone().unwrap_or_default()
}

fn settings_path() -> PathBuf {
    app_dir().join("settings.json")
}

pub fn set_settings(s: Settings) {
    let mode = match s.cookies {
        CookieMode::None => "none",
        CookieMode::Firefox => "firefox",
        CookieMode::File => "file",
    };
    let v = serde_json::json!({ "cookies": mode, "cookies_file": s.cookies_file, "request_gap_s": s.gap_secs });
    *SETTINGS.lock().unwrap() = Some(s);
    let _ = std::fs::create_dir_all(app_dir());
    let _ = std::fs::write(settings_path(), v.to_string());
}

pub fn load_settings() {
    let mut s = Settings::default();
    if let Some(v) = std::fs::read_to_string(settings_path()).ok().and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok()) {
        s.cookies = match v["cookies"].as_str() {
            Some("firefox") => CookieMode::Firefox,
            Some("file") => CookieMode::File,
            // 1.5.0 kept a browser name: only Firefox carries over (Chromium browsers fail on Windows)
            None if v["cookies_from_browser"] == "firefox" => CookieMode::Firefox,
            _ => CookieMode::None,
        };
        s.cookies_file = v["cookies_file"].as_str().map(PathBuf::from);
        if let Some(g) = v["request_gap_s"].as_f64() {
            s.gap_secs = g.clamp(*GAP_RANGE.start(), *GAP_RANGE.end());
        }
    }
    *SETTINGS.lock().unwrap() = Some(s);
}

/// Is there a Firefox profile yt-dlp could read?
pub fn firefox_installed() -> bool {
    let home = |v: &str| std::env::var_os(v).map(PathBuf::from);
    let dir = if cfg!(windows) {
        home("APPDATA").map(|d| d.join("Mozilla/Firefox/Profiles"))
    } else if cfg!(target_os = "macos") {
        home("HOME").map(|d| d.join("Library/Application Support/Firefox/Profiles"))
    } else {
        home("HOME").map(|d| d.join(".mozilla/firefox"))
    };
    dir.is_some_and(|d| d.is_dir())
}

#[derive(Debug, Clone)]
enum Cookie {
    File(PathBuf),
    Firefox,
}

impl Cookie {
    fn name(&self) -> &'static str {
        match self {
            Cookie::File(_) => "cookies.txt",
            Cookie::Firefox => "Firefox",
        }
    }
}

/// The sign-ins to try, in order, once YouTube asks for one: the picked cookies.txt, then Firefox; none when the setting is None.
fn cookie_sources() -> Vec<Cookie> {
    let s = settings();
    let mut v = vec![];
    if s.cookies == CookieMode::File {
        if let Some(p) = s.cookies_file.filter(|p| p.is_file()) {
            v.push(Cookie::File(p));
        }
    }
    if s.cookies != CookieMode::None && firefox_installed() {
        v.push(Cookie::Firefox);
    }
    v
}

// ------------------------------------------------------------------------------------------------ running yt-dlp

/// What a request shows while it waits ("rate limited, retrying in 30s") and whether to give up (the queue was cleared).
pub struct Progress<'a> {
    pub status: &'a dyn Fn(String),
    pub cancelled: &'a dyn Fn() -> bool,
}

fn no_status(_: String) {}
fn never() -> bool {
    false
}

impl Progress<'static> {
    pub fn none() -> Self {
        Progress { status: &no_status, cancelled: &never }
    }
}

/// A song that couldn't be fetched: a short message for the queue, and yt-dlp's own output for the details.
#[derive(Debug, Clone, Default)]
pub struct Fail {
    pub msg: String,
    pub raw: String,
    pub url: Option<String>,
}

pub const CLEARED: &str = "cleared";

impl Fail {
    fn new(msg: impl Into<String>) -> Self {
        Fail { msg: msg.into(), ..Default::default() }
    }
    pub fn cleared(&self) -> bool {
        self.msg == CLEARED
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Kind {
    /// the sign-in (cookies) couldn't be read: DPAPI, a locked or missing database, a bad cookies.txt
    Cookies,
    Age,
    /// "Sign in to confirm you're not a bot"
    Bot,
    RateLimit,
    Forbidden,
    Unavailable,
    Network,
    Other,
}

fn classify(stderr: &str) -> Kind {
    let errs: Vec<&str> = stderr.lines().filter(|l| l.contains("ERROR")).collect();
    let text = if errs.is_empty() { stderr.to_lowercase() } else { errs.join("\n").to_lowercase() };
    let has = |p: &[&str]| p.iter().any(|x| text.contains(x));
    if has(&["dpapi", "failed to decrypt", "cookies database", "could not copy", "netscape format", "failed to load cookies", "keyring", "cookie file"]) {
        Kind::Cookies
    } else if has(&["confirm your age", "age-restricted", "age restricted", "inappropriate for some users"]) {
        Kind::Age
    } else if has(&["not a bot", "sign in to confirm", "please sign in", "login required"]) {
        Kind::Bot
    } else if has(&["http error 429", "too many requests", "rate-limit", "rate limit", "try again later"]) {
        Kind::RateLimit
    } else if has(&["http error 403"]) {
        Kind::Forbidden
    } else if has(&["video unavailable", "video is unavailable", "private video", "has been removed", "not available in your country", "members-only", "this video is not available"]) {
        Kind::Unavailable
    } else if has(&["getaddrinfo", "unable to connect", "timed out", "connection reset", "name resolution", "network is unreachable", "failed to establish"]) {
        Kind::Network
    } else {
        Kind::Other
    }
}

const DROP: &str = "or drop the audio file in directly";

fn message(kind: Kind, stderr: &str, cookies_unreadable: bool) -> String {
    let m = match kind {
        Kind::Bot => format!("YouTube blocked this request. Try again later, set cookies in Settings, {DROP}."),
        Kind::RateLimit => format!("YouTube is limiting requests from this connection. Try again later (↻ Retry failed), {DROP}."),
        Kind::Age => format!("This video is age-restricted: YouTube only plays it to a signed-in account. Set cookies in Settings, {DROP}."),
        Kind::Forbidden => format!("YouTube refused the download (HTTP 403), even after updating yt-dlp. Try again later, {DROP}."),
        Kind::Unavailable => "This video isn't available (removed, private, or blocked where you are).".into(),
        Kind::Network => "Couldn't reach YouTube. Check the internet connection and try again.".into(),
        Kind::Cookies | Kind::Other => {
            let line = stderr.lines().filter(|l| l.contains("ERROR")).last().or(stderr.lines().last()).unwrap_or("no output");
            let short: String = line.trim_start_matches("ERROR:").trim().chars().take(160).collect();
            format!("yt-dlp couldn't get this song: {short}")
        }
    };
    if cookies_unreadable && matches!(kind, Kind::Bot | Kind::Age) {
        format!("{m} (The cookies set in Settings couldn't be read: see the log.)")
    } else {
        m
    }
}

/// One yt-dlp request at a time, from any thread (the queue, links being read, an update replacing the file).
static ONE_AT_A_TIME: Mutex<()> = Mutex::new(());
/// When the last request ended: the next one waits the pause from Settings, ±1 s.
static LAST_REQUEST: Mutex<Option<Instant>> = Mutex::new(None);

fn pause() -> Duration {
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.subsec_nanos()).unwrap_or(0);
    let jitter = (nanos % 2001) as f64 / 1000.0 - 1.0;
    Duration::from_secs_f64((settings().gap_secs + jitter).max(1.0))
}

struct Out {
    ok: bool,
    stdout: String,
    stderr: String,
}

fn exec(exe: &Path, cookie: Option<&Cookie>, args: &[&str]) -> Out {
    let _one = ONE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
    let wait = LAST_REQUEST.lock().unwrap().map(|t| pause().saturating_sub(t.elapsed())).unwrap_or_default();
    std::thread::sleep(wait);
    let mut c = Command::new(exe);
    // yt-dlp writes the cookies back into a --cookies file when it ends: it gets a copy, deleted afterwards
    let copy = std::env::temp_dir().join(format!("songspec-cookies-{}.txt", std::process::id()));
    match cookie {
        Some(Cookie::File(p)) => {
            if let Err(e) = std::fs::copy(p, &copy) {
                return Out { ok: false, stdout: String::new(), stderr: format!("ERROR: failed to load cookies from {} ({e})", p.display()) };
            }
            c.arg("--cookies").arg(&copy);
        }
        Some(Cookie::Firefox) => {
            c.args(["--cookies-from-browser", "firefox"]);
        }
        None => {}
    }
    c.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console flashing up
    }
    let out = match c.output() {
        Ok(o) => Out { ok: o.status.success(), stdout: String::from_utf8_lossy(&o.stdout).into(), stderr: String::from_utf8_lossy(&o.stderr).into() },
        Err(e) => Out { ok: false, stdout: String::new(), stderr: format!("ERROR: couldn't run yt-dlp ({e})") },
    };
    if matches!(cookie, Some(Cookie::File(_))) {
        let _ = std::fs::remove_file(&copy);
    }
    *LAST_REQUEST.lock().unwrap() = Some(Instant::now());
    out
}

/// Waits after a rate limit or bot check; one more try after each.
const BACKOFF_S: [u64; 4] = if cfg!(test) { [1, 1, 1, 1] } else { [30, 60, 120, 120] };

/// Run a yt-dlp request the careful way:
/// - no cookies first;
/// - on a bot check or age gate, the sign-ins from Settings in order (one that can't be read is skipped, never fatal);
/// - on a rate limit or bot check, wait and try again (30 s, 60 s, 120 s, 120 s), showing the countdown;
/// - on HTTP 403, update yt-dlp once and try again.
fn request(exe: &Path, args: &[&str], p: &Progress) -> Result<String, Fail> {
    let mut updated = false;
    let mut waits = BACKOFF_S.iter();
    loop {
        if (p.cancelled)() {
            return Err(Fail::new(CLEARED));
        }
        let mut out = exec(exe, None, args);
        if out.ok {
            return Ok(out.stdout);
        }
        let mut kind = classify(&out.stderr);
        log_failure(args, None, &out.stderr);
        let mut cookies_unreadable = false;
        if matches!(kind, Kind::Bot | Kind::Age) {
            for src in cookie_sources() {
                if (p.cancelled)() {
                    return Err(Fail::new(CLEARED));
                }
                (p.status)(format!("YouTube asked for a sign-in: trying {}…", src.name()));
                let o = exec(exe, Some(&src), args);
                if o.ok {
                    return Ok(o.stdout);
                }
                let k = classify(&o.stderr);
                log_failure(args, Some(&src), &o.stderr);
                if k == Kind::Cookies {
                    cookies_unreadable = true; // skip it: on to the next source, or no cookies
                    continue;
                }
                (out, kind) = (o, k);
                if !matches!(k, Kind::Bot | Kind::Age) {
                    break;
                }
            }
        }
        match kind {
            Kind::Forbidden if !updated => {
                updated = true;
                (p.status)("YouTube refused (HTTP 403): updating yt-dlp…".into());
                if refresh_ytdlp(true).unwrap_or(false) {
                    continue;
                }
            }
            Kind::Bot | Kind::RateLimit => {
                if let Some(&w) = waits.next() {
                    log(&format!("rate limited / bot check: waiting {w} s"));
                    for left in (1..=w).rev() {
                        if (p.cancelled)() {
                            return Err(Fail::new(CLEARED));
                        }
                        (p.status)(format!("rate limited, retrying in {left}s"));
                        std::thread::sleep(Duration::from_secs(1));
                    }
                    continue;
                }
            }
            _ => {}
        }
        return Err(Fail { msg: message(kind, &out.stderr, cookies_unreadable), raw: out.stderr, url: None });
    }
}

// ------------------------------------------------------------------------------------------------ yt-dlp's version, the log

static VERSION: Mutex<Option<String>> = Mutex::new(None);

/// The version of the yt-dlp in use ("2026.10.26"), asked once and remembered.
pub fn ytdlp_version() -> Option<String> {
    if let Some(v) = VERSION.lock().unwrap().clone() {
        return Some(v);
    }
    let exe = ytdlp()?;
    let mut c = Command::new(&exe);
    c.arg("--version");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000);
    }
    let o = c.output().ok().filter(|o| o.status.success())?;
    let v = String::from_utf8_lossy(&o.stdout).trim().to_string();
    *VERSION.lock().unwrap() = Some(v.clone());
    Some(v)
}

/// The version if it is already known (never runs yt-dlp: for drawing the window).
pub fn known_ytdlp_version() -> Option<String> {
    VERSION.lock().unwrap().clone()
}

fn forget_version() {
    *VERSION.lock().unwrap() = None;
}

/// "Update yt-dlp": the app's own copy is fetched again from the official release; a copy installed elsewhere updates itself
/// (yt-dlp -U). -> the version now in use.
pub fn update_ytdlp() -> Result<String, String> {
    let ours = app_dir().join(YTDLP_NAME);
    match ytdlp() {
        Some(p) if p != ours => {
            let _one = ONE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
            let o = Command::new(&p).arg("-U").output().map_err(|e| format!("couldn't run yt-dlp ({e})"))?;
            let text = String::from_utf8_lossy(&o.stdout).to_string() + &String::from_utf8_lossy(&o.stderr);
            log(&format!("yt-dlp -U ({}):\n{text}", p.display()));
            if !o.status.success() {
                return Err(text.lines().last().unwrap_or("yt-dlp -U failed").trim().to_string());
            }
        }
        _ => {
            install_ytdlp()?;
        }
    }
    forget_version();
    let v = ytdlp_version().ok_or("the updated yt-dlp doesn't run")?;
    log(&format!("yt-dlp updated: now {v}"));
    Ok(v)
}

pub fn log_path() -> PathBuf {
    app_dir().join("yt-dlp.log")
}

/// Append to the log (yt-dlp.log in the app's folder; past 1 MB it moves to yt-dlp.old.log).
pub fn log(text: &str) {
    use std::io::Write;
    let p = log_path();
    if std::fs::metadata(&p).is_ok_and(|m| m.len() > 1 << 20) {
        let _ = std::fs::rename(&p, app_dir().join("yt-dlp.old.log"));
    }
    let _ = std::fs::create_dir_all(app_dir());
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&p) {
        let _ = writeln!(f, "[{}] {}", utc_now(), text.trim_end());
    }
}

fn log_failure(args: &[&str], cookie: Option<&Cookie>, stderr: &str) {
    let v = ytdlp_version().unwrap_or_else(|| "?".into());
    let c = cookie.map(Cookie::name).unwrap_or("none");
    log(&format!("FAILED  yt-dlp {v}  cookies: {c}  args: {}\n{}", args.join(" "), stderr.trim_end()));
}

/// "2026-11-16 14:03:07 UTC" without a date crate.
fn utc_now() -> String {
    let s = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
    let (days, rem) = (s.div_euclid(86400), s.rem_euclid(86400));
    // civil-from-days (Howard Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02} {:02}:{:02}:{:02} UTC", rem / 3600, rem % 3600 / 60, rem % 60)
}

/// YouTube changes often and older yt-dlp copies start failing (HTTP 403, "unable to download"): the copy this app installed is
/// fetched again from the official release when it is older than this, or right away after a 403. Copies installed elsewhere
/// (PATH, Homebrew) are left to their owner.
const YTDLP_MAX_AGE: Duration = Duration::from_secs(3 * 24 * 3600);

pub fn refresh_ytdlp(force: bool) -> Result<bool, String> {
    let ours = app_dir().join(YTDLP_NAME);
    if ytdlp().as_deref() != Some(ours.as_path()) {
        return Ok(false);
    }
    let age = std::fs::metadata(&ours).and_then(|m| m.modified()).ok().and_then(|t| t.elapsed().ok()).unwrap_or(YTDLP_MAX_AGE);
    if !force && age < YTDLP_MAX_AGE {
        return Ok(false);
    }
    let r = install_ytdlp().map(|_| true);
    forget_version();
    log(&format!("yt-dlp refreshed ({}): {}", if force { "after HTTP 403" } else { "older than 3 days" }, match &r {
        Ok(_) => format!("now {}", ytdlp_version().unwrap_or_default()),
        Err(e) => format!("failed: {e}"),
    }));
    r
}

// ------------------------------------------------------------------------------------------------ finding and downloading

/// Downloaded songs are kept here by YouTube video ID (up to DOWNLOADS_MAX, the least recently used go first), so adding a
/// song again never asks YouTube again.
pub fn downloads_dir() -> PathBuf {
    app_dir().join("downloads")
}

const DOWNLOADS_MAX: u64 = 2 << 30;

/// "Artist - Title | length" -> the video found for it.
static LOOKUPS: Mutex<Option<std::collections::HashMap<String, String>>> = Mutex::new(None);

fn lookups_path() -> PathBuf {
    app_dir().join("lookups.json")
}

fn lookup_key(t: &Track) -> String {
    format!("{} | {}", t.label().to_lowercase().split_whitespace().collect::<Vec<_>>().join(" "), t.duration.map(|d| d.round() as i64).unwrap_or(-1))
}

fn lookup(key: &str) -> Option<String> {
    let mut g = LOOKUPS.lock().unwrap();
    let m = g.get_or_insert_with(|| std::fs::read_to_string(lookups_path()).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default());
    m.get(key).cloned()
}

fn remember(key: String, id: String) {
    let mut g = LOOKUPS.lock().unwrap();
    let m = g.get_or_insert_with(Default::default);
    m.insert(key, id);
    let _ = std::fs::create_dir_all(app_dir());
    let _ = std::fs::write(lookups_path(), serde_json::to_string(m).unwrap_or_default());
}

pub fn video_id(url: &str) -> Option<String> {
    query_param(url, "v")
}

pub fn watch_url(id: &str) -> String {
    format!("https://www.youtube.com/watch?v={id}")
}

/// A finished download of this video: (file, title). Only one with its title file counts (it's written last).
fn cached(id: &str) -> Option<(PathBuf, String)> {
    let dir = downloads_dir();
    let title = std::fs::read_to_string(dir.join(format!("{id}.title"))).ok()?;
    let file = std::fs::read_dir(&dir).ok()?.flatten().map(|e| e.path()).find(|p| {
        p.file_stem().is_some_and(|s| s == id) && p.extension().is_some_and(|e| e != "title" && e != "part" && e != "ytdl")
    })?;
    if let Ok(f) = std::fs::OpenOptions::new().append(true).open(&file) {
        let _ = f.set_modified(std::time::SystemTime::now()); // recently used: kept longest
    }
    Some((file, title.trim().to_string()))
}

/// Size of the kept downloads, in bytes.
pub fn downloads_size() -> u64 {
    std::fs::read_dir(downloads_dir()).map(|rd| rd.flatten().filter_map(|e| e.metadata().ok()).map(|m| m.len()).sum()).unwrap_or(0)
}

pub fn clear_downloads() {
    let _ = std::fs::remove_dir_all(downloads_dir());
}

/// Keep the downloads under DOWNLOADS_MAX: least recently used first.
fn prune() {
    let Ok(rd) = std::fs::read_dir(downloads_dir()) else { return };
    let mut files: Vec<(std::time::SystemTime, u64, PathBuf)> = rd
        .flatten()
        .filter(|e| e.path().extension().is_none_or(|x| x != "title"))
        .filter_map(|e| e.metadata().ok().map(|m| (m.modified().unwrap_or(std::time::UNIX_EPOCH), m.len(), e.path())))
        .collect();
    files.sort();
    let mut total: u64 = files.iter().map(|f| f.1).sum();
    for (_, len, p) in files {
        if total <= DOWNLOADS_MAX {
            break;
        }
        let _ = std::fs::remove_file(&p);
        let _ = std::fs::remove_file(p.with_extension("title"));
        total -= len;
    }
}

pub fn find_on_youtube(exe: &Path, t: &Track, p: &Progress) -> Result<String, Fail> {
    let key = lookup_key(t);
    if let Some(id) = lookup(&key) {
        return Ok(watch_url(&id));
    }
    let q = format!("ytsearch5:{} audio", t.label());
    let out = request(exe, &["--flat-playlist", "--print", "%(id)s\t%(duration)s", &q], p)?;
    let hits: Vec<(String, Option<f64>)> = out
        .lines()
        .filter_map(|l| {
            let mut p = l.split('\t');
            let id = p.next()?.trim().to_string();
            let d = p.next().and_then(|d| d.trim().parse::<f64>().ok());
            (!id.is_empty()).then_some((id, d))
        })
        .collect();
    let best = match t.duration {
        Some(want) => hits
            .iter()
            .filter(|h| h.1.is_some())
            .min_by(|a, b| (a.1.unwrap() - want).abs().partial_cmp(&(b.1.unwrap() - want).abs()).unwrap())
            .or(hits.first()),
        None => hits.first(),
    };
    let id = best.map(|h| h.0.clone()).ok_or_else(|| Fail::new("Nothing was found on YouTube for this song."))?;
    remember(key, id.clone());
    Ok(watch_url(&id))
}

/// A YouTube video's audio only (M4A / AAC, which the analyzer decodes without ffmpeg; no video is downloaded). The whole track
/// is needed: the analysis reads the song from start to end. -> (file, video title). A video downloaded before is used again.
pub fn download(exe: &Path, url: &str, p: &Progress) -> Result<(PathBuf, String), Fail> {
    if let Some(hit) = video_id(url).and_then(|id| cached(&id)) {
        return Ok(hit);
    }
    let dir = downloads_dir();
    std::fs::create_dir_all(&dir).map_err(|e| Fail::new(e.to_string()))?;
    let tmpl = dir.join("%(id)s.%(ext)s");
    let out = request(
        exe,
        &[
            "-f", "140/bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]",
            "--no-playlist", "--no-mtime",
            "--sleep-requests", "1",
            "-o", &tmpl.to_string_lossy(),
            "--print", "after_move:%(id)s\t%(filepath)s\t%(title)s",
            url,
        ],
        p,
    )?;
    let line = out.lines().rev().find(|l| l.matches('\t').count() >= 2).ok_or_else(|| Fail::new("yt-dlp didn't report a file"))?;
    let mut parts = line.splitn(3, '\t');
    let (id, path, title) = (parts.next().unwrap().trim(), parts.next().unwrap().trim(), parts.next().unwrap().trim());
    let file = PathBuf::from(path);
    if !file.is_file() {
        return Err(Fail::new("the download didn't produce a file"));
    }
    let _ = std::fs::write(dir.join(format!("{id}.title")), title);
    prune();
    Ok((file, title.to_string()))
}

// ------------------------------------------------------------------------------------------------ the temporary cache

fn cache_root() -> PathBuf {
    std::env::temp_dir().join("SongSpecAnalyzer-cache")
}

/// This session's cache folder (deleted on exit); leftovers from earlier sessions are removed now.
pub fn session_cache() -> PathBuf {
    let root = cache_root();
    if let Ok(rd) = std::fs::read_dir(&root) {
        for e in rd.flatten() {
            let _ = std::fs::remove_dir_all(e.path()); // fails harmlessly on a folder another running copy still uses
        }
    }
    root.join(std::process::id().to_string())
}

pub fn clear_cache(dir: &Path) {
    let _ = std::fs::remove_dir_all(dir);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn links() {
        let sp = |k: &str, i: &str| Some(Link::Spotify { kind: k.into(), id: i.into() });
        assert_eq!(parse("https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b?si=abc"), sp("track", "0VjIjW4GlUZAMYd2vXMi3b"));
        assert_eq!(parse("https://open.spotify.com/intl-de/album/4yP0hdKOZPNshxUOjY0cZj"), sp("album", "4yP0hdKOZPNshxUOjY0cZj"));
        assert_eq!(parse("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"), sp("playlist", "37i9dQZF1DXcBWIGoYBM5M"));
        assert_eq!(parse("https://music.apple.com/us/album/after-hours/1499378108?i=1499378615"), Some(Link::AppleTrack("1499378615".into())));
        assert_eq!(parse("https://music.apple.com/us/album/after-hours/1499378108"), Some(Link::AppleAlbum("1499378108".into())));
        assert_eq!(parse("https://youtu.be/4NRXx6U8ABQ?t=3"), Some(Link::YouTube("https://www.youtube.com/watch?v=4NRXx6U8ABQ".into())));
        assert_eq!(parse("https://www.youtube.com/watch?v=4NRXx6U8ABQ&list=PL1"), Some(Link::YouTube("https://www.youtube.com/watch?v=4NRXx6U8ABQ".into())));
        assert_eq!(parse("https://www.youtube.com/playlist?list=PL1"), Some(Link::YouTubePlaylist("https://www.youtube.com/playlist?list=PL1".into())));
        assert_eq!(parse("https://example.com/song"), None);
    }

    #[test]
    fn errors() {
        let k = |s: &str| classify(&format!("WARNING: something\nERROR: {s}"));
        assert_eq!(k("Failed to decrypt with DPAPI. See  https://github.com/yt-dlp/yt-dlp/issues/10927  for more info"), Kind::Cookies);
        assert_eq!(k("could not find chrome cookies database in \"C:\\Users\\x\\AppData\\Local\\Google\\Chrome\\User Data\""), Kind::Cookies);
        assert_eq!(k("Could not copy Chrome cookie database. See  https://github.com/yt-dlp/yt-dlp/issues/7271  for more info"), Kind::Cookies);
        assert_eq!(k("'cookies.txt' does not look like a Netscape format cookies file"), Kind::Cookies);
        assert_eq!(
            k("[youtube] 4NRXx6U8ABQ: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to manually pass cookies"),
            Kind::Bot
        );
        assert_eq!(k("[youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users. Use --cookies-from-browser or --cookies"), Kind::Age);
        assert_eq!(k("unable to download video data: HTTP Error 429: Too Many Requests"), Kind::RateLimit);
        assert_eq!(k("[youtube] abc: Video unavailable. This content isn't available, try again later."), Kind::RateLimit);
        assert_eq!(k("unable to download video data: HTTP Error 403: Forbidden"), Kind::Forbidden);
        assert_eq!(k("[youtube] abc: Video unavailable. This video has been removed by the uploader"), Kind::Unavailable);
        assert_eq!(k("[youtube] xxxxxxxxxxx: This video is unavailable"), Kind::Unavailable);
        assert_eq!(k("[youtube] abc: Private video. Sign in if you've been granted access to this video"), Kind::Unavailable);
        assert_eq!(k("Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>"), Kind::Network);
        assert_eq!(k("something new"), Kind::Other);
        // the short message never shows yt-dlp's raw line for a block
        assert!(message(Kind::Bot, "", false).starts_with("YouTube blocked this request."));
        assert!(message(Kind::Bot, "", true).contains("couldn't be read"));
    }

    #[test]
    fn old_settings_drop_chromium_browsers() {
        assert_eq!(utc_now().len(), "2026-11-16 14:03:07 UTC".len());
        let _guard = SETTINGS_TEST.lock().unwrap_or_else(|e| e.into_inner()); // the whole test: it ends by reloading the settings
        let _ = std::fs::create_dir_all(app_dir());
        for (b, want) in [("edge", CookieMode::None), ("chrome", CookieMode::None), ("firefox", CookieMode::Firefox)] {
            std::fs::write(settings_path(), format!("{{\"cookies_from_browser\":\"{b}\"}}")).unwrap();
            load_settings();
            assert_eq!(settings().cookies, want, "{b}");
            assert_eq!(settings().gap_secs, 4.0);
        }
        std::fs::remove_file(settings_path()).unwrap();
        load_settings();
    }

    static SETTINGS_TEST: Mutex<()> = Mutex::new(());

    /// A stand-in yt-dlp: any cookies -> the DPAPI error; the first request without -> the bot check; then it works. The song must
    /// still come through: unreadable cookies are skipped, the bot check is waited out.
    #[test]
    fn unreadable_cookies_never_fail_a_song() {
        let dir = std::env::temp_dir().join(format!("songspec-fake-ytdlp-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        #[cfg(windows)]
        let exe = {
            let p = dir.join("fake.cmd");
            std::fs::write(
                &p,
                "@echo off\r\n\
                 echo %* | findstr /C:\"--cookies\" >nul && (echo ERROR: Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927 1>&2& exit /b 1)\r\n\
                 if not exist \"%~dp0blocked\" (type nul > \"%~dp0blocked\"& echo ERROR: [youtube] abc: Sign in to confirm you're not a bot. 1>&2& exit /b 1)\r\n\
                 echo abc\tdone\r\n",
            )
            .unwrap();
            p
        };
        #[cfg(unix)]
        let exe = {
            use std::os::unix::fs::PermissionsExt;
            let p = dir.join("fake.sh");
            std::fs::write(
                &p,
                "#!/bin/sh\ncase \"$*\" in *--cookies*) echo 'ERROR: Failed to decrypt with DPAPI.' >&2; exit 1;; esac\n\
                 d=$(dirname \"$0\"); if [ ! -e \"$d/blocked\" ]; then touch \"$d/blocked\"; echo \"ERROR: [youtube] abc: Sign in to confirm you're not a bot.\" >&2; exit 1; fi\n\
                 printf 'abc\\tdone\\n'\n",
            )
            .unwrap();
            std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
            p
        };
        let cookies = dir.join("cookies.txt");
        std::fs::write(&cookies, "# Netscape HTTP Cookie File\n").unwrap();
        let _guard = SETTINGS_TEST.lock().unwrap_or_else(|e| e.into_inner());
        *SETTINGS.lock().unwrap() = Some(Settings { cookies: CookieMode::File, cookies_file: Some(cookies), gap_secs: 2.0 });
        let seen = Mutex::new(vec![]);
        let status = |s: String| seen.lock().unwrap().push(s);
        let p = Progress { status: &status, cancelled: &never };
        let out = request(&exe, &["--print", "id", "https://www.youtube.com/watch?v=abc"], &p).expect("the song came through");
        assert!(out.contains("done"));
        let seen = seen.into_inner().unwrap();
        assert!(seen.iter().any(|s| s.contains("trying cookies.txt")), "{seen:?}");
        assert!(seen.iter().any(|s| s.starts_with("rate limited, retrying in")), "{seen:?}");
        *SETTINGS.lock().unwrap() = None;
        let _ = std::fs::remove_dir_all(&dir);
    }
}

#[cfg(test)]
mod net_tests {
    use super::*;

    /// talks to Spotify and Apple: cargo test -p songspec-app -- --ignored
    #[test]
    #[ignore]
    fn expands_real_links() {
        for (url, min) in [
            ("https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b", 1),
            ("https://open.spotify.com/album/4yP0hdKOZPNshxUOjY0cZj", 10),
            ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", 20),
            ("https://music.apple.com/us/album/after-hours/1499378108", 10),
            ("https://music.apple.com/us/album/after-hours/1499378108?i=1499378615", 1),
        ] {
            let items = expand(&parse(url).unwrap(), None).unwrap();
            let first = match &items[0] {
                Item::Song(t) => format!("{} ({:?} s)", t.label(), t.duration.map(|d| d.round())),
                Item::Video(v) => v.clone(),
            };
            println!("{url}\n   {} songs, first: {first}", items.len());
            assert!(items.len() >= min);
        }
    }

    /// A queue of an album's songs, one after another with no cookies, through the real yt-dlp the app installed:
    /// cargo test -p songspec-app back_to_back -- --ignored --nocapture
    #[test]
    #[ignore]
    fn back_to_back() {
        let exe = std::env::var_os("SONGSPEC_YTDLP").map(PathBuf::from).expect("SONGSPEC_YTDLP = the yt-dlp to use");
        let n: usize = std::env::var("SONGSPEC_SONGS").ok().and_then(|v| v.parse().ok()).unwrap_or(12);
        let items = expand(&parse("https://open.spotify.com/album/4yP0hdKOZPNshxUOjY0cZj").unwrap(), None).unwrap();
        let t0 = Instant::now();
        let mut failed = 0;
        for it in items.iter().take(n) {
            let Item::Song(t) = it else { continue };
            let s = Instant::now();
            let status = |m: String| println!("      {m}");
            let p = Progress { status: &status, cancelled: &never };
            let r = find_on_youtube(&exe, t, &p).and_then(|u| download(&exe, &u, &p).map(|(f, title)| format!("{title} ({} kB)", std::fs::metadata(f).map(|m| m.len() / 1000).unwrap_or(0))));
            match r {
                Ok(m) => println!("ok   {:5.1}s  {} -> {m}", s.elapsed().as_secs_f64(), t.label()),
                Err(f) => {
                    failed += 1;
                    println!("FAIL {:5.1}s  {}: {}\n{}", s.elapsed().as_secs_f64(), t.label(), f.msg, f.raw);
                }
            }
        }
        println!("{} songs in {:.0} s, {failed} failed", n.min(items.len()), t0.elapsed().as_secs_f64());
        assert_eq!(failed, 0);
    }
}
