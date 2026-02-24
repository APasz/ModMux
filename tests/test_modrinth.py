from __future__ import annotations

import sys
from pathlib import Path
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import ModID, Provider
from modmux.providers.modrinth import ModrinthClient
from modmux.toggles import ToggleMode


class TestModrinthClient(unittest.IsolatedAsyncioTestCase):
    async def test_get_mod_maps_fields(self) -> None:
        project_id = "proj-123"

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith(f"/project/{project_id}"):
                payload = {
                    "id": project_id,
                    "slug": "fabric-api",
                    "title": "Fabric API",
                    "body": "Desc",
                    "team": "team-1",
                    "published": "2023-01-01T00:00:00Z",
                    "updated": "2023-01-02T00:00:00Z",
                    "versions": ["v1"],
                    "categories": ["utility", {"name": "library"}],
                }
                return httpx.Response(200, json=payload, request=request)
            if url.endswith(f"/project/{project_id}/members"):
                members = [
                    {"role": "admin", "user": {"id": "u1", "username": "AuthorOne"}},
                ]
                return httpx.Response(200, json=members, request=request)
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
        self.assertEqual(mod.latest_version_id, "v1")
        self.assertEqual(str(mod.homepage), "https://modrinth.com/mod/fabric-api")

    async def test_get_mod_skips_member_lookup_by_default(self) -> None:
        project_id = "proj-123"
        calls: dict[str, int] = {"project": 0, "members": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith(f"/project/{project_id}"):
                calls["project"] += 1
                payload = {
                    "id": project_id,
                    "slug": "fabric-api",
                    "title": "Fabric API",
                    "team": "team-1",
                }
                return httpx.Response(200, json=payload, request=request)
            if url.endswith(f"/project/{project_id}/members"):
                calls["members"] += 1
                return httpx.Response(500, request=request)
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http:
            client = ModrinthClient(None, http=http)
            mod = await client.get_mod(ModID(provider=Provider.MODRINTH, id=project_id))

        self.assertEqual(calls["project"], 1)
        self.assertEqual(calls["members"], 0)
        self.assertEqual(mod.author.id, "team-1")
        self.assertEqual(mod.author.name, "team-1")
        self.assertEqual(mod.author.raw, {"team_id": "team-1"})
