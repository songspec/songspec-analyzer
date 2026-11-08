# Evaluation report

Every number here is reproducible with the scripts in `eval/`. Per-song outputs are in `eval/results/` (git-ignored: they come from the user's own audio). Ground truth for the catalog is the user's QO & SPECTRE tracker (`C:\dev\detunetest\testdata\key_labels.csv`, matched to 369 catalog songs by Centsible's tools; 164 have a tracker BPM). The tracker spells some keys with sharps, so keys are compared by pitch class.

## 1. Tempo (milestone 2, 2026-10-25)

`eval/tempo_eval.py`. Truth = the tracker's main (first-listed) BPM. "Any listed" also accepts a match with another of the song's listed section tempos (beat switches).

| estimator | songs | within 1 % (main BPM) | within 1 % (any listed) | Acc1 (4 %) | Acc2 (4 %, x2 x3 allowed) |
|---|---|---|---|---|---|
| **service** (BPM Fixer engine + switches + house rule) | 164 | **86.0 %** | **90.2 %** | **89.0 %** | **92.7 %** |
| Centsible's own tempo (`centsible_cli`) | 155* | 84.5 % | 89.0 % | 87.7 % | 91.0 % |
| Beat This! raw beats, median interval, house rule | 164 | 46.3 % | 50.6 % | 74.4 % | 79.3 % |
| librosa `beat_track`, house rule | 164 | 33.5 % | 36.6 % | 75.0 % | 79.9 % |

\* Centsible's CLI gave no usable reading for 9 files (mostly names with curly apostrophes, a Centsible CSV issue, not a tempo miss); its numbers are over the 155 it read. Per song: both right 133, service only 15 (8 of them files Centsible had no reading for, so **7 vs 5** on songs both read), Centsible only 5, neither 11.

Context: 1 % is the tracker's own precision (its BPMs have 0–3 decimals). The 1 % rate of the raw tracker (46 %) shows how much BPM Fixer's cleaning (level choice, beat refinement) and the line fit add: the same Beat This! beats go from 46 % to 86 %.

**Beat switches and steadiness**

| songs | count | flagged by the service |
|---|---|---|
| tracker lists several BPMs (beat switches, intros) | 37 | 19 have several sections or an inconsistent flag |
| tracker lists one BPM | 127 | **0** split into sections (no false beat switches) |
| tracker notes say "inconsistent" / "played live" | 18 | 4 flagged |
| tracker notes say nothing about steadiness | 146 | 18 flagged as moderately inconsistent / live |

The steadiness flag is the **weak part**. `eval/tempo_tune.py` shows that the drift measure barely separates the tracker's "inconsistent" songs (cross-validated balanced accuracy 0.37–0.69 by artist fold). Many of those 18 are only partly inconsistent ("Intro BPM inconsistent") or "slightly inconsistent" with almost no measurable drift (0.1–0.8 %). The thresholds are therefore conservative (moderate > 3 %, live > 6 %, above the 90th percentile of unflagged songs) so that a quantised song is rarely shown as "~122 - 127".

**Parity with the shipped BPM Fixer:** `eval/tempo_parity.py` runs the installed `bpmfix-cli.exe` and the service on the same songs (both CPU) and compares the beat grids. See section 1.1 for the result. The unit-level parity (`service/tests/test_tempo.py`) asserts that every ported function's source is identical to `bpmfix.py` and that the beat grid equals the original's step by step on six synthetic cases (steady, half-time level, drift, missed beats, half-time passage, 3/4).

### 1.1 Parity run (installed `bpmfix-cli.exe` vs the service, both on the CPU, signature Auto)

| song | signature exe / ours | beats exe / ours | max difference |
|---|---|---|---|
| 01 - POP OUT | 4/4 / 4/4 | 388 / 388 | 0.0 ms |
| 01 - Quadeca - NO QUESTIONS ASKED | 4/4 / 4/4 | 832 / 832 | 1.0 ms (3-decimal rounding) |
| 01 - Quadeca - sorry4dying | 4/4 / 4/4 | 632 / 632 | 0.0 ms |
| 01 Drake - Make Them Cry | 4/4 / 4/4 | 414 / 414 | 0.0 ms |
| 01 ☆ Dark Magic | 4/4 / 4/4 | 406 / 406 | 0.0 ms |
| 01-01 - Brain Fog | 4/4 / 4/4 | 351 / 351 | 0.0 ms |

**6 / 6 identical beat grids.** Only 6 songs: the exe also time-stretches every song (minutes each), and a first full-speed run coincided with a PC crash, so this runs at below-normal priority one song at a time. More songs: `eval/tempo_parity.py --limit N` (results are cached per song). On GPU the tracker's floating point differs slightly from the CPU, so the service on a GPU host is "BPM Fixer's engine on GPU beats", not bit-identical to the exe.

### 1.2 Remaining misses (16 songs match none of the listed BPMs)
- **Compound meters not recognised (5):** the tracker says 6/8 for *tell me a joke*, *picking up hands*, *Are We Still Friends*, *GUIDE DOG*, *MONDAY*; the tracker's 6/8 value is the eighth-note tempo (176–191). The service reads them as 4/4 or 3/4 at another level. `infer_signature` counts tracker beats per bar and, as BPM Fixer's own window says, "can't tell 6/8 from 2/4". This is the largest single error class; a meter classifier (beat-level onset periodicity: 2- vs 3-subdivision) is the planned fix.
- **3:2 / 4:3 level errors (4):** *CASPER*, *emails i can't send*, *Summer Love*, *the memories we lost in translation*.
- **80 vs 160 (2):** *RUIN MY LIFE*, *Baby Pluto*: the service reads ~159.5, the tracker 80. Both are legal under the house rule; the tracker hears the slower level.
- **Small offsets (3):** *UNDER MY SKIN* 123 vs 126, *Malfunction* 141.4 vs 136, *Feelings Mutual* ~126 - 134.5 vs 126.
- **Section order / main tempo (2):** *Summer Love + Set the Mood* "95.9 / 120" (tracker 120;119.87: the 95.9 prelude is real but the tracker lists only the main part), *X* 81.5 vs 140.

Target from PLAN.md: "> 95 % after the house rule on pop/EDM". **Not met**: 86.0 % within 1 % / 89.0 % within 4 % on this catalog (dense trap, 6/8 indie, live-ish pop). GiantSteps Tempo (EDM) is still to be run; the fixes above (compound meter, level choice) are scheduled with key v2 (milestone 6), where the chord/onset features they need are built anyway.

### 1.3 6/8 (2026-11-08, `eval/meter_feat.py`, `tempo/engine.compound_meter`)

The tracker writes a 6/8 song's BPM as its eighth-note tempo ("176*"). BPM Fixer's Auto signature needs 60 % of the beat tracker's
bars to agree, and on 6/8 songs those bars are messy, so it fell back to 4/4 (or said 3/4 when 3 beats per bar was a clear
majority). The tracker's raw beats per bar separate the meters on the catalog:

| songs | most common count is 3 or 6 per bar | 4 per bar |
|---|---|---|
| the tracker says the song is in 6/8 / 12/8 (8 distinct) | 7 | 1 |
| all other songs (149) | 1 | 135 |

Rule: when 3 or 6 per bar is the most common count (in at least 30 % of bars), the song is 6/8. The tracker's beats are then eighth
notes; BPM Fixer cleans the grid as usual, and the felt 6/8 beat (a dotted quarter) is three eighths at the octave the cleaned grid
sits on. Exception: 3 beats in at least 85 % of bars stays 3/4 (a waltz's bars are regular; the catalog's 6/8 songs reach 71 %).
That cut-off is not measured on real 3/4 songs: the catalog has none. A triplet-feel feature (onsets at a third vs a half of
each beat) was tried as well and separated nothing.

| | before | after |
|---|---|---|
| within 1 % of the tracker's main BPM | 86.0 % | **86.6 %** |
| within 1 % of any listed BPM | 90.2 % | **91.5 %** |
| within 4 % (Acc1) | 89.0 % | **89.6 %** |
| within 4 %, octave errors forgiven (Acc2) | 92.7 % | 91.5 % |
| songs the tracker calls 6/8 that we call 6/8 | 0 of 8 | **7 of 8** |

Now right: *picking up hands* 180*, *GUIDE DOG* 176*, *tell me a joke* 172.5* (tracker 175), *MONDAY* 216* (the tracker's "works
in 6/8 at 216"), *FORGONE* ~127*, *TEXAS BLUE* 150*, *fantasyworld*. Wrong now: *Tofu* (tracker 140, now 105* in 6/8). Still
wrong: *RUIN MY LIFE* (the tracker only says "may work in 6/8"; 80 there, 320* here) and *Are We Still Friends* (4 per bar in the
beat tracker's bars, so the rule does not see it). Acc2 drops because a tripled eighth tempo no longer lines up for a few songs.

## 2. Detune (milestone 3, 2026-10-26)

### 2.1 Parity with the Centsible plugin's DSP (`eval/detune_parity.py`)
The service runs Centsible's C++ core through a pybind11 binding (a verbatim copy of `core/`). Against the plugin's own command-line
analyzer (`centsible_cli.exe`) on the same files, one CLI process per file: the reference pack (8 exactly shifted copies), the two
A440 test tones and 30 catalog songs.

| files | max abs difference | mean |
|---|---|---|
| 39 lossless (WAV / FLAC) | **0.005 cents** (the CLI's CSV prints 2 decimals) | 0.0026 |
| 1 MP3 (decoded by JUCE in the CLI, by libsndfile in the service) | 0.021 cents | 0.021 |

Requirement: <= 0.5 cents. The fast path the service uses (`analyze_tuning`, the tuning half of `analyzeTrack` without its key and
tempo passes) is asserted equal to `analyzeTrack`'s reading by `service/tests/test_detune.py`.

### 2.2 Accuracy on exactly shifted real music (`eval/detune_eval.py`)
12 catalog songs (60 s excerpts from 0:45), each shifted by -49, -37, -25, -12, -3, +3, +12, +25, +37, +49 cents. Error = (reading of
the shifted copy) - (reading of the original) - shift. Real songs have no known absolute tuning, so this measures what can be
measured exactly: whether each shift is recovered.

| shift method | estimator | cases | MAE (cents) | median | p95 | max | within 0.5 | within 2 |
|---|---|---|---|---|---|---|---|---|
| resample (exact) | Centsible | 108 | 0.255 | 0.175 | 0.846 | 1.17 | 87.0 % | 100.0 % |
| resample | CQT-120 | 118 | 1.149 | 0.203 | 6.920 | 8.53 | 66.9 % | 86.4 % |
| resample | librosa `estimate_tuning` | 118 | 5.981 | 2.850 | 25.245 | 45.20 | 16.9 % | 44.9 % |
| resample | **service** | 118 | **0.270** | 0.180 | 0.864 | **1.17** | 83.9 % | **100.0 %** |
| Rubber Band R3 pitch shift | Centsible | 101 | 0.687 | 0.260 | 0.930 | 35.10 | 75.2 % | 99.0 % |
| Rubber Band | CQT-120 | 111 | 1.358 | 0.517 | 7.187 | 8.95 | 48.6 % | 85.6 % |
| Rubber Band | librosa | 111 | 6.097 | 2.400 | 24.250 | 49.00 | 24.3 % | 47.7 % |
| Rubber Band | **service** | 111 | **0.641** | 0.250 | 0.930 | 35.10 | 76.6 % | **99.1 %** |

Target (PLAN.md): MAE < 2 cents. **Met** (0.27 / 0.64).

- The service's rule: Centsible's reading whenever it has one; CQT-120 (5-cent interval, low confidence, a warning) when Centsible
  has too little steady tonal evidence; librosa never decides. A first version let the cross-checks outvote Centsible when its
  interval was wide; that made the worst case 15x worse (1.17 -> 17.6 cents) and was dropped.
- One excerpt (*I'm Sorry*) has no Centsible reading at all (loud, dense, few steady peaks): the fallback reads it and recovers all
  20 shifts within 0.96 cents.
- The one large error is *CASPER* under Rubber Band (35 cents): a harmonic alias, the failure mode Centsible's own REPORT
  documents on dense mixes, reported with a wide interval (1.1 cents on the original).

## 3. Key (milestones 4 and 6, 2026-10-26)

Ground truth: the tracker's main key (first listed) for the 165 catalog songs that have one; keys compared by pitch class. Metric:
MIREX weighted score (exact 1, fifth 0.5, relative 0.3, parallel 0.2), the mir_eval convention. The catalog has only 23 artists, so
every trained model is evaluated by **nested cross-validation over 5 song-balanced artist groups** (a song is always predicted by a
model that never saw its artist and was never tuned on it). A first single held-out split (`sha1(artist) % 5`) was replaced before
any model was chosen: it put 37 % of the songs in held-out and left dev folds of 68 / 28 / 6 / 2 songs.

### 3.1 Single estimators (`eval/key_eval.py`, `eval/key_fusion.py`)

| estimator | MIREX | exact |
|---|---|---|
| **Centsible's detector** (Temperley scale + bass tonic; via the binding) | **0.564** | 45.5 % |
| Temperley 2005 on Centsible's peak chroma | 0.551 | 42.4 % |
| Albrecht-Shanahan on Centsible's peak chroma | 0.532 | 40.0 % |
| Albrecht-Shanahan on the service's tuned beat chroma (log aggregation) | 0.511 | 40.0 % |
| Krumhansl on the service's tuned beat chroma | 0.495 | 36.4 % |
| naive baseline: Krumhansl on librosa's default `chroma_cqt` (no tuning, no HPSS) | 0.489 | 38.2 % |
| EDMA (Faraldo, EDM profile) on the naive chroma | 0.473 | 35.8 % |

(All 30 profile x chroma combinations: `eval/results/key_fusion.md`.) Essentia's KeyExtractor, key-cnn and madmom could not be run
here (no Windows wheel / not installed / non-commercial licence); S-KEY is cloned but waits for approval to run downloaded code.

A bug found on the way: the first tuned chroma summed raw CQT magnitude and weighted frames by energy, so one loud sub-bass note (an
808) and the loudest section decided the profile match; every profile on it scored *below* the naive baseline (Albrecht-Shanahan
0.390). Log aggregation with equal frame weights fixed it (0.511). That variant was picked among five on all 165 songs (in-sample).

### 3.2 Fusion (rotation-equivariant logistic regression, tiebreaker features; `eval/key_fusion.py`)

| | MIREX (nested CV) | exact |
|---|---|---|
| fusion | 0.549 | 44.2 % |
| Centsible's detector alone | **0.564** | 45.5 % |

**The gate is not passed**: the fusion is worse than the best single estimator (better on 9 songs, worse on 14), so no fusion model is
installed and the service reports Centsible's key. Two caveats, both against Centsible being the fair winner: Centsible's tonic
weights were refitted on 140 of these same 165 songs (its own REPORT, section 8), so its 0.564 is partly in-sample while the fusion's
score is strictly out-of-artist; on songs it had never seen, Centsible's REPORT measured 0.586 / 44.8 % exact. A fair comparison
needs labelled songs Centsible never saw.

What would most likely move the key (not built yet, in order): S-KEY (waits for approval), a chord-based key estimator fed by the
chord engine (cadences, first / last chord), stems (Demucs: bass and vocals apart), more labelled songs (the tracker has 57,857 keys;
the missing piece is matching audio).

### 3.3 Modes (`eval/key_modes.py`)
Mode labels: the tracker's asterisk + the mode named in the notes (34 modal songs, 94 plain, 37 unclear). With the tonic given:

| rule | accuracy | modal songs found | false modes |
|---|---|---|---|
| never report a mode | 73.4 % | 0 / 34 | 0 |
| chroma templates, best margin | 66.4 % at most | 6 - 16 | 20 - 60 |
| **Centsible's mode when its probability >= 0.5** (shipped) | **75.0 %** | 2 / 34 | 1 |

The templates are off; the service names a mode only when Centsible is confident (threshold picked on all songs). Detecting the
tracker's Phrygian songs (22 of the 34) is an open problem.

### 3.4 Section keys (`eval/key_sections.py`)
Songs the tracker lists with several keys vs one key; window HMM over 8-bar windows (hop 4) of the tuned beat chroma.

| change penalty (nats) | multi-key songs split (of 30) | one-key songs split falsely (of 134) |
|---|---|---|
| 8 | 16 (53 %) | 32 (24 %) |
| 16 | 11 (37 %) | 19 (14 %) |
| **24 (shipped)** | **8 (27 %)** | **4 (3 %)** |
| 48 | 6 (20 %) | 1 (1 %) |

Shipped at 24 (chosen on all songs): a wrong "C Minor / F Major" misleads more than a missed key change. Most of the tracker's key
changes are not found; the ones that are tend to be clear (intros, last-chorus lifts).

### 3.5 Chord-based key evidence (`eval/chord_feat.py`, `service/.../key/chordkey.py`)
The chord engine ran on all 165 labelled songs (cached beats and tunings). Per candidate key: diatonic chord time, tonic-triad time,
first / last chord, cadences into the tonic (incl. minor v -> i).
- On its own (fixed weights): MIREX **0.426**, 27.4 % exact: weaker than every profile on Centsible's chroma, which says more about
  the template chord engine on dense real mixes than about the idea (BTC would be the test; it waits for approval to run).
- As fusion features (nested CV now also chooses none / tiebreak / chords / both): fusion **0.549** again, vs Centsible 0.564
  (better on 6 songs, worse on 9). The inner CV picks the chord features for one group of five. **Gate still not passed.**

### 3.6 S-KEY and the fusion that ships (approved by the user, 2026-10-26)
S-KEY (Deezer, ICASSP 2025; self-supervised, never trained on the catalog) on the same 165 songs, then the nested-CV fusion again
with S-KEY in the estimator pool (`eval/skey_feat.py`, `eval/key_fusion.py`):

| estimator | MIREX | exact | fifth | relative | parallel | other |
|---|---|---|---|---|---|---|
| **fusion** (nested CV; ships as Centsible + S-KEY + Albrecht-Shanahan on two chromas) | **0.601** | **46.1 %** | 26 | 32 | 3 | 28 |
| S-KEY alone | 0.596 | 41.8 % | 39 | 32 | 1 | 24 |
| Centsible's detector alone | 0.564 | 45.5 % | 20 | 22 | 7 | 41 |

**Gate passed** (0.601 > 0.596): the fusion model is installed (`service/.../key/fusion_model.json`). Honest size of the gain over
S-KEY alone: better on 12 songs, worse on 14; the MIREX gain comes from turning S-KEY's fifth errors into exact or relative answers
(exact 41.8 -> 46.1 %). S-KEY is the clear win over Centsible (+0.03 MIREX) and, unlike Centsible, never saw these songs. Without
S-KEY on a server the key stage falls back to Centsible's key.

### 3.7 "May borrow from" (`eval/key_borrow.py`, `service/.../key/borrow.py`, 2026-10-26)

The tracker's notes name borrowed scales in free text on about 1 560 of its rows ("Borrows heavily from F Harmonic Minor",
"Might borrow from D Mixolydian"). On the 131 single-key labelled songs with chord evidence, borrow detection runs relative to the
tracker's own key (so key errors are not counted) and is compared with every "<tonic> <scale>" the note mentions:

| songs | n | we name at least one of the note's scales | share of our names the note mentions |
|---|---|---|---|
| note names a scale | 35 | 43 % | 48 % |
| note names none | 96 | we name one in 29 % | - |

What changed the numbers: ignoring chords on the key's root (the recogniser flips the tonic major/minor when the beat has no 3rd:
29 % -> 49 % hits, 22 % -> 40 % precision), then the thresholds (MIN_SHARE 0.04 -> 0.10: 49 -> 43 % hits, 40 -> 48 % precision,
29 % instead of 42 % of silent songs flagged). Those two settings were chosen on these same 35 songs, so the numbers are optimistic;
a silent note is not proof of no borrowing, so the 29 % is an upper bound on false alarms. The service words it "May borrow from".

### 3.8 User corrections and retraining (`eval/key_retrain.py`)

Every analysis stores the key fusion's inputs (numbers only) and users can correct the key. `key_retrain.py` adds the corrected
songs to the tracker songs and cross-validates twice by group on all held-out songs with the installed configuration held fixed:
trained on tracker songs only vs with corrections. (Re-choosing the configuration is too noisy for this: in a smoke test one added
song re-chose configurations and moved the tracker songs' score 0.601 -> 0.621 by chance.) It installs only with >= 20 corrected
songs, a better score on them, no loss on the tracker songs (0.005 slack) and a win over the best single estimator. No real
corrections exist yet (tester builds collect them in data/analyzer.sqlite3).

### 3.9 Three targeted key fixes tried for 1.0 (2026-10-31): none installed

Same 165 songs, held-out by artist group. The installed fusion (Centsible + S-KEY + Albrecht-Shanahan on two chromas, no extra
features) scores 0.601 / 46.1 % exact in nested CV, 0.621 / 49.1 % with its configuration held fixed.

| change tried | held-out result | installed? |
|---|---|---|
| chord features from the chords the service actually recognises (BTC + templates) instead of the template-only chords | nested CV 0.595 / 43.6 % exact (-0.006) | no |
| a minor-bias offset chosen inside the CV (the fusion calls 72 % of songs minor, the labels 61 %; 23 of the 30 relative errors are major songs called minor) | 0.621 / 49.1 %, the same (offsets chosen 0, 0, +0.15, +0.3, +0.15) | no |
| strongly detuned songs named on the other side of the +-50 cent boundary, as the tracker's notes do ("Pitched up 0.5 semitones", key named before the shift) | 5 exact instead of 9 of the 32 songs at 35 cents or more | no |

What the error analysis found: songs detuned by 35 cents or more (32 of 165, many pitched by half a semitone on purpose) are
28 % exact against 53 % for songs within 15 cents. The estimators agree with each other on them (one-semitone disagreement
0-9 %), so it is not a tuning-reference bug; the tracker names the key the song had before it was pitched, which the audio alone
cannot tell. TODO after 1.0: for songs within ~5 cents of the boundary show both readings ("G Minor -49.6 c = F# Minor +50.4 c");
more labelled songs (the fusion has 165); a 6/8 meter classifier for tempo (section 1.2).

The fusion's fit is vectorised (same weights: max difference 4e-7 against the installed model), so the nested CV takes about two
minutes instead of half an hour, and `key_retrain.py` retrains from user corrections as quickly.

## 4. Chords with BTC (approved by the user, 2026-10-26; `eval/chord_blend.py`)
BTC (large vocabulary, 170 chords) runs on the tuning-corrected CQT and its per-frame probabilities are blended per beat with the
template evidence before the same beat-synchronous Viterbi. No chord labels exist for the catalog, so the yardstick is a proxy: the
key derived from the recognised chords against the tracker's key (164 songs).

| BTC weight | chord-derived key MIREX | exact | chord changes / minute |
|---|---|---|---|
| 0 (templates only) | 0.426 | 27.4 % | 19 |
| **0.25 (shipped)** | **0.480** | **35.4 %** | 21 |
| 0.5 | 0.478 | 34.1 % | 22 |
| 1.0 (BTC only) | 0.457 | 31.1 % | 24 |

The blend beats both sources alone; the weight was chosen on all songs via the proxy. BTC on the GPU adds ~1-4 s per song.

Example (*Power 4*, tracker: Bb minor, 153 BPM): chords Gb - Ab - Bbm repeating, progression "IV-V-vi x7". The fused key says
**Db Major**, the relative major (S-KEY and four profiles vote Db Major, Centsible Bb Minor): the classic relative confusion the
fusion still makes on loops that are IV-V-vi in one key and VI-VII-i in the other.
