#!/usr/bin/env python3
"""Put Rubber Band's command-line program into service/vendor/rubberband/, the way BPM Fixer's own tools/fetch_vendor.py does:

  rubberband-r3.exe  (conda-forge's rubberband-program-r3.exe: offline R3 engine + time map, the stretcher bpmfix.py prefers)
  sndfile.dll        (the self-contained libsndfile that ships with the soundfile package; rubberband-r3.exe needs it)
  COPYING            (Rubber Band's licence, GPL-2.0-or-later; source: https://breakfastquay.com/rubberband/)

Fix BPM (tempo/fixbpm.py) runs it as a separate program. Without it the engine falls back to pylibrb (real-time R3), which is
what BPM Fixer itself does when vendor/ is empty, and is lower quality.
Run:  .venv/Scripts/python tools/fetch_vendor.py      (needs:  python -m pip install zstandard)
"""
import io
import json
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "service" / "vendor" / "rubberband"
CONDA = "https://conda.anaconda.org/conda-forge/win-64/"
WANTED = {"rubberband-program-r3.exe": "rubberband-r3.exe", "COPYING": "COPYING"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "songspec-analyzer-build"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def latest(name, repodata):
    best = None
    for key in ("packages.conda", "packages"):
        for fn, meta in repodata.get(key, {}).items():
            if meta["name"] == name and (best is None or meta.get("timestamp", 0) > best[1].get("timestamp", 0)):
                best = (fn, meta)
    if not best:
        raise RuntimeError(f"{name} not found on conda-forge")
    return best[0]


def members(blob, fn):
    """Yield (path, bytes) for every file in a .conda or .tar.bz2 package."""
    if fn.endswith(".tar.bz2"):
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:bz2") as t:
            for m in t.getmembers():
                if m.isfile():
                    yield m.name, t.extractfile(m).read()
        return
    import zstandard
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for inner in z.namelist():
            if inner.endswith(".tar.zst"):                    # pkg-* (files) and info-* (licence)
                raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(z.read(inner)))
                with tarfile.open(fileobj=raw, mode="r|") as t:
                    for m in t:
                        if m.isfile():
                            yield m.name, t.extractfile(m).read()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    print("Reading the conda-forge index ...")
    repodata = json.loads(get(CONDA + "current_repodata.json"))
    fn = latest("rubberband", repodata)
    print(f"  {fn}")
    found = set()
    for path, data in members(get(CONDA + fn), fn):
        base = os.path.basename(path)
        if base in WANTED and base not in found:
            (DEST / WANTED[base]).write_bytes(data)
            found.add(base)
    if "rubberband-program-r3.exe" not in found:
        raise RuntimeError(f"{fn} has no rubberband-program-r3.exe")
    try:
        import _soundfile_data
        src = Path(_soundfile_data.__file__).parent / "libsndfile_x64.dll"
    except Exception:
        src = None
    if src and src.is_file():
        shutil.copy(src, DEST / "sndfile.dll")
    else:
        print("  NOTE: run this with the Windows Python that has 'soundfile' installed to get sndfile.dll")
        return 1
    (DEST / "SOURCE.txt").write_text(f"conda-forge {fn}\nRubber Band Library, GPL-2.0-or-later: https://breakfastquay.com/rubberband/\n"
                                     "sndfile.dll: libsndfile (LGPL-2.1) from the soundfile package\n", encoding="utf-8")
    print(f"wrote {DEST}: {', '.join(sorted(p.name for p in DEST.iterdir()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
