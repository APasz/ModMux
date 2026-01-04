from __future__ import annotations

import sys
from pathlib import Path
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux._errors import AuthError, NotFound, ProviderError, RateLimited
from modmux.models import Provider, ProviderCreds
from modmux.providers._base import ProviderClient


class DummyCreds(ProviderCreds):
    provider: Provider = Provider.MODRINTH

    def format_base(self, base: str) -> str:
        return "https://alt.example.com/api"


class DummyClient(ProviderClient):
    name: Provider = Provider.MODRINTH
    base = "https://example.com/api"
    domains = ("example.com",)


class TestProviderHelpers(unittest.IsolatedAsyncioTestCase):
    async def test_normalise_and_match(self) -> None:
        self.assertEqual(DummyClient._normalise_url(" example.com "), "https://example.com")
        self.assertTrue(DummyClient._match_domain("www.example.com"))
        self.assertTrue(DummyClient._match_domain("api.example.com"))
        self.assertFalse(DummyClient._match_domain("example.org"))

    async def test_abs_url_with_creds(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as http:
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
