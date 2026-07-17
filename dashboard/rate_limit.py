"""In-memory per-client rate limits; intentionally lightweight for one API process."""
from __future__ import annotations
import threading, time
from collections import defaultdict, deque

class RateLimiter:
    def __init__(self) -> None: self.events=defaultdict(deque); self.lock=threading.Lock()
    def allow(self, bucket: str, client: str, limit: int, window_seconds: int) -> tuple[bool,int]:
        now=time.monotonic(); key=(bucket,client)
        with self.lock:
            q=self.events[key]
            while q and q[0] <= now-window_seconds: q.popleft()
            if len(q) >= limit: return False,max(1,int(window_seconds-(now-q[0])))
            q.append(now); return True,0

