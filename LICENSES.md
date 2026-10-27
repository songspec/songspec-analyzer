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
| S-KEY (Deezer, `third_party/skey`) | key (fusion input) | code MIT; weights `skey.pt` (SHA-256 `78dfd0ad…5ead4`) ship inside the MIT repo, no separate weights licence stated | github.com/deezer/skey @ `918b83d`, 2026-10-26 | ship (approved by the user 2026-10-26; weights loaded with `weights_only=True`) |
| BTC (Park et al., `third_party/btc`) | chords (blended with the templates) | MIT (code); weights `btc_model_large_voca.pt` (SHA-256 `1673d23f…ab51`) ship inside the MIT repo | github.com/jayg996/BTC-ISMIR19 @ `2682317`, 2026-10-26 | ship (approved by the user 2026-10-26; `weights_only=True`) |
| nnAudio 0.3.3 | S-KEY's CQT | MIT | PyPI | ship (with S-KEY) |
| Rubber Band (`rubberband-r3.exe`, conda-forge `rubberband` 4.0.0, the same file BPM Fixer bundles) | evaluation (pitch-shift test set); Fix BPM | GPL-2.0-or-later | – | run as a separate program by the server, never linked. `tools/fetch_vendor.py` puts it in `service/vendor/rubberband/` with its COPYING and source note; the tester zip carries that folder, as the BPM Fixer installer does |
| Essentia | baseline | AGPL-3.0 | – | eval only (not installed: no Windows wheel) |
| madmom models | baseline | CC BY-NC-SA | – | eval only, not installed |
| key-cnn (Schreiber) | baseline | to verify (likely AGPL-3.0) | – | not used yet |
| Demucs | stems | MIT | – | not used yet |
| Chordino / NNLS-Chroma | chords | GPL-2.0 Vamp plugin | – | not used (BTC + templates instead) |
| chromaprint (`fpcalc`) | fingerprint cache | LGPL-2.1 (separate executable) | – | ship (Docker image) |

## Streaming services (link input)

| service | what we use | what the terms say (read 2026-10-26) | status |
|---|---|---|---|
| Apple (iTunes Search / Lookup API) | metadata for a pasted link | lookups may be cached ("large websites should set up caching logic"), ~20 calls/minute. Previews: promotional use only, near a store badge, attributed "provided courtesy of iTunes", and "streamed only, and not downloaded, saved, cached" (performance-partners.apple.com/search-api) | **metadata: ship. Server-side preview analysis: not allowed as written**; needs Apple's OK or a legal decision |
| Deezer API | nothing yet | "strictly limited for a non-commercial purpose", no direct or indirect revenue (developers.deezer.com/termsofuse) | **not used** while SongSpec is treated as commercial |
| Spotify oEmbed | title + cover of a pasted link | public embed metadata | ship |
| Spotify Web API | artists, duration, ISRC (only with credentials) | Developer Terms to be read before enabling; dev-mode apps need the owner's Premium | off until credentials and a read of the terms |
| Hooktheory TheoryTab API | key / BPM cross-check (milestone 7b) | needs an account and its terms | off until credentials |

Result: a link returns the full analysis when the same song was uploaded before (ISRC or artist + title + duration), else asks for
the file (`422 upload_needed` with the track it found). The brief's preview route waits for SongSpec's decision.
