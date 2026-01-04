"""Steam Workshop provider integration."""

from datetime import datetime, timezone
from typing import cast
from urllib.parse import parse_qs, urlsplit

from httpx import AsyncClient
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr

from .._errors import NotFound, ProviderError
from .._log import get_logger
from ..models import Author, Mod, ModID, Provider, ProviderCreds
from ..utils.discovery import register
from ._base import ProviderClient

log = get_logger(__name__)


class SteamCreds(ProviderCreds):
    """Credential model for Steam Web API access."""

    provider: Provider = Provider.STEAM
    api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("token", "key", "api_key"))

    def params(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"key": self.api_key.get_secret_value()}


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
                name = _coalesce(entry.get("tag"), entry.get("name"))
                if name is not None:
                    tags.append(str(name))
    return tags


@register
class SteamClient(ProviderClient):
    """Client for Steam Workshop mod metadata."""

    name: Provider = Provider.STEAM
    base = "https://api.steampowered.com"
    creds_model = SteamCreds
    domains = ("steamcommunity.com",)

    def __init__(self, creds: SteamCreds | None, *, http: AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)

    @classmethod
    def parse_url(cls, url: str) -> ModID | None:
        parts = urlsplit(cls._normalise_url(url))
        if not cls._match_domain(parts.hostname):
            return None
        query = parse_qs(parts.query)
        mod_id = ""
        if "id" in query and query["id"]:
            mod_id = query["id"][0]
        if not mod_id:
            segments = cls._path_segments(parts.path)
            for index, segment in enumerate(segments):
                if segment == "filedetails" and index + 1 < len(segments):
                    mod_id = segments[index + 1]
                    break
        if not mod_id:
            return None
        game = None
        if "appid" in query and query["appid"]:
            game = query["appid"][0]
        return ModID(provider=Provider.STEAM, id=mod_id, game=game)

    async def get_mod(self, mod_id: ModID) -> Mod:
        """Fetch a single mod from Steam Workshop.

        Args;
            mod_id: Provider-specific mod identifier.

        Returns;
            A normalised Mod instance.
        """
        payload = {
            "itemcount": 1,
            "publishedfileids[0]": str(mod_id.id),
        }
        data = await self._post_json("ISteamRemoteStorage/GetPublishedFileDetails/v1/", data=payload)
        response = data.get("response") if isinstance(data, dict) else None
        if not isinstance(response, dict):
            raise ProviderError(f"{self.name}: unexpected response shape")

        details_list = response.get("publishedfiledetails")
        if not isinstance(details_list, list) or not details_list:
            raise ProviderError(f"{self.name}: missing workshop details")

        details = details_list[0]
        if not isinstance(details, dict):
            raise ProviderError(f"{self.name}: unexpected workshop payload")

        result = details.get("result")
        if result is not None:
            try:
                result_code = int(result)
            except (TypeError, ValueError) as exc:
                raise ProviderError(f"{self.name}: unexpected workshop result {result!r}") from exc
            if result_code != 1:
                if result_code == 9:
                    raise NotFound(f"{self.name}: workshop item {mod_id.id!r} not found")
                raise ProviderError(f"{self.name}: workshop result={result_code}")

        title = _coalesce(details.get("title"), mod_id.id)
        description = details.get("description")
        if description is not None:
            description = str(description)

        homepage = _coalesce(details.get("url"), details.get("file_url"), details.get("preview_url"))
        if homepage and not str(homepage).startswith(("http://", "https://")):
            homepage = None

        created_at = _parse_timestamp(details.get("time_created"))
        updated_at = _parse_timestamp(details.get("time_updated"))

        author_id = _coalesce(details.get("creator"), "unknown")
        author = Author(provider=Provider.STEAM, id=str(author_id), name=str(author_id))

        tags = _extract_tags(details.get("tags"))

        game_id = _coalesce(mod_id.game, details.get("consumer_app_id"), details.get("creator_app_id"))
        mod_key = ModID(provider=Provider.STEAM, id=str(mod_id.id), game=str(game_id) if game_id is not None else None)

        return Mod(
            provider=Provider.STEAM,
            id=mod_key,
            slug=None,
            name=str(title),
            description_md=description,
            author=author,
            homepage=cast(AnyHttpUrl | None, homepage),
            tags=tags,
            created_at=created_at,
            updated_at=updated_at,
            latest_version_id=None,
            raw=details,
        )
