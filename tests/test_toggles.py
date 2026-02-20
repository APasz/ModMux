from __future__ import annotations

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux import toggles
from modmux.toggles import ToggleMode, UNDEFINED, coerce_toggle_mode, resolve_toggle


class TestToggleHelpers(unittest.TestCase):
    def test_coerce_toggle_mode_accepts_bool_and_enum(self) -> None:
        self.assertIs(coerce_toggle_mode(ToggleMode.AUTO), ToggleMode.AUTO)
        self.assertIs(coerce_toggle_mode(True), ToggleMode.ON)
        self.assertIs(coerce_toggle_mode(False), ToggleMode.OFF)

    def test_resolve_toggle_handles_auto_and_undefined(self) -> None:
        self.assertTrue(resolve_toggle(ToggleMode.AUTO, default=True))
        self.assertFalse(resolve_toggle(UNDEFINED, default=False))

    def test_invalid_toggle_value_raises(self) -> None:
        with self.assertRaises(TypeError):
            coerce_toggle_mode("invalid")

    def test_hikari_undefined_interop_without_dependency(self) -> None:
        fake_hikari_undefined = object()
        original = toggles.HIKARI_UNDEFINED
        toggles.HIKARI_UNDEFINED = fake_hikari_undefined
        try:
            self.assertTrue(toggles.is_undefined(fake_hikari_undefined))
            self.assertFalse(resolve_toggle(fake_hikari_undefined, default=False))
        finally:
            toggles.HIKARI_UNDEFINED = original
