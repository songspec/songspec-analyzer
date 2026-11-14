# SongSpec Analyzer desktop app (Rust)

A native desktop app (Windows, and macOS on Apple Silicon): drop songs in, get key, BPM, detune, time signature, borrowed chords and chords. One portable
`SongSpecAnalyzer.exe`, with no Python, Node or installer. The neural networks are built into the exe.

```
rust/
  songspec-core/   the analysis engine (library): decoding, DSP, Beat This!, BPM Fixer's tempo engine, Centsible (C++), key fusion,
                   chords (templates + BTC), borrows, house-style names. No GUI or OS APIs (WASM-able with `centsible` off).
  songspec-app/    the egui app (SongSpecAnalyzer.exe)
  songspec-cli/    command line, JSON out: used by the parity check
  models/          the ONNX networks (git-ignored; made by tools/export_onnx.py)
```

## Build

Requires Rust (stable, MSVC toolchain), Visual Studio 2022 Build Tools (C++, for Centsible), and the Python dev environment once,
to export the models.

```bash
.venv/Scripts/python tools/fetch_models.py        # Beat This! checkpoint (SHA-256 checked)
.venv/Scripts/python tools/fetch_third_party.py   # S-KEY and BTC
.venv/Scripts/python tools/export_onnx.py         # -> rust/models/*.onnx, each checked against PyTorch
cd rust
cargo build --release -p songspec-app             # -> rust/target/release/SongSpecAnalyzer.exe
cargo build --release -p songspec-cli             # optional, for eval/rust_parity.py
cargo test --release -p songspec-core             # notation (shared fixture with Python / JS) + borrow cases
```

`cargo build --profile fast` gives the same optimised code without LTO, and it builds much faster while you develop.

### macOS (Apple Silicon)

The same `cargo build --release -p songspec-app` works on a Mac; `bash tools/make_macos_app.sh target/release/SongSpecAnalyzer
VERSION dist` then wraps it in `SongSpec Analyzer.app` (icon, Info.plist, ad-hoc signature) and a drag-to-Applications `.dmg`.
[.github/workflows/macos.yml](../.github/workflows/macos.yml) does all of it on GitHub's Apple Silicon runners: it takes the models
from the hidden `models-1` draft release (SHA-256 checked), runs the tests, checks the CLI on a synthetic song
(`tools/make_test_song.py`: C Major, 120 BPM, 4/4, C G Am F), packages the .dmg and attaches it to each published release.

## Checks

- `eval/rust_dsp_check.py SONG`: every DSP stage against librosa / torchaudio (about 1e-6; beat tracks identical).
- `eval/rust_tempo_check.py N`: the BPM Fixer port against the service on the same raw beats (40 / 40 identical).
- `eval/rust_parity.py N`: the whole analysis against the Python service, song by song (key, BPM, signature, detune ±0.5 ¢);
  results in `eval/results/rust_parity.md`.
- `tools/measure_idle_ram.ps1`: the app's idle memory as Task Manager shows it.

## What differs from the service

- Resampling: soxr's HQ filter is not available in Rust; `dsp::resample_hq` is a Kaiser-windowed sinc fitted to soxr's measured
  response (identical below 10 kHz). It feeds Beat This! and the 22 kHz analyses, so a beat or a chord can rarely land
  differently; see the parity report.
- Hook Theory cross-check, Spotify ISRC lookup and user corrections are web-service features and are not in the app. Links:
  Apple Music via Apple's public search (no account), Spotify as a search link.
- Section-key modes are shown as plain Major / Minor (the service's chroma-template modes never pass their margin either).
