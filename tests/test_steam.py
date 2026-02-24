from __future__ import annotations

import sys
from pathlib import Path
import unittest
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import ModID, Provider
from modmux.providers.steam import SteamClient
from modmux.toggles import ToggleMode


class TestSteamClient(unittest.IsolatedAsyncioTestCase):
    async def test_get_mod_populates_translations(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            params = parse_qs(request.content.decode())
            language = params.get("language", [None])[0]

            title = "Base Title"
            description = "Base Desc"
            if language == "10":
                title = "JP Title"
                description = "JP Desc"
            elif language == "5":
                title = "ES Title"
                description = "ES Desc"
            elif language == "0":
                title = "EN Title"
                description = "EN Desc"

            payload = {
                "response": {
                    "publishedfiledetails": [
                        {
                            "result": 1,
                            "publishedfileid": "123",
                            "title": title,
                            "description": description,
                            "creator": "author1",
                        }
                    ]
                }
            }
            return httpx.Response(200, json=payload, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = SteamClient(None, http=http)
            mod_id = ModID(provider=Provider.STEAM, id="123")
            mod = await client.get_mod(mod_id, locales=["ja", "en-gb", "es-es"])

        self.assertEqual(mod.name.value, "Base Title")
        self.assertEqual(mod.name.translations["ja"], "JP Title")
        self.assertEqual(mod.name.translations["en-gb"], "EN Title")
        self.assertEqual(mod.name.translations["es-es"], "ES Title")
        self.assertIsNotNone(mod.description_md)
        assert mod.description_md is not None
        self.assertEqual(mod.description_md.translations["ja"], "JP Desc")
        self.assertEqual(mod.description_md.translations["en-gb"], "EN Desc")
        self.assertEqual(mod.description_md.translations["es-es"], "ES Desc")

    async def test_get_mod_skips_author_lookup_by_default(self) -> None:
        calls: dict[str, int] = {"details": 0, "user": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/ISteamRemoteStorage/GetPublishedFileDetails/v1/"):
                calls["details"] += 1
                payload = {
                    "response": {
                        "publishedfiledetails": [
                            {
                                "result": 1,
                                "publishedfileid": "123",
                                "title": "Base Title",
                                "description": "Base Desc",
                                "creator": "76561198000000001",
                            }
                        ]
                    }
                }
                return httpx.Response(200, json=payload, request=request)
            if path.endswith("/ISteamUser/GetPlayerSummaries/v2/"):
                calls["user"] += 1
                return httpx.Response(500, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = SteamClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.STEAM, id="123"))

        self.assertEqual(calls["details"], 1)
        self.assertEqual(calls["user"], 0)
        self.assertEqual(mod.author.id, "76561198000000001")
        self.assertEqual(mod.author.name, "76561198000000001")
        self.assertEqual(mod.author.raw, {"creator": "76561198000000001"})

    async def test_get_mod_resolves_author_when_requested(self) -> None:
        calls: dict[str, int] = {"details": 0, "user": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/ISteamRemoteStorage/GetPublishedFileDetails/v1/"):
                calls["details"] += 1
                payload = {
                    "response": {
                        "publishedfiledetails": [
                            {
                                "result": 1,
                                "publishedfileid": "123",
                                "title": "Base Title",
                                "description": "Base Desc",
                                "creator": "76561198000000001",
                            }
                        ]
                    }
                }
                return httpx.Response(200, json=payload, request=request)
            if path.endswith("/ISteamUser/GetPlayerSummaries/v2/"):
                calls["user"] += 1
                payload = {
                    "response": {
                        "players": [
                            {
                                "steamid": "76561198000000001",
                                "personaname": "DisplayName",
                            }
                        ]
                    }
                }
                return httpx.Response(200, json=payload, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = SteamClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.STEAM, id="123"), author_resolution=ToggleMode.ON)

        self.assertEqual(calls["details"], 1)
        self.assertEqual(calls["user"], 1)
        self.assertEqual(mod.author.id, "76561198000000001")
        self.assertEqual(mod.author.name, "DisplayName")
        self.assertEqual(mod.author.raw.get("steamid"), "76561198000000001")
        self.assertEqual(mod.author.raw.get("personaname"), "DisplayName")
