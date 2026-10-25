"""Development server: the API plus the reference demo (/demo) and client tests (/client/test/), on http://127.0.0.1:8765.
Run from the project root:  .venv/Scripts/python tools/dev_server.py  [port]"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SONGSPEC_SERVE_DEMO", "1")
os.environ.setdefault("SONGSPEC_DATA_DIR", str(ROOT / "data" / "dev"))
os.environ.setdefault("SONGSPEC_RATE_PER_MIN", "0")
sys.path.insert(0, str(ROOT / "service"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    uvicorn.run("songspec_analyzer.api.app:app", host="127.0.0.1", port=port, log_level="info")
