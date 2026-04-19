from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import ModID, Provider
from modmux.providers.curseforge import CurseforgeClient


def _ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, request=request))


class TestCurseforgeClient(unittest.IsolatedAsyncioTestCase):
    async def test_get_mod_maps_slug_lookup_and_fields(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/mods/search"):
                self.assertEqual(request.url.params.get("gameId"), "432")
                self.assertEqual(request.url.params.get("slug"), "jei")
                return httpx.Response(200, json={"data": [{"id": 238222}]}, request=request)
            if path.endswith("/mods/238222"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": 238222,
                            "gameId": 432,
                            "name": "Just Enough Items",
                            "slug": "jei",
                            "summary": "Recipe viewer",
                            "dateCreated": "2023-01-01T00:00:00Z",
                            "dateModified": "2023-01-02T00:00:00Z",
                            "links": {"websiteUrl": "https://example.com/jei"},
                            "categories": ["utility", {"name": "api"}],
                            "authors": [{"id": 7, "name": "mezz"}],
                            "latestFiles": [{"id": 99}],
                        }
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = CurseforgeClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.CURSEFORGE, id="jei", game="432"))

        self.assertEqual(mod.id.id, "238222")
        self.assertEqual(mod.id.game, "432")
        self.assertEqual(mod.slug, "jei")
        self.assertEqual(mod.name.value, "Just Enough Items")
        self.assertEqual(mod.author.id, "7")
        self.assertEqual(mod.author.name, "mezz")
        self.assertEqual(mod.tags, ["utility", "api"])
        self.assertEqual(mod.latest_version_id, "99")
        self.assertEqual(str(mod.homepage), "https://example.com/jei")

    async def test_get_mod_slug_requires_game(self) -> None:
        async with httpx.AsyncClient(transport=_ok_transport()) as http:
            client = CurseforgeClient(None, http=http)
            with self.assertRaises(ValueError):
                await client.get_mod(ModID(provider=Provider.CURSEFORGE, id="jei"))

    async def test_get_mods_batches_same_game_and_falls_back_without_game(self) -> None:
        calls: dict[str, int] = {"search": 0, "batch": 0, "single": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/mods/search"):
                calls["search"] += 1
                return httpx.Response(200, json={"data": [{"id": 238222}]}, request=request)
            if path.endswith("/mods") and request.method == "POST":
                calls["batch"] += 1
                self.assertEqual(request.content.decode(), '{"modIds":[238222]}')
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 238222,
                                "gameId": 432,
                                "name": "Just Enough Items",
                                "slug": "jei",
                                "authors": [{"id": 7, "name": "mezz"}],
                            }
                        ]
                    },
                    request=request,
                )
            if path.endswith("/mods/245755"):
                calls["single"] += 1
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": 245755,
                            "gameId": 432,
                            "name": "Xaero's Minimap",
                            "slug": "xaeros-minimap",
                        }
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = CurseforgeClient(None, http=http)
            mods = await client.get_mods(
                [
                    ModID(provider=Provider.CURSEFORGE, id="jei", game="432"),
                    ModID(provider=Provider.CURSEFORGE, id="245755"),
                ]
            )

        self.assertEqual(calls, {"search": 1, "batch": 1, "single": 1})
        self.assertEqual([mod.id.id for mod in mods], ["238222", "245755"])
        self.assertEqual([mod.slug for mod in mods], ["jei", "xaeros-minimap"])
