from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux.models import Provider
from modmux.providers.modio import ModioCreds
from modmux.providers.modrinth import ModrinthCreds
from modmux.providers.nexusmods import NexusCreds
from modmux.providers.steam import SteamCreds
from modmux.providers.wube import WubeCreds


class TestCreds(unittest.TestCase):
    def test_modrinth_aliases(self) -> None:
        creds = ModrinthCreds.model_validate({"token": "secret"})
        self.assertEqual(creds.provider, Provider.MODRINTH)
        self.assertEqual(creds.headers(), {"Authorization": "secret"})

    def test_modio_params_and_base(self) -> None:
        creds = ModioCreds.model_validate({"token": "key", "user": "user123"})
        self.assertEqual(creds.params(), {"api_key": "key"})
        self.assertEqual(creds.format_base("https://api.mod.io/v1"), "https://u-user123.modapi.io/v1")

    def test_nexus_headers(self) -> None:
        creds = NexusCreds.model_validate({"token": "k"})
        self.assertEqual(creds.headers(), {"apikey": "k"})

    def test_steam_optional(self) -> None:
        creds = SteamCreds.model_validate({})
        self.assertEqual(creds.params(), {})

    def test_wube_download_params(self) -> None:
        creds = WubeCreds.model_validate({"token": "token", "user": "alice"})
        self.assertEqual(creds.params(), {})
        self.assertEqual(creds.download_params(), {"username": "alice", "token": "token"})
        self.assertTrue(creds.has_download_credentials())
