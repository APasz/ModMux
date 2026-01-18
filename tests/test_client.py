from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import cast

import httpx
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux import Muxer
from modmux.models import Author, LocaleTag, LocalisedText, Mod, ModID, Provider, ProviderCreds
from modmux.providers._base import ProviderClient
from modmux.providers.modrinth import ModrinthClient, ModrinthCreds
from modmux.providers.steam import SteamCreds


class StubProvider(ProviderClient):
    name: Provider = Provider.MODRINTH
    base = "https://example.com"

    def __init__(self, creds: ProviderCreds | None, *, http: httpx.AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)
        self.last_mod_id: ModID | None = None

    async def get_mod(self, mod_id: ModID, *, locales: list[LocaleTag] | None = None) -> Mod:
        self.last_mod_id = mod_id
        author = Author(provider=Provider.MODRINTH, id="a1", name="Author")
        return Mod(provider=Provider.MODRINTH, id=mod_id, name=LocalisedText(value="Stub"), author=author)


class TestMuxer(unittest.IsolatedAsyncioTestCase):
    async def test_get_mod_rejects_provider_mismatch(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as http:
            muxer = Muxer(http=http)
            mod_id = ModID(provider=Provider.STEAM, id="1")
            with self.assertRaises(ValueError):
                await muxer.get_mod(Provider.MODRINTH, mod_id)

    async def test_get_mod_from_url_overrides_game(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as http:
            muxer = Muxer(http=http)
            stub = StubProvider(None, http=http)
            muxer.providers[Provider.MODRINTH] = stub

            mod = await muxer.get_mod_from_url("https://modrinth.com/mod/fabric-api", game="overridden")

            self.assertIsNotNone(stub.last_mod_id)
            assert stub.last_mod_id is not None
            self.assertEqual(stub.last_mod_id.game, "overridden")
            self.assertEqual(mod.id, stub.last_mod_id)

    async def test_get_mod_from_url_unknown(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as http:
            muxer = Muxer(http=http)
            with self.assertRaises(ValueError):
                await muxer.get_mod_from_url("https://example.com/mod/123")

    async def test_coerce_creds_dict(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as http:
            muxer = Muxer(creds={Provider.MODRINTH: {"token": "secret"}}, http=http)
            creds = muxer._coerce_creds(Provider.MODRINTH, ModrinthClient)
            self.assertIsInstance(creds, ModrinthCreds)
            assert creds is not None
            self.assertEqual(creds.headers(), {"Authorization": "secret"})

    async def test_creds_sequence(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
        ) as http:
            creds_list = [ModrinthCreds(api_key=SecretStr("secret")), SteamCreds(api_key=SecretStr("key"))]
            muxer = Muxer(creds=creds_list, http=http)
            self.assertIs(muxer.tokens[Provider.MODRINTH], creds_list[0])
            self.assertIs(muxer.tokens[Provider.STEAM], creds_list[1])

    async def test_creds_sequence_duplicate_provider(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
        ) as http:
            creds_list = [ModrinthCreds(api_key=SecretStr("secret")), ModrinthCreds(api_key=SecretStr("other"))]
            with self.assertRaises(ValueError):
                Muxer(creds=creds_list, http=http)

    async def test_creds_sequence_invalid_item(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
        ) as http:
            creds_list = [ModrinthCreds(api_key=SecretStr("secret")), cast(ProviderCreds, object())]
            with self.assertRaises(TypeError):
                Muxer(creds=creds_list, http=http)

    async def test_coerce_creds_mismatch(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as http:
            muxer = Muxer(http=http)
            muxer.tokens[Provider.STEAM] = ProviderCreds(provider=Provider.MODRINTH)
            with self.assertRaises(ValueError):
                muxer._coerce_creds(Provider.STEAM, ModrinthClient)

    async def test_coerce_creds_type_error(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as http:
            muxer = Muxer(http=http)
            muxer.tokens[Provider.MODRINTH] = cast(ProviderCreds | dict, object())
            with self.assertRaises(TypeError):
                muxer._coerce_creds(Provider.MODRINTH, ModrinthClient)

    async def test_aclose_respects_external_http(self) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as http:
            muxer = Muxer(http=http)
            await muxer.aclose()
            self.assertFalse(http.is_closed)

    async def test_aclose_closes_internal_http(self) -> None:
        muxer = Muxer()
        await muxer.aclose()
        self.assertTrue(muxer._http.is_closed)
