from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import ModID, Provider
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
        self.assertIsNotNone(mod.created_at)
        self.assertIsNotNone(mod.updated_at)

    async def test_get_mod_requires_game(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            client = NexusmodsClient(None, http=http)
            with self.assertRaises(ValueError):
                await client.get_mod(ModID(provider=Provider.NEXUSMODS, id="123"))
