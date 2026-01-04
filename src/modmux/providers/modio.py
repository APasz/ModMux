"""mod.io provider integration."""

from datetime import datetime, timezone
from typing import cast
from urllib.parse import urlsplit

from httpx import AsyncClient
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr

from .._errors import NotFound, ProviderError
from .._log import get_logger
from ..cache import ModioLookupCache
from ..models import Author, Mod, ModID, Provider, ProviderCreds
from ..utils.discovery import register
from ._base import ProviderClient

log = get_logger(__name__)


class ModioCreds(ProviderCreds):
    """Credential model for mod.io API access."""

    provider: Provider = Provider.MODIO
    api_key: SecretStr = Field(validation_alias=AliasChoices("token", "key", "api_key"))
    user_id: SecretStr = Field(validation_alias=AliasChoices("user", "user_id"))

    def params(self) -> dict[str, str]:
        return {"api_key": self.api_key.get_secret_value()}

    def format_base(self, base: str) -> str:
        return f"https://u-{self.user_id.get_secret_value()}.modapi.io/v1"


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
        try:
            return datetime.fromisoformat(value)
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


def _is_numeric(value: str) -> bool:
    return value.isdigit()


def _extract_first_item(payload: object) -> dict | None:
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                return entry
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _mod_cache_key(game_id: str, value: str) -> str:
    return f"{game_id}:{value}"


@register
class ModioClient(ProviderClient):
    """Client for mod.io mod metadata."""

    name: Provider = Provider.MODIO
    base = "https://api.mod.io/v1"
    creds_model = ModioCreds
    domains = ("mod.io",)

    def __init__(self, creds: ModioCreds | None, *, http: AsyncClient, cache: ModioLookupCache | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)
        self.creds: ModioCreds | None = creds
        self.cache: ModioLookupCache | None = cache

    @classmethod
    def parse_url(cls, url: str) -> ModID | None:
        parts = urlsplit(cls._normalise_url(url))
        if not cls._match_domain(parts.hostname):
            return None
        segments = cls._path_segments(parts.path)
        if len(segments) >= 4 and segments[0] == "g" and segments[2] in {"m", "mods"}:
            return ModID(provider=Provider.MODIO, id=segments[3], game=segments[1])
        if len(segments) >= 4 and segments[0] == "games" and segments[2] == "mods":
            return ModID(provider=Provider.MODIO, id=segments[3], game=segments[1])
        return None

    async def get_mod(self, mod_id: ModID) -> Mod:
        """Fetch a single mod from mod.io.

        Args;
            mod_id: Provider-specific mod identifier.

        Returns;
            A normalised Mod instance.

        Raises;
            ValueError: If the game id is missing from the ModID or user id is missing.
        """
        if not mod_id.game:
            raise ValueError("mod.io requires ModID.game (game id).")
        if not self.creds or not self.creds.user_id:
            raise ValueError("mod.io requires a user id for API access.")

        game_id = str(mod_id.game).strip()
        if not _is_numeric(game_id):
            if self.cache:
                cached_id = await self.cache.game_slug_to_id.get(game_id)
                if cached_id is not None:
                    game_id = str(cached_id)
            if not _is_numeric(game_id):
                game_data = await self._get_json("games", params={"name_id": game_id, "limit": 1})
                game_item = _extract_first_item(game_data)
                if not game_item or game_item.get("id") is None:
                    raise NotFound(f"{self.name}: game {mod_id.game!r} not found")
                game_id = str(game_item["id"])
                game_slug = _coalesce(game_item.get("name_id"), game_item.get("slug"))
                if self.cache and game_slug is not None:
                    await self.cache.game_slug_to_id.set(str(game_slug), game_id)
                    await self.cache.game_id_to_slug.set(game_id, str(game_slug))

        mod_value = str(mod_id.id).strip()
        if not _is_numeric(mod_value):
            if self.cache:
                cached_id = await self.cache.mod_slug_to_id.get(_mod_cache_key(game_id, mod_value))
                if cached_id is not None:
                    mod_value = str(cached_id)
            if not _is_numeric(mod_value):
                mod_data = await self._get_json(f"games/{game_id}/mods", params={"name_id": mod_value, "limit": 1})
                mod_item = _extract_first_item(mod_data)
                if not mod_item or mod_item.get("id") is None:
                    raise NotFound(f"{self.name}: mod {mod_id.id!r} not found")
                mod_value = str(mod_item["id"])
                mod_slug = _coalesce(mod_item.get("name_id"), mod_item.get("slug"))
                if self.cache and mod_slug is not None:
                    key = _mod_cache_key(game_id, str(mod_slug))
                    await self.cache.mod_slug_to_id.set(key, mod_value)
                    await self.cache.mod_id_to_slug.set(_mod_cache_key(game_id, mod_value), str(mod_slug))

        data = await self._get_json(f"games/{game_id}/mods/{mod_value}")
        if isinstance(data, dict) and (data.get("error") or data.get("error_ref")):
            raise ProviderError(f"{self.name}: {data.get('error') or data.get('error_ref')}")

        payload = data.get("data") if isinstance(data, dict) and "data" in data else data
        if isinstance(payload, list):
            if not payload:
                raise NotFound(f"{self.name}: mod {mod_id.id!r} not found")
            payload = payload[0]
        if not isinstance(payload, dict):
            raise ProviderError(f"{self.name}: unexpected response shape")

        name = _coalesce(payload.get("name"), payload.get("mod_name"), mod_id.id)
        slug = _coalesce(payload.get("name_id"), payload.get("slug"))
        description = _coalesce(payload.get("description"), payload.get("summary"))
        if description is not None:
            description = str(description)

        homepage = _coalesce(payload.get("profile_url"), payload.get("homepage_url"), payload.get("url"))
        if homepage and not str(homepage).startswith(("http://", "https://")):
            homepage = None

        submitted_by = payload.get("submitted_by")
        if not isinstance(submitted_by, dict):
            submitted_by = {}

        author_id = _coalesce(
            submitted_by.get("id"),
            submitted_by.get("user_id"),
            submitted_by.get("member_id"),
            "unknown",
        )
        author_name = _coalesce(
            submitted_by.get("username"),
            submitted_by.get("name"),
            submitted_by.get("name_id"),
            author_id,
            "unknown",
        )

        created_at = _parse_timestamp(_coalesce(payload.get("date_added"), payload.get("created_at")))
        updated_at = _parse_timestamp(_coalesce(payload.get("date_updated"), payload.get("updated_at")))

        tags = _extract_tags(payload.get("tags"))

        modfile = payload.get("modfile")
        latest_version_id = None
        if isinstance(modfile, dict) and modfile.get("id") is not None:
            latest_version_id = str(modfile.get("id"))

        game_id = _coalesce(payload.get("game_id"), game_id)
        mod_key = ModID(provider=Provider.MODIO, id=str(_coalesce(payload.get("id"), mod_value)), game=str(game_id))
        author = Author(provider=Provider.MODIO, id=str(author_id), name=str(author_name))

        if self.cache:
            payload_slug = _coalesce(payload.get("name_id"), payload.get("slug"))
            if payload_slug is not None:
                payload_slug = str(payload_slug)
                game_key = str(game_id)
                await self.cache.mod_slug_to_id.set(_mod_cache_key(game_key, payload_slug), str(mod_key.id))
                await self.cache.mod_id_to_slug.set(_mod_cache_key(game_key, str(mod_key.id)), payload_slug)

        return Mod(
            provider=Provider.MODIO,
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
