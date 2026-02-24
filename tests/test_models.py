from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import Author, LocalisedText, Mod, ModID, ModVersion, Provider


class TestModels(unittest.TestCase):
    def test_mod_defaults(self) -> None:
        mod_id = ModID(provider=Provider.MODRINTH, id="fabric-api")
        author = Author(provider=Provider.MODRINTH, id="a1", name="Author")
        mod = Mod(provider=Provider.MODRINTH, id=mod_id, name=LocalisedText(value="Fabric API"), author=author)

        self.assertEqual(mod.tags, [])
        self.assertEqual(mod.raw, {})
        self.assertEqual(mod.author.raw, {})
        self.assertIsNone(mod.latest_version)
        self.assertIsInstance(mod.name, LocalisedText)
        self.assertEqual(mod.name.value, "Fabric API")

    def test_mod_version_defaults(self) -> None:
        mod_id = ModID(provider=Provider.MODRINTH, id="fabric-api")
        version = ModVersion(id=mod_id)

        self.assertEqual(version.files, [])
        self.assertEqual(version.dependencies, [])
        self.assertEqual(version.game_versions, [])

    def test_provider_enum(self) -> None:
        self.assertEqual(Provider("MODRINTH"), Provider.MODRINTH)
