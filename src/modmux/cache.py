"""Caching helpers for async provider lookups."""

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any


class AsyncTTLCache:
    """Simple async-aware TTL cache with per-key locks."""

    def __init__(self, ttl: float | None = 60, maxsize: int = 512) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._data: dict[Any, tuple[float | None, Any]] = {}
        self._locks = defaultdict(asyncio.Lock)

    async def get(self, key: Any) -> Any | None:
        """Retrieve a cached value if it has not expired.

        Args;
            key: Cache key to look up.

        Returns;
            The cached value if present and valid, otherwise `None`.
        """
        entry = self._data.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if expires_at is None:
            return value
        now = time.monotonic()
        if expires_at > now:
            return value
        self._data.pop(key, None)
        return None

    async def set(self, key: Any, value: Any, *, ttl: float | None = None) -> None:
        """Store a value in the cache with an optional TTL override.

        Args;
            key: Cache key to store.
            value: Value to store.
            ttl: Optional override for the default TTL.
        """
        effective_ttl = self.ttl if ttl is None else ttl
        expires_at = None if effective_ttl is None else time.monotonic() + effective_ttl
        if len(self._data) >= self.maxsize:
            self._data.pop(next(iter(self._data)))
        self._data[key] = (expires_at, value)

    async def get_or_set(
        self, key: Any, coro_factory: Callable[[], Awaitable[Any]], *, ttl: float | None = None
    ) -> Any:
        """Retrieve a value or populate it with a coroutine factory.

        Args;
            key: Cache key to look up.
            coro_factory: Coroutine factory that returns the value on a miss.
            ttl: Optional override for the default TTL when populating.

        Returns;
            The cached or newly computed value.
        """
        value = await self.get(key)
        if value is not None:
            return value

        async with self._locks[key]:
            value = await self.get(key)
            if value is not None:
                return value
            value = await coro_factory()
            await self.set(key, value, ttl=ttl)
            return value


HOUR = 3_600


class ModioLookupCache:
    """Dedicated caches for mod.io slug/id lookups."""

    def __init__(self, *, slug_ttl: float | None = 24 * HOUR, maxsize: int = 2_048) -> None:
        self.game_slug_to_id = AsyncTTLCache(ttl=slug_ttl, maxsize=maxsize)
        self.game_id_to_slug = AsyncTTLCache(ttl=slug_ttl, maxsize=maxsize)
        self.mod_slug_to_id = AsyncTTLCache(ttl=slug_ttl, maxsize=maxsize)
        self.mod_id_to_slug = AsyncTTLCache(ttl=slug_ttl, maxsize=maxsize)
