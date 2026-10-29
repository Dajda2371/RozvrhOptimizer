from __future__ import annotations

import asyncio

from rozvrh_mcp.cache import TTLCache


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_ttl_expiry():
    clock = Clock()
    cache: TTLCache[str] = TTLCache(ttl=10, clock=clock)
    assert cache.get("a") is None
    cache.set("a", "x")
    assert cache.get("a") == "x"
    clock.now += 9.9
    assert cache.get("a") == "x"
    clock.now += 0.2
    assert cache.get("a") is None
    assert cache.stats()["hits"] == 2 and cache.stats()["misses"] == 2


def test_invalidate_with_predicate():
    cache: TTLCache[int] = TTLCache(ttl=10)
    cache.set(("lists",), 1)
    cache.set(("permanent", "class", "C8"), 2)
    cache.set(("actual", "class", "C8"), 3)
    assert cache.invalidate(lambda k: isinstance(k, tuple) and len(k) == 3) == 2
    assert cache.get(("lists",)) == 1
    assert cache.invalidate() == 1
    assert cache.stats()["entries"] == 0


async def test_single_flight():
    cache: TTLCache[str] = TTLCache(ttl=10)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "value"

    results = await asyncio.gather(*(cache.get_or_fetch("k", factory) for _ in range(5)))
    assert results == ["value"] * 5
    assert calls == 1
