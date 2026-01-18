"""Public ModMux API."""

from .client import Muxer, modmux_client
from .models import (
    Author,
    Dependency,
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
from .providers.curseforge import CurseforgeCreds
from .providers.modio import ModioCreds
from .providers.modrinth import ModrinthCreds
from .providers.nexusmods import NexusCreds
from .providers.steam import SteamCreds
from .providers.wube import WubeCreds
from .utils.urls import parse_url

__all__ = [
    "Author",
    "CurseforgeCreds",
    "Dependency",
    "FileAsset",
    "LocaleTag",
    "LocalisedText",
    "Mod",
    "ModID",
    "ModSummary",
    "ModVersion",
    "ModioCreds",
    "ModrinthCreds",
    "Muxer",
    "NexusCreds",
    "Provider",
    "ProviderCreds",
    "SteamCreds",
    "modmux_client",
    "parse_url",
    "WubeCreds",
]
