"""Tri-state toggle helpers and optional interop sentinels."""

from enum import StrEnum


class ToggleMode(StrEnum):
    """Generic tri-state toggle mode."""

    AUTO = "auto"
    ON = "on"
    OFF = "off"


class UndefinedType:
    """Sentinel type representing an omitted argument."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNDEFINED"


UNDEFINED = UndefinedType()

try:  # Optional interoperability with hikari without hard dependency.
    from hikari.undefined import UNDEFINED as HIKARI_UNDEFINED  # pyright: ignore[reportMissingImports]
except Exception:  # pragma: no cover - depends on optional third-party package
    HIKARI_UNDEFINED = None


def is_undefined(value: object) -> bool:
    """Return True when value is an explicit undefined sentinel."""
    return value is UNDEFINED or (HIKARI_UNDEFINED is not None and value is HIKARI_UNDEFINED)


def coerce_toggle_mode(
    value: ToggleMode | bool | UndefinedType | object, *, default: ToggleMode = ToggleMode.AUTO
) -> ToggleMode:
    """Coerce toggle-like values into ToggleMode.

    Accepted inputs:
    - ToggleMode: used directly.
    - bool: True -> ON, False -> OFF.
    - UNDEFINED / hikari.UNDEFINED: resolved as `default`.
    """
    if is_undefined(value):
        return default
    if isinstance(value, ToggleMode):
        return value
    if value is True:
        return ToggleMode.ON
    if value is False:
        return ToggleMode.OFF
    raise TypeError(f"Invalid toggle value: {value!r}")


def resolve_toggle(value: ToggleMode | bool | UndefinedType | object, *, default: bool) -> bool:
    """Resolve a toggle-like value to a concrete boolean."""
    mode = coerce_toggle_mode(value)
    if mode is ToggleMode.AUTO:
        return default
    return mode is ToggleMode.ON
