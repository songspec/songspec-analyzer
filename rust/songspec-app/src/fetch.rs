//! Songs from links. Spotify tracks, albums and playlists (from Spotify's public embed pages) and Apple Music songs and albums
//! (Apple's iTunes Lookup) give title, artist and length, and each song is then found on YouTube; YouTube videos and playlists are
//! used directly. Audio comes from YouTube through yt-dlp, which is not bundled: it is downloaded from its official GitHub release
//! on first use, after the user confirms, and checked against the release's SHA-256 list.
//! Downloads go to a per-session temporary cache that is deleted when the app closes (and leftovers on the next start).

use std::path::{Path, PathBuf};
use std::process::Command;

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
            let out = run(exe, &["--flat-playlist", "--no-warnings", "--print", "%(id)s", u])?;
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
    std::fs::write(&p, bytes).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    }
    Ok(p)
}

fn run(exe: &Path, args: &[&str]) -> Result<String, String> {
    let mut c = Command::new(exe);
    c.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console flashing up
    }
    let out = c.output().map_err(|e| format!("couldn't run yt-dlp ({e})"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let last = err.lines().filter(|l| l.contains("ERROR")).last().unwrap_or(err.lines().last().unwrap_or("")).to_string();
        return Err(format!("yt-dlp: {}", last.trim()));
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

/// The best YouTube video for a song: of the first 5 search results, the one whose length is closest to the song's.
pub fn find_on_youtube(exe: &Path, t: &Track) -> Result<String, String> {
    let q = format!("ytsearch5:{} audio", t.label());
    let out = run(exe, &["--flat-playlist", "--no-warnings", "--print", "%(id)s\t%(duration)s", &q])?;
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
    best.map(|h| format!("https://www.youtube.com/watch?v={}", h.0)).ok_or_else(|| "nothing found on YouTube".into())
}

/// Download a YouTube video's audio (M4A / AAC, which the analyzer decodes without ffmpeg) into `dir`. -> (file, video title).
pub fn download(exe: &Path, url: &str, dir: &Path) -> Result<(PathBuf, String), String> {
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let tmpl = dir.join("%(id)s.%(ext)s");
    let out = run(
        exe,
        &[
            "-f", "140/bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]",
            "--no-playlist", "--no-warnings", "--no-part", "--no-mtime",
            "-o", &tmpl.to_string_lossy(),
            "--print", "after_move:%(filepath)s\t%(title)s",
            url,
        ],
    )?;
    let line = out.lines().rev().find(|l| l.contains('\t')).ok_or("yt-dlp didn't report a file")?;
    let (path, title) = line.split_once('\t').unwrap();
    let p = PathBuf::from(path.trim());
    if !p.is_file() {
        return Err("the download didn't produce a file".into());
    }
    Ok((p, title.trim().to_string()))
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
}
