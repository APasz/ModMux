from __future__ import annotations

import gc
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.cache import AsyncTTLCache


class TestAsyncTTLCache(unittest.IsolatedAsyncioTestCase):
    async def test_get_or_set_caches_value(self) -> None:
        cache = AsyncTTLCache(ttl=None, maxsize=4)
        calls = 0

        async def factory() -> str:
            nonlocal calls
            calls += 1
            return "value"

        first = await cache.get_or_set("key", factory)
        second = await cache.get_or_set("key", factory)

        self.assertEqual(first, "value")
        self.assertEqual(second, "value")
        self.assertEqual(calls, 1)

    async def test_maxsize_eviction(self) -> None:
        cache = AsyncTTLCache(ttl=None, maxsize=1)
        await cache.set("first", "a")
        await cache.set("second", "b")

        self.assertIsNone(await cache.get("first"))
        self.assertEqual(await cache.get("second"), "b")

    async def test_replacing_existing_value_does_not_evict_another_key(self) -> None:
        cache = AsyncTTLCache(ttl=None, maxsize=2)
        await cache.set("first", "a")
        await cache.set("second", "b")
        await cache.set("second", "updated")

        self.assertEqual(await cache.get("first"), "a")
        self.assertEqual(await cache.get("second"), "updated")

    def test_maxsize_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            AsyncTTLCache(maxsize=0)

    async def test_get_or_set_caches_none_values(self) -> None:
        cache = AsyncTTLCache(ttl=None, maxsize=4)
        calls = 0

        async def factory() -> None:
            nonlocal calls
            calls += 1
            return None

        first = await cache.get_or_set("key", factory)
        second = await cache.get_or_set("key", factory)

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(calls, 1)

    async def test_lock_table_does_not_grow_with_evicted_keys(self) -> None:
        cache = AsyncTTLCache(ttl=None, maxsize=1)

        async def factory(value: int) -> int:
            return value

        for index in range(5):
            await cache.get_or_set(f"key-{index}", lambda index=index: factory(index))

        gc.collect()

        self.assertEqual(len(cache._data), 1)
        self.assertLessEqual(len(cache._locks), 1)
