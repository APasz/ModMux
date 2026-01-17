from __future__ import annotations

import sys
from pathlib import Path
import unittest
from urllib.parse import parse_qs

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import ModID, Provider
from modmux.providers.steam import SteamClient


class TestSteamClient(unittest.IsolatedAsyncioTestCase):
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
