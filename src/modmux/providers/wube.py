"""Factorio (Wube) provider integration."""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

from httpx import AsyncClient
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr

from .._log import get_logger
from ..models import (
    Author,
    Dependency,
    DependencyRelation,
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
from .colour import Colour

log = get_logger(__name__)


class WubeCreds(ProviderCreds):
    """Credential model for the Factorio mod portal API."""

    provider: Provider = Provider.WUBE
    api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("token", "key", "api_key"))

    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": self.api_key.get_secret_value()}


def _coalesce(*values: object) -> object | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _parse_timestamp(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return None
    return None


def _extract_tags(raw: object) -> list[str]:
    tags: list[str] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str):
                if entry.strip():
                    tags.append(entry)
                continue
            if isinstance(entry, dict):
                name = _coalesce(entry.get("name"), entry.get("tag"))
                if name is not None:
                    tags.append(str(name))
    return tags


def _clean_url(value: object | None) -> str | None:
    if value is None:
        return None
    url = str(value)
    if not url.startswith(("http://", "https://")):
        return None
    return url


_DEPENDENCY_PATTERN = re.compile(
    r"^\s*(?P<prefix>\(\?\)|[!?~]?)\s*(?P<name>[^\s<>=]+)\s*(?:(?P<op><=|>=|=|<|>)\s*(?P<version>\S+))?\s*$"
)


def _pick_latest_release(
    releases: object,
    latest_release: object,
) -> Mapping[str, object] | None:
    release_entries = [entry for entry in releases if isinstance(entry, dict)] if isinstance(releases, list) else []

    if isinstance(latest_release, dict):
        latest_version = latest_release.get("version")
        if latest_version is not None:
            latest_version_value = str(latest_version)
            for entry in release_entries:
                if str(entry.get("version")) == latest_version_value:
                    merged_entry = dict(entry)
                    merged_entry.update(latest_release)
                    return merged_entry
        return latest_release

    best_release: Mapping[str, object] | None = None
    best_timestamp: datetime | None = None
    for entry in release_entries:
        released_at = _parse_timestamp(entry.get("released_at"))
        if best_release is None:
            best_release = entry
            best_timestamp = released_at
            continue
        if released_at is not None and (best_timestamp is None or released_at > best_timestamp):
            best_release = entry
            best_timestamp = released_at
    return best_release


def _parse_dependencies(raw_dependencies: object) -> list[Dependency]:
    dependencies: list[Dependency] = []
    if not isinstance(raw_dependencies, list):
        return dependencies

    for raw_dependency in raw_dependencies:
        if not isinstance(raw_dependency, str):
            continue
        match = _DEPENDENCY_PATTERN.match(raw_dependency)
        if match is None:
            continue

        prefix = match.group("prefix")

        name = match.group("name")
        operator = match.group("op")
        version = match.group("version")
        version_req = None
        if operator and version:
            version_req = f"{operator} {version}"

        dependencies.append(
            Dependency(
                provider=Provider.WUBE,
                id=ModID(provider=Provider.WUBE, id=name),
                version_req=version_req,
                relation=(
                    DependencyRelation.OPTIONAL
                    if prefix in {"?", "(?)"}
                    else DependencyRelation.INCOMPATIBLE
                    if prefix == "!"
                    else DependencyRelation.REQUIRED
                ),
            )
        )

    return dependencies


@register
class WubeClient(ProviderClient):
    """Client for Factorio mod portal metadata."""

    name: Provider = Provider.WUBE
    display_name: str = "Factorio Mods"
    colour: Colour = Colour("#201810", "#C5C5C5", "#C78627")
    base = "https://mods.factorio.com/api"
    creds_model = WubeCreds
    domains = ("mods.factorio.com",)

    def __init__(self, creds: WubeCreds | None, *, http: AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)

    @classmethod
    def parse_url(cls, url: str) -> ModID | None:
        parts = urlsplit(cls._normalise_url(url))
        if not cls._match_domain(parts.hostname):
            return None
        segments = cls._path_segments(parts.path)
        if len(segments) >= 2 and segments[0] in {"mod", "mods"}:
            return ModID(provider=Provider.WUBE, id=segments[1])
        return None

    async def get_mod(
        self,
        mod_id: ModID,
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> Mod:
        """Fetch a single mod from the Factorio mod portal.

        Args;
            mod_id: Provider-specific mod identifier.
            locales: Optional locale tags to request translations for.
            author_resolution: Author enrichment toggle.

        Returns;
            A normalised Mod instance.
        """
        payload = await self._get_json(f"mods/{mod_id.id}/full")
        if not isinstance(payload, dict):
            raise ProviderError(f"{self.name}: unexpected response shape")

        slug = _coalesce(payload.get("name"), mod_id.id)
        name = _coalesce(payload.get("title"), payload.get("name"), mod_id.id)
        description = _coalesce(payload.get("description"), payload.get("summary"))
        if description is not None:
            description = str(description)

        owner = _coalesce(payload.get("owner"), payload.get("author"), "unknown")
        author = Author(provider=Provider.WUBE, id=str(owner), name=str(owner), raw={"owner": owner})

        tags = _extract_tags(payload.get("tags"))
        category = _coalesce(payload.get("category"))
        if category is not None:
            tags.append(str(category))

        homepage = _clean_url(_coalesce(payload.get("homepage"), payload.get("homepage_url"), payload.get("url")))
        if homepage is None and slug is not None:
            homepage = f"https://mods.factorio.com/mod/{slug}"

        releases = payload.get("releases")
        created_at = None
        updated_at = None
        if isinstance(releases, list) and releases:
            release_dates = []
            for entry in releases:
                if not isinstance(entry, dict):
                    continue
                released_at = _parse_timestamp(entry.get("released_at"))
                if released_at is not None:
                    release_dates.append(released_at)
            if release_dates:
                created_at = min(release_dates)
                updated_at = max(release_dates)

        latest_release = payload.get("latest_release")
        if isinstance(latest_release, dict):
            released_at = _parse_timestamp(latest_release.get("released_at"))
            if released_at is not None:
                updated_at = released_at if updated_at is None or released_at > updated_at else updated_at
                if created_at is None:
                    created_at = released_at

        mod_key = ModID(provider=Provider.WUBE, id=str(slug))
        latest_release_payload = _pick_latest_release(releases, latest_release)
        latest_version = None
        latest_version_id = None
        if latest_release_payload is not None:
            latest_version_id_raw = latest_release_payload.get("version")
            if latest_version_id_raw is not None:
                latest_version_id = str(latest_version_id_raw)

            latest_files: list[FileAsset] = []
            file_name = latest_release_payload.get("file_name")
            if isinstance(file_name, str) and latest_version_id is not None:
                latest_files.append(
                    FileAsset(
                        file_id=latest_version_id,
                        filename=file_name,
                    )
                )

            info_json = latest_release_payload.get("info_json")
            latest_version = ModVersion(
                id=mod_key,
                name=latest_version_id,
                version=latest_version_id,
                published_at=_parse_timestamp(latest_release_payload.get("released_at")),
                files=latest_files,
                dependencies=_parse_dependencies(
                    info_json.get("dependencies") if isinstance(info_json, dict) else None
                ),
                raw=dict(latest_release_payload),
            )

        return Mod(
            provider=Provider.WUBE,
            id=mod_key,
            slug=str(slug) if slug is not None else None,
            name=LocalisedText(value=str(name)),
            description_md=LocalisedText(value=description) if description is not None else None,
            author=author,
            homepage=cast(AnyHttpUrl | None, homepage),
            tags=tags,
            created_at=created_at,
            updated_at=updated_at,
            latest_version_id=latest_version_id,
            latest_version=latest_version,
            raw=payload,
        )
