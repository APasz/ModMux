from __future__ import annotations

import sys
from pathlib import Path
import unittest

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
