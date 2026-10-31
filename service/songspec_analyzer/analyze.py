"""One call, no server: an audio file in, the analysis out. The result is exactly what the HTTP API returns for a finished analysis
(contract/analysis.schema.json): key, tempo (BPM, beats, sections), chords, detune, modes, warnings.

    from songspec_analyzer import analyze_file
    result = analyze_file("song.mp3")
    result["key"]["display"]["key"], result["tempo"]["bpm"], result["detune"]["cents"], result["chords"]["segments"]

    python -m songspec_analyzer song.mp3 [--high-accuracy] [--out result.json]

The caller's file is never touched: a copy is analysed (the pipeline deletes its upload when it ends). Without data_dir, the job
database lives in a temporary folder and is removed afterwards; pass data_dir to keep it (results are then cached by audio
fingerprint, so the same song comes back instantly).
"""
import shutil
import tempfile
from pathlib import Path

OPTIONS = {"high_accuracy": bool, "signature": str, "hooktheory": bool}     # the same options as POST /v1/analyses


def analyze_file(path, options=None, data_dir=None):
    from .api.app import public_view
    from .config import Settings
    from .pipeline import run_job
    from .store import Store

    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"no such audio file: {src}")
    opts = {k: t(v) for k, t in OPTIONS.items() for kk, v in (options or {}).items() if kk == k and v is not None}
    with tempfile.TemporaryDirectory(prefix="songspec-") as td:
        settings = Settings(data_dir=Path(data_dir).resolve() if data_dir else Path(td))
        settings.ensure_dirs()
        copy = Path(tempfile.mkdtemp(dir=settings.uploads_dir)) / src.name
        shutil.copyfile(src, copy)
        store = Store(settings.db_path)
        try:
            aid = store.create("upload", file_name=src.name, options=opts, audio_path=str(copy))
            run_job(aid, store, settings)
            return public_view(store.get(aid), store)
        finally:
            store.close()
            shutil.rmtree(copy.parent, ignore_errors=True)
