from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import DependencyRelation, DownloadAccess, ModID, Provider
from modmux.modmux_errors import NotFound
from modmux.providers.modrinth import ModrinthClient
from modmux.toggles import ToggleMode


class TestModrinthClient(unittest.IsolatedAsyncioTestCase):
    async def test_get_mod_maps_fields(self) -> None:
        project_id = "proj-123"
        latest_version_id = "ver-123"

        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            params = parse_qs(urlsplit(str(request.url)).query)
            if path.endswith("/projects"):
                self.assertEqual(params.get("ids"), [f'["{project_id}"]'])
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": project_id,
                            "slug": "fabric-api",
                            "title": "Fabric API",
                            "body": "Desc",
                            "team": "team-1",
                            "published": "2023-01-01T00:00:00Z",
                            "updated": "2023-01-02T00:00:00Z",
                            "versions": [latest_version_id],
                            "categories": ["utility", {"name": "library"}],
                        }
                    ],
                    request=request,
                )
            if path.endswith("/versions"):
                self.assertEqual(params.get("ids"), [f'["{latest_version_id}"]'])
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": latest_version_id,
                            "project_id": project_id,
                            "name": "Fabric API 1.0.0",
                            "version_number": "1.0.0",
                            "changelog": "Updated internals",
                            "date_published": "2023-01-03T00:00:00Z",
                            "game_versions": ["1.20.1"],
                            "loaders": ["fabric"],
                            "files": [
                                {
                                    "url": "https://cdn.modrinth.com/data/proj-123/versions/ver-123/fabric-api.jar",
                                    "filename": "fabric-api.jar",
                                    "size": 12345,
                                }
                            ],
                            "dependencies": [
                                {
                                    "project_id": "dep-1",
                                    "version_id": "dep-ver-1",
                                    "dependency_type": "required",
                                },
                                {
                                    "project_id": "dep-2",
                                    "dependency_type": "embedded",
                                },
                                {
                                    "version_id": "external-ver",
                                    "file_name": "external.jar",
                                    "dependency_type": "optional",
                                },
                            ],
                        }
                    ],
                    request=request,
                )
            if path.endswith("/teams"):
                self.assertEqual(params.get("ids"), ['["team-1"]'])
                return httpx.Response(
                    200,
                    json=[[{"role": "admin", "user": {"id": "u1", "username": "AuthorOne"}}]],
                    request=request,
                )
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = ModrinthClient(None, http=http)
            mod_id = ModID(provider=Provider.MODRINTH, id=project_id)
            mod = await client.get_mod(mod_id, author_resolution=ToggleMode.ON)

        self.assertEqual(mod.id.id, project_id)
        self.assertEqual(mod.slug, "fabric-api")
        self.assertEqual(mod.name.value, "Fabric API")
        self.assertEqual(mod.author.id, "u1")
        self.assertEqual(mod.author.name, "AuthorOne")
        self.assertEqual(mod.author.raw.get("role"), "admin")
        self.assertEqual(mod.tags, ["utility", "library"])
        self.assertEqual(mod.latest_version_id, latest_version_id)
        self.assertEqual(str(mod.homepage), "https://modrinth.com/mod/fabric-api")
        assert mod.latest_version is not None
        self.assertEqual(mod.latest_version.version, "1.0.0")
        self.assertEqual(mod.latest_version.loaders, ["fabric"])
        self.assertEqual(mod.latest_version.game_versions, ["1.20.1"])
        self.assertEqual(len(mod.latest_version.files), 1)
        self.assertEqual(mod.latest_version.files[0].filename, "fabric-api.jar")
        self.assertEqual(mod.latest_version.files[0].download.access, DownloadAccess.DIRECT)
        self.assertEqual(
            str(mod.latest_version.files[0].download.url),
            "https://cdn.modrinth.com/data/proj-123/versions/ver-123/fabric-api.jar",
        )
        dependency_summary = [
            (dependency.id.id, dependency.version_req, dependency.relation)
            for dependency in mod.latest_version.dependencies
        ]
        self.assertEqual(
            dependency_summary,
            [
                ("dep-1", "dep-ver-1", DependencyRelation.REQUIRED),
                ("dep-2", None, DependencyRelation.EMBEDDED),
            ],
        )

    async def test_get_mod_skips_member_lookup_by_default(self) -> None:
        project_id = "proj-123"
        calls: dict[str, int] = {"projects": 0, "teams": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/projects"):
                calls["projects"] += 1
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": project_id,
                            "slug": "fabric-api",
                            "title": "Fabric API",
                            "team": "team-1",
                        }
                    ],
                    request=request,
                )
            if path.endswith("/teams"):
                calls["teams"] += 1
                return httpx.Response(500, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = ModrinthClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.MODRINTH, id=project_id))

        self.assertEqual(calls["projects"], 1)
        self.assertEqual(calls["teams"], 0)
        self.assertEqual(mod.author.id, "team-1")
        self.assertEqual(mod.author.name, "team-1")
        self.assertEqual(mod.author.raw, {"team_id": "team-1"})

    async def test_get_mods_batches_projects_and_team_members(self) -> None:
        calls: dict[str, int] = {"projects": 0, "teams": 0}
        latest_version_id = "ver-123"

        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            params = parse_qs(urlsplit(str(request.url)).query)
            if path.endswith("/projects"):
                calls["projects"] += 1
                self.assertEqual(params.get("ids"), ['["proj-123", "fabric-api"]'])
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": "proj-123",
                            "slug": "fabric-api",
                            "title": "Fabric API",
                            "team": "team-1",
                            "versions": [latest_version_id],
                        },
                    ],
                    request=request,
                )
            if path.endswith("/versions"):
                self.assertEqual(params.get("ids"), [f'["{latest_version_id}"]'])
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": latest_version_id,
                            "project_id": "proj-123",
                            "version_number": "1.0.0",
                            "files": [],
                            "dependencies": [],
                        }
                    ],
                    request=request,
                )
            if path.endswith("/teams"):
                calls["teams"] += 1
                self.assertEqual(params.get("ids"), ['["team-1"]'])
                return httpx.Response(
                    200,
                    json=[[{"role": "owner", "user": {"id": "u1", "username": "AuthorOne"}}]],
                    request=request,
                )
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = ModrinthClient(None, http=http)
            mods = await client.get_mods(
                [
                    ModID(provider=Provider.MODRINTH, id="proj-123"),
                    ModID(provider=Provider.MODRINTH, id="fabric-api"),
                ],
                author_resolution=ToggleMode.ON,
            )

        self.assertEqual(calls, {"projects": 1, "teams": 1})
        self.assertEqual([mod.id.id for mod in mods], ["proj-123", "proj-123"])
        self.assertEqual([mod.author.name for mod in mods], ["AuthorOne", "AuthorOne"])
        self.assertEqual([mod.latest_version_id for mod in mods], [latest_version_id, latest_version_id])
        self.assertTrue(all(mod.latest_version is not None for mod in mods))

    async def test_get_mod_raises_not_found_when_batch_response_omits_project(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/projects"):
                return httpx.Response(200, json=[], request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = ModrinthClient(None, http=http)
            with self.assertRaises(NotFound):
                await client.get_mod(ModID(provider=Provider.MODRINTH, id="missing"))
