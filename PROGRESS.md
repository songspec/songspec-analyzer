# PROGRESS

Plan: `PLAN.md`. Integration guide for SongSpec's main dev: `INTEGRATION.md`.

| # | milestone | status |
|---|---|---|
| 1 | Scaffold: service, queue, store, upload + limits, cache, SSE, contract, client, demo, INTEGRATION draft | **done** (2026-10-25), see below |
| 2 | Tempo: BPM Fixer port, Beat This!, house BPM rule | **done** (2026-10-25), see below; >95 % target **not met** (86 %) |
| 3 | Detune: Centsible pybind11 binding + cross-checks | **done** (2026-10-26), see below |
| 4 | Key v1 + eval harness + baselines | **done** (2026-10-26) |
| 5 | Chords | **done**: BTC + templates (weight 0.25), exports; real-music accuracy only by proxy (no chord labels) |
| 6 | Key v2 (fusion, modes, sections) | **done**: with S-KEY the fusion passes the gate (0.601 vs S-KEY 0.596, Centsible 0.564) and ships |
| 7 | Link input + Hooktheory | links -> metadata -> earlier uploads done; previews blocked by API terms; Hooktheory waits for credentials |
| 8 | Polish, Docker, final INTEGRATION.md | |
| 9 | Optional: Fix BPM, mashup helper | mashup helper done; Fix BPM not started |

## Milestone 1 (2026-10-25)

Built:
- `service/`: FastAPI app (`/v1/analyses` upload with a 100 MB / 15 min check, SSE `/events`, results, `json`/`csv`/`tracker-row` exports, feedback, version/health/schema), in-process worker pool (RQ adapter for scale-out, **not exercised yet**), SQLite store, exact-file cache plus Chromaprint fingerprint cache (when `fpcalc` is installed: **not installed on this PC**, so only the exact-file path is tested), audio deleted as soon as each job ends plus a 24 h sweeper, restart recovery, per-client rate limit, optional API key.
- Staged pipeline: each stage publishes its section; stages not built yet report `unavailable`. Only `decode` runs today.
- House-style display module (key spelling, modal asterisk + note, multi-section keys, Camelot / Open Key, Roman numerals, house BPM rule incl. live ranges and compound meters, detune strings, tracker row).
- `contract/analysis.schema.json` generated from the Pydantic models, with an example; a test fails if it goes stale.
- `client/`: headless ES-module client (upload with progress, SSE-over-fetch with a polling fallback, queue, links, exports, feedback) + `.d.ts` + formatters mirroring the server's.
- `demo/index.html`: reference harness (not the SongSpec UI).
- `INTEGRATION.md` draft; `docker/Dockerfile` + `compose.yaml` (**not built**: Docker isn't installed on this PC).

Tests:
- `pytest` (service): **82 passed** (69 house-style cases, 11 API tests: upload → done, audio deleted, cache hit, garbage/short files, size/duration limits, rate limit, API key, SSE, feedback/exports, unknown id, sweeper; 2 contract tests).
- Client formatters in a real browser (`/client/test/`): **77/77 passed** on the same shared cases.
- Client end to end in the browser: synthetic WAV → upload → SSE updates → done; a second upload of the same audio → `cached: true`.

Environment facts found during recon:
- Beat This! `final0.ckpt` is on this PC inside the installed BPM Fixer (`C:\Program Files\BPM Fixer\_internal\models`), and its SHA-256 matches the published `8c328b45…8eb8331`.
- `C:\cb` has a built `centsible_cli.exe` (the plugin's DSP), which serves as the parity reference for milestone 3.
- Labels: `C:\dev\detunetest\testdata` has the 369-song catalog, `key_labels.csv` (165 songs) and the full tracker export (57,857 rows). The tracker spells some keys with sharps (C# Minor, F# Minor, G# Minor), so evaluation compares keys by pitch class, not by spelling.
- GPU: RTX 4070, but the installed torch is a CPU build.

Known gaps: Docker image and RQ path untested; `fpcalc` missing locally (fingerprint cache untested); link input returns 501 until milestone 7.

## Milestone 2 (2026-10-25): tempo

Built:
- `tempo/_bpmfix_port.py`: BPM Fixer's analysis functions **copied verbatim** by `tools/port_bpmfix.py` (34 functions, 7 constants; a test asserts every source is identical to `bpmfix.py`). `tempo/engine.py` runs them in `process()`'s order (Beat This! → signature → `prepare_anchors` → level guard → `refine_beats` → `fill_weak_runs`), then adds a precise line-fit BPM, signature confidence, downbeats on the cleaned grid, **beat-switch detection** (on the raw tracker beats, octave-folded; the switch must be a jump, not a ramp, and the bar length must change with it; then BPM Fixer's pipeline runs per section) and per-section **steadiness** measured on the dominant local tempo (level hops are ignored).
- House BPM display matched to the user's tracker data (`153 / 104`, `~98.1`, `~122 - 127`, `120*`; compound meters show the eighth-note tempo `180*`, which is what the tracker's 6/8 rows contain). The 80/160 rule is decided on the displayed value (159.98 shows as 80).
- Beat This! `final0.ckpt` vendored (`tools/fetch_models.py`, SHA-256 manifest, no runtime download).
- Python 3.12 venv on **BPM Fixer's exact pins** (numpy 2.5.4, scipy 1.18.1, librosa 1.0.0, beat-this 1.1.0) + torch 2.14.1 CUDA 13.0 (RTX 4070). The old 3.11 venv is kept as `.venv311` (ignored); the Docker image moved to Python 3.12.
- No-beat audio becomes a `skipped` stage with a readable warning, not an error.

Results (`eval/REPORT.md` §1):
- Catalog, 164 songs with a tracker BPM: **86.0 % within 1 % of the main BPM**, 90.2 % within 1 % of any listed BPM, Acc2 92.7 %. Same audio: Centsible 84.5 %, raw Beat This! 46.3 %, librosa 33.5 %.
- Beat switches: 0 of 127 single-BPM songs split falsely; 19 of 37 multi-BPM songs flagged.
- Steadiness ("inconsistent" notes) is weak: 4 of 18 found, 18 of 146 flagged without a note; thresholds kept conservative.
- Parity with the installed BPM Fixer exe: **6 / 6 songs give the identical beat grid** (same count and signature, 0.0 ms on 5, 1.0 ms rounding on 1).
- Tests: 105 passed (service), JS formatters 82/82 in the browser.

Not met / open:
- PLAN target "> 95 % after the house rule": 86 %. Biggest error class: 6/8 songs read as 4/4 or 3/4 (5 of 16 misses), then 3:2 / 4:3 level errors (4). A meter classifier is planned with key v2.
- GiantSteps Tempo not run yet (needs the dataset download).
- The PC crashed during the first exe parity run (CPU-heavy: the exe time-stretches while Beat This! runs on the CPU). The parity script now runs at below-normal priority with 4 threads.

## Milestone 3 (2026-10-26): detune

Built:
- `service/native/centsible/`: a verbatim copy of Centsible's `core/` plus `binding.cpp` (pybind11): `analyze_track`, `analyze_tuning`
  (the plugin's reading only), `key_probabilities` (the full key decision: 24 key / 7 mode probabilities, bass and scale chroma),
  `detune_curve` (the windowed curve Beatswitch draws). `tools/build_native.py` builds it (MSVC here, gcc in Docker).
- Detune stage: Centsible's reading, CQT-120 and librosa as recorded cross-checks, CQT-120 as the fallback when Centsible has no
  reading. Quick-key stage: Centsible's detector on the fused tuning (seconds after upload; replaced by the fused key later), which
  also fills the detune sentence "Plays in Bb Minor (+38.0 ¢ above Bb) ...". Drift stage: tuning per 10 s window, flagged > 5 cents.

Results (`eval/REPORT.md` section 2):
- Parity with `centsible_cli.exe`: 39 lossless files, max 0.005 cents (target 0.5).
- Exactly shifted real music: service MAE 0.27 cents (resample) / 0.64 (Rubber Band), 100 % / 99.1 % within 2 cents (target: MAE < 2).
- Tests: 114 passed.

Machine: the PC crashed three times under sustained full-CPU load (Kernel-Power 41; one bugcheck 0x13A KERNEL_MODE_HEAP_CORRUPTION; the
first on 2026-10-24, before this project). Since then every evaluation runs at below-normal priority on 4 threads and the Centsible
CLI with --jobs 1-2 (its default is one file per core). No crash since.

Waiting on the user:
- S-KEY (Deezer) is cloned to `third_party/skey` and reviewed, but running downloaded code was blocked by the session's safety check:
  it needs the user's OK.

## Milestones 4 - 7 (2026-10-26)

- **Key v1** (`eval/key_eval.py`): 165 tracker-labelled songs, features cached. Centsible's detector 0.564 MIREX / 45.5 % exact is the
  best single estimator; the best profile is Temperley 2005 on Centsible's chroma (0.551). Found and fixed: the first tuned chroma was
  dominated by sub-bass and loud sections (every profile below the naive baseline); log aggregation lifted them by ~0.1.
- **Key v2** (`eval/key_fusion.py`, nested CV by artist group): the fusion scores 0.549, below Centsible alone; the gate holds, no model
  is installed, the service reports Centsible's key. Modes: chroma templates add more false modes than they find; the service names a
  mode only when Centsible's own mode probability is >= 0.5 (75.0 % vs 73.4 % for never). Section keys: 3 % false splits on one-key
  songs, 27 % of multi-key songs found. Details: `eval/REPORT.md` section 3.
- **Chords v1**: our own beat-synchronous template + Viterbi engine (full vocabulary, inversions from the bass, roman numerals,
  summary, repeating progressions) and exports (MIDI with the song's tempo map, ChordPro, TXT) on the server and byte-identical in the
  client. Tested on rendered progressions (>= 95 % root and major/minor per beat, detuned too); not measured on real music: no chord
  labels exist for the catalog (Isophonics / Billboard songs are not in it).
- **Links**: Spotify / Apple Music links resolve to metadata and return the analysis of an earlier upload of the same song (ISRC or
  artist + title + duration; uploads now store their tags). Otherwise `422 upload_needed`. Previews are not analysed: Apple's terms
  say previews are streamed only, Deezer's API is non-commercial (`LICENSES.md`).
- **Mashup helper**: `GET /v1/match?a=&b=` + `client.match()`.
- Tests: 151 passed (service); client 93 / 93 in the browser.

Waiting on the user (no work is blocked by these, but they decide the next accuracy gains):
1. Approval to run downloaded third-party code: S-KEY (key) and BTC (chords). Both are MIT-licensed; S-KEY is cloned and reviewed.
2. Preview analysis vs Apple's / Deezer's terms (SongSpec's legal call).
3. Hooktheory credentials; a Spotify developer app (optional).
4. Labelled songs Centsible never saw, or chord annotations, for fair key / chord numbers.

## S-KEY and BTC (2026-10-26, approved by the user)

- S-KEY (Deezer) runs from `third_party/skey` (pinned commit, weights by SHA-256, loaded with `weights_only=True` plus an allow-list of
  the numpy types its checkpoint stores). Alone 0.596 MIREX on the 165 songs; the nested-CV fusion (Centsible + S-KEY + Albrecht-
  Shanahan x2) 0.601 / 46.1 % exact passes the gate and is installed. Without S-KEY a server falls back to Centsible's key.
- BTC runs from `third_party/btc` (pinned, SHA-256, `weights_only=True`; a one-line `np.float` alias for its 2019 code). Its
  probabilities are blended with the template evidence (weight 0.25): chord-derived key 0.426 -> 0.480 on the proxy.
- `tools/fetch_third_party.py` fetches both at the pinned commits (the Dockerfile runs it). Engine 0.3.0.
- Progressions now use every chord in a bar (a bar holding Gb Ab Bbm used to read as just "Gb").
- Known weak spot: relative major / minor on IV-V-vi / VI-VII-i loops (e.g. *Power 4* reads Db Major, tracker Bb minor).

## "May borrow from" and key corrections (2026-10-26, engine 0.4.0)

- **May borrow from**: `key.borrows` + the key note (`May borrow from C Mixolydian, C Minor`; after a mode `D Dorian - May borrow
  from D Mixolydian`). Outside chords are credited to the closest scale on the key's tonic: every church mode, parallel Major/Minor,
  Harmonic / Melodic Minor, Phrygian Dominant, Harmonic Major, Lydian Dominant, Hungarian Minor, and the relative minor's Harmonic
  Minor (the tracker's "Bb Major ... borrows from G Harmonic Minor"). Against the tracker's notes: 43 % hits, 48 % precision
  (`eval/REPORT.md` 3.7), hence "may". In key sections, only chords in the main key count.
- **Key corrections**: `client.correctKey(id, "A# Phrygian")` / `POST .../feedback {field: "key"}` (validated, `GET /v1/keys` for a
  picker). Shown for every later view of the same song (file hash, fingerprint, ISRC, cached copies, links), with `key.corrected`
  keeping the detected key; roman numerals, borrows and the detune sentence follow the correction. The test page has a "key is
  wrong?" picker (tonic + 13 modes).
- **Learning from corrections**: each analysis stores the key fusion's inputs (`key_evidence` table, numbers only).
  `eval/key_retrain.py --db <analyzer.sqlite3> [--install]` retrains with corrections (installed configuration held fixed) and installs
  only with >= 20 corrected songs that it gets better on held-out, no loss on the tracker songs, and a win over the best single
  estimator. No corrections collected yet; Qo's `data/analyzer.sqlite3` is where
  they will come from.
- Mode table gained Melodic Minor, Harmonic Major, Lydian Dominant, Hungarian Minor (parsing, display, borrows, corrections).
