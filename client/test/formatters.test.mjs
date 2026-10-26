// Runs the shared house-style cases (service/tests/display_cases.json) against formatters.js.
// Browser: open client/test/index.html through the service (SONGSPEC_SERVE_DEMO=1). Node 18+: node client/test/formatters.test.mjs
import * as F from "../formatters.js";
import * as X from "../exports.js";

export async function runAll(loadJson, loadFixture = null) {
  const C = await loadJson();
  const results = [];
  const check = (name, got, want) => results.push({ name, ok: JSON.stringify(got) === JSON.stringify(want), got, want });
  const pk = (k) => F.parseKey(k);

  for (const c of C.house_key) { const k = pk(c.key); check(`house_key ${c.key}`, F.houseKey(k.tonic, k.mode), c.expect); }
  for (const c of C.key_note) {
    const k = pk(c.key);
    check(`key_note ${c.key}/${c.source}`, F.keyDisplay(k.tonic, k.mode, { source: c.source, sourceText: c.key }).note, c.expect);
  }
  for (const c of C.camelot) {
    const k = pk(c.key);
    check(`camelot ${c.key}`, [F.camelot(k.tonic, k.mode), F.openKey(k.tonic, k.mode)], [c.camelot, c.open_key]);
  }
  for (const c of C.sections) {
    const secs = c.sections.map((s) => ({ start: s.start, end: s.end, ...pk(s.key) }));
    const d = F.sectionsDisplay(secs, c.labels);
    check(`sections ${c.expect_key}`, [d.key, d.note], [c.expect_key, c.expect_note]);
  }
  for (const c of C.house_bpm) {
    const d = F.bpmDisplay(c.raw, c.sig);
    check(`house_bpm ${c.raw} ${c.sig}`, [d.bpm, d.display], [c.bpm, c.display]);
  }
  for (const c of C.bpm_sections) {
    const d = F.bpmDisplay(c.raw, c.sig, c.sections);
    const rng = d.range && d.range.map((x) => Math.round(x * 100) / 100);
    check(`bpm_sections ${c.display}`, [d.display, d.note, rng], [c.display, c.note, c.range]);
  }
  for (const c of C.detune) {
    const d = F.detuneDisplay(c.cents, { totalCents: c.total_cents ?? null, tonic: c.tonic ? F.parsePitch(c.tonic) : null, mode: c.mode ?? null });
    for (const k of ["display", "fix", "key_text", "total_text"]) if (k in c) check(`detune ${c.cents} ${k}`, d[k], c[k]);
  }
  for (const c of C.roman) {
    const ch = F.parseChord(c.chord), k = pk(c.key);
    check(`roman ${c.chord} in ${c.key}`, F.roman(ch.root, ch.quality, k.tonic, k.mode), c.expect);
  }
  for (const c of C.tracker_row) check(`tracker_row ${c.song}`, F.trackerRow(c.song, c.bpm, c.key, c.notes), c.expect);

  // client-only transforms
  check("transpose Ebmaj7/G +2", F.transposeChord("Ebmaj7/G", 2), "Fmaj7/A");
  check("transpose Bbm -1", F.transposeChord("Bbm", -1), "Am");
  check("transpose N", F.transposeChord("N", 3), "N");
  check("simplify Ebmaj7/G", F.simplifyChord("Ebmaj7/G"), "Eb");
  check("simplify Dm7b5", F.simplifyChord("Dm7b5"), "Ddim");
  check("simplify Cm9", F.simplifyChord("Cm9"), "Cm");

  // exports.js must produce exactly what the service produces (fixture written by service/tests/test_chords.py)
  if (loadFixture) {
    const fx = await loadFixture();
    for (const c of fx.cases) {
      const opt = { transpose: c.transpose, simplify: c.simplify };
      const bytes = X.midi(fx.analysis, opt);
      const b64 = typeof btoa === "function" ? btoa(String.fromCharCode(...bytes)) : Buffer.from(bytes).toString("base64");
      check(`export midi t${c.transpose} s${c.simplify}`, b64, c.midi_b64);
      check(`export chordpro t${c.transpose} s${c.simplify}`, X.chordpro(fx.analysis, opt), c.chordpro);
      check(`export txt t${c.transpose} s${c.simplify}`, X.txt(fx.analysis, opt), c.txt);
    }
    check("export transform", X.transform("Ebmaj7/G", 2), "Fmaj7/A");
    check("progression text", X.progressionText(fx.analysis, { transpose: 2 }), "Bm - G - D - A");
  }
  return results;
}

if (typeof process !== "undefined" && process.argv && import.meta.url.endsWith(process.argv[1]?.replace(/\\/g, "/").split("/").pop())) {
  const fs = await import("node:fs");
  const url = new URL("../../service/tests/display_cases.json", import.meta.url);
  const fx = new URL("./exports_fixture.json", import.meta.url);
  const res = await runAll(async () => JSON.parse(fs.readFileSync(url, "utf-8")), async () => JSON.parse(fs.readFileSync(fx, "utf-8")));
  const bad = res.filter((r) => !r.ok);
  for (const b of bad) console.log("FAIL", b.name, "got", b.got, "want", b.want);
  console.log(`${res.length - bad.length}/${res.length} passed`);
  process.exit(bad.length ? 1 : 0);
}
