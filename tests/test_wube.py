from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import DependencyRelation, DownloadAccess, ModID, Provider
from modmux.modmux_errors import AuthError, ProviderError
from modmux.providers.wube import WubeClient, WubeCreds


class TestWubeClient(unittest.IsolatedAsyncioTestCase):
    async def test_get_mod_maps_fields(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/mods/space-exploration/full"):
                payload = {
                    "name": "space-exploration",
                    "title": "Space Exploration",
                    "description": "Big expansion mod",
                    "owner": "Earendel",
                    "tags": ["overhaul", {"name": "space"}],
                    "category": "content",
                    "releases": [
                        {"version": "1.0.0", "released_at": "2023-01-01T00:00:00Z"},
                        {
                            "version": "1.1.0",
                            "released_at": "2023-01-02T00:00:00Z",
                            "file_name": "space-exploration_1.1.0.zip",
                            "download_url": "/download/space-exploration_1.1.0.zip",
                            "info_json": {
                                "dependencies": [
                                    "base >= 1.1",
                                    "? alien-biomes",
                                    "! old-conflict",
                                ]
                            },
                        },
                    ],
                }
                return httpx.Response(200, json=payload, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = WubeClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.WUBE, id="space-exploration"))

        self.assertEqual(mod.id.id, "space-exploration")
        self.assertEqual(mod.slug, "space-exploration")
        self.assertEqual(mod.name.value, "Space Exploration")
        self.assertEqual(mod.author.id, "Earendel")
        self.assertEqual(mod.author.name, "Earendel")
        self.assertEqual(mod.tags, ["overhaul", "space", "content"])
        self.assertEqual(mod.latest_version_id, "1.1.0")
        self.assertEqual(str(mod.homepage), "https://mods.factorio.com/mod/space-exploration")
        self.assertIsNotNone(mod.latest_version)
        assert mod.latest_version is not None
        self.assertEqual(mod.latest_version.version, "1.1.0")
        self.assertEqual(mod.latest_version.files[0].filename, "space-exploration_1.1.0.zip")
        self.assertEqual(mod.latest_version.files[0].download.access, DownloadAccess.WEB)
        self.assertTrue(mod.latest_version.files[0].download.requires_authentication)
        self.assertEqual(
            [dependency.id.id for dependency in mod.latest_version.dependencies],
            ["base", "alien-biomes", "old-conflict"],
        )
        self.assertEqual(
            [dependency.version_req for dependency in mod.latest_version.dependencies],
            [">= 1.1", None, None],
        )
        self.assertEqual(
            [dependency.relation for dependency in mod.latest_version.dependencies],
            [
                DependencyRelation.REQUIRED,
                DependencyRelation.OPTIONAL,
                DependencyRelation.INCOMPATIBLE,
            ],
        )

    async def test_get_mod_rejects_bad_payload(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=["bad"], request=request))
        async with httpx.AsyncClient(transport=transport) as http:
            client = WubeClient(None, http=http)
            with self.assertRaises(ProviderError):
                await client.get_mod(ModID(provider=Provider.WUBE, id="space-exploration"))

    async def test_resolve_download_requires_username_and_token(self) -> None:
        payload = {
            "name": "space-exploration",
            "title": "Space Exploration",
            "owner": "Earendel",
            "releases": [
                {
                    "version": "1.1.0",
                    "file_name": "space-exploration_1.1.0.zip",
                    "download_url": "/download/space-exploration_1.1.0.zip",
                }
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params, httpx.QueryParams())
            return httpx.Response(200, json=payload, request=request)

        transport = httpx.MockTransport(handler)
        mod_id = ModID(provider=Provider.WUBE, id="space-exploration")

        async with httpx.AsyncClient(transport=transport) as http:
            client = WubeClient(WubeCreds.model_validate({"token": "token", "user": "alice"}), http=http)
            download = await client.resolve_download(mod_id, "1.1.0")

        self.assertEqual(download.access, DownloadAccess.WEB)
        self.assertEqual(str(download.url), "https://mods.factorio.com/download/space-exploration_1.1.0.zip")
        self.assertTrue(download.requires_authentication)

        async with httpx.AsyncClient(transport=transport) as http:
            client = WubeClient(WubeCreds.model_validate({"token": "token"}), http=http)
            with self.assertRaises(AuthError):
                await client.resolve_download(mod_id, "1.1.0")
