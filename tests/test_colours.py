from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux import providers
from modmux.models import Provider
from modmux.providers.colour import Colour, ColourValue
from modmux.utils.discovery import REGISTRY, load_providers


class TestColourValue(unittest.TestCase):
    def test_hex_normalisation(self) -> None:
        self.assertEqual(ColourValue("#AbC").as_hex(), "#aabbcc")
        self.assertEqual(ColourValue("123456").as_hex(), "#123456")

    def test_rgb_conversions(self) -> None:
        value = ColourValue("#1a2b3c")
        self.assertEqual(value.as_rgb(), (26, 43, 60))
        self.assertEqual(value.as_rgb_css(), "rgb(26, 43, 60)")
        self.assertEqual(value.as_int(), 0x1A2B3C)

    def test_invalid_value_raises(self) -> None:
        with self.assertRaises(ValueError):
            ColourValue("not-a-colour")


class TestProviderColours(unittest.TestCase):
    def test_registered_provider_colours(self) -> None:
        load_providers(providers)
        self.assertGreaterEqual(len(REGISTRY), len(Provider))
        for provider in Provider:
            self.assertIn(provider, REGISTRY)
            cls = REGISTRY[provider]
            self.assertIn("colour", cls.__dict__, f"{provider} must define `colour` on the subclass")
            self.assertIsInstance(cls.colour, Colour)
            self.assertTrue(cls.colour.primary.as_hex().startswith("#"))

    def test_palette_shape(self) -> None:
        colour = Colour("#123456", "#abcdef", "#fedcba", "#a1a1a1", "#aba521")
        self.assertEqual(colour.as_hexes(), ("#123456", "#abcdef", "#fedcba", "#a1a1a1", "#aba521"))
