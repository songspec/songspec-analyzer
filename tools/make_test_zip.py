#!/usr/bin/env python3
"""Build dist/SongSpec-Analyzer-test.zip: the service, the test page, the bundled models and the prebuilt Centsible module, with
"Start SongSpec Analyzer.bat" at the top. Libraries are NOT bundled: the launcher installs them on the tester's PC (first run).
Run:  .venv/Scripts/python tools/make_test_zip.py"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "SongSpec-Analyzer-test.zip"
TOP = "SongSpec Analyzer"
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "tests", "native", "songspec_analyzer.egg-info"}

INCLUDE = [                                   # (source, path inside the zip)
    ("tools/Start SongSpec Analyzer.bat", "Start SongSpec Analyzer.bat"),
    ("tools/README-TESTERS.txt", "README.txt"),
    ("LICENSES.md", "LICENSES.md"),
    ("tools/launch.py", "tools/launch.py"),
    ("service", "service"),
    ("client", "client"),
    ("demo", "demo"),
    ("contract", "contract"),
    ("third_party/skey/skey", "third_party/skey/skey"),
    ("third_party/skey/LICENSE", "third_party/skey/LICENSE"),
    ("third_party/btc/btc_model.py", "third_party/btc/btc_model.py"),
    ("third_party/btc/utils", "third_party/btc/utils"),
    ("third_party/btc/run_config.yaml", "third_party/btc/run_config.yaml"),
    ("third_party/btc/test/btc_model_large_voca.pt", "third_party/btc/test/btc_model_large_voca.pt"),
    ("third_party/btc/LICENSE", "third_party/btc/LICENSE"),
]


def files(src):
    p = ROOT / src
    if p.is_file():
        yield p
        return
    for f in sorted(p.rglob("*")):
        if f.is_file() and not (set(f.relative_to(ROOT).parts) & SKIP_DIRS):
            yield f


def main():
    OUT.parent.mkdir(exist_ok=True)
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for src, dst in INCLUDE:
            base = ROOT / src
            for f in files(src):
                rel = dst if base.is_file() else f"{dst}/{f.relative_to(base).as_posix()}"
                z.write(f, f"{TOP}/{rel}")
                n += 1
    print(f"wrote {OUT} ({n} files, {OUT.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
