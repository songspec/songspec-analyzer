//! Names in house style, the SongSpec tracker's spellings (the Rust twin of service/songspec_analyzer/display/key.py and
//! client/formatters.js; all three are checked against service/tests/display_cases.json).
//!
//! Key field: Major C Db D Eb E F F# G Ab A Bb B, Minor C C# D Eb E F F# G G# A Bb B ("Eb Minor", "Db Major"); a mode shows
//! its parent with "*" and its own name in the note ("Bb Minor*", note "A# Phrygian"). Mode names take the parent's spelling
//! except Phrygian / Locrian / Phrygian Dominant on D#, G#, A#. Chord roots follow the key signature (sharps in sharp keys).

pub const FLAT_NAMES: [&str; 12] = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
pub const SHARP_NAMES: [&str; 12] = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
pub const MAJOR_NAMES: [&str; 12] = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
pub const MINOR_NAMES: [&str; 12] = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"];
const SHARP_MODES: [&str; 3] = ["phrygian", "locrian", "phrygian_dominant"];
const SHARP_MAJOR_KEYS: [usize; 6] = [7, 2, 9, 4, 11, 6];

/// (mode id, parent "Major"/"Minor", display name, intervals from the tonic)
pub const MODES: [(&str, &str, &str, &[usize]); 15] = [
    ("major", "Major", "Major", &[0, 2, 4, 5, 7, 9, 11]),
    ("minor", "Minor", "Minor", &[0, 2, 3, 5, 7, 8, 10]),
    ("ionian", "Major", "Ionian", &[0, 2, 4, 5, 7, 9, 11]),
    ("dorian", "Minor", "Dorian", &[0, 2, 3, 5, 7, 9, 10]),
    ("phrygian", "Minor", "Phrygian", &[0, 1, 3, 5, 7, 8, 10]),
    ("lydian", "Major", "Lydian", &[0, 2, 4, 6, 7, 9, 11]),
    ("mixolydian", "Major", "Mixolydian", &[0, 2, 4, 5, 7, 9, 10]),
    ("aeolian", "Minor", "Aeolian", &[0, 2, 3, 5, 7, 8, 10]),
    ("locrian", "Minor", "Locrian", &[0, 1, 3, 5, 6, 8, 10]),
    ("harmonic_minor", "Minor", "Harmonic Minor", &[0, 2, 3, 5, 7, 8, 11]),
    ("phrygian_dominant", "Minor", "Phrygian Dominant", &[0, 1, 4, 5, 7, 8, 10]),
    ("melodic_minor", "Minor", "Melodic Minor", &[0, 2, 3, 5, 7, 9, 11]),
    ("harmonic_major", "Major", "Harmonic Major", &[0, 2, 4, 5, 7, 8, 11]),
    ("lydian_dominant", "Major", "Lydian Dominant", &[0, 2, 4, 6, 7, 9, 10]),
    ("hungarian_minor", "Minor", "Hungarian Minor", &[0, 2, 3, 6, 7, 8, 11]),
];

pub fn mode_info(mode: &str) -> (&'static str, &'static str, &'static str, &'static [usize]) {
    let m = norm_mode(mode).unwrap_or("major");
    *MODES.iter().find(|x| x.0 == m).unwrap()
}

pub fn norm_mode(mode: &str) -> Option<&'static str> {
    let m = mode.trim().to_lowercase().replace([' ', '-'], "_");
    let m = match m.as_str() {
        "maj" => "major".to_string(),
        "min" => "minor".to_string(),
        "dorion" => "dorian".to_string(),
        _ => m,
    };
    MODES.iter().find(|x| x.0 == m).map(|x| x.0)
}

pub fn is_plain(mode: &str) -> bool {
    matches!(norm_mode(mode), Some("major" | "minor" | "ionian" | "aeolian"))
}

pub fn is_minor(mode: &str) -> bool {
    mode_info(mode).1 == "Minor"
}

/// "Bb" / "A#" / "c#" -> pitch class.
pub fn parse_pitch(name: &str) -> Option<usize> {
    let s = name.trim();
    let mut ch = s.chars();
    let base: i32 = match ch.next()?.to_ascii_uppercase() {
        'C' => 0,
        'D' => 2,
        'E' => 4,
        'F' => 5,
        'G' => 7,
        'A' => 9,
        'B' => 11,
        _ => return None,
    };
    let mut pc = base;
    for c in ch {
        match c {
            '#' | '♯' => pc += 1,
            'b' | '♭' => pc -= 1,
            ' ' => {}
            _ => return None,
        }
    }
    Some(pc.rem_euclid(12) as usize)
}

/// "A# Phrygian" / "Bb minor" / "C# Major" / "F#m" -> (tonic, mode id).
pub fn parse_key(text: &str) -> Option<(usize, &'static str)> {
    let t = text.trim().trim_end_matches('*').trim();
    let bytes: Vec<char> = t.chars().collect();
    let mut n = 1;
    if bytes.len() > 1 && matches!(bytes[1], '#' | '♯' | 'b' | '♭') {
        n = 2;
    }
    let tonic: String = bytes[..n.min(bytes.len())].iter().collect();
    let rest: String = bytes[n.min(bytes.len())..].iter().collect::<String>().trim().to_string();
    let pc = parse_pitch(&tonic)?;
    let mode = if rest == "m" { "minor" } else if rest.is_empty() { "major" } else { norm_mode(&rest)? };
    Some((pc, mode))
}

/// The tonic as the key field spells it (by the parent Major / Minor).
pub fn key_tonic(pc: usize, mode: &str) -> &'static str {
    if is_minor(mode) { MINOR_NAMES[pc % 12] } else { MAJOR_NAMES[pc % 12] }
}

/// The tonic as a mode name spells it ("A# Phrygian", "Bb Dorian").
pub fn mode_tonic(pc: usize, mode: &str) -> &'static str {
    let m = norm_mode(mode).unwrap_or("major");
    if SHARP_MODES.contains(&m) && [3, 8, 10].contains(&(pc % 12)) {
        return SHARP_NAMES[pc % 12];
    }
    key_tonic(pc, m)
}

/// "A# Phrygian", "C# Minor", "Eb Harmonic Minor".
pub fn mode_name(pc: usize, mode: &str) -> String {
    format!("{} {}", mode_tonic(pc, mode), mode_info(mode).2)
}

/// Key field: "Bb Minor", "Bb Minor*" (modal), "Db Major", "C# Minor".
pub fn house_key(pc: usize, mode: &str) -> String {
    let (_, parent, _, _) = mode_info(mode);
    format!("{} {}{}", key_tonic(pc, mode), parent, if is_plain(mode) { "" } else { "*" })
}

/// index tonic + 12 * minor -> "Db Major", "C# Minor"
pub fn key_name(index: usize) -> String {
    house_key(index % 12, if index >= 12 { "minor" } else { "major" })
}

pub fn camelot(pc: usize, mode: &str) -> String {
    let minor = is_minor(mode);
    let major_pc = if minor { (pc + 3) % 12 } else { pc % 12 };
    let num = ((7 * major_pc) % 12 + 7) % 12 + 1;
    format!("{}{}", num, if minor { "A" } else { "B" })
}

pub fn open_key(pc: usize, mode: &str) -> String {
    let c = camelot(pc, mode);
    let num: i32 = c[..c.len() - 1].parse().unwrap();
    let n = (num - 8).rem_euclid(12) + 1;
    format!("{}{}", n, if c.ends_with('A') { "m" } else { "d" })
}

/// {key, note}: the note names the mode for a modal key.
pub fn key_display(pc: usize, mode: &str) -> (String, Option<String>) {
    let note = if is_plain(mode) { None } else { Some(mode_name(pc, mode)) };
    (house_key(pc, mode), note)
}

fn fmt_time(t: f64) -> String {
    let t = t.round().max(0.0) as i64;
    format!("{}:{:02}", t / 60, t % 60)
}

/// Several section keys -> one key field ("C Minor* / C Major") and one note ("0:00-1:35 in F Minor, 1:35-3:32 in C Minor").
pub fn sections_display(sections: &[(f64, f64, usize, &str)]) -> (Option<String>, Option<String>) {
    let mut merged: Vec<(usize, &str, f64, f64)> = Vec::new();
    for &(start, end, pc, mode) in sections {
        let m = norm_mode(mode).unwrap_or("major");
        if let Some(last) = merged.last_mut() {
            if last.0 == pc % 12 && last.1 == m {
                last.3 = end;
                continue;
            }
        }
        merged.push((pc % 12, m, start, end));
    }
    if merged.is_empty() {
        return (None, None);
    }
    if merged.len() == 1 {
        let (k, n) = key_display(merged[0].0, merged[0].1);
        return (Some(k), n);
    }
    let mut names: Vec<String> = Vec::new();
    for m in &merged {
        let n = house_key(m.0, m.1);
        if !names.contains(&n) {
            names.push(n);
        }
    }
    let parts: Vec<String> = merged.iter().map(|m| format!("{}-{} in {}", fmt_time(m.2), fmt_time(m.3), mode_name(m.0, m.1))).collect();
    (Some(names.join(" / ")), Some(parts.join(", ")))
}

pub fn sharp_key(pc: usize, mode: &str) -> bool {
    let rel = if is_minor(mode) { (pc + 3) % 12 } else { pc % 12 };
    SHARP_MAJOR_KEYS.contains(&rel)
}

/// (root, quality, bass) of a chord label; None for "N".
pub fn split_chord(label: &str) -> Option<(usize, String, Option<usize>)> {
    if label == "N" || label == "X" {
        return None;
    }
    let (main, bass) = match label.split_once('/') {
        Some((a, b)) => (a, parse_pitch(b)),
        None => (label, None),
    };
    let n = if main.len() > 1 && matches!(&main[1..2], "#" | "b") { 2 } else { 1 };
    let root = parse_pitch(&main[..n])?;
    Some((root, main[n..].to_string(), bass))
}

/// A chord label respelled for the key: "Db" -> "C#" in F# Major, unchanged in a flat key.
pub fn spell_chord(label: &str, pc: usize, mode: &str) -> String {
    if !sharp_key(pc, mode) {
        return label.to_string();
    }
    match split_chord(label) {
        None => label.to_string(),
        Some((r, q, b)) => {
            let mut s = format!("{}{}", SHARP_NAMES[r], q);
            if let Some(b) = b {
                s += &format!("/{}", SHARP_NAMES[b]);
            }
            s
        }
    }
}

const ROMAN: [&str; 7] = ["I", "II", "III", "IV", "V", "VI", "VII"];

/// Roman numeral relative to the key's parent scale (i-VI-III-VII in a minor key, ♭VII for Bb in C Major).
pub fn roman(root: usize, quality: &str, tonic: usize, mode: &str) -> String {
    let minor_key = is_minor(mode);
    let scale: &[usize] = if minor_key { &[0, 2, 3, 5, 7, 8, 10] } else { &[0, 2, 4, 5, 7, 9, 11] };
    let iv = (root + 12 - tonic % 12) % 12;
    let (deg, acc) = if let Some(d) = scale.iter().position(|&x| x == iv) {
        (d, "")
    } else if minor_key && [4, 9, 11].contains(&iv) {
        (scale.iter().position(|&x| x == (iv + 11) % 12).unwrap(), "♯")
    } else {
        (scale.iter().position(|&x| x == (iv + 1) % 12).unwrap(), "♭")
    };
    let q = quality;
    let minorish = (q.starts_with('m') && !q.starts_with("maj")) || q.starts_with("dim") || q == "m7b5";
    let num = if minorish { ROMAN[deg].to_lowercase() } else { ROMAN[deg].to_string() };
    let suffix = if q.starts_with("dim7") {
        "°7".to_string()
    } else if q.starts_with("dim") {
        "°".into()
    } else if q == "m7b5" {
        "ø7".into()
    } else if q.starts_with("aug") {
        "+".into()
    } else if q == "7" || q == "m7" {
        "7".into()
    } else if q == "maj7" {
        "maj7".into()
    } else if q == "9" || q == "m9" {
        "9".into()
    } else if q == "6" || q == "m6" {
        "6".into()
    } else if q.starts_with("sus") {
        q.to_string()
    } else if q == "add9" {
        "add9".into()
    } else {
        String::new()
    };
    format!("{acc}{num}{suffix}")
}

// ---------------------------------------------------------------------------------------------------- detune (display/detune.py)

const MINUS: &str = "−";

pub fn signed(c: f64, decimals: usize) -> String {
    let s = format!("{:.*}", decimals, c.abs());
    if s.parse::<f64>().unwrap() == 0.0 {
        return format!("{:.*}", decimals, 0.0);
    }
    format!("{}{}", if c > 0.0 { "+" } else { MINUS }, s)
}

pub struct DetuneText {
    pub display: String,
    pub fix: String,
    pub a4_hz: f64,
    pub key_text: Option<String>,
}

pub fn detune_display(cents: f64, key: Option<(usize, &str)>) -> DetuneText {
    let c = crate::np::py_round(cents, 1);
    let fix = if c.abs() >= 0.05 { format!("pitch by {} ¢ to fix", signed(-c, 1)) } else { "on A440, nothing to fix".into() };
    let a4 = crate::np::py_round(440.0 * 2f64.powf(c / 1200.0), 2);
    let key_text = match key {
        Some((pc, mode)) if c.abs() >= 0.05 => {
            let k = house_key(pc, mode).trim_end_matches('*').to_string();
            let tonic = k.split(' ').next().unwrap().to_string();
            let (dir, verb) = if c > 0.0 { ("above", "down") } else { ("below", "up") };
            Some(format!("Plays in {k} ({} ¢ {dir} {tonic}). If you pitch {verb} {:.1} ¢, it's {k} at A440.", signed(c, 1), c.abs()))
        }
        _ => None,
    };
    DetuneText { display: format!("{} ¢", signed(c, 1)), fix, a4_hz: a4, key_text }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cases() -> serde_json::Value {
        serde_json::from_str(include_str!("../../../service/tests/display_cases.json")).unwrap()
    }

    #[test]
    fn shared_house_key_cases() {
        for c in cases()["house_key"].as_array().unwrap() {
            let (pc, m) = parse_key(c["key"].as_str().unwrap()).unwrap();
            assert_eq!(house_key(pc, m), c["expect"].as_str().unwrap(), "{}", c["key"]);
        }
    }

    #[test]
    fn shared_mode_name_and_note_cases() {
        for c in cases()["mode_name"].as_array().unwrap() {
            let (pc, m) = parse_key(c["key"].as_str().unwrap()).unwrap();
            assert_eq!(mode_name(pc, m), c["expect"].as_str().unwrap(), "{}", c["key"]);
        }
        for c in cases()["key_note"].as_array().unwrap() {
            if !c["source"].is_null() {
                continue; // Hook Theory notes are a service feature
            }
            let (pc, m) = parse_key(c["key"].as_str().unwrap()).unwrap();
            assert_eq!(key_display(pc, m).1.as_deref(), c["expect"].as_str(), "{}", c["key"]);
        }
    }

    #[test]
    fn shared_chord_spelling_camelot_roman_cases() {
        let all = cases();
        for c in all["chord_spelling"].as_array().unwrap() {
            let (pc, m) = parse_key(c["key"].as_str().unwrap()).unwrap();
            assert_eq!(spell_chord(c["chord"].as_str().unwrap(), pc, m), c["expect"].as_str().unwrap());
        }
        for c in all["camelot"].as_array().unwrap() {
            let (pc, m) = parse_key(c["key"].as_str().unwrap()).unwrap();
            assert_eq!(camelot(pc, m), c["camelot"].as_str().unwrap());
            assert_eq!(open_key(pc, m), c["open_key"].as_str().unwrap());
        }
        for c in all["roman"].as_array().unwrap() {
            let (r, q, _) = split_chord(c["chord"].as_str().unwrap()).unwrap();
            let (pc, m) = parse_key(c["key"].as_str().unwrap()).unwrap();
            assert_eq!(roman(r, &q, pc, m), c["expect"].as_str().unwrap(), "{} in {}", c["chord"], c["key"]);
        }
        for c in all["detune"].as_array().unwrap() {
            let key = c["tonic"].as_str().map(|t| (parse_pitch(t).unwrap(), norm_mode(c["mode"].as_str().unwrap()).unwrap()));
            let d = detune_display(c["cents"].as_f64().unwrap(), key);
            if let Some(v) = c["display"].as_str() {
                assert_eq!(d.display, v);
            }
            if let Some(v) = c["fix"].as_str() {
                assert_eq!(d.fix, v);
            }
            if let Some(v) = c["key_text"].as_str() {
                assert_eq!(d.key_text.as_deref(), Some(v));
            }
        }
    }

    #[test]
    fn shared_section_cases() {
        for c in cases()["sections"].as_array().unwrap() {
            if !c["labels"].is_null() {
                continue; // labelled sections come from Hook Theory in the service
            }
            let secs: Vec<(f64, f64, usize, &str)> = c["sections"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| {
                    let (pc, m) = parse_key(s["key"].as_str().unwrap()).unwrap();
                    (s["start"].as_f64().unwrap(), s["end"].as_f64().unwrap(), pc, m)
                })
                .collect();
            let (k, n) = sections_display(&secs);
            assert_eq!(k.as_deref(), c["expect_key"].as_str());
            assert_eq!(n.as_deref(), c["expect_note"].as_str());
        }
    }
}
