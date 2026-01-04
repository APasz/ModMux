"""Shared base client for provider integrations."""

import abc
import asyncio
import random
from collections.abc import Mapping
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from httpx import RemoteProtocolError

from .._errors import AuthError, NotFound, ProviderError, RateLimited
from .._log import get_logger
from ..models import Mod, ModID, Provider, ProviderCreds

log = get_logger("base")


class ProviderClient(abc.ABC):
    """Base class for provider-specific API clients."""

    name: Provider
    base: str
    creds_model: type[ProviderCreds] | None = None
    domains: tuple[str, ...] = ()

    def __init__(
        self,
        creds: ProviderCreds | None = None,
        *,
        http: httpx.AsyncClient,
        cache: object | None = None,
    ) -> None:
        self.http = http
        self.creds = creds
        self.limiter = AsyncLimiter(5, 1)
        self.cache = cache

    async def get_mod(self, mod_id: ModID) -> Mod:  # * override per provider
        """Fetch a mod by provider-specific identifier.

        Args;
            mod_id: Provider-specific mod identifier.

        Returns;
            A normalised Mod instance.
        """
        ...

    async def close(self) -> None:
        """Close the underlying HTTP client if it is still open."""
        if self.http and not self.http.is_closed:
            await self.http.aclose()

    @classmethod
    def parse_url(cls, url: str) -> ModID | None:
        """Parse a provider URL into a ModID.

        Args;
            url: Provider URL to parse.

        Returns;
            A ModID if the URL matches this provider; otherwise None.
        """
        return None

    @classmethod
    def _normalise_url(cls, url: str) -> str:
        cleaned = url.strip()
        if not cleaned:
            return ""
        if "://" not in cleaned:
            return f"https://{cleaned}"
        return cleaned

    @classmethod
    def _match_domain(cls, host: str | None) -> bool:
        if not host:
            return False
        cleaned = host.lower()
        if cleaned.startswith("www."):
            cleaned = cleaned[4:]
        for domain in cls.domains:
            domain_clean = domain.lower()
            if cleaned == domain_clean or cleaned.endswith(f".{domain_clean}"):
                return True
        return False

    @staticmethod
    def _path_segments(path: str) -> list[str]:
        return [segment for segment in path.split("/") if segment]

    def _auth_headers(self) -> dict[str, str]:  # * override per provider as needed
        if not self.creds:
            return {}
        return self.creds.headers()

    def _auth_params(self) -> dict[str, str]:  # * override per provider as needed
        if not self.creds:
            return {}
        return self.creds.params()

    def _abs_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://") or path_or_url.startswith("www."):
            return path_or_url
        base = self.base
        if self.creds:
            base = self.creds.format_base(base)
        return f"{base.rstrip('/')}/{path_or_url.lstrip('/')}"

    async def _get(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_attempts: int = 2,
    ) -> httpx.Response:
        """Perform a rate-limited GET with retries and error mapping.

        Args;
            path_or_url: Relative path or absolute URL to request.
            params: Optional query parameters.
            headers: Optional headers to merge with auth headers.
            timeout: Optional request timeout.
            max_attempts: Maximum request attempts on retryable failures.

        Returns;
            The successful HTTP response.

        Raises;
            AuthError: If authentication fails.
            NotFound: If the resource is not found.
            RateLimited: If retries are exhausted after rate limiting.
            ProviderError: For other provider or transport failures.
        """
        url = self._abs_url(path_or_url)
        req_headers: dict[str, str] = self._auth_headers()
        if headers:
            req_headers.update(headers)

        req_params: dict[str, Any] | None = {}
        auth_params = self._auth_params()
        if auth_params:
            req_params.update(auth_params)
        if params:
            req_params.update(params)
        if not req_params:
            req_params = None

        effective_timeout = timeout

        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                async with self.limiter:
                    response = await self.http.get(
                        url,
                        params=req_params,
                        headers=req_headers,
                        timeout=effective_timeout,
                    )

                if 200 <= response.status_code < 300:
                    return response

                sc = response.status_code
                if sc in (401, 403):
                    raise AuthError(f"{self.name}: {sc} on GET {url}")
                if sc == 404:
                    raise NotFound(f"{self.name}: 404 on GET {url}")
                if sc == 429:
                    if attempt < max_attempts - 1:
                        retry_after = response.headers.get("Retry-After")
                        try:
                            delay = float(retry_after) if retry_after is not None else 1.0
                        except ValueError:
                            delay = 1.0
                        await asyncio.sleep(delay + random.uniform(0, 0.25))
                        continue
                    raise RateLimited(f"{self.name}: 429 on GET {url}")
                if 500 <= sc < 600:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(0.5 * (2**attempt) + random.uniform(0, 0.25))
                        continue
                    raise ProviderError(f"{self.name}: {sc} on GET {url}")

                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise ProviderError(f"{self.name}: {e}") from e
                return response

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout, RemoteProtocolError) as e:
                last_exc = e
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.2))
                    continue
                raise ProviderError(f"{self.name}: transport error on GET {url}") from e

        if last_exc:  # jic
            raise ProviderError(f"{self.name}: GET {url} failed") from last_exc
        raise ProviderError(f"{self.name}: GET {url} failed for unknown reasons")

    async def _get_json(self, path_or_url: str, **kwargs: Any) -> Any:
        """Perform a GET request and parse the response as JSON.

        Args;
            path_or_url: Relative path or absolute URL to request.
            **kwargs: Passed through to `_get`.

        Returns;
            The parsed JSON payload.

        Raises;
            ProviderError: If the response JSON is invalid.
        """
        response = await self._get(path_or_url, **kwargs)
        try:
            return response.json()
        except ValueError as e:
            raise ProviderError(f"{self.name}: invalid JSON from {response.request.url}") from e

    async def _post(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_attempts: int = 2,
    ) -> httpx.Response:
        """Perform a rate-limited POST with retries and error mapping.

        Args;
            path_or_url: Relative path or absolute URL to request.
            params: Optional query parameters.
            headers: Optional headers to merge with auth headers.
            data: Optional form-encoded payload.
            json: Optional JSON payload.
            timeout: Optional request timeout.
            max_attempts: Maximum request attempts on retryable failures.

        Returns;
            The successful HTTP response.

        Raises;
            AuthError: If authentication fails.
            NotFound: If the resource is not found.
            RateLimited: If retries are exhausted after rate limiting.
            ProviderError: For other provider or transport failures.
        """
        url = self._abs_url(path_or_url)
        req_headers: dict[str, str] = self._auth_headers()
        if headers:
            req_headers.update(headers)

        req_params: dict[str, Any] | None = {}
        auth_params = self._auth_params()
        if auth_params:
            req_params.update(auth_params)
        if params:
            req_params.update(params)
        if not req_params:
            req_params = None

        effective_timeout = timeout

        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                async with self.limiter:
                    response = await self.http.post(
                        url,
                        params=req_params,
                        headers=req_headers,
                        data=data,
                        json=json,
                        timeout=effective_timeout,
                    )

                if 200 <= response.status_code < 300:
                    return response

                sc = response.status_code
                if sc in (401, 403):
                    raise AuthError(f"{self.name}: {sc} on POST {url}")
                if sc == 404:
                    raise NotFound(f"{self.name}: 404 on POST {url}")
                if sc == 429:
                    if attempt < max_attempts - 1:
                        retry_after = response.headers.get("Retry-After")
                        try:
                            delay = float(retry_after) if retry_after is not None else 1.0
                        except ValueError:
                            delay = 1.0
                        await asyncio.sleep(delay + random.uniform(0, 0.25))
                        continue
                    raise RateLimited(f"{self.name}: 429 on POST {url}")
                if 500 <= sc < 600:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(0.5 * (2**attempt) + random.uniform(0, 0.25))
                        continue
                    raise ProviderError(f"{self.name}: {sc} on POST {url}")

                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise ProviderError(f"{self.name}: {e}") from e
                return response

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout, RemoteProtocolError) as e:
                last_exc = e
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.2))
                    continue
                raise ProviderError(f"{self.name}: transport error on POST {url}") from e

        if last_exc:  # jic
            raise ProviderError(f"{self.name}: POST {url} failed") from last_exc
        raise ProviderError(f"{self.name}: POST {url} failed for unknown reasons")

    async def _post_json(self, path_or_url: str, **kwargs: Any) -> Any:
        """Perform a POST request and parse the response as JSON.

        Args;
            path_or_url: Relative path or absolute URL to request.
            **kwargs: Passed through to `_post`.

        Returns;
            The parsed JSON payload.

        Raises;
            ProviderError: If the response JSON is invalid.
        """
        response = await self._post(path_or_url, **kwargs)
        try:
            return response.json()
        except ValueError as e:
            raise ProviderError(f"{self.name}: invalid JSON from {response.request.url}") from e
