// Runs the shared house-style cases (service/tests/display_cases.json) against formatters.js.
// Browser: open client/test/index.html through the service (SONGSPEC_SERVE_DEMO=1). Node 18+: node client/test/formatters.test.mjs
import * as F from "../formatters.js";

export async function runAll(loadJson) {
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
  for (const c of C.bpm_live) {
    const secs = c.sections.map((b) => ({ bpm: b }));
    const d = F.bpmDisplay(c.raw, c.sig, secs, secs.length > 0);
    check(`bpm_live ${c.display}`, [d.display, d.note], [c.display, c.note]);
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
  return results;
}

if (typeof process !== "undefined" && process.argv && import.meta.url.endsWith(process.argv[1]?.replace(/\\/g, "/").split("/").pop())) {
  const fs = await import("node:fs");
  const url = new URL("../../service/tests/display_cases.json", import.meta.url);
  const res = await runAll(async () => JSON.parse(fs.readFileSync(url, "utf-8")));
  const bad = res.filter((r) => !r.ok);
  for (const b of bad) console.log("FAIL", b.name, "got", b.got, "want", b.want);
  console.log(`${res.length - bad.length}/${res.length} passed`);
  process.exit(bad.length ? 1 : 0);
}
