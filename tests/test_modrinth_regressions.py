from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import ModID, Provider
from modmux.providers.modrinth import ModrinthClient


class TestModrinthRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_latest_version_uses_release_id_when_version_number_missing(self) -> None:
        async with httpx.AsyncClient() as http:
            client = ModrinthClient(None, http=http)
            latest_version = client._build_latest_version(
                ModID(provider=Provider.MODRINTH, id="proj-123"),
                {
                    "id": "ver-123",
                    "name": "Fabric API",
                    "dependencies": [
                        {
                            "project_id": "dep-1",
                            "version_id": "dep-ver-1",
                            "dependency_type": "required",
                        }
                    ],
                },
            )

        self.assertIsNotNone(latest_version)
        assert latest_version is not None
        self.assertEqual(latest_version.version, "ver-123")
