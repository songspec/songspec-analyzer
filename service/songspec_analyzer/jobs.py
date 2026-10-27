"""Job queue. Default: an in-process worker pool (zero infrastructure). QUEUE=redis: RQ workers in separate processes
(they must share the database and the uploads folder; start them with `rq worker songspec`)."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .config import Settings
from .pipeline import run_job
from .store import Store


class InlineQueue:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.workers), thread_name_prefix="analysis")

    def submit(self, aid, kind="analysis"):
        self.pool.submit(JOBS[kind], aid, self.store, self.settings)

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


class RedisQueue:
    def __init__(self, store: Store, settings: Settings):
        from redis import Redis
        from rq import Queue
        self.q = Queue("songspec", connection=Redis.from_url(settings.redis_url), default_timeout=1800)

    def submit(self, aid, kind="analysis"):
        self.q.enqueue("songspec_analyzer.jobs.rq_entry", aid, kind, job_id=aid)

    def shutdown(self):
        pass


def rq_entry(aid, kind="analysis"):
    """What an RQ worker runs: its own store / settings from the same environment."""
    from .config import get_settings
    s = get_settings()
    JOBS[kind](aid, Store(s.db_path), s)


def run_fix_job(aid, store, settings):
    """Fix BPM: BPM Fixer's process() on the uploaded file; the output is kept for the requester until the audio TTL."""
    from .tempo.fixbpm import D, run
    row = store.get(aid)
    store.update(aid, status="running")
    lines = []
    try:
        o = row["options"]
        out, info, lines = run(row["audio_path"], settings.fixed_dir, settings.models_dir, target_bpm=o.get("target_bpm"),
                               signature=o.get("signature", D["signature"]), bpm_mode=o.get("bpm_mode", D["bpm_mode"]),
                               fmt=o.get("format", "mp3"), keep_edges=o.get("keep_edges", D["keep_edges"]),
                               loudness=o.get("loudness", D["loudness"]), verify=o.get("verify", D["verify"]),
                               log=lambda m: store.merge_result(aid, {"progress": {"stage": "fix", "message": m.strip()[:200]}}))
        rep = info["report"]
        store.update(aid, status="done", output_path=out, result={
            "fix": {"target_bpm": info["target"], "signature": info["signature"], "file_name": os.path.basename(out).split("_", 1)[-1],
                    "stretch": {"slowest": round(rep["max_up"], 3), "fastest": round(rep["max_down"], 3),
                                "pct_over_20": round(rep["pct_over"], 1),
                                "heavy": [[round(a, 2), round(b, 2), round(r, 3)] for a, b, r in rep["ranges"]]},
                    "verify": None if not info.get("align") else
                    {"on_grid_pct": round(info["align"][0], 1), "median_ms": round(info["align"][1], 1),
                     "with_hit_pct": round(info["align"][2], 1)},
                    "irregularity_before_pct": round(info["irr_before"], 2), "log": lines[-60:]},
            "progress": None})
    except Exception as e:
        store.update(aid, status="error", error=str(e)[:300] if isinstance(e, RuntimeError) else
                     f"Fix BPM failed ({type(e).__name__}).", result={"fix": {"log": lines[-60:]}})
    finally:
        p = store.get(aid)["audio_path"]
        if p:
            try:
                os.remove(p)
            except OSError:
                pass
            store.update(aid, audio_path=None)


JOBS = {"analysis": run_job, "fix": run_fix_job}


def make_queue(store: Store, settings: Settings):
    return RedisQueue(store, settings) if settings.queue == "redis" else InlineQueue(store, settings)


class AudioSweeper(threading.Thread):
    """Safety net for the 24 h audio TTL: deletes any upload that outlived it (normally a job deletes its own audio when it ends)."""

    def __init__(self, store: Store, settings: Settings, every=600):
        super().__init__(daemon=True, name="audio-sweeper")
        self.store, self.settings, self.every = store, settings, every
        self._stop = threading.Event()

    def sweep_once(self):
        cutoff = time.time() - self.settings.audio_ttl_s
        n = 0
        for aid, path, column in self.store.expired_audio(cutoff):
            try:
                os.remove(path)
            except OSError:
                pass
            self.store.update(aid, **{column: None})
            n += 1
        # stray files without a row (crash between save and insert)
        for p in list(self.settings.uploads_dir.glob("*")) + list(self.settings.fixed_dir.glob("*")):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
            except OSError:
                pass
        return n

    def run(self):
        while not self._stop.wait(self.every):
            self.sweep_once()

    def stop(self):
        self._stop.set()
