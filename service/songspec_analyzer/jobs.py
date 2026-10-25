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

    def submit(self, aid):
        self.pool.submit(run_job, aid, self.store, self.settings)

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


class RedisQueue:
    def __init__(self, store: Store, settings: Settings):
        from redis import Redis
        from rq import Queue
        self.q = Queue("songspec", connection=Redis.from_url(settings.redis_url), default_timeout=1800)

    def submit(self, aid):
        self.q.enqueue("songspec_analyzer.jobs.rq_entry", aid, job_id=aid)

    def shutdown(self):
        pass


def rq_entry(aid):
    """What an RQ worker runs: its own store / settings from the same environment."""
    from .config import get_settings
    s = get_settings()
    run_job(aid, Store(s.db_path), s)


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
        for aid, path in self.store.expired_audio(cutoff):
            try:
                os.remove(path)
            except OSError:
                pass
            self.store.update(aid, audio_path=None)
            n += 1
        # stray files without a row (crash between save and insert)
        for p in self.settings.uploads_dir.glob("*"):
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
