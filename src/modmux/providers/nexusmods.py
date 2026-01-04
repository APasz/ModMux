"""Nexus Mods provider integration."""

from datetime import datetime, timezone
from typing import cast

from httpx import AsyncClient
from pydantic import AnyHttpUrl, Field, SecretStr

from .._log import get_logger
from ..models import Author, Mod, ModID, Provider, ProviderCreds
from ..utils.discovery import register
from ._base import ProviderClient

log = get_logger(__name__)


class NexusCreds(ProviderCreds):
    """Credential model for Nexus Mods API access."""

    provider: Provider = Provider.NEXUSMODS
    api_key: SecretStr = Field(alias="token")

    def headers(self) -> dict[str, str]:
        return {"apikey": self.api_key.get_secret_value()}


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


@register
class NexusmodsClient(ProviderClient):
    """Client for Nexus Mods mod metadata."""

    name: Provider = Provider.NEXUSMODS
    base = "https://api.nexusmods.com/v1"
    creds_model = NexusCreds

    def __init__(self, creds: NexusCreds | None, *, http: AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)

    async def get_mod(self, mod_id: ModID) -> Mod:
        """Fetch a single mod from Nexus Mods.

        Args;
            mod_id: Provider-specific mod identifier.

        Returns;
            A normalised Mod instance.

        Raises;
            ValueError: If the game domain is missing from the ModID.
        """
        if not mod_id.game:
            raise ValueError("Nexus Mods requires ModID.game (game domain name).")
        data = await self._get_json(f"games/{mod_id.game}/mods/{mod_id.id}.json")

        user = data.get("user")
        if not isinstance(user, dict):
            user = {}

        author_name = _coalesce(
            data.get("author"),
            data.get("uploaded_by"),
            user.get("name"),
            user.get("username"),
            "unknown",
        )
        author_id = _coalesce(
            data.get("user_id"),
            user.get("member_id"),
            user.get("user_id"),
            author_name,
        )

        created_at = _parse_timestamp(
            _coalesce(data.get("created_timestamp"), data.get("created_time"), data.get("created_at"))
        )
        updated_at = _parse_timestamp(
            _coalesce(data.get("updated_timestamp"), data.get("updated_time"), data.get("updated_at"))
        )

        tags: list[str] = []
        category_name = _coalesce(data.get("category_name"), data.get("category"))
        if category_name:
            tags.append(str(category_name))
        raw_tags = data.get("tags")
        if isinstance(raw_tags, list):
            tags.extend(str(tag) for tag in raw_tags if tag)

        slug = _coalesce(data.get("mod_slug"), data.get("slug"))
        homepage = _coalesce(data.get("mod_page_url"), data.get("nexusmods_url"), data.get("url"))
        if homepage and not str(homepage).startswith(("http://", "https://")):
            homepage = None

        mod_key = ModID(provider=Provider.NEXUSMODS, id=str(mod_id.id), game=mod_id.game)
        author = Author(provider=Provider.NEXUSMODS, id=str(author_id), name=str(author_name))

        description = _coalesce(data.get("description"), data.get("description_markdown"), data.get("summary"))
        if description is not None:
            description = str(description)

        return Mod(
            provider=Provider.NEXUSMODS,
            id=mod_key,
            slug=str(slug) if slug is not None else None,
            name=str(_coalesce(data.get("name"), data.get("mod_name"), mod_id.id)),
            description_md=description,
            author=author,
            homepage=cast(AnyHttpUrl | None, homepage),
            tags=tags,
            created_at=created_at,
            updated_at=updated_at,
            latest_version_id=str(data.get("version")) if data.get("version") is not None else None,
            raw=data,
        )
