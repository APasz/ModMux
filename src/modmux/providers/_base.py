"""Shared base client for provider integrations."""

import abc
import asyncio
import random
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import httpx
from aiolimiter import AsyncLimiter

from ..models import Author, DownloadAccess, DownloadInfo, LocaleTag, Mod, ModID, Provider, ProviderCreds
from ..modmux_errors import AuthError, NotFound, ProviderError, RateLimited
from ..toggles import ToggleMode, UndefinedType
from .colour import Colour


class ProviderClient(abc.ABC):
    """Base class for provider-specific API clients."""

    name: Provider
    display_name: str
    base: str
    colour: Colour = Colour("#808080")
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

    @abc.abstractmethod
    async def get_mod(
        self,
        mod_id: ModID,
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> Mod:
        """Fetch a mod by provider-specific identifier.

        Args;
            mod_id: Provider-specific mod identifier.
            locales: Optional locale tags to request translations for.
            author_resolution: Author enrichment toggle.

        Returns;
            A normalised Mod instance.
        """
        raise NotImplementedError

    async def get_mods(
        self,
        mod_ids: Sequence[ModID],
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> list[Mod]:
        """Fetch multiple mods by provider-specific identifiers.

        The default implementation fans out to `get_mod()` and preserves the
        input order. Providers with native bulk endpoints should override this.

        Args;
            mod_ids: Provider-scoped mod identifiers.
            locales: Optional locale tags to request translations for.
            author_resolution: Author enrichment toggle.

        Returns;
            A list of normalised Mod instances in input order.
        """
        if not mod_ids:
            return []
        return await asyncio.gather(
            *(self.get_mod(mod_id, locales=locales, author_resolution=author_resolution) for mod_id in mod_ids)
        )

    async def resolve_download(self, mod_id: ModID, file_id: str) -> DownloadInfo:
        """Return or resolve access details for a release file.

        Providers that mint a fresh URL override this method. The default uses
        the matching latest-version file descriptor when that descriptor is
        already usable.

        Args:
            mod_id: Provider-specific mod identifier.
            file_id: Provider-specific release file identifier.

        Returns:
            Current release-file access details.

        Raises:
            NotFound: If the requested file is not part of the latest version.
            ProviderError: If the provider needs a custom resolution request.
        """
        mod = await self.get_mod(mod_id)
        latest_version = mod.latest_version
        if latest_version is None:
            raise NotFound(f"{self.name}: file {file_id!r} not found for mod {mod_id.id!r}")

        requested_file_id = str(file_id).strip()
        for asset in latest_version.files:
            if asset.file_id != requested_file_id:
                continue
            if asset.download.access is DownloadAccess.RESOLVABLE:
                raise ProviderError(f"{self.name}: download resolution is not implemented for file {file_id!r}")
            return asset.download

        raise NotFound(f"{self.name}: file {file_id!r} not found for mod {mod_id.id!r}")

    async def get_user(self, user_id: str) -> Author:
        """Fetch a provider user by id.

        Providers with dedicated user endpoints can override this. The base
        implementation is a cheap fallback that mirrors id into name.

        Args;
            user_id: Provider user identifier.

        Returns;
            A normalised Author instance.
        """
        value = str(user_id).strip() or "unknown"
        return Author(provider=self.name, id=value, name=value, raw={})

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

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, Any] | None = None,
        json: Any | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_attempts: int = 2,
        follow_redirects: bool = False,
        allowed_status_codes: Sequence[int] = (),
    ) -> httpx.Response:
        """Perform a rate-limited request with retries and error mapping."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        url = self._abs_url(path_or_url)
        request_headers = self._auth_headers()
        if headers:
            request_headers.update(headers)

        request_params: dict[str, Any] = self._auth_params()
        if params:
            request_params.update(params)

        for attempt in range(max_attempts):
            try:
                async with self.limiter:
                    if method == "GET":
                        response = await self.http.get(
                            url,
                            params=request_params or None,
                            headers=request_headers,
                            timeout=timeout,
                            follow_redirects=follow_redirects,
                        )
                    else:
                        response = await self.http.post(
                            url,
                            params=request_params or None,
                            headers=request_headers,
                            data=data,
                            json=json,
                            timeout=timeout,
                            follow_redirects=follow_redirects,
                        )

                if 200 <= response.status_code < 300 or response.status_code in allowed_status_codes:
                    return response

                status_code = response.status_code
                if status_code in (401, 403):
                    raise AuthError(f"{self.name}: {status_code} on {method} {url}")
                if status_code == 404:
                    raise NotFound(f"{self.name}: 404 on {method} {url}")
                if status_code == 429:
                    if attempt < max_attempts - 1:
                        retry_after = response.headers.get("Retry-After")
                        try:
                            delay = float(retry_after) if retry_after is not None else 1.0
                        except ValueError:
                            delay = 1.0
                        await asyncio.sleep(delay + random.uniform(0, 0.25))
                        continue
                    raise RateLimited(f"{self.name}: 429 on {method} {url}")
                if 500 <= status_code < 600:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(0.5 * (2**attempt) + random.uniform(0, 0.25))
                        continue
                    raise ProviderError(f"{self.name}: {status_code} on {method} {url}")

                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise ProviderError(f"{self.name}: {exc}") from exc
                return response

            except httpx.TransportError as exc:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0.3 + random.uniform(0, 0.2))
                    continue
                raise ProviderError(f"{self.name}: transport error on {method} {url}") from exc

        raise ProviderError(f"{self.name}: {method} {url} failed for unknown reasons")

    async def _get(
        self,
        path_or_url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
        max_attempts: int = 2,
        follow_redirects: bool = False,
        allowed_status_codes: Sequence[int] = (),
    ) -> httpx.Response:
        """Perform a rate-limited GET with retries and error mapping."""
        return await self._request(
            "GET",
            path_or_url,
            params=params,
            headers=headers,
            timeout=timeout,
            max_attempts=max_attempts,
            follow_redirects=follow_redirects,
            allowed_status_codes=allowed_status_codes,
        )

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
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """Perform a rate-limited POST with retries and error mapping."""
        return await self._request(
            "POST",
            path_or_url,
            params=params,
            headers=headers,
            data=data,
            json=json,
            timeout=timeout,
            max_attempts=max_attempts,
            follow_redirects=follow_redirects,
        )

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
