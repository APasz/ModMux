from __future__ import annotations

import sys
from pathlib import Path
import unittest

import httpx
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import ModID, Provider
from modmux.providers.modio import ModioClient, ModioCreds


class TestModioClient(unittest.IsolatedAsyncioTestCase):
    async def test_get_mod_populates_translations(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = urlsplit(str(request.url)).path
            if path.endswith("/games/456/mods/123"):
                locale = request.headers.get("Accept-Language")
                if locale == "ja":
                    payload = {
                        "data": {
                            "id": 123,
                            "name": "JP Name",
                            "summary": "JP Summary",
                            "description": "JP Desc",
                        }
                    }
                else:
                    payload = {
                        "data": {
                            "id": 123,
                            "name": "Base Name",
                            "summary": "Base Summary",
                            "description": "Base Desc",
                        }
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
