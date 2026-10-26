// Chord exports in the browser: MIDI (chords on the detected beat grid with a tempo map), ChordPro, plain text.
// Byte-for-byte the same as the service's /exports/{midi|chordpro|txt} (service/songspec_analyzer/export/chords.py); the
// client tests compare both on the same fixture (client/test/exports_fixture.json, written by the service's test suite).
import { FLAT_NAMES as FLAT, parseChord } from "./formatters.js";

const PPQ = 480;
const QUALITIES = {
  "": [0, 4, 7], m: [0, 3, 7], "7": [0, 4, 7, 10], maj7: [0, 4, 7, 11], m7: [0, 3, 7, 10], dim: [0, 3, 6], aug: [0, 4, 8],
  sus2: [0, 2, 7], sus4: [0, 5, 7], "6": [0, 4, 7, 9], m6: [0, 3, 7, 9], "9": [0, 4, 7, 10, 2], add9: [0, 4, 7, 2],
  m7b5: [0, 3, 6, 10], dim7: [0, 3, 6, 9],
};
const TRIAD = { "": "", m: "m", "7": "", maj7: "", m7: "m", dim: "dim", aug: "aug", sus2: "", sus4: "", "6": "", m6: "m",
  "9": "", add9: "", m7b5: "dim", dim7: "dim" };
const mod12 = (x) => ((x % 12) + 12) % 12;

/** Transpose and/or simplify one chord name (flats kept): "Ebmaj7/G" +2 -> "Fmaj7/A"; simplify -> "F". */
export function transform(name, transpose = 0, simplify = false) {
  if (name === "N" || name === "X") return name;
  const c = parseChord(name);
  let q = c.quality, bass = c.bass;
  if (simplify) { q = TRIAD[q] ?? q; bass = null; }
  let out = FLAT[mod12(c.root + transpose)] + q;
  if (bass != null) out += "/" + FLAT[mod12(bass + transpose)];
  return out;
}

export function chordNotes(name) {
  if (name === "N" || name === "X") return [];
  const c = parseChord(name);
  const notes = [...new Set(QUALITIES[c.quality].map((i) => 48 + mod12(c.root + i) + (mod12(c.root + i) < 5 ? 12 : 0)))]
    .sort((a, b) => a - b);
  return [36 + (c.bass != null ? c.bass : c.root), ...notes];
}

const vlq = (n) => { const o = [n & 0x7f]; n >>>= 7; while (n) { o.unshift((n & 0x7f) | 0x80); n >>>= 7; } return o; };
const u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
const u16 = (n) => [(n >>> 8) & 255, n & 255];
const text = (s) => [...new TextEncoder().encode(s)];

function track(events) {
  // stable sort by tick, like Python's sorted(key=tick)
  const ev = events.map((e, i) => [e[0], i, e[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let data = [], last = 0;
  for (const [t, , bytes] of ev) { data.push(...vlq(t - last), ...bytes); last = t; }
  data.push(...vlq(0), 0xff, 0x2f, 0x00);
  return [0x4d, 0x54, 0x72, 0x6b, ...u32(data.length), ...data];
}

const pyRound = (x) => {                     // Python's round(): halves to even
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 ? 2 * Math.round(x / 2) : r;
};

/** Type-1 MIDI as a Uint8Array. analysis = GET /v1/analyses/{id} (needs tempo.beats and chords.segments). */
export function midi(analysis, { transpose = 0, simplify = false } = {}) {
  const beats = analysis.tempo?.beats || [];
  const segs = analysis.chords?.segments || [];
  if (beats.length < 2 || !segs.length) throw new Error("needs beats and chords");
  const grid = (beats[0] > 0.02 ? [0.0] : []).concat(beats);
  const tick = (t) => {
    let lo = 0, hi = grid.length;                // bisect_right
    while (lo < hi) { const m = (lo + hi) >> 1; if (t < grid[m]) hi = m; else lo = m + 1; }
    const i = Math.min(Math.max(lo - 1, 0), grid.length - 2);
    return Math.max(0, pyRound((i + (t - grid[i]) / (grid[i + 1] - grid[i])) * PPQ));
  };
  const t0 = [[0, [0xff, 0x03, ...vlq(9), ...text("Tempo map")]]];
  for (let k = 0; k < grid.length - 1; k++) {
    const us = Math.min(pyRound((grid[k + 1] - grid[k]) * 1e6), 0xffffff);
    t0.push([k * PPQ, [0xff, 0x51, 0x03, (us >> 16) & 255, (us >> 8) & 255, us & 255]]);
  }
  const t1 = [[0, [0xff, 0x03, ...vlq(6), ...text("Chords")]]];
  for (const s of segs) {
    const name = transform(s.chord, transpose, simplify);
    const a = tick(s.start), z = Math.max(tick(s.end) - 1, a + 1);
    for (const n of chordNotes(name)) { t1.push([a, [0x90, n, 80]]); t1.push([z, [0x80, n, 0]]); }
    if (name !== "N") { const b = text(name); t1.push([a, [0xff, 0x06, ...vlq(b.length), ...b]]); }
  }
  const key = analysis.key?.display?.key;
  const t2 = [[0, [0xff, 0x03, ...vlq(4), ...text("Info")]]];
  if (key) { const b = text(`Key: ${key}`); t2.push([0, [0xff, 0x01, ...vlq(b.length), ...b]]); }
  return new Uint8Array([0x4d, 0x54, 0x68, 0x64, ...u32(6), ...u16(1), ...u16(3), ...u16(PPQ), ...track(t0), ...track(t1), ...track(t2)]);
}

function bars(analysis, transpose, simplify) {
  const segs = analysis.chords?.segments || [];
  const downs = analysis.tempo?.downbeats || [];
  if (!downs.length) return segs.map((s) => [transform(s.chord, transpose, simplify)]);
  return downs.map((d, i) => {
    const e = i + 1 < downs.length ? downs[i + 1] : Infinity;
    const names = segs.filter((s) => s.end > d + 1e-3 && s.start < e - 1e-3).map((s) => transform(s.chord, transpose, simplify));
    return names.length ? names : ["N"];
  });
}

function header(a) {
  const t = a.track || {};
  const title = t.title || (t.file_name || "Untitled").replace(/\.[^.]+$/, "");
  return { title, artist: t.artist, key: a.key?.display?.key, bpm: a.tempo?.display?.bpm };
}

export function chordpro(analysis, { transpose = 0, simplify = false } = {}) {
  const h = header(analysis);
  const lines = [`{title: ${h.title}}`];
  if (h.artist) lines.push(`{artist: ${h.artist}}`);
  if (h.key) lines.push(`{key: ${h.key.split(" / ")[0].replace(/\*/g, "")}}`);
  if (h.bpm) lines.push(`{tempo: ${h.bpm}}`);
  if (transpose) lines.push(`{comment: transposed ${transpose > 0 ? "+" : ""}${transpose} semitones}`);
  const b = bars(analysis, transpose, simplify);
  for (let i = 0; i < b.length; i += 4)
    lines.push(b.slice(i, i + 4).map((bar) => "| " + bar.map((c) => `[${c}]`).join(" ")).join(" ") + " |");
  return lines.join("\n") + "\n";
}

export function txt(analysis, { transpose = 0, simplify = false } = {}) {
  const h = header(analysis);
  const head = `${h.title}` + (h.artist ? ` - ${h.artist}` : "") + (h.key && h.bpm ? `  (${h.key}, ${h.bpm} BPM)` : "");
  const b = bars(analysis, transpose, simplify);
  const rows = [];
  for (let i = 0; i < b.length; i += 4) rows.push(b.slice(i, i + 4).map((bar) => bar.join(" ")).join(" | "));
  const progs = analysis.chords?.progressions || [];
  const tail = progs.length ? ["", "Progressions: " + progs.map((p) => p.display).join(", ")] : [];
  return [head, "", ...rows, ...tail].join("\n") + "\n";
}

/** A copy-to-clipboard progression line: "Am - F - C - G" (the most repeated loop, else the first 8 chords). */
export function progressionText(analysis, { transpose = 0, simplify = false } = {}) {
  const p = analysis.chords?.progressions?.[0];
  const names = p ? p.chords.split("-") : (analysis.chords?.segments || []).filter((s) => s.chord !== "N").slice(0, 8).map((s) => s.chord);
  return names.map((n) => transform(n, transpose, simplify)).join(" - ");
}
