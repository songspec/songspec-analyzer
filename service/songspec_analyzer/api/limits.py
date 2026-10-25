"""Per-client token bucket for starting analyses (reading results is not limited)."""
import threading
import time


class RateLimiter:
    def __init__(self, per_min: float, burst: int):
        self.rate = per_min / 60.0
        self.burst = float(burst)
        self._b = {}
        self._lock = threading.Lock()

    def allow(self, key: str):
        """(allowed, seconds until the next token)."""
        if self.rate <= 0:
            return True, 0.0
        now = time.monotonic()
        with self._lock:
            tokens, last = self._b.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                self._b[key] = (tokens - 1.0, now)
                return True, 0.0
            self._b[key] = (tokens, now)
            return False, (1.0 - tokens) / self.rate
