// House-style formatters (SongSpec's BPM & Key Tracker conventions). Pure functions, no DOM.
// The server already fills every `display` field with these strings; use these for client-side transforms
// (transpose, simplify, custom layouts). Mirrors service/songspec_analyzer/display/*.py and is tested against the
// same cases (service/tests/display_cases.json).

export const FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
export const SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const MINUS = "−";

// mode -> [parent, display name, intervals]
export const MODES = {
  major: ["Major", "Major", [0, 2, 4, 5, 7, 9, 11]],
  minor: ["Minor", "Minor", [0, 2, 3, 5, 7, 8, 10]],
  ionian: ["Major", "Ionian", [0, 2, 4, 5, 7, 9, 11]],
  dorian: ["Minor", "Dorian", [0, 2, 3, 5, 7, 9, 10]],
  phrygian: ["Minor", "Phrygian", [0, 1, 3, 5, 7, 8, 10]],
  lydian: ["Major", "Lydian", [0, 2, 4, 6, 7, 9, 11]],
  mixolydian: ["Major", "Mixolydian", [0, 2, 4, 5, 7, 9, 10]],
  aeolian: ["Minor", "Aeolian", [0, 2, 3, 5, 7, 8, 10]],
  locrian: ["Minor", "Locrian", [0, 1, 3, 5, 6, 8, 10]],
  harmonic_minor: ["Minor", "Harmonic Minor", [0, 2, 3, 5, 7, 8, 11]],
  phrygian_dominant: ["Minor", "Phrygian Dominant", [0, 1, 4, 5, 7, 8, 10]],
};
const PLAIN = new Set(["major", "minor", "ionian", "aeolian"]);

const mod12 = (x) => ((x % 12) + 12) % 12;

export function parsePitch(name) {
  const m = /^\s*([A-Ga-g])\s*([#♯b♭]*)\s*$/.exec(name);
  if (!m) throw new Error(`not a note name: ${name}`);
  let pc = PC[m[1].toUpperCase()];
  for (const ch of m[2]) pc += ch === "#" || ch === "♯" ? 1 : -1;
  return mod12(pc);
}

export function normMode(mode) {
  let m = String(mode).trim().toLowerCase().replace(/[ -]/g, "_");
  m = { maj: "major", min: "minor", dorion: "dorian" }[m] || m;
  if (!(m in MODES)) throw new Error(`unknown mode: ${mode}`);
  return m;
}

/** "A# Phrygian" / "Bb minor" / "F#m" -> {tonic, mode} */
export function parseKey(text) {
  const t = text.trim().replace(/\*+$/, "").trim();
  const m = /^([A-Ga-g][#♯b♭]?)\s*(.*)$/.exec(t);
  if (!m) throw new Error(`not a key: ${text}`);
  const rest = m[2].trim();
  return { tonic: parsePitch(m[1]), mode: rest === "m" ? "minor" : normMode(rest || "major") };
}

export const tonicName = (pc, spelling = "flat") => (spelling === "flat" ? FLAT_NAMES : SHARP_NAMES)[mod12(pc)];
export const isMinor = (mode) => MODES[normMode(mode)][0] === "Minor";
export const modeName = (tonic, mode, spelling = "flat") => `${tonicName(tonic, spelling)} ${MODES[normMode(mode)][1]}`;
const spellingOf = (text) => (/^\s*[A-Ga-g]\s*[#♯]/.test(text || "") ? "sharp" : "flat");

/** "Bb Minor", "Bb Minor*" (modal), "Db Major" */
export function houseKey(tonic, mode) {
  const m = normMode(mode);
  return `${FLAT_NAMES[mod12(tonic)]} ${MODES[m][0]}${PLAIN.has(m) ? "" : "*"}`;
}

export function camelot(tonic, mode) {
  const minor = isMinor(mode);
  const majorPc = minor ? mod12(tonic + 3) : mod12(tonic);
  const num = mod12(mod12(7 * majorPc) + 7) + 1;
  return `${num}${minor ? "A" : "B"}`;
}

export function openKey(tonic, mode) {
  const c = camelot(tonic, mode);
  const num = mod12(parseInt(c, 10) - 8) + 1;
  return `${num}${c.endsWith("A") ? "m" : "d"}`;
}

/** {key, note, camelot, open_key}; source "hooktheory" appends " - Key from Hook Theory"; sourceText keeps the source's spelling */
export function keyDisplay(tonic, mode, { source = null, sourceText = null } = {}) {
  const m = normMode(mode);
  let note = PLAIN.has(m) ? null : modeName(tonic, m, sourceText ? spellingOf(sourceText) : "flat");
  if (source === "hooktheory") note = note ? `${note} - Key from Hook Theory` : "Key from Hook Theory";
  return { key: houseKey(tonic, m), note, camelot: camelot(tonic, m), open_key: openKey(tonic, m) };
}

export function fmtTime(t) {
  const s = Math.max(0, Math.round(t));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** sections: [{start, end, tonic, mode}] -> {key: "C Minor* / C Major", note: "Intro in C Phrygian Dominant, ..."} */
export function sectionsDisplay(sections, labels = null) {
  const merged = [];
  sections.forEach((s, i) => {
    const lab = labels ? labels[i] : null;
    const k = [mod12(s.tonic), normMode(s.mode)];
    const last = merged[merged.length - 1];
    if (last && last.k[0] === k[0] && last.k[1] === k[1]) {
      last.end = s.end;
      if (lab && !last.labels.includes(lab)) last.labels.push(lab);
      return;
    }
    merged.push({ k, start: s.start, end: s.end, labels: lab ? [lab] : [] });
  });
  if (!merged.length) return { key: null, note: null };
  if (merged.length === 1) {
    const d = keyDisplay(...merged[0].k);
    return { key: d.key, note: d.note };
  }
  const names = [];
  for (const m of merged) {
    const n = houseKey(...m.k);
    if (!names.includes(n)) names.push(n);
  }
  const parts = merged.map((m) => {
    const where = m.labels.length ? m.labels.join(" & ") : `${fmtTime(m.start)}-${fmtTime(m.end)}`;
    return `${where} in ${modeName(...m.k)}`;
  });
  return { key: names.join(" / "), note: parts.join(", ") };
}

// ------------------------------------------------------------------------------------------------ BPM
export const COMPOUND = new Set(["6/8", "9/8", "12/8"]);
export const LIVE_NOTE = "BPM inconsistent - played live";
const r2 = (x) => Math.round(x * 100) / 100;

/** House rule: <80 doubled, >=160 halved, compound meters keep the felt value. -> {bpm, compound} */
export function houseBpm(raw, signature = "4/4") {
  if (!(raw > 0) || !Number.isFinite(raw)) return { bpm: null, compound: false };
  if (COMPOUND.has(signature)) return { bpm: r2(raw), compound: true };
  let b = raw;
  while (b < 80) b *= 2;
  while (b >= 160) b /= 2;
  return { bpm: r2(b), compound: false };
}

/** 114.02 -> "114", 82.5 -> "82.5" */
export function fmtBpm(b) {
  if (b == null) return null;
  const r = Math.round(b);
  return Math.abs(b - r) <= 0.05 + 1e-9 ? String(r) : b.toFixed(1);
}

/** -> {bpm, display, note, compound, range} */
export function bpmDisplay(raw, signature = "4/4", sections = null, live = false) {
  const { bpm, compound } = houseBpm(raw, signature);
  const out = { bpm, display: fmtBpm(bpm), note: null, compound, range: null };
  if (live && sections && sections.length >= 2) {
    let vals = sections.filter((s) => s.bpm).map((s) => houseBpm(s.bpm, signature).bpm);
    vals = vals.map((v) => (v < 0.75 * bpm ? v * 2 : v > 1.5 * bpm ? v / 2 : v));
    const lo = Math.min(...vals), hi = Math.max(...vals);
    if (Math.round(hi) - Math.round(lo) >= 2) {
      out.range = [r2(lo), r2(hi)];
      out.display = `~${Math.round(lo)} - ${Math.round(hi)}`;
      out.note = LIVE_NOTE;
    }
  }
  if (signature && signature !== "4/4") {
    const sig = `Played in ${signature} time signature`;
    out.note = out.note ? `${out.note}. ${sig}` : sig;
  }
  return out;
}

// ------------------------------------------------------------------------------------------------ detune
export function signed(c, decimals = 1) {
  const s = Math.abs(c).toFixed(decimals);
  if (parseFloat(s) === 0) return (0).toFixed(decimals);
  return (c > 0 ? "+" : MINUS) + s;
}

export function detuneDisplay(cents, { totalCents = null, tonic = null, mode = null } = {}) {
  const c = Math.round(cents * 10) / 10;
  const out = {
    display: `${signed(c)} ¢`,
    fix: Math.abs(c) >= 0.05 ? `pitch by ${signed(-c)} ¢ to fix` : "on A440, nothing to fix",
    a4_hz: r2(440 * 2 ** (c / 1200)),
    semitones: 0, total_text: null, key_text: null,
  };
  if (totalCents != null && Math.abs(totalCents) >= 50) {
    const semis = Math.round((totalCents - c) / 100);
    out.semitones = semis;
    out.total_text = `${semis > 0 ? "+" : MINUS}${Math.abs(semis)} ${Math.abs(semis) === 1 ? "semitone" : "semitones"}, ${signed(c)} ¢`;
  }
  if (tonic != null && mode != null && Math.abs(c) >= 0.05) {
    const k = houseKey(tonic, mode).replace(/\*$/, "");
    const tn = k.split(" ")[0];
    out.key_text = `Plays in ${k} (${signed(c)} ¢ ${c > 0 ? "above" : "below"} ${tn}). ` +
      `If you pitch ${c > 0 ? "down" : "up"} ${Math.abs(c).toFixed(1)} ¢, it's ${k} at A440.`;
  }
  return out;
}

// ------------------------------------------------------------------------------------------------ chords
export const CHORD_RE = /^([A-G][#b]?)(.*?)(?:\/([A-G][#b]?))?$/;
const ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII"];

export function parseChord(name) {
  if (name === "N" || name === "X") return null;
  const m = CHORD_RE.exec(name);
  if (!m) throw new Error(`not a chord: ${name}`);
  return { root: parsePitch(m[1]), quality: m[2], bass: m[3] ? parsePitch(m[3]) : null };
}

export function chordName(root, quality, bass = null) {
  let s = FLAT_NAMES[mod12(root)] + quality;
  if (bass != null && mod12(bass) !== mod12(root)) s += "/" + FLAT_NAMES[mod12(bass)];
  return s;
}

/** Roman numeral relative to the key's parent scale: i-VI-III-VII in a minor key, ♭VII for Bb in C Major. */
export function roman(root, quality, tonic, mode) {
  const minorKey = isMinor(mode);
  const scale = MODES[minorKey ? "minor" : "major"][2];
  const iv = mod12(root - tonic);
  let deg, acc;
  if (scale.includes(iv)) { deg = scale.indexOf(iv); acc = ""; }
  else if (minorKey && [4, 9, 11].includes(iv)) { deg = scale.indexOf(mod12(iv - 1)); acc = "♯"; }
  else { deg = scale.indexOf(mod12(iv + 1)); acc = "♭"; }
  const q = quality;
  const minorish = (q.startsWith("m") && !q.startsWith("maj")) || q.startsWith("dim") || q === "m7b5";
  const num = minorish ? ROMAN[deg].toLowerCase() : ROMAN[deg];
  let suffix = "";
  if (q.startsWith("dim7")) suffix = "°7";
  else if (q.startsWith("dim")) suffix = "°";
  else if (q === "m7b5") suffix = "ø7";
  else if (q.startsWith("aug")) suffix = "+";
  else if (q === "7" || q === "m7") suffix = "7";
  else if (q === "maj7") suffix = "maj7";
  else if (q === "9" || q === "m9") suffix = "9";
  else if (q === "6" || q === "m6") suffix = "6";
  else if (q.startsWith("sus")) suffix = q;
  else if (q === "add9") suffix = "add9";
  return acc + num + suffix;
}

/** "Ebmaj7/G" transposed by n semitones (flats kept). "N" stays "N". */
export function transposeChord(name, n) {
  const c = parseChord(name);
  if (!c) return name;
  return chordName(c.root + n, c.quality, c.bass == null ? null : c.bass + n);
}

const TRIAD = { "": "", m: "m", dim: "dim", aug: "aug", "7": "", maj7: "", m7: "m", "6": "", m6: "m", "9": "", add9: "",
  m9: "m", m7b5: "dim", dim7: "dim", sus2: "", sus4: "" };

/** Beginner toggle: reduce any chord to its triad (slash bass dropped). */
export function simplifyChord(name) {
  const c = parseChord(name);
  if (!c) return name;
  return chordName(c.root, TRIAD[c.quality] ?? (c.quality.startsWith("m") && !c.quality.startsWith("maj") ? "m" : ""));
}

// ------------------------------------------------------------------------------------------------ tracker row
const clean = (s) => String(s ?? "").replace(/\t/g, " ").split(/\s+/).filter(Boolean).join(" ");

/** Song<TAB>BPM<TAB>Key<TAB>Note, exactly like the Google Sheet. */
export function trackerRow(song, bpm, key, notes) {
  const note = notes.filter((n) => n && clean(n)).map(clean).join(". ");
  return [clean(song), clean(bpm), clean(key), note].join("\t");
}

/** The tracker row for a finished analysis (GET /v1/analyses/{id}). */
export function trackerRowFor(analysis) {
  const t = analysis.track || {}, tempo = analysis.tempo || {}, key = analysis.key || {};
  const song = t.title || (t.file_name || "").replace(/\.[^.]+$/, "");
  return trackerRow(song, tempo.display?.bpm ?? "", key.display?.key ?? "", [key.display?.note, tempo.display?.note]);
}
