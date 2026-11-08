//! Chord exports and copy texts, ported from the website client (client/exports.js, client/formatters.js): transpose / simplify,
//! MIDI (chords on the song's own beat grid with a tempo map), ChordPro, the progression line and the tracker row.

use songspec_core::analysis::Analysis;
use songspec_core::chords::{quality, triad_of};
use songspec_core::notation::{self, FLAT_NAMES, SHARP_NAMES};

const PPQ: i64 = 480;

/// One chord name transposed by `tr` semitones and/or reduced to its triad. Spelled for the (transposed) key: sharps in sharp keys.
pub fn transform(name: &str, tr: i32, simplify: bool, key: Option<(usize, &str)>) -> String {
    if name == "N" || name == "X" || (tr == 0 && !simplify) {
        return name.to_string();
    }
    let Some((root, q, bass)) = notation::split_chord(name) else { return name.to_string() };
    let (q, bass) = if simplify { (triad_of(&q).to_string(), None) } else { (q, bass) };
    let sharp = key.map(|(pc, m)| notation::sharp_key((pc as i32 + tr).rem_euclid(12) as usize, m)).unwrap_or(false);
    let names = if sharp { SHARP_NAMES } else { FLAT_NAMES };
    let mv = |pc: usize| names[(pc as i32 + tr).rem_euclid(12) as usize];
    let mut out = format!("{}{}", mv(root), q);
    if let Some(b) = bass {
        out += &format!("/{}", mv(b));
    }
    out
}

pub fn song_key(a: &Analysis) -> Option<(usize, String)> {
    a.key.as_ref().map(|k| (k.tonic_pc, k.mode_id.clone()))
}

fn chord_notes(name: &str) -> Vec<u8> {
    let Some((root, q, bass)) = notation::split_chord(name) else { return vec![] };
    let Some((_, iv, _, _)) = quality(&q) else { return vec![] };
    let mut notes: Vec<u8> = iv.iter().map(|i| { let pc = (root + i) % 12; 48 + pc as u8 + if pc < 5 { 12 } else { 0 } }).collect();
    notes.sort();
    notes.dedup();
    let mut out = vec![36 + bass.unwrap_or(root) as u8];
    out.extend(notes);
    out
}

fn vlq(mut n: u32) -> Vec<u8> {
    let mut o = vec![(n & 0x7f) as u8];
    n >>= 7;
    while n > 0 {
        o.insert(0, ((n & 0x7f) | 0x80) as u8);
        n >>= 7;
    }
    o
}

fn track(events: Vec<(i64, Vec<u8>)>) -> Vec<u8> {
    let mut ev: Vec<(i64, usize, Vec<u8>)> = events.into_iter().enumerate().map(|(i, (t, b))| (t, i, b)).collect();
    ev.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
    let mut data = Vec::new();
    let mut last = 0;
    for (t, _, b) in ev {
        data.extend(vlq((t - last) as u32));
        data.extend(b);
        last = t;
    }
    data.extend([0x00, 0xff, 0x2f, 0x00]);
    let mut out = b"MTrk".to_vec();
    out.extend((data.len() as u32).to_be_bytes());
    out.extend(data);
    out
}

fn meta(kind: u8, text: &str) -> Vec<u8> {
    let b = text.as_bytes();
    let mut v = vec![0xff, kind];
    v.extend(vlq(b.len() as u32));
    v.extend_from_slice(b);
    v
}

/// Type-1 MIDI: tempo map, chords, info. None without beats and chords.
pub fn midi(a: &Analysis, tr: i32, simplify: bool) -> Option<Vec<u8>> {
    let beats = &a.tempo.as_ref()?.beats;
    let segs = &a.chords.as_ref()?.segments;
    if beats.len() < 2 || segs.is_empty() {
        return None;
    }
    let key = song_key(a);
    let key = key.as_ref().map(|(p, m)| (*p, m.as_str()));
    let mut grid = if beats[0] > 0.02 { vec![0.0] } else { vec![] };
    grid.extend(beats.iter().cloned());
    let tick = |t: f64| -> i64 {
        let lo = grid.partition_point(|&g| g <= t);
        let i = (lo as i64 - 1).clamp(0, grid.len() as i64 - 2) as usize;
        (((i as f64 + (t - grid[i]) / (grid[i + 1] - grid[i])) * PPQ as f64).round_ties_even() as i64).max(0)
    };
    let mut t0 = vec![(0, meta(0x03, "Tempo map"))];
    for k in 0..grid.len() - 1 {
        let us = (((grid[k + 1] - grid[k]) * 1e6).round_ties_even() as i64).min(0xffffff) as u32;
        t0.push((k as i64 * PPQ, vec![0xff, 0x51, 0x03, (us >> 16) as u8, (us >> 8) as u8, us as u8]));
    }
    let mut t1 = vec![(0, meta(0x03, "Chords"))];
    for s in segs {
        let name = transform(&s.chord, tr, simplify, key);
        let st = tick(s.start);
        let en = (tick(s.end) - 1).max(st + 1);
        for n in chord_notes(&name) {
            t1.push((st, vec![0x90, n, 80]));
            t1.push((en, vec![0x80, n, 0]));
        }
        if name != "N" {
            t1.push((st, meta(0x06, &name)));
        }
    }
    let mut t2 = vec![(0, meta(0x03, "Info"))];
    if let Some(k) = &a.key {
        t2.push((0, meta(0x01, &format!("Key: {}", k.display.key))));
    }
    let mut out = b"MThd".to_vec();
    out.extend(6u32.to_be_bytes());
    out.extend([0, 1, 0, 3]);
    out.extend((PPQ as u16).to_be_bytes());
    out.extend(track(t0));
    out.extend(track(t1));
    out.extend(track(t2));
    Some(out)
}

fn bars(a: &Analysis, tr: i32, simplify: bool) -> Vec<Vec<String>> {
    let key = song_key(a);
    let key = key.as_ref().map(|(p, m)| (*p, m.as_str()));
    let segs = a.chords.as_ref().map(|c| c.segments.as_slice()).unwrap_or(&[]);
    let downs = a.tempo.as_ref().map(|t| t.downbeats.as_slice()).unwrap_or(&[]);
    if downs.is_empty() {
        return segs.iter().map(|s| vec![transform(&s.chord, tr, simplify, key)]).collect();
    }
    (0..downs.len())
        .map(|i| {
            let (d, e) = (downs[i], if i + 1 < downs.len() { downs[i + 1] } else { f64::INFINITY });
            let n: Vec<String> = segs.iter().filter(|s| s.end > d + 1e-3 && s.start < e - 1e-3).map(|s| transform(&s.chord, tr, simplify, key)).collect();
            if n.is_empty() { vec!["N".into()] } else { n }
        })
        .collect()
}

pub fn title_of(a: &Analysis, file_name: &str) -> String {
    a.track.title.clone().unwrap_or_else(|| std::path::Path::new(file_name).file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default())
}

pub fn chordpro(a: &Analysis, file_name: &str, tr: i32, simplify: bool) -> String {
    let mut lines = vec![format!("{{title: {}}}", title_of(a, file_name))];
    if let Some(ar) = &a.track.artist {
        lines.push(format!("{{artist: {ar}}}"));
    }
    if let Some(k) = &a.key {
        lines.push(format!("{{key: {}}}", k.display.key.split(" / ").next().unwrap().replace('*', "")));
    }
    if let Some(b) = a.tempo.as_ref().and_then(|t| t.display.bpm.clone()) {
        lines.push(format!("{{tempo: {b}}}"));
    }
    if tr != 0 {
        lines.push(format!("{{comment: transposed {}{} semitones}}", if tr > 0 { "+" } else { "" }, tr));
    }
    let b = bars(a, tr, simplify);
    for row in b.chunks(4) {
        lines.push(row.iter().map(|bar| format!("| {}", bar.iter().map(|c| format!("[{c}]")).collect::<Vec<_>>().join(" "))).collect::<Vec<_>>().join(" ") + " |");
    }
    lines.join("\n") + "\n"
}

/// "Am - F - C - G": the most repeated loop, else the first 8 chords.
pub fn progression_text(a: &Analysis, tr: i32, simplify: bool) -> String {
    let key = song_key(a);
    let key = key.as_ref().map(|(p, m)| (*p, m.as_str()));
    let Some(c) = &a.chords else { return String::new() };
    let names: Vec<String> = match c.progressions.first() {
        Some(p) => p.chords.split('-').map(|s| s.to_string()).collect(),
        None => c.segments.iter().filter(|s| s.chord != "N").take(8).map(|s| s.chord.clone()).collect(),
    };
    names.iter().map(|n| transform(n, tr, simplify, key)).collect::<Vec<_>>().join(" - ")
}

fn clean(s: &str) -> String {
    s.replace('\t', " ").split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Song<TAB>BPM<TAB>Key<TAB>Note, exactly like the tracker sheet.
pub fn tracker_row(a: &Analysis, file_name: &str) -> String {
    let bpm = a.tempo.as_ref().and_then(|t| t.display.bpm.clone()).unwrap_or_default();
    let key = a.key.as_ref().map(|k| k.display.key.clone()).unwrap_or_default();
    let notes: Vec<String> = [a.key.as_ref().and_then(|k| k.display.note.clone()), a.tempo.as_ref().and_then(|t| t.display.note.clone())]
        .into_iter()
        .flatten()
        .map(|n| clean(&n))
        .filter(|n| !n.is_empty())
        .collect();
    [clean(&title_of(a, file_name)), clean(&bpm), clean(&key), notes.join(". ")].join("\t")
}
