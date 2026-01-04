"""Factorio (Wube) provider integration."""

from datetime import datetime, timezone
from typing import cast
from urllib.parse import urlsplit

from httpx import AsyncClient
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr

from .._errors import ProviderError
from .._log import get_logger
from ..models import Author, Mod, ModID, Provider, ProviderCreds
from ..utils.discovery import register
from ._base import ProviderClient

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
        return datetime.fromtimestamp(value, tz=timezone.utc)
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


@register
class WubeClient(ProviderClient):
    """Client for Factorio mod portal metadata."""

    name: Provider = Provider.WUBE
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

    async def get_mod(self, mod_id: ModID) -> Mod:
        """Fetch a single mod from the Factorio mod portal.

        Args;
            mod_id: Provider-specific mod identifier.

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
        author = Author(provider=Provider.WUBE, id=str(owner), name=str(owner))

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
        latest_version_id = None
        if isinstance(releases, list) and releases:
            release_dates = []
            for entry in releases:
                if not isinstance(entry, dict):
                    continue
                released_at = _parse_timestamp(entry.get("released_at"))
                if released_at is not None:
                    release_dates.append(released_at)
                if latest_version_id is None and entry.get("version") is not None:
                    latest_version_id = str(entry.get("version"))
            if release_dates:
                created_at = min(release_dates)
                updated_at = max(release_dates)

        latest_release = payload.get("latest_release")
        if isinstance(latest_release, dict):
            if latest_release.get("version") is not None:
                latest_version_id = str(latest_release.get("version"))
            released_at = _parse_timestamp(latest_release.get("released_at"))
            if released_at is not None:
                updated_at = released_at if updated_at is None or released_at > updated_at else updated_at
                if created_at is None:
                    created_at = released_at

        mod_key = ModID(provider=Provider.WUBE, id=str(slug))

        return Mod(
            provider=Provider.WUBE,
            id=mod_key,
            slug=str(slug) if slug is not None else None,
            name=str(name),
            description_md=description,
            author=author,
            homepage=cast(AnyHttpUrl | None, homepage),
            tags=tags,
            created_at=created_at,
            updated_at=updated_at,
            latest_version_id=latest_version_id,
            raw=payload,
        )
