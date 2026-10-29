"""Small in-memory TTL cache with per-key single-flight for async fetchers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, Generic, TypeVar

V = TypeVar("V")


class TTLCache(Generic[V]):
    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = float(ttl)
        self._clock = clock
        self._entries: dict[Hashable, tuple[float, V]] = {}
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable) -> V | None:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        expires_at, value = entry
        if expires_at <= self._clock():
            self._entries.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key: Hashable, value: V, ttl: float | None = None) -> None:
        self._entries[key] = (self._clock() + (self.ttl if ttl is None else ttl), value)

    def invalidate(self, predicate: Callable[[Hashable], bool] | None = None) -> int:
        if predicate is None:
            count = len(self._entries)
            self._entries.clear()
            return count
        doomed = [k for k in self._entries if predicate(k)]
        for k in doomed:
            self._entries.pop(k, None)
        return len(doomed)

    async def get_or_fetch(self, key: Hashable, factory: Callable[[], Awaitable[V]], ttl: float | None = None) -> V:
        """Return the cached value or run `factory` once even under concurrent callers."""
        value = self.get(key)
        if value is not None:
            return value
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            value = self.get(key)
            if value is not None:
                return value
            value = await factory()
            self.set(key, value, ttl)
            return value

    def stats(self) -> dict[str, Any]:
        now = self._clock()
        live = sum(1 for exp, _ in self._entries.values() if exp > now)
        return {"entries": live, "hits": self.hits, "misses": self.misses, "ttl_seconds": self.ttl}
