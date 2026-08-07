"""Caching helpers for async provider lookups."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any
from weakref import WeakValueDictionary


class AsyncTTLCache:
    """Simple async-aware TTL cache with per-key locks."""

    def __init__(self, ttl: float | None = 60, maxsize: int = 512) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self.ttl = ttl
        self.maxsize = maxsize
        self._data: dict[Any, tuple[float | None, Any]] = {}
        self._locks: WeakValueDictionary[Any, asyncio.Lock] = WeakValueDictionary()

    def _lookup(self, key: Any) -> tuple[bool, Any]:
        entry = self._data.get(key)
        if entry is None:
            return False, None
        expires_at, value = entry
        if expires_at is None:
            return True, value
        now = time.monotonic()
        if expires_at > now:
            return True, value
        self._data.pop(key, None)
        return False, None

    def _lock_for(self, key: Any) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is not None:
            return lock
        lock = asyncio.Lock()
        self._locks[key] = lock
        return lock

    async def get(self, key: Any) -> Any | None:
        """Retrieve a cached value if it has not expired.

        Args;
            key: Cache key to look up.

        Returns;
            The cached value if present and valid, otherwise `None`.
        """
        found, value = self._lookup(key)
        if not found:
            return None
        return value

    async def set(self, key: Any, value: Any, *, ttl: float | None = None) -> None:
        """Store a value in the cache with an optional TTL override.

        Args;
            key: Cache key to store.
            value: Value to store.
            ttl: Optional override for the default TTL.
        """
        effective_ttl = self.ttl if ttl is None else ttl
        expires_at = None if effective_ttl is None else time.monotonic() + effective_ttl
        if key not in self._data and len(self._data) >= self.maxsize:
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
        found, value = self._lookup(key)
        if found:
            return value

        async with self._lock_for(key):
            found, value = self._lookup(key)
            if found:
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
