# PROGRESS

Plan: `PLAN.md`. Integration guide for SongSpec's main dev: `INTEGRATION.md`.

| # | milestone | status |
|---|---|---|
| 1 | Scaffold: service, queue, store, upload + limits, cache, SSE, contract, client, demo, INTEGRATION draft | **done** (2026-10-25), see below |
| 2 | Tempo: BPM Fixer port, Beat This!, house BPM rule | next |
| 3 | Detune: Centsible pybind11 binding + cross-checks | |
| 4 | Key v1 + eval harness + baselines | |
| 5 | Chords | |
| 6 | Key v2 (fusion, modes, sections) | |
| 7 | Link input + Hooktheory | |
| 8 | Polish, Docker, final INTEGRATION.md | |
| 9 | Optional: Fix BPM, mashup helper | |

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
