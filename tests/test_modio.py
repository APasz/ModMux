from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.cache import ModioLookupCache
from modmux.models import DependencyRelation, DownloadAccess, ModID, Provider
from modmux.providers.modio import ModioClient, ModioCreds


class TestModioClient(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_download_mints_direct_url(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            self.assertEqual(path, "/v1/games/456/mods/123/files/999")
            return httpx.Response(
                200,
                json={
                    "id": 999,
                    "download": {
                        "binary_url": "https://api.mod.io/download/999?signature=token",
                        "date_expires": 1700003600,
                    },
                },
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            creds = ModioCreds.model_validate({"token": "key", "user": "user123"})
            client = ModioClient(creds, http=http)
            download = await client.resolve_download(ModID(provider=Provider.MODIO, id="123", game="456"), "999")

        self.assertEqual(download.access, DownloadAccess.DIRECT)
        self.assertEqual(str(download.url), "https://api.mod.io/download/999?signature=token")
        self.assertIsNotNone(download.expires_at)

    async def test_get_mod_populates_latest_version_and_dependencies(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/games/456/mods") and request.url.params.get("id-in") == "123":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 123,
                                "game_id": 456,
                                "name": "Base Name",
                                "description": "Base Desc",
                                "date_added": "2023-01-01T00:00:00Z",
                                "date_updated": "2023-01-02T00:00:00Z",
                                "dependencies": True,
                                "modfile": {
                                    "id": 999,
                                    "filename": "base-name.zip",
                                    "filesize": 2048,
                                    "version": "1.2.3",
                                    "changelog": "Fixed things",
                                    "date_added": 1700000000,
                                    "download": {
                                        "binary_url": "https://api.mod.io/v1/games/456/mods/123/files/999/download/token",
                                        "date_expires": 1700003600,
                                    },
                                },
                                "submitted_by": {"id": 123, "username": "user-123"},
                            }
                        ]
                    },
                    request=request,
                )
            if path.endswith("/games/456/mods/123/dependencies"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": 77, "game_id": 456, "name": "Dep One"},
                            {"id": 88, "game_id": 456, "name": "Dep Two"},
                        ]
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            creds = ModioCreds.model_validate({"token": "key", "user": "user123"})
            client = ModioClient(creds, http=http)
            mod = await client.get_mod(ModID(provider=Provider.MODIO, id="123", game="456"))

        self.assertEqual(mod.latest_version_id, "999")
        self.assertIsNotNone(mod.latest_version)
        assert mod.latest_version is not None
        self.assertEqual(mod.latest_version.version, "1.2.3")
        self.assertEqual(mod.latest_version.files[0].filename, "base-name.zip")
        self.assertEqual(mod.latest_version.files[0].size_bytes, 2048)
        self.assertEqual(mod.latest_version.files[0].download.access, DownloadAccess.DIRECT)
        expires_at = mod.latest_version.files[0].download.expires_at
        assert expires_at is not None
        self.assertEqual(expires_at.year, 2023)
        self.assertEqual([dependency.id.id for dependency in mod.latest_version.dependencies], ["77", "88"])
        self.assertEqual(
            [dependency.relation for dependency in mod.latest_version.dependencies],
            [DependencyRelation.REQUIRED, DependencyRelation.REQUIRED],
        )
        self.assertIsNotNone(mod.created_at)
        self.assertIsNotNone(mod.updated_at)

    async def test_get_mod_populates_translations(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/games/456/mods"):
                locale = request.headers.get("Accept-Language")
                query = dict(request.url.params)
                self.assertEqual(query.get("id-in"), "123")
                if locale == "ja":
                    payload = {
                        "data": [
                            {
                                "id": 123,
                                "name": "JP Name",
                                "summary": "JP Summary",
                                "description": "JP Desc",
                            }
                        ]
                    }
                else:
                    payload = {
                        "data": [
                            {
                                "id": 123,
                                "name": "Base Name",
                                "summary": "Base Summary",
                                "description": "Base Desc",
                            }
                        ]
                    }
                return httpx.Response(200, json=payload, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            creds = ModioCreds.model_validate({"token": "key", "user": "user123"})
            client = ModioClient(creds, http=http)
            mod_id = ModID(provider=Provider.MODIO, id="123", game="456")
            mod = await client.get_mod(mod_id, locales=["ja"])

        self.assertEqual(mod.name.value, "Base Name")
        self.assertEqual(mod.name.translations["ja"], "JP Name")
        self.assertIsNotNone(mod.description_md)
        assert mod.description_md is not None
        self.assertEqual(mod.description_md.translations["ja"], "JP Desc")

    async def test_get_mods_batches_numeric_ids_and_translations(self) -> None:
        calls: dict[str, int] = {"mods": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/games/456/mods"):
                calls["mods"] += 1
                locale = request.headers.get("Accept-Language")
                requested_ids = request.url.params.get("id-in", "").split(",")
                data = []
                for mod_id in requested_ids:
                    if not mod_id:
                        continue
                    name = f"Base {mod_id}"
                    description = f"Base Desc {mod_id}"
                    if locale == "ja":
                        name = f"JP {mod_id}"
                        description = f"JP Desc {mod_id}"
                    data.append(
                        {
                            "id": int(mod_id),
                            "game_id": 456,
                            "name": name,
                            "description": description,
                            "submitted_by": {"id": int(mod_id), "username": f"user-{mod_id}"},
                        }
                    )
                return httpx.Response(200, json={"data": data}, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            creds = ModioCreds.model_validate({"token": "key", "user": "user123"})
            client = ModioClient(creds, http=http)
            mod_ids = [
                ModID(provider=Provider.MODIO, id="123", game="456"),
                ModID(provider=Provider.MODIO, id="456", game="456"),
            ]
            mods = await client.get_mods(mod_ids, locales=["ja"])

        self.assertEqual(calls["mods"], 2)
        self.assertEqual([mod.id.id for mod in mods], ["123", "456"])
        self.assertEqual(mods[0].name.value, "Base 123")
        self.assertEqual(mods[0].name.translations["ja"], "JP 123")
        self.assertEqual(mods[1].name.value, "Base 456")
        self.assertEqual(mods[1].name.translations["ja"], "JP 456")
        self.assertEqual(mods[0].author.name, "user-123")
        self.assertEqual(mods[1].author.name, "user-456")

    async def test_get_mods_resolves_slug_inputs_and_uses_cache(self) -> None:
        calls: dict[str, int] = {"games": 0, "slug": 0, "mods": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/games"):
                calls["games"] += 1
                self.assertEqual(request.url.params.get("name_id"), "factorio")
                return httpx.Response(200, json={"data": [{"id": 456, "name_id": "factorio"}]}, request=request)
            if path.endswith("/games/456/mods") and request.url.params.get("name_id") == "space-exploration":
                calls["slug"] += 1
                return httpx.Response(
                    200,
                    json={"data": [{"id": 123, "name_id": "space-exploration"}]},
                    request=request,
                )
            if path.endswith("/games/456/mods") and request.url.params.get("id-in") == "123":
                calls["mods"] += 1
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 123,
                                "game_id": 456,
                                "name": "Space Exploration",
                                "name_id": "space-exploration",
                                "submitted_by": {"id": 1, "username": "earendel"},
                            }
                        ]
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        cache = ModioLookupCache()
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            creds = ModioCreds.model_validate({"token": "key", "user": "user123"})
            client = ModioClient(creds, http=http, cache=cache)
            mod_id = ModID(provider=Provider.MODIO, id="space-exploration", game="factorio")
            first = await client.get_mod(mod_id)
            second = await client.get_mod(mod_id)

        self.assertEqual(calls, {"games": 1, "slug": 1, "mods": 2})
        self.assertEqual(first.id.id, "123")
        self.assertEqual(second.id.id, "123")
        self.assertEqual(first.slug, "space-exploration")

    async def test_get_mods_skips_failed_localization(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/games/456/mods") and request.url.params.get("id-in") == "123":
                if request.headers.get("Accept-Language") == "ja":
                    return httpx.Response(500, request=request)
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 123,
                                "game_id": 456,
                                "name": "Base Name",
                                "description": "Base Desc",
                                "submitted_by": {"id": 123, "username": "user-123"},
                            }
                        ]
                    },
                    request=request,
                )
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            creds = ModioCreds.model_validate({"token": "key", "user": "user123"})
            client = ModioClient(creds, http=http)
            mods = await client.get_mods([ModID(provider=Provider.MODIO, id="123", game="456")], locales=["ja"])

        self.assertEqual(mods[0].name.value, "Base Name")
        self.assertEqual(mods[0].name.translations, {})

    async def test_get_mods_requires_access_and_game(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, request=request))
        async with httpx.AsyncClient(transport=transport) as http:
            client = ModioClient(None, http=http)
            with self.assertRaises(ValueError):
                await client.get_mods([ModID(provider=Provider.MODIO, id="123", game="456")])

            creds = ModioCreds.model_validate({"token": "key", "user": "user123"})
            client = ModioClient(creds, http=http)
            with self.assertRaises(ValueError):
                await client.get_mods([ModID(provider=Provider.MODIO, id="123")])
