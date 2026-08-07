"""Nexus Mods provider integration."""

from collections.abc import Mapping
from typing import cast
from urllib.parse import urlsplit

from httpx import AsyncClient
from pydantic import AnyHttpUrl, Field, SecretStr

from ..models import (
    Author,
    DownloadAccess,
    DownloadInfo,
    FileAsset,
    LocaleTag,
    LocalisedText,
    Mod,
    ModID,
    ModVersion,
    Provider,
    ProviderCreds,
)
from ..modmux_errors import ProviderError
from ..toggles import ToggleMode, UndefinedType
from ..utils.discovery import register
from ._base import ProviderClient
from ._helpers import coalesce, parse_timestamp
from .colour import Colour


class NexusCreds(ProviderCreds):
    """Credential model for Nexus Mods API access."""

    provider: Provider = Provider.NEXUSMODS
    api_key: SecretStr = Field(alias="token")

    def headers(self) -> dict[str, str]:
        return {"apikey": self.api_key.get_secret_value()}


def _extract_file_payloads(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        return []
    return [entry for entry in raw_files if isinstance(entry, dict)]


def _is_visible_file(entry: Mapping[str, object]) -> bool:
    category_id = entry.get("category_id")
    if isinstance(category_id, int) and category_id in {6, 7}:
        return False
    category_name = entry.get("category_name")
    if isinstance(category_name, str) and category_name.strip().casefold() in {"deleted", "archived"}:
        return False
    return True


def _file_matches_version(entry: Mapping[str, object], version: str) -> bool:
    for key in ("version", "mod_version"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip() == version:
            return True
    return False


def _select_latest_file_payloads(mod_payload: Mapping[str, object], files_payload: object) -> list[dict[str, object]]:
    visible_files = [entry for entry in _extract_file_payloads(files_payload) if _is_visible_file(entry)]
    if not visible_files:
        return []

    raw_version = coalesce(mod_payload.get("version"))
    if raw_version is None:
        return visible_files

    latest_version = str(raw_version).strip()
    if not latest_version:
        return visible_files

    matching_files = [entry for entry in visible_files if _file_matches_version(entry, latest_version)]
    if matching_files:
        return matching_files
    return []


def _build_file_asset(entry: Mapping[str, object]) -> FileAsset | None:
    file_id = entry.get("file_id")
    filename = coalesce(entry.get("file_name"), entry.get("name"))
    if file_id is None or filename is None:
        return None
    size = entry.get("size")
    size_bytes = size if isinstance(size, int) else None
    return FileAsset(
        file_id=str(file_id),
        filename=str(filename),
        size_bytes=size_bytes,
        download=DownloadInfo(access=DownloadAccess.RESOLVABLE, requires_authentication=True),
    )


@register
class NexusmodsClient(ProviderClient):
    """Client for Nexus Mods mod metadata."""

    name: Provider = Provider.NEXUSMODS
    display_name: str = "Nexus Mods"
    colour: Colour = Colour("#FB923C", "#FFFFFF", "#303030")
    base = "https://api.nexusmods.com/v1"
    creds_model = NexusCreds
    domains = ("nexusmods.com",)

    def __init__(self, creds: NexusCreds | None, *, http: AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)

    def _build_latest_version(
        self,
        mod_key: ModID,
        mod_payload: Mapping[str, object],
        files_payload: object,
    ) -> ModVersion | None:
        selected_files = _select_latest_file_payloads(mod_payload, files_payload)
        latest_files = [asset for entry in selected_files if (asset := _build_file_asset(entry)) is not None]

        raw_version = coalesce(mod_payload.get("version"))
        version = str(raw_version).strip() if raw_version is not None else None
        if version == "":
            version = None

        if version is None and not latest_files:
            return None

        published_candidates = [
            timestamp
            for entry in selected_files
            if (timestamp := parse_timestamp(entry.get("uploaded_time"))) is not None
        ]
        published_at = max(published_candidates) if published_candidates else None

        return ModVersion(
            id=mod_key,
            name=version,
            version=version,
            published_at=published_at,
            files=latest_files,
            raw={
                "version": version,
                "files": [dict(entry) for entry in selected_files],
            },
        )

    async def resolve_download(self, mod_id: ModID, file_id: str) -> DownloadInfo:
        """Mint a direct Nexus Mods CDN URL for a release file."""
        if not mod_id.game:
            raise ValueError("Nexus Mods requires ModID.game (game domain name).")

        file_value = str(file_id).strip()
        if not file_value.isdigit():
            raise ValueError("Nexus Mods file ids must be numeric.")

        payload = await self._get_json(f"games/{mod_id.game}/mods/{mod_id.id}/files/{file_value}/download_link.json")
        if not isinstance(payload, list):
            raise ProviderError(f"{self.name}: unexpected download URL response shape")

        for mirror in payload:
            if not isinstance(mirror, Mapping):
                continue
            url = coalesce(mirror.get("URI"), mirror.get("uri"))
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return DownloadInfo.direct(url)

        raise ProviderError(f"{self.name}: download URL response did not include a valid URL")

    @classmethod
    def parse_url(cls, url: str) -> ModID | None:
        parts = urlsplit(cls._normalise_url(url))
        if not cls._match_domain(parts.hostname):
            return None
        segments = cls._path_segments(parts.path)
        game = None
        host = parts.hostname.lower() if parts.hostname else ""
        if host.endswith(".nexusmods.com"):
            host = host[4:] if host.startswith("www.") else host
            if host != "nexusmods.com":
                game = host[: -len(".nexusmods.com")]
        if len(segments) >= 3 and segments[1] == "mods":
            game = game or segments[0]
            return ModID(provider=Provider.NEXUSMODS, id=segments[2], game=game)
        if len(segments) >= 2 and segments[0] == "mods" and game:
            return ModID(provider=Provider.NEXUSMODS, id=segments[1], game=game)
        return None

    async def get_mod(
        self,
        mod_id: ModID,
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> Mod:
        """Fetch a single mod from Nexus Mods.

        Args;
            mod_id: Provider-specific mod identifier.
            locales: Optional locale tags to request translations for.
            author_resolution: Author enrichment toggle.

        Returns;
            A normalised Mod instance.

        Raises;
            ValueError: If the game domain is missing from the ModID.
        """
        if not mod_id.game:
            raise ValueError("Nexus Mods requires ModID.game (game domain name).")
        data = await self._get_json(f"games/{mod_id.game}/mods/{mod_id.id}.json")
        files_data = await self._get_json(f"games/{mod_id.game}/mods/{mod_id.id}/files.json")

        user = data.get("user")
        if not isinstance(user, dict):
            user = {}

        author_name = coalesce(
            data.get("author"),
            data.get("uploaded_by"),
            user.get("name"),
            user.get("username"),
            "unknown",
        )
        author_id = coalesce(
            data.get("user_id"),
            user.get("member_id"),
            user.get("user_id"),
            author_name,
        )

        created_at = parse_timestamp(
            coalesce(data.get("created_timestamp"), data.get("created_time"), data.get("created_at"))
        )
        updated_at = parse_timestamp(
            coalesce(data.get("updated_timestamp"), data.get("updated_time"), data.get("updated_at"))
        )

        tags: list[str] = []
        category_name = coalesce(data.get("category_name"), data.get("category"))
        if category_name:
            tags.append(str(category_name))
        raw_tags = data.get("tags")
        if isinstance(raw_tags, list):
            tags.extend(str(tag) for tag in raw_tags if tag)

        slug = coalesce(data.get("mod_slug"), data.get("slug"))
        homepage = coalesce(data.get("mod_page_url"), data.get("nexusmods_url"), data.get("url"))
        if homepage and not str(homepage).startswith(("http://", "https://")):
            homepage = None

        mod_key = ModID(provider=Provider.NEXUSMODS, id=str(mod_id.id), game=mod_id.game)
        author = Author(provider=Provider.NEXUSMODS, id=str(author_id), name=str(author_name), raw=dict(user))
        latest_version = self._build_latest_version(mod_key, data, files_data)

        description = coalesce(data.get("description"), data.get("description_markdown"), data.get("summary"))
        if description is not None:
            description = str(description)

        return Mod(
            provider=Provider.NEXUSMODS,
            id=mod_key,
            slug=str(slug) if slug is not None else None,
            name=LocalisedText(value=str(coalesce(data.get("name"), data.get("mod_name"), mod_id.id))),
            description_md=LocalisedText(value=description) if description is not None else None,
            author=author,
            homepage=cast(AnyHttpUrl | None, homepage),
            tags=tags,
            created_at=created_at,
            updated_at=updated_at,
            latest_version_id=str(data.get("version")) if data.get("version") is not None else None,
            latest_version=latest_version,
            raw={
                "mod": data,
                "files": files_data,
            },
        )
