"""Fixed-window per-user rate limiting to stop a runaway Cliq loop or an
abusive user from hammering the Freshservice API (and burning its API quota
for the whole organisation).

In-memory by default (fine for a single replica, matches SessionStore's
default). Swap in a Redis-backed counter if you scale to multiple replicas.
"""
import time
from collections import defaultdict, deque


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f'Rate limit exceeded, retry after {retry_after:.1f}s')


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.time()
        window_start = now - self.window_seconds
        hits = self._hits[key]
        while hits and hits[0] < window_start:
            hits.popleft()
        if len(hits) >= self.max_requests:
            retry_after = self.window_seconds - (now - hits[0])
            raise RateLimitExceeded(max(retry_after, 1.0))
        hits.append(now)
