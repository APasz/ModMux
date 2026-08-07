from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import (
    Author,
    DownloadAccess,
    DownloadInfo,
    FileAsset,
    LocalisedText,
    Mod,
    ModID,
    ModVersion,
    Provider,
    ProviderCreds,
)
from modmux.modmux_errors import AuthError, NotFound, ProviderError, RateLimited
from modmux.providers._base import ProviderClient


def _ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, request=request))


class DummyCreds(ProviderCreds):
    provider: Provider = Provider.MODRINTH

    def format_base(self, base: str) -> str:
        return "https://alt.example.com/api"


class DummyClient(ProviderClient):
    name: Provider = Provider.MODRINTH
    base = "https://example.com/api"
    domains = ("example.com",)

    async def get_mod(self, mod_id: ModID, **_: object) -> Mod:
        raise NotImplementedError


class IncompleteClient(ProviderClient):
    name: Provider = Provider.MODRINTH
    base = "https://example.com/api"


class DownloadDummyClient(DummyClient):
    def __init__(self, download: DownloadInfo, *, http: httpx.AsyncClient) -> None:
        super().__init__(None, http=http)
        self.download = download

    async def get_mod(self, mod_id: ModID, **_: object) -> Mod:
        author = Author(provider=Provider.MODRINTH, id="author", name="Author")
        latest_version = ModVersion(
            id=mod_id,
            files=[FileAsset(file_id="release", filename="release.zip", download=self.download)],
        )
        return Mod(
            provider=Provider.MODRINTH,
            id=mod_id,
            name=LocalisedText(value="Example"),
            author=author,
            latest_version=latest_version,
        )


class TestProviderHelpers(unittest.IsolatedAsyncioTestCase):
    async def test_normalise_and_match(self) -> None:
        self.assertEqual(DummyClient._normalise_url(" example.com "), "https://example.com")
        self.assertTrue(DummyClient._match_domain("www.example.com"))
        self.assertTrue(DummyClient._match_domain("api.example.com"))
        self.assertFalse(DummyClient._match_domain("example.org"))

    async def test_get_mod_is_required(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            with self.assertRaises(TypeError):
                IncompleteClient(None, http=http)  # pyright: ignore[reportAbstractUsage]

    async def test_abs_url_with_creds(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            client = DummyClient(DummyCreds(provider=Provider.MODRINTH), http=http)
            self.assertEqual(client._abs_url("mods/1"), "https://alt.example.com/api/mods/1")
            self.assertEqual(client._abs_url("https://example.com/mods/1"), "https://example.com/mods/1")

    async def test_get_status_mappings(self) -> None:
        async def assert_status(status: int, exc_type: type[Exception]) -> None:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(status, request=request)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                client = DummyClient(None, http=http)
                with self.assertRaises(exc_type):
                    await client._get("/mods/1", max_attempts=1)

        await assert_status(401, AuthError)
        await assert_status(404, NotFound)
        await assert_status(429, RateLimited)
        await assert_status(500, ProviderError)
        await assert_status(418, ProviderError)

    async def test_get_json_invalid(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not-json", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DummyClient(None, http=http)
            with self.assertRaises(ProviderError):
                await client._get_json("/mods/1")

    async def test_post_status_mappings(self) -> None:
        async def assert_status(status: int, exc_type: type[Exception]) -> None:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(status, request=request)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                client = DummyClient(None, http=http)
                with self.assertRaises(exc_type):
                    await client._post("/mods/1", max_attempts=1)

        await assert_status(401, AuthError)
        await assert_status(404, NotFound)
        await assert_status(429, RateLimited)
        await assert_status(500, ProviderError)
        await assert_status(418, ProviderError)

    async def test_post_json_invalid(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not-json", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DummyClient(None, http=http)
            with self.assertRaises(ProviderError):
                await client._post_json("/mods/1")

    async def test_get_retries_after_rate_limit(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "bad"}, request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DummyClient(None, http=http)
            with patch("modmux.providers._base.asyncio.sleep", new=AsyncMock()) as sleep_mock:
                response = await client._get("/mods/1")

        self.assertEqual(calls, 2)
        self.assertEqual(response.json(), {"ok": True})
        sleep_mock.assert_awaited_once()

    async def test_get_retries_after_transport_error(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DummyClient(None, http=http)
            with patch("modmux.providers._base.asyncio.sleep", new=AsyncMock()) as sleep_mock:
                response = await client._get("/mods/1")

        self.assertEqual(calls, 2)
        self.assertEqual(response.json(), {"ok": True})
        sleep_mock.assert_awaited_once()

    async def test_get_and_post_map_connect_timeouts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DummyClient(None, http=http)
            with self.assertRaises(ProviderError):
                await client._get("/mods/1", max_attempts=1)
            with self.assertRaises(ProviderError):
                await client._post("/mods/1", max_attempts=1)

    async def test_request_attempt_count_must_be_positive(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            client = DummyClient(None, http=http)
            with self.assertRaises(ValueError):
                await client._get("/mods/1", max_attempts=0)

    async def test_post_retries_after_server_error(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(500, request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DummyClient(None, http=http)
            with patch("modmux.providers._base.asyncio.sleep", new=AsyncMock()) as sleep_mock:
                response = await client._post("/mods/1")

        self.assertEqual(calls, 2)
        self.assertEqual(response.json(), {"ok": True})
        sleep_mock.assert_awaited_once()

    async def test_get_user_default_fallback(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            client = DummyClient(None, http=http)
            author = await client.get_user("user-1")
            self.assertEqual(author.provider, Provider.MODRINTH)
            self.assertEqual(author.id, "user-1")
            self.assertEqual(author.name, "user-1")
            self.assertEqual(author.raw, {})

    async def test_resolve_download_returns_existing_direct_access(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            client = DownloadDummyClient(DownloadInfo.direct("https://example.com/release.zip"), http=http)
            download = await client.resolve_download(ModID(provider=Provider.MODRINTH, id="example"), "release")

        self.assertEqual(download.access, DownloadAccess.DIRECT)
        self.assertEqual(str(download.url), "https://example.com/release.zip")

    async def test_resolve_download_rejects_unimplemented_resolution(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            client = DownloadDummyClient(
                DownloadInfo(access=DownloadAccess.RESOLVABLE),
                http=http,
            )
            with self.assertRaises(ProviderError):
                await client.resolve_download(ModID(provider=Provider.MODRINTH, id="example"), "release")
