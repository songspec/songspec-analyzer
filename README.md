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
