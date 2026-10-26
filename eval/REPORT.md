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
