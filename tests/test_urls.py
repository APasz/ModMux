from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux import Provider, parse_url


class TestParseUrl(unittest.TestCase):
    def test_parse_url_rejects_empty(self) -> None:
        self.assertIsNone(parse_url(""))
        self.assertIsNone(parse_url("   "))

    def test_parse_url_known_providers(self) -> None:
        cases = [
            ("https://modrinth.com/mod/fabric-api", Provider.MODRINTH, "fabric-api", None),
            ("https://www.curseforge.com/minecraft/mc-mods/jei", Provider.CURSEFORGE, "jei", "minecraft"),
            ("https://www.nexusmods.com/skyrim/mods/123", Provider.NEXUSMODS, "123", "skyrim"),
            ("https://skyrim.nexusmods.com/mods/123", Provider.NEXUSMODS, "123", "skyrim"),
            ("https://mod.io/g/4321/m/some-mod", Provider.MODIO, "some-mod", "4321"),
            ("https://steamcommunity.com/sharedfiles/filedetails/?id=12345&appid=480", Provider.STEAM, "12345", "480"),
            ("https://mods.factorio.com/mod/rso-mod", Provider.WUBE, "rso-mod", None),
        ]
        for url, provider, mod_id, game in cases:
            with self.subTest(url=url):
                parsed = parse_url(url)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.provider, provider)
                self.assertEqual(parsed.id, mod_id)
                self.assertEqual(parsed.game, game)

    def test_parse_url_unknown_host(self) -> None:
        self.assertIsNone(parse_url("https://example.com/mod/123"))
