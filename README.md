# SongSpec Analyzer

Key, BPM, chords and detune analysis for songspec.org mashup producers: an HTTP service plus a headless JS client.

- `PLAN.md`: architecture, choices, evaluation, milestones
- `PROGRESS.md`: what is done and measured
- `INTEGRATION.md`: for SongSpec's main dev (run, configure, call, display)
- `contract/`: the versioned result schema

```bash
.venv/Scripts/python tools/dev_server.py                      # API + reference demo on http://127.0.0.1:8765
cd service && ../.venv/Scripts/python -m pytest -q            # service tests
```

## Desktop app (v1.1)

`rust/` holds a native Windows app, `SongSpecAnalyzer.exe`: one portable file with no Python and no installer. It is the same
analysis ported to Rust, and its results match the service on 19 of 20 songs (key, BPM, signature and detune; see
`eval/results/rust_parity.md`). Build steps and checks are in `rust/README.md`. The web service below is unchanged.

## Dropping it into the SongSpec site

Audio in, JSON out (`contract/analysis.schema.json`): `key` (tonic, scale, mode, Camelot, "May borrow from", section keys),
`tempo` (BPM by the house rule, beats, downbeats, sections), `chords` (segments with roman numerals, progressions), `detune`
(cents from A440, drift). Three ways in, same document from each:

1. **HTTP service (recommended for the site).** `docker compose -f docker/compose.yaml up --build` (API on :8000), or
   `uvicorn songspec_analyzer.api.app:app --app-dir service`. The browser or the site's backend posts the file to
   `POST /v1/analyses` and follows `GET /v1/analyses/{id}/events`; `client/analyzer-client.js` does all of that headlessly
   (no UI, so the site keeps full control of the look): `createClient({ baseUrl }).analyzeFile(file, { onUpdate })`.
2. **In-process Python:** `from songspec_analyzer import analyze_file; analyze_file("song.mp3")`.
3. **CLI:** `python -m songspec_analyzer song.mp3 --out result.json`.

Setup: Python 3.12, `pip install -r service/requirements.txt -e service`, then `tools/fetch_models.py` and
`tools/fetch_third_party.py` (S-KEY and BTC, the shipped key and chord accuracy). Configuration is environment variables only
(`SONGSPEC_*`; nothing machine-specific in the code); the full list, the API, the client and how to display each field are in
`INTEGRATION.md`. Accuracy, measured on held-out songs: `eval/REPORT.md`.
