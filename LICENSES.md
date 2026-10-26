# Licences of what the service uses

Status per component: **ship** = verified and fine to serve; **eval only** = used only to measure, never served; **pending** = not verified or not approved yet. "Verified" means read at the source on the date given.

| component | used for | licence | source checked | status |
|---|---|---|---|---|
| Centsible core (`service/native/centsible/core`) | detune, quick key | the user's own code | – | ship |
| BPM Fixer engine (`tempo/_bpmfix_port.py`) | tempo | the user's own code | – | ship |
| Beat This! code (`beat-this` 1.1.0) | beat tracking | MIT | PyPI / CPJKU repo | ship |
| Beat This! weights `final0.ckpt` | beat tracking | CPJKU's repo states its own terms for checkpoints; to confirm before production | – | **pending** |
| librosa 1.0.0, numpy, scipy, soundfile, soxr, scikit-learn | DSP | ISC / BSD / MIT | PyPI | ship |
| FastAPI, uvicorn, pydantic | API | MIT / BSD | PyPI | ship |
| PyTorch 2.14.1 | models | BSD-3 | PyPI | ship |
| pybind11 | binding | BSD-3 | PyPI | ship |
| Key profiles (`key/profiles.py`) | key | numbers from published papers, copied from Essentia's `key.cpp` and justkeydding's `keyprofile.cc` (MIT); data, cited per profile | 2026-10-26 | ship |
| S-KEY (Deezer, `third_party/skey`) | key (planned) | code MIT; weights `skey.pt` (SHA-256 `78dfd0ad…5ead4`) ship inside the MIT repo, no separate weights licence stated | github.com/deezer/skey @ `918b83d`, 2026-10-26 | **pending your approval to run downloaded code** |
| nnAudio 0.3.3 | S-KEY's CQT | MIT | PyPI | ship (with S-KEY) |
| Rubber Band (`rubberband-r3.exe` from BPM Fixer) | evaluation (pitch-shift test set); optional Fix BPM | GPL-2.0-or-later | – | eval only; server-side only if Fix BPM ships |
| Essentia | baseline | AGPL-3.0 | – | eval only (not installed: no Windows wheel) |
| madmom models | baseline | CC BY-NC-SA | – | eval only, not installed |
| key-cnn (Schreiber) | baseline | to verify (likely AGPL-3.0) | – | not used yet |
| Demucs | stems | MIT | – | not used yet |
| BTC, Chordino | chords | to verify | – | not used yet |
| chromaprint (`fpcalc`) | fingerprint cache | LGPL-2.1 (separate executable) | – | ship (Docker image) |
