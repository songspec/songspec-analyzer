//! Spotify / Apple Music links for a song, on request only (a button): Apple Music through Apple's public iTunes Search API (no
//! account needed), Spotify as a search link (its API needs credentials the app does not have).

#[derive(Clone, Debug)]
pub struct Links {
    /// (label "Artist - Title", Apple Music URL)
    pub apple: Vec<(String, String)>,
    pub spotify_search: String,
}

/// What to search for: the file's own tags, else the file name without its extension and track number.
pub fn query(artist: Option<&str>, title: Option<&str>, file_name: &str) -> String {
    match (artist, title) {
        (Some(a), Some(t)) if !a.trim().is_empty() && !t.trim().is_empty() => format!("{} {}", a.trim(), t.trim()),
        (_, Some(t)) if !t.trim().is_empty() => t.trim().to_string(),
        _ => {
            let stem = std::path::Path::new(file_name).file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
            // "01 - Song", "1-02 Song", "03. Song" -> "Song"
            let t = stem.trim_start_matches(|c: char| c.is_ascii_digit() || c == '-' || c == '.' || c == ' ');
            if t.is_empty() { stem } else { t.to_string() }
        }
    }
}

fn encode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            b' ' => "+".to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

pub fn lookup(q: &str) -> Result<Links, String> {
    let url = format!("https://itunes.apple.com/search?term={}&media=music&entity=song&limit=3", encode(q));
    let body = ureq::get(&url)
        .header("User-Agent", "SongSpecAnalyzer-Desktop")
        .call()
        .map_err(|e| format!("Apple Music lookup failed ({e})"))?
        .into_body()
        .read_to_string()
        .map_err(|e| e.to_string())?;
    let v: serde_json::Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;
    let apple = v["results"]
        .as_array()
        .map(|r| {
            r.iter()
                .filter_map(|x| {
                    let url = x["trackViewUrl"].as_str()?;
                    Some((format!("{} - {}", x["artistName"].as_str().unwrap_or("?"), x["trackName"].as_str().unwrap_or("?")), url.to_string()))
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(Links { apple, spotify_search: format!("https://open.spotify.com/search/{}", encode(q).replace('+', "%20")) })
}
