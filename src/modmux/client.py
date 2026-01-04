"""Shared ModMux client utilities."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from . import providers
from ._log import get_logger
from .cache import ModioLookupCache
from .models import Mod, ModID, Provider, ProviderCreds
from .providers._base import ProviderClient
from .utils.discovery import REGISTRY, load_providers

log = get_logger()


class Muxer:
    """Coordinator for provider clients with shared HTTP and credentials."""

    def __init__(
        self,
        *,
        creds: dict[Provider, ProviderCreds | dict | None] | None = None,
        cache: dict[Provider, object | None] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._external_http = http
        self._http = http or httpx.AsyncClient(timeout=30)
        self.tokens = creds or {}
        self._cache = cache or {}

        load_providers(providers)
        self.providers = self._init_providers()

    def _init_providers(self) -> dict[Provider, ProviderClient]:
        providers: dict[Provider, ProviderClient] = {}
        for provider, cls in REGISTRY.items():
            creds = self._coerce_creds(provider, cls)
            cache = self._cache.get(provider)
            if provider is Provider.MODIO and cache is None:
                cache = ModioLookupCache()
            providers[provider] = cls(creds, http=self._http, cache=cache)
        return providers

    def _coerce_creds(self, provider: Provider, cls: type[ProviderClient]) -> ProviderCreds | None:
        raw = self.tokens.get(provider)
        if raw is None:
            return None
        if isinstance(raw, ProviderCreds):
            if raw.provider != provider:
                raise ValueError(f"Credential provider mismatch: expected {provider}, got {raw.provider}")
            return raw
        if isinstance(raw, dict):
            payload = dict(raw)
            payload.setdefault("provider", provider)
            model = cls.creds_model or ProviderCreds
            return model.model_validate(payload)
        raise TypeError(f"Unsupported creds type for {provider}: {type(raw)!r}")

    def _p(self, provider: Provider) -> ProviderClient:
        try:
            return self.providers[provider]
        except KeyError:
            raise ValueError(f"Unknown provider: {provider}")

    async def get_mod(self, provider: Provider, mod_id: ModID) -> Mod:
        """Fetch a mod using the configured provider client.

        Args;
            provider: Provider to query.
            mod_id: Provider-specific mod identifier.

        Returns;
            A normalised Mod instance.

        Raises;
            ValueError: If the ModID provider does not match the target provider.
        """
        if mod_id.provider != provider:
            raise ValueError(f"ModID.provider must match {provider}, got {mod_id.provider}")
        return await self._p(provider).get_mod(mod_id)

    async def aclose(self) -> None:
        """Close the internal HTTP client if it is owned by the muxer."""
        if not self._external_http:
            await self._http.aclose()

    def __str__(self) -> str:
        return f"<ModMuxer: {len(self.providers)} providers>"


@asynccontextmanager
async def modmux_client(
    creds: dict[Provider, ProviderCreds | dict | None] | None = None,
    cache: dict[Provider, object | None] | None = None,
    http: httpx.AsyncClient | None = None,
) -> AsyncIterator[Muxer]:
    """Provide a managed ModMux client for async usage.

    Args;
        creds: Optional credentials per provider.
        cache: Optional per-provider caches.
        http: Optional externally managed HTTP client.

    Yields;
        A ready ModMux client.
    """
    client = Muxer(creds=creds, cache=cache, http=http)
    try:
        yield client
    finally:
        await client.aclose()
