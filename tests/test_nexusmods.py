from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import DownloadAccess, ModID, Provider
from modmux.providers.nexusmods import NexusmodsClient


def _ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, request=request))


class TestNexusmodsClient(unittest.IsolatedAsyncioTestCase):
    async def test_get_mod_maps_fields(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/games/skyrim/mods/123.json"):
                payload = {
                    "name": "SkyUI",
                    "mod_slug": "skyui",
                    "description": "Inventory UI overhaul",
                    "author": "schlangster",
                    "user_id": 11,
                    "user": {"member_id": 11, "name": "schlangster"},
                    "created_time": "2023-01-01T00:00:00Z",
                    "updated_time": "2023-01-02T00:00:00Z",
                    "category_name": "Utilities",
                    "tags": ["ui", "skse"],
                    "mod_page_url": "https://www.nexusmods.com/skyrim/mods/123",
                    "version": "5.2",
                }
                return httpx.Response(200, json=payload, request=request)
            if request.url.path.endswith("/games/skyrim/mods/123/files.json"):
                payload = {
                    "files": [
                        {
                            "file_id": 500,
                            "file_name": "skyui_5_2.zip",
                            "size": 4096,
                            "uploaded_time": "2023-01-02T00:00:00Z",
                            "version": "5.2",
                            "category_name": "MAIN",
                        },
                        {
                            "file_id": 499,
                            "file_name": "skyui_5_1.zip",
                            "size": 2048,
                            "uploaded_time": "2022-12-31T00:00:00Z",
                            "version": "5.1",
                            "category_name": "OLD",
                        },
                    ]
                }
                return httpx.Response(200, json=payload, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = NexusmodsClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.NEXUSMODS, id="123", game="skyrim"))

        self.assertEqual(mod.id.id, "123")
        self.assertEqual(mod.id.game, "skyrim")
        self.assertEqual(mod.slug, "skyui")
        self.assertEqual(mod.name.value, "SkyUI")
        self.assertEqual(mod.author.id, "11")
        self.assertEqual(mod.author.name, "schlangster")
        self.assertEqual(mod.tags, ["Utilities", "ui", "skse"])
        self.assertEqual(mod.latest_version_id, "5.2")
        self.assertEqual(str(mod.homepage), "https://www.nexusmods.com/skyrim/mods/123")
        self.assertIsNotNone(mod.latest_version)
        assert mod.latest_version is not None
        self.assertEqual(mod.latest_version.version, "5.2")
        self.assertEqual([file.file_id for file in mod.latest_version.files], ["500"])
        self.assertEqual(mod.latest_version.files[0].filename, "skyui_5_2.zip")
        self.assertEqual(mod.latest_version.files[0].size_bytes, 4096)
        self.assertEqual(mod.latest_version.files[0].download.access, DownloadAccess.RESOLVABLE)
        self.assertTrue(mod.latest_version.files[0].download.requires_authentication)
        self.assertIsNotNone(mod.created_at)
        self.assertIsNotNone(mod.updated_at)

    async def test_get_mod_requires_game(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            client = NexusmodsClient(None, http=http)
            with self.assertRaises(ValueError):
                await client.get_mod(ModID(provider=Provider.NEXUSMODS, id="123"))

    async def test_resolve_download_mints_direct_url(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/games/skyrim/mods/123/files/500/download_link.json")
            return httpx.Response(
                200,
                json=[{"URI": "https://cdn.nexusmods.com/files/123/skyui.zip"}],
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = NexusmodsClient(None, http=http)
            download = await client.resolve_download(ModID(provider=Provider.NEXUSMODS, id="123", game="skyrim"), "500")

        self.assertEqual(download.access, DownloadAccess.DIRECT)
        self.assertEqual(str(download.url), "https://cdn.nexusmods.com/files/123/skyui.zip")

    async def test_get_mod_leaves_files_empty_when_versions_do_not_match(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/games/skyrim/mods/123.json"):
                return httpx.Response(
                    200,
                    json={
                        "name": "SkyUI",
                        "author": "schlangster",
                        "user_id": 11,
                        "version": "5.2",
                    },
                    request=request,
                )
            if request.url.path.endswith("/games/skyrim/mods/123/files.json"):
                return httpx.Response(
                    200,
                    json={
                        "files": [
                            {
                                "file_id": 500,
                                "file_name": "skyui_main.zip",
                                "size": 4096,
                                "uploaded_time": "2023-01-02T00:00:00Z",
                                "version": "legacy",
                                "category_name": "MAIN",
                            },
                            {
                                "file_id": 501,
                                "file_name": "skyui_archived.zip",
                                "size": 1024,
                                "uploaded_time": "2022-01-01T00:00:00Z",
                                "version": "legacy",
                                "category_name": "ARCHIVED",
                            },
                        ]
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = NexusmodsClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.NEXUSMODS, id="123", game="skyrim"))

        self.assertIsNotNone(mod.latest_version)
        assert mod.latest_version is not None
        self.assertEqual(mod.latest_version.version, "5.2")
        self.assertEqual(mod.latest_version.files, [])
