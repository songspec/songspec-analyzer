"""python -m songspec_analyzer song.mp3 [more.flac ...] [--high-accuracy] [--signature 6/8] [--out result.json]

Prints the analysis as JSON (the same document the HTTP API returns). Several files print a JSON list. Exit code 1 if any failed."""
import argparse
import contextlib
import json
import sys

from .analyze import analyze_file


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m songspec_analyzer", description="Key, BPM, chords and detune of audio files.")
    ap.add_argument("files", nargs="+", help="audio files (anything ffmpeg can decode)")
    ap.add_argument("--high-accuracy", action="store_true", help="slower, more thorough beat analysis")
    ap.add_argument("--signature", help="force the time signature, e.g. 4/4, 6/8 (default: detected)")
    ap.add_argument("--data-dir", help="keep the job database here (caches results by audio fingerprint)")
    ap.add_argument("--out", help="write the JSON here instead of printing it")
    a = ap.parse_args(argv)
    options = {"high_accuracy": a.high_accuracy or None, "signature": a.signature}
    results = []
    for f in a.files:
        print(f"analysing {f} ...", file=sys.stderr, flush=True)
        try:
            with contextlib.redirect_stdout(sys.stderr):          # some model code prints progress; stdout stays pure JSON
                results.append(analyze_file(f, options, a.data_dir))
        except Exception as e:
            results.append({"file": f, "status": "error", "error": f"{type(e).__name__}: {e}"})
    doc = results[0] if len(results) == 1 else results
    text = json.dumps(doc, indent=1, ensure_ascii=False)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 1 if any(r.get("status") != "done" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
