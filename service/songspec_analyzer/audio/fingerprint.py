"""Cache keys for audio: the exact file hash, and a Chromaprint fingerprint (via the fpcalc tool, when installed) so the same song in
another file or encoding finds the cached analysis."""
import hashlib
import json
import shutil
import subprocess


def file_sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def chromaprint(path, length=120):
    """(fingerprint, duration) from fpcalc, or (None, None) when fpcalc is not available or fails.
    Fingerprints the first `length` seconds, like AcoustID does."""
    fp = shutil.which("fpcalc")
    if not fp:
        return None, None
    try:
        r = subprocess.run([fp, "-json", "-length", str(length), path], capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout)
        return d.get("fingerprint"), float(d.get("duration") or 0) or None
    except Exception:
        return None, None
