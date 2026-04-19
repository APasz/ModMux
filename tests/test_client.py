from __future__ import annotations

import sys
import unittest
from collections.abc import Sequence
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
from modmux.toggles import UNDEFINED, ToggleMode, UndefinedType


def _ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, request=request))


class StubProvider(ProviderClient):
    name: Provider = Provider.MODRINTH
    base = "https://example.com"

    def __init__(self, creds: ProviderCreds | None, *, http: httpx.AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)
        self.last_mod_id: ModID | None = None
        self.last_mod_ids: list[ModID] = []
        self.last_author_resolution: ToggleMode = ToggleMode.AUTO
        self.last_user_id: str | None = None

    async def get_mod(
        self,
        mod_id: ModID,
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> Mod:
        self.last_mod_id = mod_id
        if isinstance(author_resolution, ToggleMode):
            self.last_author_resolution = author_resolution
        elif author_resolution:
            self.last_author_resolution = ToggleMode.ON
        else:
            self.last_author_resolution = ToggleMode.OFF
        author = Author(provider=Provider.MODRINTH, id="a1", name="Author")
        return Mod(provider=Provider.MODRINTH, id=mod_id, name=LocalisedText(value="Stub"), author=author)

    async def get_mods(
        self,
        mod_ids: Sequence[ModID],
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> list[Mod]:
        self.last_mod_ids = list(mod_ids)
        if isinstance(author_resolution, ToggleMode):
            self.last_author_resolution = author_resolution
        elif author_resolution:
            self.last_author_resolution = ToggleMode.ON
        else:
            self.last_author_resolution = ToggleMode.OFF
        author = Author(provider=Provider.MODRINTH, id="a1", name="Author")
        return [
            Mod(provider=Provider.MODRINTH, id=mod_id, name=LocalisedText(value="Stub"), author=author)
            for mod_id in mod_ids
        ]

    async def get_user(self, user_id: str) -> Author:
        self.last_user_id = user_id
        return Author(provider=Provider.MODRINTH, id=user_id, name=f"user-{user_id}")


class TestMuxer(unittest.IsolatedAsyncioTestCase):
    async def test_get_mod_rejects_provider_mismatch(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            muxer = Muxer(http=http)
            mod_id = ModID(provider=Provider.STEAM, id="1")
            with self.assertRaises(ValueError):
                await muxer.get_mod(Provider.MODRINTH, mod_id)

    async def test_get_mod_from_url_overrides_game(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            muxer = Muxer(http=http)
            stub = StubProvider(None, http=http)
            muxer.providers[Provider.MODRINTH] = stub

            mod = await muxer.get_mod_from_url(
                "https://modrinth.com/mod/fabric-api",
                game="overridden",
                author_resolution=True,
            )

            self.assertIsNotNone(stub.last_mod_id)
            assert stub.last_mod_id is not None
            self.assertEqual(stub.last_mod_id.game, "overridden")
            self.assertEqual(stub.last_author_resolution, ToggleMode.ON)
            self.assertEqual(mod.id, stub.last_mod_id)

    async def test_get_mod_author_resolution_enum(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
        ) as http:
            muxer = Muxer(http=http)
            stub = StubProvider(None, http=http)
            muxer.providers[Provider.MODRINTH] = stub
            mod_id = ModID(provider=Provider.MODRINTH, id="abc")

            await muxer.get_mod(Provider.MODRINTH, mod_id, author_resolution=ToggleMode.ON)
            self.assertEqual(stub.last_author_resolution, ToggleMode.ON)

    async def test_get_mod_author_resolution_undefined(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
        ) as http:
            muxer = Muxer(http=http)
            stub = StubProvider(None, http=http)
            muxer.providers[Provider.MODRINTH] = stub
            mod_id = ModID(provider=Provider.MODRINTH, id="abc")

            await muxer.get_mod(Provider.MODRINTH, mod_id, author_resolution=UNDEFINED)
            self.assertEqual(stub.last_author_resolution, ToggleMode.OFF)

    async def test_get_user_delegates_to_provider(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
        ) as http:
            muxer = Muxer(http=http)
            stub = StubProvider(None, http=http)
            muxer.providers[Provider.MODRINTH] = stub

            author = await muxer.get_user(Provider.MODRINTH, "abc123")

            self.assertEqual(stub.last_user_id, "abc123")
            self.assertEqual(author.id, "abc123")
            self.assertEqual(author.name, "user-abc123")

    async def test_get_mod_from_url_unknown(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            muxer = Muxer(http=http)
            with self.assertRaises(ValueError):
                await muxer.get_mod_from_url("https://example.com/mod/123")

    async def test_get_mods_rejects_provider_mismatch(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            muxer = Muxer(http=http)
            mod_ids = [
                ModID(provider=Provider.MODRINTH, id="abc"),
                ModID(provider=Provider.STEAM, id="1"),
            ]
            with self.assertRaises(ValueError):
                await muxer.get_mods(Provider.MODRINTH, mod_ids)

    async def test_get_mods_delegates_to_provider(self) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
        ) as http:
            muxer = Muxer(http=http)
            stub = StubProvider(None, http=http)
            muxer.providers[Provider.MODRINTH] = stub
            mod_ids = [
                ModID(provider=Provider.MODRINTH, id="abc"),
                ModID(provider=Provider.MODRINTH, id="def"),
            ]

            mods = await muxer.get_mods(Provider.MODRINTH, mod_ids, author_resolution=ToggleMode.ON)

            self.assertEqual(stub.last_mod_ids, mod_ids)
            self.assertEqual(stub.last_author_resolution, ToggleMode.ON)
            self.assertEqual([mod.id for mod in mods], mod_ids)

    async def test_coerce_creds_dict(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
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
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            muxer = Muxer(http=http)
            muxer.tokens[Provider.STEAM] = ProviderCreds(provider=Provider.MODRINTH)
            with self.assertRaises(ValueError):
                muxer._coerce_creds(Provider.STEAM, ModrinthClient)

    async def test_coerce_creds_type_error(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            muxer = Muxer(http=http)
            muxer.tokens[Provider.MODRINTH] = cast(ProviderCreds | dict, object())
            with self.assertRaises(TypeError):
                muxer._coerce_creds(Provider.MODRINTH, ModrinthClient)

    async def test_aclose_respects_external_http(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            muxer = Muxer(http=http)
            await muxer.aclose()
            self.assertFalse(http.is_closed)

    async def test_aclose_closes_internal_http(self) -> None:
        muxer = Muxer()
        await muxer.aclose()
        self.assertTrue(muxer._http.is_closed)
