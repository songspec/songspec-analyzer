"""Job and result store (SQLite by default). Holds derived data only: audio lives in data/uploads until the analysis ends.

One table of analyses (status, per-stage state, the result JSON as it grows) and one of user corrections.
Cache keys: the exact file hash, the Chromaprint fingerprint + duration, and the ISRC (for link lookups).
"""
import json
import sqlite3
import threading
import time
import uuid

from . import ENGINE_VERSION

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,              -- queued | running | done | error
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    engine_version TEXT NOT NULL,
    source TEXT,                       -- upload | spotify-preview | apple-preview | deezer-preview
    file_name TEXT,
    file_sha256 TEXT,
    fingerprint TEXT,
    duration REAL,
    isrc TEXT,
    options TEXT,
    stages TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    audio_path TEXT,
    cached_from TEXT
);
CREATE INDEX IF NOT EXISTS ix_sha ON analyses(file_sha256);
CREATE INDEX IF NOT EXISTS ix_fp ON analyses(fingerprint);
CREATE INDEX IF NOT EXISTS ix_isrc ON analyses(isrc);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL,
    fingerprint TEXT,
    isrc TEXT,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    comment TEXT,
    user TEXT,
    created_at REAL NOT NULL
);
"""

_JSON_COLS = ("options", "stages", "result")


class Store:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(analyses)")}
        for col in ("norm_title", "norm_artist", "output_path"):   # added later (links, Fix BPM); older databases get them here
            if col not in cols:
                self._db.execute(f"ALTER TABLE analyses ADD COLUMN {col} TEXT")
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_meta ON analyses(norm_artist, norm_title)")
        self._db.commit()

    # ---------------------------------------------------------------- analyses
    def create(self, source, file_name=None, options=None, audio_path=None, isrc=None):
        now = time.time()
        aid = uuid.uuid4().hex
        with self._lock:
            self._db.execute(
                "INSERT INTO analyses (id, status, created_at, updated_at, engine_version, source, file_name, options, "
                "audio_path, isrc) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)",
                (aid, now, now, ENGINE_VERSION, source, file_name, json.dumps(options or {}), audio_path, isrc))
            self._db.commit()
        return aid

    def get(self, aid):
        with self._lock:
            row = self._db.execute("SELECT * FROM analyses WHERE id = ?", (aid,)).fetchone()
        return self._row(row)

    def update(self, aid, **fields):
        if not fields:
            return
        fields["updated_at"] = time.time()
        for k in _JSON_COLS:
            if k in fields:
                fields[k] = json.dumps(fields[k])
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE analyses SET {cols} WHERE id = ?", (*fields.values(), aid))
            self._db.commit()

    def merge_result(self, aid, part, stage=None, stage_state="done"):
        """Merge one stage's piece into the stored result (top-level keys are replaced, which is how stages own their sections)."""
        with self._lock:
            row = self.get(aid)
            res = row["result"]
            res.update(part)
            stages = row["stages"]
            if stage:
                stages[stage] = stage_state
            self.update(aid, result=res, stages=stages)
            return res, stages

    def set_stage(self, aid, stage, state):
        with self._lock:
            stages = self.get(aid)["stages"]
            stages[stage] = state
            self.update(aid, stages=stages)
            return stages

    def find_cached(self, file_sha256=None, fingerprint=None, duration=None, isrc=None, exclude=None):
        """A finished analysis of the same audio by this engine version: exact bytes first, then fingerprint + duration (+-1 s), then ISRC.
        Full uploads beat previews for the ISRC lookup."""
        q = ("SELECT * FROM analyses WHERE status = 'done' AND engine_version = ? AND id != ? AND cached_from IS NULL "
             "AND {} ORDER BY (source = 'upload') DESC, updated_at DESC LIMIT 1")
        tries = []
        if file_sha256:
            tries.append(("file_sha256 = ?", (file_sha256,)))
        if fingerprint and duration:
            tries.append(("fingerprint = ? AND ABS(duration - ?) <= 1.0", (fingerprint, duration)))
        if isrc:
            tries.append(("isrc = ?", (isrc,)))
        with self._lock:
            for where, args in tries:
                row = self._db.execute(q.format(where), (ENGINE_VERSION, exclude or "", *args)).fetchone()
                if row:
                    return self._row(row)
        return None

    def find_by_meta(self, isrc=None, norm_title=None, norm_artist=None, duration=None):
        """A finished full-file analysis of the song a link points to: by ISRC, else by normalised artist + title (+ duration within
        3 s when both are known). Only uploads count (never another link's result)."""
        q = ("SELECT * FROM analyses WHERE status = 'done' AND engine_version = ? AND source = 'upload' AND {} "
             "ORDER BY updated_at DESC LIMIT 1")
        with self._lock:
            if isrc:
                row = self._db.execute(q.format("isrc = ?"), (ENGINE_VERSION, isrc)).fetchone()
                if row:
                    return self._row(row)
            if norm_title and norm_artist:
                where, args = "norm_title = ? AND norm_artist = ?", [norm_title, norm_artist]
                if duration:
                    where += " AND (duration IS NULL OR ABS(duration - ?) <= 3.0)"
                    args.append(duration)
                row = self._db.execute(q.format(where), (ENGINE_VERSION, *args)).fetchone()
                if row:
                    return self._row(row)
        return None

    def expired_audio(self, older_than):
        with self._lock:
            rows = self._db.execute("SELECT id, audio_path FROM analyses WHERE audio_path IS NOT NULL AND created_at < ?",
                                    (older_than,)).fetchall()
            outs = self._db.execute("SELECT id, output_path FROM analyses WHERE output_path IS NOT NULL AND created_at < ?",
                                    (older_than,)).fetchall()
        return [(r["id"], r["audio_path"], "audio_path") for r in rows] + [(r["id"], r["output_path"], "output_path") for r in outs]

    def stale_jobs(self):
        """Jobs left queued/running by a process that died (restart recovery)."""
        with self._lock:
            rows = self._db.execute("SELECT id FROM analyses WHERE status IN ('queued', 'running')").fetchall()
        return [r["id"] for r in rows]

    # ---------------------------------------------------------------- feedback
    def add_feedback(self, aid, field, value, comment=None, user=None):
        row = self.get(aid)
        with self._lock:
            self._db.execute(
                "INSERT INTO feedback (analysis_id, fingerprint, isrc, field, value, comment, user, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (aid, row["fingerprint"] if row else None, row["isrc"] if row else None, field, value, comment, user, time.time()))
            self._db.commit()

    def feedback(self):
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM feedback ORDER BY id").fetchall()]

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _row(row):
        if row is None:
            return None
        d = dict(row)
        for k in _JSON_COLS:
            d[k] = json.loads(d[k]) if d.get(k) else {}
        return d
