# SongSpec Analyzer: plan

Status: v0.1 of the plan, 2026-10-25. Building proceeds as described here. The decisions marked **ASK** in section 12 wait for you; everything else carries on.

## 0. The one constraint that shapes everything

You don't own songspec.org. SongSpec's main dev mounts this into the site and **owns the UI**. So what ships is:

| deliverable | what it is | who uses it |
|---|---|---|
| **Analysis service** (`service/`) | a Python FastAPI app plus workers in one Docker image. Upload or link in, versioned JSON out, progress over SSE. | the site's backend or frontend calls it over HTTP |
| **JSON contract** (`contract/analysis.schema.json`, `contract/openapi.json`) | the schema in section 8, versioned (`schema_version`), with every field documented | the main dev, for types and validation |
| **Headless JS client** (`client/`, `@songspec/analyzer-client`) | plain ES modules plus a `.d.ts` file, zero dependencies, no build step, no DOM and no CSS. Covers upload, link submit, progress events, result typing, and exports (MIDI / ChordPro / CSV / tracker row) | the main dev's components, in any framework |
| **Display helpers** (also in the client) | pure functions: house key style, house BPM rule, Camelot / Open Key, Roman numerals, transpose. Also computed server-side, so a display string is never invented in two places | optional; the server's `display` fields are the source of truth |
| **Reference demo** (`demo/index.html`) | one unstyled page that exercises every API feature. A test harness and a usage example, **not** the product UI | me (testing), the main dev (reading the example) |
| **`INTEGRATION.md`** | how to run, configure, deploy, call and mount it, plus what each field means and how it may be shown | the main dev |

UI rule: I never decide layout, colours or components for the site. Where the brief describes UI (tiles, timeline lanes, chips), I make sure the **data** for it is in the contract, and `INTEGRATION.md` describes the intended presentation as a suggestion.

## 1. Architecture

```
                     songspec.org (main dev's code, any framework)
                     ┌──────────────────────────────────────────────┐
                     │  UI components ──uses──> @songspec/analyzer-client
                     └──────────────┬───────────────────────────────┘
                                    │ HTTPS  (multipart upload / JSON link,  SSE progress)
                                    ▼
┌────────────────────────── SongSpec Analyzer service (one Docker image) ─────────────────────────┐
│  FastAPI  /v1/...  ── rate limit, size/duration limits, CORS, optional API key                  │
│     │                                                                                          │
│     ├── cache lookup: chromaprint fingerprint + duration ─┐   ISRC index ─┐                    │
│     │                                                     ▼               ▼                    │
│     │                                    SQLite (default) / Postgres: jobs, results JSON,     │
│     │                                    fingerprints, ISRC map, feedback (derived data only) │
│     ▼                                                                                          │
│  job queue: in-process worker pool (default, zero infra)  |  Redis/Valkey + RQ (QUEUE=redis)   │
│     ▼                                                                                          │
│  analysis worker (staged pipeline, each stage publishes a partial result -> SSE)               │
│    1 decode (ffmpeg) ─ 2 detune (Centsible) ─ 3 quick key ─ 4 beats/tempo (Beat This! + BPM    │
│    Fixer) ─ 5 [stems: HPSS or Demucs] ─ 6 chords ─ 7 full key fusion + modes + sections ─      │
│    8 metadata cross-check (Hooktheory) ─ 9 finalize/display fields                             │
│     ▼                                                                                          │
│  temp storage: uploaded audio, deleted after analysis (TTL 24 h max, swept on a timer)         │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
        link resolvers (metadata + 30 s previews only): Spotify oEmbed / Web API, iTunes Lookup, Deezer
```

Defaults chosen so that the main dev can run it with one command (`docker compose up`) and no other infrastructure. Redis and Postgres are drop-in options for scale.

Progressive results: every stage writes its piece of the result, and `GET /v1/analyses/{id}` returns whatever exists so far with `stages: {detune: "done", tempo: "running", ...}`. SSE pushes the same thing as `stage` events. Order: detune and quick key arrive in seconds, then tempo, then chords, then fused key and sections.

## 2. Repository layout

```
SongSpec Analyzer/
  PLAN.md  PROGRESS.md  INTEGRATION.md  README.md  LICENSES.md
  contract/            analysis.schema.json, openapi.json (generated), examples/
  service/
    songspec_analyzer/
      api/             FastAPI app, routes, SSE, limits, auth hook
      jobs/            queue abstraction (inline | rq), job store, TTL sweeper
      pipeline/        orchestrator: stages, partial results, versions
      audio/           decode (ffmpeg), resample, fingerprint (chromaprint)
      detune/          centsible binding wrapper + fallback estimators + fusion
      tempo/           tempo.py: BPM Fixer port, house BPM rule, sections
      chords/          models, beat-sync Viterbi, vocabulary, roman numerals, progressions
      key/             estimators, features, fusion model, modes, sections HMM
      links/           spotify, apple, deezer resolvers; hooktheory client
      display/         house key style, BPM display, camelot / open key, tracker row
      export/          MIDI, ChordPro, CSV, JSON
    native/centsible/  pybind11 binding + a COPY of Centsible's core/ (the source folder is never edited)
    models/            vendored checkpoints with SHA-256 manifest (git-ignored, fetched by tools/fetch_models.py)
    tests/
  client/              analyzer-client.js, analyzer-client.d.ts, formatters.js, exports.js, README
  demo/                index.html (reference, unstyled)
  eval/                datasets/ (manifests only), runners, baselines, REPORT.md, ci_subset/
  tools/               fetch_models.py, make_detune_set.py, build_labels.py
  docker/              Dockerfile, compose.yaml
  Everything Claude Needs/   read-only reference (git-ignored)
```

## 3. Modules and choices

### 3.1 Tempo and beats: port of BPM Fixer (`tempo/`)
Port the analysis half of `app/bpmfix.py` **verbatim in logic**: `detect_beats` (Beat This!, `dbn=False`, GPU if present), `TIME_SIGNATURES`, `sig_spec`, `period_to_bpm` / `bpm_to_period`, `infer_signature`, `_log_mode`, `level_reference`, `choose_level`, `_pll`, `_start_index`, `clean_sequence`, `bars_reference`, `anchors_from_bars`, `prepare_anchors`, `pick_reference_period`, `find_bad_runs`, `enforce_level`, `onset_env`, `retrack_region`, `_tidy`, `refine_beats`, `fill_weak_runs`, `irregularity`, plus the "song's own tempo" block from `process()` (felt range 70–180, the 2nd/98th-percentile range). GUI, stretching and saving stay out, except behind the optional Fix BPM milestone.

- **Model:** `beat_this-final0.ckpt`, vendored. A copy is already on this PC inside the installed BPM Fixer, and its SHA-256 `8c328b45…8eb8331` matches the published value in `tools/fetch_vendor.py`. `tools/fetch_models.py` copies or downloads it into `service/models/` and refuses on a hash mismatch. Nothing downloads at request time.
- **New on top of the port** (the port itself stays bit-compatible with BPM Fixer on the same input):
  - signature confidence: the share of bars whose beat count equals the mode (`infer_signature` already computes it).
  - tempo sections: a change-point split of the smoothed curve (PELT on log-BPM, minimum section 8 bars, minimum shift 2 %), with a merge for drift. `feel = "live"` when irregularity > 2.5 % or sections differ by more than 2 %. Both thresholds are tuned on the eval sets.
  - **House BPM rule** (`display/bpm.py`): below 80 → ×2, 160 or above → ½, unless the meter is compound (6/8, 9/8, 12/8), where the raw felt value is kept and `compound: true` is set. Rounded to 2 decimals internally. Displayed as a whole number when within ±0.05 of one, otherwise with one decimal. Live drift is displayed as `~122 - 127` plus the note `BPM inconsistent - played live`. `alternates` lists ½× and 2×. Hooktheory BPM wins the display when it's available, and `bpm_source` says so.

### 3.2 Detune: Centsible (`detune/`)
**What Centsible does** (from `core/src/spectral_likelihood.h`, documented so the port can be checked):
- Mono input. STFT with a 2^14-sample Blackman-Harris window (~340 ms; 2^15 above 50 kHz, 2^16 above 150 kHz) and hop = window/4. Frames with RMS < 1e-4 count as silence.
- Peaks between 28 Hz and 6 kHz above 0.002 × the frame maximum that are local maxima over ±2 bins. Each peak gets a **soft weight**, the product of: sinusoidal prominence (log2 ratio to the bins ±8 away); a harmonic mask (time-median vs frequency-median over 9 frames, i.e. HPSS); phase-vocoder instantaneous-frequency stability between the frame pairs before and after (σ 0.8 ¢); amplitude steadiness; and amplitude relative to its octave band's maximum (√, so a loud 808 can't decide alone). The upper band (> 1.5 kHz) gets half weight.
- Each peak's IF → cents vs A440 mod 100 → linear-interpolated deposit into a **400-bin (0.25 ¢) histogram** per register band (<150 Hz, 150–1500 Hz, 1.5–6 kHz) per **~5 s chunk**.
- **Model:** the tonal histogram (bands 1+2) is fitted by maximum likelihood with a *partial-aware template*: partials h = 1…24 sit at 1200·log2(h·√((1+Bh²)/(1+B))) mod 100, with weights h^−α, Gaussian width σ (optionally a narrow plus broad mixture), and a uniform background fitted by EM. The search runs over σ ∈ {0.25…5}, α ∈ {0.6…4.5} and string stiffness B (adopted only if it beats B = 0 by 15 + 0.08·mass nats). There's a full-circle scan, a parabolic refine, then a golden-section polish on the analytic density. If the stretched-string tonal fit disagrees with a clean low-register fit, the low register wins.
- **Confidence:** a 40-draw block bootstrap over chunks → 95 % half-width `ciCents`, plus `confidence = clamp(1 − ci/2) · clamp(mass/600)`. Lock: CI ≤ 0.3 ¢ over 6+ chunks (release at 0.5 ¢).
- Extras: a second tuning (mixture EM, LR gain > 14 after the 1/40 effective-sample shrink, share ≥ 12 %, ≥ 5 ¢ apart); drift (first half vs second half, ≥ 1.5 ¢ and 3× the bootstrap scatter); song-change detection. `detune_timeline.h` also gives the **windowed detune curve** (2–4 s windows with a bootstrap CI), which is exactly the drift lane the brief asks for.
- Reported accuracy (REPORT.md): synthetic sweep max 0.038 ¢; real catalog 88.4 % of exact-shift cases within 0.5 ¢. Known failure: harmonic aliases 13–15 ¢ away on dense mixes, usually with a wide interval. Against librosa it's ~15× better.

**Port decision: bind, don't rewrite.** `core/` is pure C++20 with no JUCE, so I copy it into `service/native/centsible/core/` and expose `analyzeTrack`, `analyzeBuffer`, the detune timeline and Centsible's key estimate through **pybind11**. That *is* the plugin's DSP, so plugin and service agree by construction (0.0 ¢ difference, which beats the 0.5 ¢ requirement). The parity test still runs `C:\cb\...\centsible_cli.exe --csv` and the binding on the same files and asserts |Δ| ≤ 0.05 ¢ (float noise only). A pure-Python port is a **fallback only**, used if the binding can't be built for the deploy target: it would be checked against the binding to ≤ 0.5 ¢ on the synthetic set and the catalog. Wheels: built in the Docker image (Linux, gcc) and locally (MSVC).

**Cross-check estimators** (each one gives cents plus a confidence):
1. `chroma120`: CQT at 120 bins/octave on the HPSS harmonic part (or Demucs other+vocals+bass), folded to one semitone, with parabolic peak interpolation and an energy-weighted circular mean.
2. `librosa`: `piptrack` + `pitch_tuning` at 0.1 ¢ resolution, harmonic part only.
3. `f0_stems`: torchcrepe (or PESTO, license permitting) on the vocal and bass stems; voiced frames with confidence > 0.8, sustained notes (≥ 150 ms within ±15 ¢) weighted up; circular median.
4. Essentia TuningFrequency: **eval baseline only** (AGPL, section 10).

Fusion: a confidence-weighted **circular median**, where Centsible's weight comes from its CI. A cross-check only moves the answer when Centsible's interval is wide (> 2 ¢) or it reports `competing`. Every vote is kept in `detune.estimators`. When the total shift is ≥ 50 ¢, `semitones` and the "Plays in Bb Minor (+38 ¢ above Bb)…" wording come from `display/`. **The fused tuning is passed to every chroma, key and chord stage** (CQT `tuning=` and a reference-frequency shift for the CNNs, by resampling when a model has a fixed front end).

### 3.3 Chords (`chords/`)
- **Primary: BTC** (Bi-directional Transformer for Chord recognition, large vocabulary, 170 classes; repo `jayg996/BTC-ISMIR19`, license to verify).
- **Second opinion: Chordino / NNLS-Chroma** via the `vamp` Python host (GPL plugin, server-side only).
- **Third vote:** madmom CRF/CNN only if its non-commercial license is cleared (default: off in production, kept as an eval baseline).
- Candidates to benchmark (2024–2026, permissive licenses only): I'll search for the current state of the art and list what I find in `eval/REPORT.md`.
- Input: the tuning-corrected signal; drums removed (HPSS by default, Demucs in high-accuracy mode); the bass stem sets roots and inversions.
- **Beat-synchronous decoding:** per-frame posteriors from each model are mapped onto a shared vocabulary, ensembled with weights learned on the eval sets, pooled per beat, then Viterbi-decoded with a transition prior. Changes are cheap on downbeats, moderate on beats and expensive inside a beat. A one-beat chord survives only above an evidence threshold.
- Vocabulary: maj, min, 7, maj7, m7, dim, aug, sus2, sus4, 6, m6, 9, add9, m7b5, dim7, plus slash inversions from the bass stem. `simplify=triads` is a client-side transform that the contract supports (each segment carries `triad`).
- Roman numerals relative to the fused key and mode (borrowed chords marked, e.g. ♭VII in Mixolydian, ♭VI from the parallel minor). Summary per chord (count, seconds). Repeating progressions: a beat-level sequence → minimal-period detection on bar-aligned windows → `vi–IV–I–V ×8`.
- Exports: MIDI (chords on the beat grid plus a tempo map from the beat times), ChordPro/TXT, JSON, plain progression text. Generated server-side (`/exports/{fmt}`) **and** client-side (`client/exports.js`, the same algorithm) so the site can export with no extra round trip. Transpose ±N is a pure client function, which the server also accepts as `?transpose=`.

### 3.4 Key (`key/`): ensemble with evidence fusion
Preprocessing: tuning correction everywhere; drums removed (HPSS by default, Demucs `htdemucs` in high-accuracy mode or on GPU); intro/outro/silence trimmed by an RMS + spectral-flatness activity mask; the main body weighted by beat-synced energy.

Estimators, each giving a 24-way probability vector (plus mode scores where possible):
1. **Profiles** on tuned, beat-synced chroma: Krumhansl-Kessler, Temperley, Albrecht-Shanahan, EDMA/EDMM (Faraldo) and Bgate. Values are taken from the papers or Essentia's source tables (as data, cited), **not from memory**; Centsible's REPORT admits its Shaath/AS/EDMA numbers were typed from memory. Each runs on the drumless mix and on the harmonic stems; correlation is softmaxed with a learned temperature.
2. **Centsible's two-stage detector** (Temperley scale plus bass-tonic via harmonic cancellation) through the binding. It's tuned on your catalog and gets 45 % exact / 72 % scale on songs it never saw. Kept as a vote.
3. **CNNs:** S-KEY (Deezer 2025), Korzeniowski's key CNN (madmom: eval only unless licensed), Schreiber's key-cnn (license to verify; likely AGPL → eval only). Benchmarked; kept only if they add accuracy *and* are licensed for serving.
4. **Chord-based key:** diatonic-membership scoring of the chord sequence, weighted by duration, downbeat position, first/last chord and cadences (V→I, IV→I, ♭VII→I, iv→i).
5. **Bass tonic:** a duration-weighted pitch-class histogram from bass-stem f0 (torchcrepe) or the low-band chroma, plus what lands on downbeats and phrase ends.
6. **Melody:** basic-pitch (Apache-2.0) on the vocal stem: pitch-class distribution and phrase-final notes.
7. **Metadata prior:** Hooktheory key when a confident match exists (configurable strength, default a strong prior).

Fusion: multinomial logistic regression over the stacked log-probabilities plus the tiebreaker features below, all in a **tonic-relative (rotation-equivariant) parametrization**. The model scores "is candidate k the key" from features computed relative to k, so 165 labelled songs are enough and all 24 keys share parameters. Calibration: temperature scaling on a validation fold. Hold-out: a fixed test split hashed by artist (no artist in both train and test), never used for tuning. A small MLP is tried only if logistic regression saturates.

Dedicated tiebreaker features for the classic confusions:
- relative (C vs Am): bass-tonic share, final chord, cadence counts, tonic-triad share.
- fifth (C vs G/F): leading-tone (B vs B♭) energy, IV-vs-V chord weight.
- parallel (C vs Cm): third quality in tonic-chord frames.
- modal (G Mixolydian vs C Major): bass tonic, ♭VII→I cadences.

Output: the top 3 with probabilities; an `ambiguous` flag plus text when p1 < τ (τ is picked on validation so that "unambiguous" answers are ≥ 90 % correct).

**Modes:** once the tonic is fixed, score the tuned pitch-class distribution against Ionian, Dorian, Phrygian, Lydian, Mixolydian, Aeolian, Locrian, Harmonic Minor and Phrygian Dominant (templates with a learned margin). A mode is reported only when it beats plain major/minor by that margin. Your tracker's notes column ("F Phrygian…") gives mode labels for evaluation.

**Section keys:** windows aligned to downbeats (8 bars, hop 4), each giving a 24- (or mode-) way likelihood → an HMM with a key-change penalty plus a modulation-aware transition prior (half-step and whole-step lifts are cheap near the end) → Viterbi → sections. Key changes and tempo sections land on the same timeline.

**House style** (`display/key.py`): flats for black-key tonics (Db, Eb, Gb, Ab, Bb) and the full words "Major"/"Minor". Modal → the parent major/minor gets an asterisk ("Bb Minor\*") and the note keeps the source spelling ("A# Phrygian", with " - Key from Hook Theory" when that was the source). Multi-section → `"C Minor* / C Major"` plus a note listing the sections. Camelot and Open Key are attached. **Tracker row** = `Song\tBPM\tKey\tNote`, exactly in this style.

Spelling note for modes: "parent major/minor" means the major or minor that best shares the mode's colour. Phrygian, Dorian, Locrian and Aeolian → Minor of the *same tonic* ("A# Phrygian" → "Bb Minor\*"); Lydian and Mixolydian → Major of the same tonic; Harmonic Minor → Minor; Phrygian Dominant → Minor. This matches your tracker's own rows (e.g. "F Minor\* … F Phrygian"). Section 12 asks you to confirm.

### 3.5 Link input (`links/`)
- Spotify: oEmbed (title/artist, no auth) → optional Web API client-credentials lookup for ISRC (`external_ids.isrc`, coded defensively because the field may be missing) → without an ISRC, Deezer/iTunes search by title + artist + duration (±2 s).
- Apple Music: track id from the URL (`?i=` or the `/song/` path) → iTunes Lookup → metadata, ISRC where present, and `previewUrl`.
- Audio = the official 30 s previews only (iTunes `previewUrl`, Deezer `api.deezer.com/track/isrc:<ISRC>`). Previews are analyzed and deleted, never stored. Results get `source: "*-preview"`, the brief's warning text, and confidences scaled by learned preview penalties (measured in eval: preview-vs-full accuracy).
- An ISRC cache maps ISRC → the best available result. A later full upload of the same ISRC (matched by fingerprint) supersedes the preview result.

### 3.6 Metadata cross-check (`links/hooktheory.py`)
Adapter for the Hooktheory TheoryTab API (it needs credentials, see section 12). It matches by artist + title (normalized, fuzzy ratio ≥ 0.9) and records the key, BPM, URL and match score. In fusion it's a prior, never a silent override: disagreement with a high-confidence audio estimate sets `metadata.hooktheory.agrees = false` and adds a warning. Other databases (GetSongBPM) only through their official APIs and only as labelled weak hints, off by default. No scraping.

### 3.7 API (summary; full OpenAPI generated from code)
| method | path | purpose |
|---|---|---|
| POST | `/v1/analyses` | multipart `file`, or JSON `{"url": "..."}`; options `{high_accuracy, signature, hooktheory}` → `{id, status, cached}` |
| GET | `/v1/analyses/{id}` | the partial or complete result (schema below) plus `stages` |
| GET | `/v1/analyses/{id}/events` | SSE: `stage`, `progress`, `done`, `error` |
| GET | `/v1/analyses/{id}/exports/{fmt}` | `midi`, `chordpro`, `txt`, `json`, `csv`, `tracker-row` (`?transpose=N&simplify=1`) |
| POST | `/v1/analyses/{id}/feedback` | `{field: "key"|"bpm"|..., correct: "..."}` → stored with the fingerprint/ISRC |
| GET | `/v1/health`, `/v1/version` | ops |

Limits: 100 MB, 15 min, ffmpeg-decodable (BPM Fixer's `AUDIO_EXTS` as the accepted list), a per-IP / per-key token bucket, CORS allow-list from env. Auth: by default none (put it behind the site); optional `X-API-Key`; an `X-SongSpec-User` pass-through header for per-user limits and feedback attribution.

## 4. Data contract
The brief's section 8 schema, with additions, all fields optional while stages are running:
- top level: `schema_version`, `id`, `status`, `stages{}`, `track`, `detune`, `tempo`, `key`, `chords`, `warnings[]`, `versions`.
- `detune`: plus `ci_cents`, `a4_hz`, `total_cents`, `display` ("+23.4 ¢"), `fix` ("pitch by −23.4 ¢"), `drift[]` with `ci`, `drift_flag`, `secondary`.
- `tempo`: plus `bpm` (house rule, number), `bpm_source`, `compound`, `range` [lo, hi] when live, `downbeats`, `beats` (seconds, 3 decimals), `curve` downsampled to ≤ 1500 points like BPM Fixer's `--machine` output.
- `key`: plus `tonic`, `scale` (major/minor), `ambiguous`, `ambiguity_text`, `sections[]` with `display`, `modulations[]`.
- `chords.segments[]`: plus `triad`, `quality`, `on_downbeat`; `chords.vocabulary`.
- Every user-facing string the brief specifies is precomputed under `display` fields, so the site never has to re-implement the house style. Raw numbers stay alongside them.

The schema lives in `contract/analysis.schema.json` (JSON Schema 2020-12). Pydantic models in the service generate it, and a test asserts the generated and committed copies match. Breaking changes bump `schema_version` major and keep `/v1` stable.

## 5. Evaluation (`eval/`)
| set | content | how to get it | use |
|---|---|---|---|
| **Your catalog** (`C:\dev\detunetest\testdata`) | 369 songs with audio; 165 with tracker keys (95 verified), BPMs, mode notes | already on this PC; read in place, never copied into this repo | **primary key/BPM/mode ground truth**, the closest to real users |
| Tracker export (57,857 rows) | keys/BPMs, Hooktheory-sourced | on this PC | labels for any audio later matched (fingerprint/ISRC), and the source for preview-based eval (see below) |
| GiantSteps Key + GiantSteps MTG Key | EDM, 604 + 1486 tracks | annotations CC BY-SA; audio = Beatport 2-min previews via the official download scripts | key (EDM), tempo |
| GTZAN key annotations | 10 genres | annotations public; GTZAN audio is research-distributed | key, robustness |
| McGill Billboard, Isophonics | chords and keys | annotations public; **no audio** (commercial recordings) | only if matching audio exists (your catalog, previews) |
| RWC Pop | 100 songs, chords/keys/beats | **paid** (AIST) | **ASK** |
| Schreiber tempo/key sets | tempo/key annotations | public annotations; audio varies | tempo |
| Synthetic detune set | pitch-shift clean tracks by −49…+49 ¢ (exact Kaiser resampling plus Rubber Band) | generated | detune MAE, parity |
| Preview set | iTunes/Deezer 30 s previews for tracker rows | official API previews, analyzed and deleted, only derived features cached | the size jump: thousands of labelled keys/BPMs. Subject to API terms (section 10) |

Metrics: mir_eval key weighted score plus raw accuracy, a confusion breakdown (fifth/relative/parallel/other), and mode accuracy; mir_eval chords (root, majmin, sevenths, MIREX) plus segmentation; tempo Acc1/Acc2 plus accuracy after the house rule; beat F-measure; detune MAE on the synthetic set.

Baselines on the same audio: Essentia KeyExtractor (`edma`, `temperley`; via WSL/Docker, eval only), librosa/KS, key-cnn, S-KEY, madmom key, Centsible key alone, and published numbers on the same datasets (cited).

Robustness: 30 s preview vs full, MP3 128 kbps, ±30 ¢ detune, time-stretch ±8 %, live recordings.

Gates: **the fused key must beat the best single estimator on the held-out split** (the catalog test split and GiantSteps test), or the estimator that doesn't add is dropped. CI runs `eval/ci_subset` (≈20 short clips, synthetic + a few CC-licensed tracks) on every commit.

Targets (to revisit after baselines): key weighted score above the best single baseline on every dataset, with a +5 points weighted target on the catalog hold-out; detune MAE < 2 ¢ (Centsible's synthetic numbers suggest < 0.2 ¢); tempo after the house rule > 95 % on pop/EDM; beat F > 0.88 on GiantSteps tempo; chords majmin ≥ BTC alone + 2 points.

## 6. Licences (to verify before production; the table lives in `LICENSES.md` with links)
| component | licence (as I know it) | status |
|---|---|---|
| Centsible core | yours | ship |
| BPM Fixer engine (port) | yours | ship |
| Beat This! code | MIT | ship |
| Beat This! weights (`final0.ckpt`) | verify (repo states its own terms for checkpoints) | **verify** |
| Demucs (htdemucs) | MIT | ship |
| basic-pitch | Apache-2.0 | ship |
| librosa / mir_eval / numpy / scipy / FastAPI | ISC / MIT / BSD | ship |
| torchcrepe / CREPE | MIT | ship (verify torchcrepe weights) |
| PESTO | verify (possibly LGPL-3.0) | verify |
| BTC | verify | **verify** |
| Chordino / NNLS-Chroma | GPL-2.0 Vamp plugin | server-side OK (no network clause) |
| madmom code / models | BSD / **CC BY-NC-SA** | models eval-only unless licensed |
| Essentia | **AGPL-3.0** | eval-only (serving it would oblige source release) |
| key-cnn (Schreiber) | verify (likely AGPL-3.0) | eval-only unless permissive |
| S-KEY | verify | verify |
| Rubber Band | GPL-2.0-or-later (commercial licence available) | server-side only for Fix BPM; never bundled to clients |
| chromaprint / pyacoustid | LGPL-2.1 / MIT | ship (dynamic linking via fpcalc) |
| Redis | RSAL/SSPL (≥ 7.4), AGPL option in 8.x | default is no Redis; optional, or Valkey (BSD) |
| Spotify / Apple / Deezer / Hooktheory APIs | their terms | metadata and previews only, attribution, no audio storage |

Each "verify" gets checked against the current repo, model card and paper before that component goes into a production build. The result goes in `LICENSES.md`.

## 7. Compute and hosting
- Dev machine: RTX 4070 12 GB, Ryzen 5 5600X (12 threads), 32 GB, Windows 11, Python 3.11, torch 2.14 **CPU build** (no CUDA), VS 2022, CMake, ffmpeg.
- Production hosting is the main dev's call. **Default: CPU-only container.** Beat This! on CPU takes ~5–15 s per song; HPSS replaces Demucs by default; `high_accuracy=true` (or a GPU being present) enables htdemucs, ~1–2 min per song on CPU and ~10 s on GPU. Model sizes are chosen so that the CPU path finishes a typical 3–4 min song in < 60 s.
- For eval speed I'll install CUDA torch in this project's own `.venv` (free, local only, not your global Python).

## 8. Milestones with definitions of done
| # | milestone | done when |
|---|---|---|
| 1 | Scaffold: repo, venv, service skeleton (FastAPI, inline queue, SQLite store, upload with limits, fingerprint cache, TTL sweeper, SSE), contract v0, client skeleton, demo page, INTEGRATION.md draft | `pytest` green; demo page uploads a file and receives staged results (stubs) through SSE; schema test passes |
| 2 | Tempo: BPM Fixer port, Beat This! vendored with SHA check, house BPM rule, sections, feel | port matches `bpmfix.py` outputs on 20 songs (identical anchors); house-rule unit tests; tempo eval recorded (catalog BPMs, GiantSteps tempo) |
| 3 | Detune: Centsible pybind11 binding, cross-check estimators, fusion, drift curve, synthetic set | binding vs `centsible_cli.exe` within 0.05 ¢ on the synthetic set and 30 catalog songs; synthetic MAE < 2 ¢ (expected < 0.2); tuning feeds the downstream stages |
| 4 | Key v1: profiles (paper tables), Centsible key, CNNs (licence-checked), eval harness, baselines | `eval/REPORT.md` tables for every baseline; held-out split fixed and recorded |
| 5 | Chords: BTC + Chordino ensemble, beat-sync Viterbi, vocabulary, roman numerals, progressions, exports (server + client) | mir_eval chord metrics recorded and ensemble ≥ BTC alone; MIDI/ChordPro round-trip tests |
| 6 | Key v2: chord/bass/melody evidence, Demucs option, learned fusion, calibration, modes, section HMM | fused key beats the best single estimator on the hold-out; calibration error (ECE) < 0.05; mode accuracy reported |
| 7 | Links: Spotify/Apple resolution, previews, ISRC cache, Hooktheory adapter | resolver tests with recorded fixtures; preview-vs-full numbers in REPORT; preview warnings and confidence scaling |
| 8 | Polish: house style everywhere, tracker row, feedback store, client formatters/exports, demo player (click overlay, retune preview via WebAudio), Docker image, INTEGRATION.md final | `docker compose up` works from a clean checkout; client unit tests; the main dev can integrate from INTEGRATION.md alone |
| 9 | Optional: Fix BPM (full `process()` on the server, Rubber Band R3 `--centre-focus`), mashup helper endpoint (`/v1/match?a=&b=`) | output verified with BPM Fixer's own verification pass |

Each milestone: tests plus the eval subset, a commit, and an entry in `PROGRESS.md`. A regression in an eval score blocks "done".

## 9. Working rules (carried over from your Centsible notes)
- Honest numbers: report real results, never loosen a tolerance silently, flag what doesn't pass.
- Never tune on the hold-out split.
- Heavy jobs run one at a time, and I say what is running.
- The `Everything Claude Needs` folder is read-only and git-ignored; code is copied out, never edited in place.
- Your audio stays where it is (`C:\dev\detunetest\testdata`), referenced by path in a local, git-ignored config.

## 10. Risks
1. **Labelled data is small** (165 catalog keys). Mitigations: rotation-equivariant fusion (few parameters), GiantSteps, and the preview set (if the API terms allow analysis for evaluation).
2. **Licences may remove the strongest CNNs** (key-cnn, madmom) from production. The fusion is built so that any estimator can be absent at serve time (trained with estimator dropout).
3. **BTC's licence or weights** may not allow commercial serving. Fallbacks: Chordino plus our own beat-synced template decoder, or a permissively licensed model found in the 2024–2026 search.
4. **Hooktheory API access** may need approval. Built as an adapter that is off until credentials exist.
5. **Spotify Web API** dev-mode needs Premium on the app owner's account. oEmbed plus Deezer/iTunes search works without it.
6. **CPU-only hosting** makes Demucs slow; it stays opt-in.

## 11. What the main dev will need to decide (documented in INTEGRATION.md, not asked of you)
Hosting (container platform, GPU or not), auth model (behind the site's session, or an API key), CORS origins, the database (SQLite volume or Postgres URL), and how the UI presents everything.

## 12. Open questions for you (each has a default, so nothing stalls)
| # | question | default if you don't answer |
|---|---|---|
| 1 | **ASK (money):** buy RWC Pop for evaluation? | no, skip RWC |
| 2 | **ASK (licence):** does SongSpec make money (ads, paid tiers)? That decides madmom (NC) and the AGPL components | treat SongSpec as commercial: madmom, Essentia and any AGPL code are eval-only |
| 3 | **ASK (account/API terms):** Hooktheory TheoryTab API credentials, and is a strong Hooktheory prior OK? | adapter built but off until credentials exist; prior strength configurable, default "strong" |
| 4 | **ASK (account):** a Spotify developer app (needs Premium on the owner's account) for ISRC lookups? | no Spotify Web API; oEmbed plus Deezer/iTunes search |
| 5 | Can I use iTunes/Deezer 30 s previews of tracker songs to build a large eval set (analysis only, audio deleted)? | yes for eval, but only after I check both APIs' terms and record the result in LICENSES.md |
| 6 | Parent key for modes: Phrygian/Dorian/Locrian → "Minor\*" of the same tonic, Lydian/Mixolydian → "Major\*" (section 3.4)? | yes, as written |
| 7 | Can I install CUDA torch in this project's `.venv` (~2.5 GB download) to speed up eval? | yes |
| 8 | Should the main dev get the code as a git repo you hand over (`git bundle`/zip), or will you push it to a GitHub repo? | a local git repo; I'll make a clean `git bundle` and zip at each milestone |
