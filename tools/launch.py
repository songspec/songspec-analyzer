#!/usr/bin/env python3
"""One-click launcher for testing SongSpec Analyzer on a Windows PC (run by "Start SongSpec Analyzer.bat").

First run: creates .venv next to this folder with the Python that runs this script (3.12), installs the analysis stack (CPU PyTorch;
NVIDIA GPU users can set SONGSPEC_TORCH=cu130 before the first run), then on every run starts the server on a free port and opens the
test page in the browser. Close the window (or Ctrl+C) to stop. Data (the job database) goes to .\\data; uploaded audio is deleted after
each analysis.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".songspec-installed"
TORCH = os.environ.get("SONGSPEC_TORCH", "cpu")          # cpu | cu130 | cu128 ...
PACKAGES = ["-r", str(ROOT / "service" / "requirements.txt"), "pybind11", "nnAudio==0.3.3", "pyyaml", "tqdm", "pylibrb==0.1.2",
            "imageio-ffmpeg", "-e", str(ROOT / "service")]
DEPS_VERSION = "2"        # bump when PACKAGES change: an older install then updates itself on the next start


def say(msg):
    print(f"[SongSpec] {msg}", flush=True)


def run(cmd):
    r = subprocess.run(cmd)
    if r.returncode != 0:
        say(f"FAILED: {' '.join(map(str, cmd))[:200]}")
        input("Press Enter to close.")
        sys.exit(1)


def install():
    if sys.version_info[:2] != (3, 12):
        say(f"This needs Python 3.12 (this is {sys.version.split()[0]}).")
        input("Press Enter to close.")
        sys.exit(1)
    if not VPY.exists():
        say("Creating the Python environment (.venv) ...")
        run([sys.executable, "-m", "venv", str(VENV)])
    say("Installing the analysis libraries (first run only; about 1 GB to download, a few minutes) ...")
    run([str(VPY), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    run([str(VPY), "-m", "pip", "install", "torch==2.14.1", "torchaudio==2.11.0", "--index-url",
         f"https://download.pytorch.org/whl/{TORCH}", "-q"])
    run([str(VPY), "-m", "pip", "install", "-q"] + PACKAGES)
    STAMP.write_text(DEPS_VERSION, encoding="utf-8")


def free_port(start=8765):
    for p in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("no free port between 8765 and 8814")


def check():
    """The pieces that are bundled rather than installed: say plainly if one is missing."""
    missing = []
    if not (ROOT / "service" / "models" / "beat_this-final0.ckpt").exists():
        missing.append("service/models/beat_this-final0.ckpt (beats and tempo)")
    if not list((ROOT / "service" / "songspec_analyzer" / "detune").glob("_centsible*")):
        missing.append("the Centsible module (detune)")
    for f, what in (("third_party/skey/skey/models/skey.pt", "S-KEY (key)"), ("third_party/btc/test/btc_model_large_voca.pt", "BTC (chords)")):
        if not (ROOT / f).exists():
            missing.append(f"{f} ({what})")
    if not (ROOT / "service" / "vendor" / "rubberband" / "rubberband-r3.exe").exists():
        missing.append("service/vendor/rubberband/rubberband-r3.exe (Fix BPM falls back to a lower-quality stretcher)")
    for m in missing:
        say(f"WARNING: missing {m}")


def main():
    os.chdir(ROOT)
    if not STAMP.exists() or STAMP.read_text(encoding="utf-8").strip() != DEPS_VERSION:
        install()
    check()
    port = free_port()
    env = dict(os.environ, SONGSPEC_SERVE_DEMO="1", SONGSPEC_DATA_DIR=str(ROOT / "data"), SONGSPEC_RATE_PER_MIN="0",
               SONGSPEC_CORS_ORIGINS=f"http://127.0.0.1:{port}")
    say(f"Starting the analyzer on http://127.0.0.1:{port} ...")
    server = subprocess.Popen([str(VPY), "-m", "uvicorn", "songspec_analyzer.api.app:app", "--app-dir", "service",
                               "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        if server.poll() is not None:
            say("The server stopped while starting (see the messages above).")
            input("Press Enter to close.")
            sys.exit(1)
        try:
            v = json.load(urllib.request.urlopen(url + "/v1/version", timeout=2))
            break
        except Exception:
            time.sleep(1)
    else:
        say("The server did not answer within 2 minutes.")
    say(f"Ready (engine {v.get('engine', '?')}). Opening {url}/demo/ - keep this window open while testing; close it to stop.")
    if not os.environ.get("SONGSPEC_NO_BROWSER"):
        webbrowser.open(url + "/demo/")
    try:
        server.wait()
    except KeyboardInterrupt:
        server.terminate()


if __name__ == "__main__":
    main()
