"""CurseForge provider integration."""

from datetime import datetime, timezone
from typing import cast
from urllib.parse import urlsplit

from httpx import AsyncClient
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr

from .._errors import NotFound, ProviderError
from .._log import get_logger
from ..models import Author, Mod, ModID, Provider, ProviderCreds
from ..utils.discovery import register
from ._base import ProviderClient

log = get_logger(__name__)


class CurseforgeCreds(ProviderCreds):
    """Credential model for CurseForge API access."""

    provider: Provider = Provider.CURSEFORGE
    api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("token", "key", "api_key"))

    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"x-api-key": self.api_key.get_secret_value()}


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
                name = _coalesce(entry.get("name"), entry.get("slug"))
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
class CurseforgeClient(ProviderClient):
    """Client for CurseForge mod metadata."""

    name: Provider = Provider.CURSEFORGE
    base = "https://api.curseforge.com/v1"
    creds_model = CurseforgeCreds
    domains = ("curseforge.com",)

    def __init__(self, creds: CurseforgeCreds | None, *, http: AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)

    @classmethod
    def parse_url(cls, url: str) -> ModID | None:
        parts = urlsplit(cls._normalise_url(url))
        if not cls._match_domain(parts.hostname):
            return None
        segments = cls._path_segments(parts.path)
        if len(segments) < 3:
            return None
        if segments[1] not in {"mc-mods", "mods", "addons", "modpacks", "texture-packs", "worlds", "customization"}:
            return None
        game = segments[0]
        slug = segments[2]
        return ModID(provider=Provider.CURSEFORGE, id=slug, game=game)

    async def get_mod(self, mod_id: ModID) -> Mod:
        """Fetch a single mod from CurseForge.

        Args;
            mod_id: Provider-specific mod identifier.

        Returns;
            A normalised Mod instance.

        Raises;
            ValueError: If a slug is provided without a game id.
        """
        mod_value = str(mod_id.id).strip()
        if not mod_value.isdigit():
            if not mod_id.game:
                raise ValueError("CurseForge slug lookup requires ModID.game (game id).")
            search = await self._get_json(
                "mods/search",
                params={"gameId": mod_id.game, "slug": mod_value, "pageSize": 1},
            )
            search_data = search.get("data") if isinstance(search, dict) else None
            if not isinstance(search_data, list) or not search_data:
                raise NotFound(f"{self.name}: mod {mod_id.id!r} not found")
            first = search_data[0]
            if not isinstance(first, dict) or first.get("id") is None:
                raise ProviderError(f"{self.name}: unexpected search response")
            mod_value = str(first["id"])

        data = await self._get_json(f"mods/{mod_value}")
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            raise ProviderError(f"{self.name}: unexpected response shape")

        name = _coalesce(payload.get("name"), payload.get("slug"), mod_id.id)
        slug = _coalesce(payload.get("slug"))
        description = _coalesce(payload.get("summary"))
        if description is not None:
            description = str(description)

        raw_links = payload.get("links")
        links = cast(dict[str, object], raw_links) if isinstance(raw_links, dict) else {}
        homepage = _clean_url(_coalesce(links.get("websiteUrl"), links.get("sourceUrl"), links.get("wikiUrl")))
        if homepage is None:
            homepage = _clean_url(payload.get("url"))

        created_at = _parse_timestamp(payload.get("dateCreated"))
        updated_at = _parse_timestamp(payload.get("dateModified"))

        tags = _extract_tags(payload.get("categories"))

        authors = payload.get("authors")
        author_id = "unknown"
        author_name = "unknown"
        if isinstance(authors, list) and authors:
            first = authors[0]
            if isinstance(first, dict):
                author_id = str(_coalesce(first.get("id"), first.get("userId"), author_id))
                author_name = str(_coalesce(first.get("name"), first.get("username"), author_id))

        latest_files = payload.get("latestFiles")
        latest_version_id = None
        if isinstance(latest_files, list) and latest_files:
            first_file = latest_files[0]
            if isinstance(first_file, dict) and first_file.get("id") is not None:
                latest_version_id = str(first_file.get("id"))

        game_id = _coalesce(payload.get("gameId"), mod_id.game)
        mod_key = ModID(
            provider=Provider.CURSEFORGE,
            id=str(payload.get("id", mod_value)),
            game=str(game_id) if game_id else None,
        )
        author = Author(provider=Provider.CURSEFORGE, id=str(author_id), name=str(author_name))

        return Mod(
            provider=Provider.CURSEFORGE,
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
