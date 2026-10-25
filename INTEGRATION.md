# Integrating SongSpec Analyzer into songspec.org

For SongSpec's main dev. This repo is a **self-contained analysis service plus a headless JS client**. The UI is yours: nothing here decides layout, styling or components. `demo/` is only a test harness that shows every API feature.

> Status: draft (milestone 1). The API shape and the result schema are stable (`/v1`, schema 1.x). The analysis stages fill in milestone by milestone; until a stage lands, its section is missing and `stages[name]` is `"unavailable"`. See `PROGRESS.md`.

## 1. Run it

Local, no Docker (Python 3.10+):
```bash
python -m venv .venv
.venv/Scripts/pip install -e "service[ml]"       # Linux/macOS: .venv/bin/pip
.venv/Scripts/python tools/fetch_models.py      # vendored model weights, SHA-256 checked (milestone 2+)
.venv/Scripts/python tools/dev_server.py        # API + demo on http://127.0.0.1:8765 (demo at /demo/, client tests at /client/test/)
```
Production-style:
```bash
docker compose -f docker/compose.yaml up --build    # API on :8000, data in a named volume
uvicorn songspec_analyzer.api.app:app --app-dir service --host 0.0.0.0 --port 8000 --workers 1   # same thing without Docker
```
Use **one** API process per data volume with the default inline queue (it runs analyses in a thread pool inside the API process). To scale out, set `SONGSPEC_QUEUE=redis` and run `rq worker songspec` processes that share the database and the uploads folder.

## 2. Configuration (environment variables)

| variable | default | meaning |
|---|---|---|
| `SONGSPEC_DATA_DIR` | `./data` | SQLite database + temporary uploads |
| `SONGSPEC_MAX_UPLOAD_MB` | `100` | per file |
| `SONGSPEC_MAX_DURATION_S` | `900` | 15 minutes |
| `SONGSPEC_AUDIO_TTL_S` | `86400` | hard limit for keeping uploaded audio (normally deleted the moment its analysis ends) |
| `SONGSPEC_WORKERS` | `1` | analyses in parallel (each uses ~1-2 CPU cores and up to ~2 GB RAM in high-accuracy mode) |
| `SONGSPEC_QUEUE` | `inline` | `inline` or `redis` |
| `SONGSPEC_REDIS_URL` | `redis://localhost:6379/0` | for `QUEUE=redis` |
| `SONGSPEC_CORS_ORIGINS` | `*` | comma-separated, e.g. `https://songspec.org,https://www.songspec.org` |
| `SONGSPEC_API_KEYS` | (empty) | comma-separated; when set, every request needs `X-API-Key` |
| `SONGSPEC_RATE_PER_MIN` / `SONGSPEC_RATE_BURST` | `10` / `5` | new analyses per client (by `X-SongSpec-User` header, else IP) |
| `SONGSPEC_HIGH_ACCURACY` | `0` | default for the `high_accuracy` option (stem separation; slow on CPU) |
| `SONGSPEC_SERVE_DEMO` | `0` | `1` mounts `/demo`, `/client` (development only) |
| `SONGSPEC_HOOKTHEORY_USER` / `_PASSWORD` | (empty) | Hooktheory cross-check; off when empty |
| `SONGSPEC_SPOTIFY_CLIENT_ID` / `_SECRET` | (empty) | ISRC lookup for Spotify links; optional |

Auth options: (a) put the service behind your backend and call it server-to-server with an API key, or (b) call it from the browser (CORS allow-list) and pass the signed-in user's id in `X-SongSpec-User` for per-user rate limits and feedback attribution. If the browser calls it directly, don't put the API key in frontend code; use a short proxy route instead.

## 3. API (v1)

| method | path | |
|---|---|---|
| `POST` | `/v1/analyses` | multipart: `file` (+ optional `options` = JSON string). Or JSON `{"url": "<spotify/apple link>", "options": {...}}`. → `202` with the first view |
| `GET` | `/v1/analyses/{id}` | the current view (partial while running) |
| `GET` | `/v1/analyses/{id}/events` | SSE: `update` (full view on every change), then `done` or `error` |
| `GET` | `/v1/analyses/{id}/exports/{fmt}` | `json`, `csv`, `tracker-row` now; `midi`, `chordpro`, `txt` with the chord milestone. `?transpose=N&simplify=1` |
| `POST` | `/v1/analyses/{id}/feedback` | `{"field": "key"|"bpm"|"detune"|"signature"|"chords"|"other", "correct": "Bb Minor", "comment": "..."}` |
| `GET` | `/v1/version`, `/v1/health`, `/v1/schema` | engine/schema version, accepted formats, limits; liveness; the JSON Schema |

Options: `high_accuracy` (bool, stem separation), `signature` (force a time signature, e.g. `"6/8"`), `hooktheory` (bool, default on when configured).

Errors are always `{"error": {"code", "message"}}`, and `message` is safe to show to users. Codes: `no_input`, `too_large` (413), `too_long` (413), `empty_file`, `rate_limited` (429, with `Retry-After`), `unauthorized` (401), `not_found`, `not_ready` (409), `links_not_ready` (501, until milestone 7). A file that can't be decoded becomes an analysis with `status: "error"` and a readable `error`.

Caching: the exact same file returns instantly (`cached: true`). The same song in another file is matched by Chromaprint fingerprint when `fpcalc` is installed (included in the Docker image).

Privacy: uploaded audio is deleted as soon as its analysis finishes (24 h hard limit). Only the derived JSON is kept. Audio is never served back.

## 4. Client (`client/`)

Plain ES modules, no build step, no dependencies. Copy the folder into the site or import it from wherever you host it. Types: `analyzer-client.d.ts`.

```js
import { createClient, trackerRowFor, transposeChord } from "./analyzer-client.js";

const analyzer = createClient({ baseUrl: "https://analyzer.songspec.org", headers: { "X-SongSpec-User": userId } });

const result = await analyzer.analyzeFile(file, {
  options: { high_accuracy: false },
  onUploadProgress: (f) => setUpload(f),          // 0..1 (uses XHR when given)
  onUpdate: (a) => setAnalysis(a),                // partial results: render whatever sections exist
});
// several files, one after another
await analyzer.analyzeQueue(files, { onFileUpdate: (i, a) => update(i, a) });
// link input
await analyzer.analyzeLink("https://open.spotify.com/track/...", { onUpdate });
// exports / feedback
location.href = analyzer.exportUrl(result.id, "midi", { transpose: -2 });
navigator.clipboard.writeText(trackerRowFor(result));
await analyzer.feedback(result.id, "key", "Bb Minor");
```
`watch()` reads the SSE stream through `fetch` (so headers work) and falls back to polling when a proxy buffers SSE. Errors are `AnalyzerError {status, code, message, retryAfter}`.

Pure formatters for client-side transforms (all exported from the same module): `houseKey`, `keyDisplay`, `camelot`, `openKey`, `sectionsDisplay`, `houseBpm`, `fmtBpm`, `bpmDisplay`, `detuneDisplay`, `roman`, `transposeChord`, `simplifyChord`, `trackerRow`, `trackerRowFor`. They're tested against the same cases as the server (`client/test/`).

## 5. The result, and how it's meant to be shown

The schema is `contract/analysis.schema.json` (every field documented), with an example in `contract/examples/complete.json`.

- **Partial results:** render each section as soon as `stages[name] === "done"`. Order: `detune` and `key_quick` (seconds), then `tempo`, `chords`, `key` (fused, with sections), `metadata`. `progress.message` is a ready-made status line ("Finding beats…").
- **Display strings:** every user-facing value the brief specifies is precomputed in a `display` object in SongSpec's house style (flats, "Major"/"Minor", the asterisk for modal keys, the house BPM rule, "+23.4 ¢"). Show those as they are; the raw numbers beside them are for logic and custom visuals.

What the product brief asks the page to offer, as *suggestions* (your design decides the form):

| idea | data |
|---|---|
| Key tile with confidence and a top-3 popover | `key.display.key`, `key.display.note`, `key.confidence`, `key.candidates`, `key.camelot`, `key.open_key`, `key.display.ambiguity_text` |
| BPM tile with ½× / 2× chips and a 6/8 badge | `tempo.display.bpm`, `tempo.display.note`, `tempo.display.alternates`, `tempo.compound`, `tempo.signature`, `tempo.bpm_source` |
| Detune tile with direction and "pitch by … to fix" | `detune.display.display`, `detune.display.fix`, `detune.cents` (sign = direction), `detune.display.key_text` |
| Time signature tile | `tempo.signature`, `tempo.signature_conf` |
| "Where did this come from" info | `detune.estimators`, `key.estimators`, `key.metadata.hooktheory` |
| Shared timeline: beat grid, chord lane, key-section lane, tempo curve, detune drift | `tempo.beats`, `tempo.downbeats`, `chords.segments`, `key.sections`, `key.modulations`, `tempo.curve`, `tempo.sections`, `detune.drift` (when `detune.drift_flag`) |
| Click track over the detected beats (a quick check that the beats are right) | `tempo.beats`: schedule WebAudio clicks at those times |
| Retune preview ("hear it at A440") | play with `detune` = `-detune.cents` (WebAudio `AudioBufferSourceNode.detune`) |
| Chord summary, progressions, simplify, transpose | `chords.summary`, `chords.progressions[].display`, `segment.triad`, `transposeChord()` |
| Preview-based warning (link input) | `warnings[]`, `track.source` ends in `-preview` |
| "This is wrong" button | `POST …/feedback` |

The audio for playback stays in the user's browser (the `File` they dropped). The service never sends audio back.

## 6. Versioning
`schema_version` follows semver: 1.x only adds fields. `versions.engine` changes whenever results may change (the cache is keyed on it, so an engine update re-analyses on demand).
