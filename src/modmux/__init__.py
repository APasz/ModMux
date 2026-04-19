"""Public ModMux API."""

from .client import Muxer, modmux_client
from .models import (
    Author,
    Dependency,
    DependencyRelation,
    FileAsset,
    LocaleTag,
    LocalisedText,
    Mod,
    ModID,
    ModSummary,
    ModVersion,
    Provider,
    ProviderCreds,
)
from .modmux_errors import AuthError, BatchResponseError, ModMuxError, NotFound, ProviderError, RateLimited
from .providers._base import ProviderClient
from .providers.colour import Colour, ColourValue
from .providers.curseforge import CurseforgeCreds
from .providers.modio import ModioCreds
from .providers.modrinth import ModrinthCreds
from .providers.nexusmods import NexusCreds
from .providers.steam import SteamCreds
from .providers.wube import WubeCreds
from .toggles import ToggleMode
from .utils.urls import parse_url

__all__ = [
    "AuthError",
    "Author",
    "BatchResponseError",
    "Colour",
    "ColourValue",
    "CurseforgeCreds",
    "Dependency",
    "DependencyRelation",
    "FileAsset",
    "LocaleTag",
    "LocalisedText",
    "Mod",
    "ModID",
    "ModMuxError",
    "ModSummary",
    "ModVersion",
    "ModioCreds",
    "ModrinthCreds",
    "Muxer",
    "NexusCreds",
    "NotFound",
    "Provider",
    "ProviderClient",
    "ProviderCreds",
    "ProviderError",
    "RateLimited",
    "SteamCreds",
    "ToggleMode",
    "modmux_client",
    "parse_url",
    "WubeCreds",
]
