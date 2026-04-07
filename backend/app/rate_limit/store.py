import threading
import time

import redis

from app.core.config import get_settings


settings = get_settings()


class InMemoryRateLimitStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[str, int]] = {}

    def hit(self, *, key: str, limit: int, window_seconds: int) -> dict:
        now = int(time.time())
        with self._lock:
            state = self._counters.get(key)
            if state is None or state["reset_at"] <= now:
                state = {"count": 0, "reset_at": now + window_seconds}
                self._counters[key] = state

            state["count"] += 1
            allowed = state["count"] <= limit
            remaining = max(limit - state["count"], 0)
            retry_after = max(state["reset_at"] - now, 1)

        return {"allowed": allowed, "remaining": remaining, "retry_after": retry_after}


class RedisRateLimitStore:
    def __init__(self, url: str) -> None:
        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.client.ping()

    def hit(self, *, key: str, limit: int, window_seconds: int) -> dict:
        pipe = self.client.pipeline()
        pipe.incr(key, 1)
        pipe.ttl(key)
        current, ttl = pipe.execute()
        if int(current) == 1 or int(ttl) < 0:
            self.client.expire(key, window_seconds)
            ttl = window_seconds

        current = int(current)
        ttl = int(ttl if ttl and ttl > 0 else window_seconds)
        return {
            "allowed": current <= limit,
            "remaining": max(limit - current, 0),
            "retry_after": max(ttl, 1),
        }


def _build_store():
    if settings.redis_url:
        try:
            return RedisRateLimitStore(settings.redis_url)
        except Exception:
            pass
    return InMemoryRateLimitStore()


rate_limit_store = _build_store()

