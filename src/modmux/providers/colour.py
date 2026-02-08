"""Shared provider colour metadata helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEX_PATTERN = re.compile(r"^#?(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")


@dataclass(frozen=True, slots=True)
class ColourValue:
    """A validated and normalised RGB colour value."""

    _hex: str

    def __init__(self, value: str) -> None:
        cleaned = value.strip()
        if not _HEX_PATTERN.fullmatch(cleaned):
            raise ValueError(f"Invalid hex colour {value!r}; expected #RGB or #RRGGBB")
        cleaned = cleaned.removeprefix("#")
        if len(cleaned) == 3:
            cleaned = "".join(ch * 2 for ch in cleaned)
        object.__setattr__(self, "_hex", f"#{cleaned.lower()}")

    def as_hex(self) -> str:
        """Return the normalised hex value."""
        return self._hex

    def as_rgb(self) -> tuple[int, int, int]:
        """Return the colour as an RGB tuple."""
        raw = self._hex.removeprefix("#")
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))

    def as_rgb_css(self) -> str:
        """Return the colour as an rgb(...) CSS string."""
        red, green, blue = self.as_rgb()
        return f"rgb({red}, {green}, {blue})"

    def as_int(self) -> int:
        """Return the RGB value as an integer (0xRRGGBB)."""
        return int(self._hex.removeprefix("#"), 16)


@dataclass(frozen=True, slots=True)
class Colour:
    """Provider colour palette with up to five swatches."""

    primary: ColourValue
    secondary: ColourValue | None = None
    tertiary: ColourValue | None = None
    quaternary: ColourValue | None = None
    quinary: ColourValue | None = None

    def __init__(
        self,
        primary: str,
        secondary: str | None = None,
        tertiary: str | None = None,
        quaternary: str | None = None,
        quinary: str | None = None,
    ) -> None:
        object.__setattr__(self, "primary", ColourValue(primary))
        object.__setattr__(self, "secondary", ColourValue(secondary) if secondary is not None else None)
        object.__setattr__(self, "tertiary", ColourValue(tertiary) if tertiary is not None else None)
        object.__setattr__(self, "quaternary", ColourValue(quaternary) if quaternary is not None else None)
        object.__setattr__(self, "quinary", ColourValue(quinary) if quinary is not None else None)

    def as_hexes(self) -> tuple[str, ...]:
        """Return all configured swatches as hex values."""
        values = [self.primary.as_hex()]
        if self.secondary is not None:
            values.append(self.secondary.as_hex())
        if self.tertiary is not None:
            values.append(self.tertiary.as_hex())
        if self.quaternary is not None:
            values.append(self.quaternary.as_hex())
        if self.quinary is not None:
            values.append(self.quinary.as_hex())
        return tuple(values)
