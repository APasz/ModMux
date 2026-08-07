from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import DownloadAccess, ModID, Provider
from modmux.modmux_errors import NotFound, ProviderError
from modmux.providers.steam import SteamClient
from modmux.toggles import ToggleMode


class TestSteamClient(unittest.IsolatedAsyncioTestCase):
    def test_parse_url(self) -> None:
        parsed = SteamClient.parse_url("https://steamcommunity.com/sharedfiles/filedetails/?id=12345&appid=480")
        self.assertEqual(parsed, ModID(provider=Provider.STEAM, id="12345", game="480"))

        path_parsed = SteamClient.parse_url("https://steamcommunity.com/sharedfiles/filedetails/67890")
        self.assertEqual(path_parsed, ModID(provider=Provider.STEAM, id="67890", game=None))

        self.assertIsNone(SteamClient.parse_url("https://example.com/sharedfiles/filedetails/?id=1"))

    async def test_get_mods_batches_details_translations_and_authors(self) -> None:
        calls: dict[str, int] = {"details": 0, "users": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/ISteamRemoteStorage/GetPublishedFileDetails/v1/"):
                calls["details"] += 1
                params = parse_qs(request.content.decode())
                language = params.get("language", [None])[0]
                ids = [params[key][0] for key in sorted(params) if key.startswith("publishedfileids[")]
                details = []
                for mod_id in ids:
                    title = f"Base {mod_id}"
                    description = f"Base Desc {mod_id}"
                    if language == "10":
                        title = f"JP {mod_id}"
                        description = f"JP Desc {mod_id}"
                    details.append(
                        {
                            "result": 1,
                            "publishedfileid": mod_id,
                            "title": title,
                            "description": description,
                            "creator": f"user-{mod_id}",
                        }
                    )
                return httpx.Response(200, json={"response": {"publishedfiledetails": details}}, request=request)

            if path.endswith("/ISteamUser/GetPlayerSummaries/v2/"):
                calls["users"] += 1
                query = parse_qs(urlsplit(str(request.url)).query)
                steamids = query.get("steamids", [""])[0].split(",")
                players = [
                    {
                        "steamid": steam_id,
                        "personaname": f"Name {steam_id}",
                    }
                    for steam_id in steamids
                    if steam_id
                ]
                return httpx.Response(200, json={"response": {"players": players}}, request=request)

            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = SteamClient(None, http=http)
            mod_ids = [
                ModID(provider=Provider.STEAM, id="123"),
                ModID(provider=Provider.STEAM, id="456"),
            ]
            mods = await client.get_mods(mod_ids, locales=["ja"], author_resolution=ToggleMode.ON)

        self.assertEqual(calls["details"], 2)
        self.assertEqual(calls["users"], 1)
        self.assertEqual([mod.id.id for mod in mods], ["123", "456"])
        self.assertEqual(mods[0].name.value, "Base 123")
        self.assertEqual(mods[0].name.translations["ja"], "JP 123")
        self.assertEqual(mods[1].name.value, "Base 456")
        self.assertEqual(mods[1].name.translations["ja"], "JP 456")
        self.assertEqual(mods[0].author.name, "Name user-123")
        self.assertEqual(mods[1].author.name, "Name user-456")

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
                            "time_created": "2023-01-01T00:00:00Z",
                            "time_updated": "2023-01-02T00:00:00Z",
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
        self.assertIsNotNone(mod.created_at)
        self.assertIsNotNone(mod.updated_at)

    async def test_get_mod_populates_latest_version_files_when_exposed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "response": {
                        "publishedfiledetails": [
                            {
                                "result": 1,
                                "publishedfileid": "123",
                                "filename": "city-pack.zip",
                                "file_size": 4096,
                                "hcontent_file": "ugc-123",
                                "file_url": "https://steamusercontent-a.akamaihd.net/ugc/city-pack.zip",
                                "revision_change_number": 77,
                                "time_updated": "2023-01-02T00:00:00Z",
                                "creator": "author1",
                                "title": "City Pack",
                            }
                        ]
                    }
                },
                request=request,
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = SteamClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.STEAM, id="123"))

        self.assertEqual(mod.latest_version_id, "77")
        self.assertIsNotNone(mod.latest_version)
        assert mod.latest_version is not None
        self.assertEqual(mod.latest_version.name, "city-pack.zip")
        self.assertEqual(mod.latest_version.version, "77")
        self.assertEqual([file.file_id for file in mod.latest_version.files], ["ugc-123"])
        self.assertEqual(mod.latest_version.files[0].filename, "city-pack.zip")
        self.assertEqual(mod.latest_version.files[0].size_bytes, 4096)
        self.assertEqual(mod.latest_version.files[0].download.access, DownloadAccess.DIRECT)

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

    async def test_get_user_validates_and_rejects_an_unmatched_player(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "response": {
                        "players": [{"steamid": "first", "personaname": "FallbackName"}],
                    }
                },
                request=request,
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = SteamClient(None, http=http)
            with self.assertRaises(ValueError):
                await client.get_user(" ")
            with self.assertRaises(NotFound):
                await client.get_user("missing")

    async def test_get_mods_falls_back_when_localization_or_user_lookup_fails(self) -> None:
        calls: dict[str, int] = {"details": 0, "users": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/ISteamRemoteStorage/GetPublishedFileDetails/v1/"):
                calls["details"] += 1
                params = parse_qs(request.content.decode())
                if params.get("language", [None])[0] == "10":
                    return httpx.Response(500, request=request)
                return httpx.Response(
                    200,
                    json={
                        "response": {
                            "publishedfiledetails": [
                                {
                                    "result": 1,
                                    "publishedfileid": "123",
                                    "title": "Base Title",
                                    "description": "Base Desc",
                                    "creator": "creator-1",
                                }
                            ]
                        }
                    },
                    request=request,
                )
            if path.endswith("/ISteamUser/GetPlayerSummaries/v2/"):
                calls["users"] += 1
                return httpx.Response(500, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = SteamClient(None, http=http)
            mods = await client.get_mods(
                [ModID(provider=Provider.STEAM, id="123")],
                locales=["ja", "zz"],
                author_resolution=ToggleMode.ON,
            )

        self.assertEqual(calls["details"], 3)
        self.assertEqual(calls["users"], 2)
        self.assertEqual(mods[0].name.translations, {})
        self.assertEqual(mods[0].author.id, "creator-1")
        self.assertEqual(mods[0].author.name, "creator-1")

    async def test_get_mods_raises_for_missing_or_invalid_results(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            params = parse_qs(request.content.decode())
            mod_id = params.get("publishedfileids[0]", [""])[0]
            result: object = 1
            if mod_id == "missing":
                result = 9
            elif mod_id == "bad":
                result = "oops"
            return httpx.Response(
                200,
                json={
                    "response": {
                        "publishedfiledetails": [
                            {
                                "result": result,
                                "publishedfileid": mod_id,
                                "title": "Title",
                            }
                        ]
                    }
                },
                request=request,
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = SteamClient(None, http=http)
            with self.assertRaises(NotFound):
                await client.get_mods([ModID(provider=Provider.STEAM, id="missing")])
            with self.assertRaises(ProviderError):
                await client.get_mods([ModID(provider=Provider.STEAM, id="bad")])
