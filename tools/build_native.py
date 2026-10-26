#!/usr/bin/env python3
"""Build the Centsible binding (service/native/centsible -> service/songspec_analyzer/detune/_centsible.*.pyd|so).

  --sync   first re-copy Centsible's core/ from 'Everything Claude Needs/Centsible Source/core' (read-only source)
Needs CMake and a C++20 compiler (Windows: Visual Studio 2022; Linux: g++ 10+). Run with the project's Python.
"""
import glob
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "service" / "native" / "centsible"
SOURCE = ROOT / "Everything Claude Needs" / "Centsible Source" / "core"
DEST = ROOT / "service" / "songspec_analyzer" / "detune"
BUILD = ROOT / "build" / "centsible"


def main():
    if "--sync" in sys.argv:
        shutil.rmtree(NATIVE / "core", ignore_errors=True)
        shutil.copytree(SOURCE, NATIVE / "core")
        print(f"copied {SOURCE} -> {NATIVE / 'core'}")
    import pybind11
    cfg = ["cmake", "-S", str(NATIVE), "-B", str(BUILD), f"-Dpybind11_DIR={pybind11.get_cmake_dir()}",
           f"-DPython_EXECUTABLE={sys.executable}"]
    if sys.platform == "win32":
        cfg += ["-G", "Visual Studio 17 2022", "-A", "x64"]
    else:
        cfg += ["-DCMAKE_BUILD_TYPE=Release"]
    subprocess.run(cfg, check=True)
    subprocess.run(["cmake", "--build", str(BUILD), "--config", "Release", "-j"], check=True)
    built = [p for p in glob.glob(str(BUILD / "**" / "_centsible*"), recursive=True) if p.endswith((".pyd", ".so"))]
    if not built:
        raise SystemExit("build produced no _centsible module")
    DEST.mkdir(parents=True, exist_ok=True)
    for old in DEST.glob("_centsible*"):
        old.unlink()
    shutil.copy2(built[0], DEST)
    print(f"installed {Path(built[0]).name} -> {DEST}")


if __name__ == "__main__":
    main()
