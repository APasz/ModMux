"""Modrinth provider integration."""

from datetime import datetime, timezone
from typing import cast
from urllib.parse import urlsplit

from httpx import AsyncClient
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr

from .._errors import ModMuxError, ProviderError
from .._log import get_logger
from ..models import Author, Mod, ModID, Provider, ProviderCreds
from ..utils.discovery import register
from ._base import ProviderClient

log = get_logger(__name__)


class ModrinthCreds(ProviderCreds):
    """Credential model for Modrinth API access."""

    provider: Provider = Provider.MODRINTH
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
class ModrinthClient(ProviderClient):
    """Client for Modrinth mod metadata."""

    name: Provider = Provider.MODRINTH
    base = "https://api.modrinth.com/v2"
    creds_model = ModrinthCreds
    domains = ("modrinth.com",)

    def __init__(self, creds: ModrinthCreds | None, *, http: AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)

    @classmethod
    def parse_url(cls, url: str) -> ModID | None:
        parts = urlsplit(cls._normalise_url(url))
        if not cls._match_domain(parts.hostname):
            return None
        segments = cls._path_segments(parts.path)
        if len(segments) < 2:
            return None
        if segments[0] not in {"mod", "project", "modpack", "resourcepack", "plugin", "shader", "datapack"}:
            return None
        return ModID(provider=Provider.MODRINTH, id=segments[1])

    async def _fetch_author(self, project_id: str, team_id: str | None) -> Author:
        try:
            members = await self._get_json(f"project/{project_id}/members")
        except ModMuxError as exc:
            log.debug("Failed to fetch Modrinth members: %s", exc)
            members = None

        if isinstance(members, list):
            preferred = ["owner", "admin"]
            chosen = None
            for role in preferred:
                for entry in members:
                    if not isinstance(entry, dict):
                        continue
                    entry_role = str(entry.get("role", "")).lower()
                    if entry_role == role:
                        chosen = entry
                        break
                if chosen:
                    break
            if chosen is None:
                for entry in members:
                    if isinstance(entry, dict):
                        chosen = entry
                        break

            if chosen:
                user = chosen.get("user")
                if not isinstance(user, dict):
                    user = {}
                author_id = _coalesce(user.get("id"), user.get("user_id"), chosen.get("user_id"), team_id, "unknown")
                author_name = _coalesce(user.get("username"), user.get("name"), author_id, "unknown")
                return Author(provider=Provider.MODRINTH, id=str(author_id), name=str(author_name))

        fallback_id = team_id or "unknown"
        return Author(provider=Provider.MODRINTH, id=str(fallback_id), name=str(fallback_id))

    async def get_mod(self, mod_id: ModID) -> Mod:
        """Fetch a single mod from Modrinth.

        Args;
            mod_id: Provider-specific mod identifier.

        Returns;
            A normalised Mod instance.
        """
        payload = await self._get_json(f"project/{mod_id.id}")
        if not isinstance(payload, dict):
            raise ProviderError(f"{self.name}: unexpected response shape")

        project_id = str(_coalesce(payload.get("id"), mod_id.id))
        team_id = _coalesce(payload.get("team"), payload.get("team_id"))
        author = await self._fetch_author(project_id, str(team_id) if team_id is not None else None)

        slug = _coalesce(payload.get("slug"), payload.get("id"))
        name = _coalesce(payload.get("title"), payload.get("name"), payload.get("slug"), mod_id.id)
        description = _coalesce(payload.get("body"), payload.get("description"), payload.get("summary"))
        if description is not None:
            description = str(description)

        created_at = _parse_timestamp(_coalesce(payload.get("published"), payload.get("date_created")))
        updated_at = _parse_timestamp(_coalesce(payload.get("updated"), payload.get("date_modified")))

        tags = _extract_tags(payload.get("categories"))
        versions = payload.get("versions")
        latest_version_id = None
        if isinstance(versions, list) and versions:
            latest_version_id = str(versions[0])

        homepage = _clean_url(
            _coalesce(
                payload.get("project_url"),
                payload.get("issues_url"),
                payload.get("source_url"),
                payload.get("wiki_url"),
                payload.get("discord_url"),
            )
        )
        if homepage is None and slug is not None:
            homepage = f"https://modrinth.com/mod/{slug}"

        mod_key = ModID(provider=Provider.MODRINTH, id=project_id)

        return Mod(
            provider=Provider.MODRINTH,
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
