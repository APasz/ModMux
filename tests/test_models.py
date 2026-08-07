from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import (
    Author,
    Dependency,
    DependencyRelation,
    DownloadAccess,
    DownloadInfo,
    FileAsset,
    LocalisedText,
    Mod,
    ModID,
    ModVersion,
    Provider,
)
from modmux.providers.modrinth import ModrinthCreds


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

    def test_dependency_defaults(self) -> None:
        dependency = Dependency(id=ModID(provider=Provider.WUBE, id="base"))

        self.assertEqual(dependency.relation, DependencyRelation.REQUIRED)

    def test_file_asset_defaults_to_unavailable_download(self) -> None:
        asset = FileAsset(file_id="file-1", filename="file.zip")

        self.assertEqual(asset.download.access, DownloadAccess.UNAVAILABLE)
        self.assertIsNone(asset.download.url)

    def test_download_info_rejects_url_for_resolvable_access(self) -> None:
        with self.assertRaises(ValidationError):
            DownloadInfo.model_validate({"access": DownloadAccess.RESOLVABLE, "url": "https://example.com/file.zip"})

    def test_provider_enum(self) -> None:
        self.assertEqual(Provider("MODRINTH"), Provider.MODRINTH)

    def test_provider_creds_are_hashable(self) -> None:
        creds = ModrinthCreds.model_validate({"token": "secret"})
        same_creds = ModrinthCreds.model_validate({"token": "secret"})

        self.assertIsInstance(hash(creds), int)
        self.assertEqual(hash(creds), hash(same_creds))

    def test_localised_text_is_not_hashable_with_mutable_translations(self) -> None:
        text = LocalisedText(value="Name", translations={"en": "Name"})

        with self.assertRaises(TypeError):
            hash(text)
