SongSpec Analyzer - test build
==============================

What it is: the analysis engine for songspec.org (key, BPM, chords, detune) with a local test page.
Everything runs on your PC; songs are never uploaded anywhere else.

START
  Double-click  "Start SongSpec Analyzer.bat"
  - First run: installs Python libraries into this folder (about 1 GB download, a few minutes).
    If Python 3.12 is not installed it offers to install a private copy (uv, no admin rights).
  - Then the test page opens in your browser. Keep the black window open while testing; close it to stop.
  Needs: Windows 10/11 64-bit, an internet connection for the first run, ~3 GB of disk.

TEST PAGE
  - Drop songs on the box (several at once are analysed one after another; ~20-60 s per song on a CPU).
  - Key / BPM / Detune / Time signature fill in as they are measured.
  - Play the song: the current chord is shown live; "Click track" clicks on the detected beats (are they right?);
    "Hear at A440" plays it corrected by its detune.
  - "This is wrong..." saves your correction (that is the most useful thing to do!).
  - "Copy tracker row" copies  Song <tab> BPM <tab> Key <tab> Note  for the Google Sheet.
  - Fix BPM (bottom): makes a song run at one constant tempo and gives you the file.
  - Mashup helper (bottom): pick two analysed songs, it says how to pitch / stretch B onto A.

KNOWN WEAK SPOTS
  - Relative keys (C Major vs A Minor, Db Major vs Bb Minor) on loops that fit both.
  - 6/8 songs are often read as 4/4 or 3/4.
  - "BPM inconsistent / played live" is only flagged for clear drift.

GPU: with an NVIDIA card, set SONGSPEC_TORCH=cu130 before the FIRST start to install the GPU version (bigger download, faster).
Problems: delete the .venv folder and start again; send the black window's messages to the developer.
