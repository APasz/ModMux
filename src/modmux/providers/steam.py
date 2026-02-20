"""Steam Workshop provider integration."""

from datetime import datetime, timezone
from typing import cast
from urllib.parse import parse_qs, urlsplit

from httpx import AsyncClient
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr

from .._log import get_logger
from ..models import Author, LocaleTag, LocalisedText, Mod, ModID, Provider, ProviderCreds
from ..modmux_errors import ModMuxError, NotFound, ProviderError
from ..toggles import ToggleMode, UndefinedType, resolve_toggle
from ..utils.discovery import register
from ._base import ProviderClient
from .colour import Colour

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


_STEAM_LANGUAGE_MAP: dict[str, str] = {
    "bg": "23",
    "zh-cn": "6",
    "zh-tw": "7",
    "cs": "19",
    "da": "13",
    "nl": "14",
    "en-us": "0",
    "en-gb": "0",
    "fi": "15",
    "fr": "2",
    "de": "1",
    "el": "24",
    "hu": "18",
    "id": "30",
    "it": "3",
    "ja": "10",
    "ko": "4",
    "no": "16",
    "pl": "12",
    "pt-br": "22",
    "ro": "20",
    "ru": "8",
    "es-es": "5",
    "sv-se": "17",
    "th": "9",
    "tr": "21",
    "uk": "26",
    "vi": "28",
}


def _normalise_locale(locale: LocaleTag) -> str:
    return str(locale).strip().replace("_", "-").lower()


def _normalise_locales(locales: list[LocaleTag] | None) -> list[str]:
    if not locales:
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for locale in locales:
        tag = _normalise_locale(locale)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def _steam_language_for(locale: str) -> str | None:
    if not locale:
        return None
    if locale in _STEAM_LANGUAGE_MAP:
        return _STEAM_LANGUAGE_MAP[locale]
    if "-" in locale:
        base = locale.split("-", 1)[0]
        if base in _STEAM_LANGUAGE_MAP:
            return _STEAM_LANGUAGE_MAP[base]
        candidates = [value for key, value in _STEAM_LANGUAGE_MAP.items() if key.startswith(f"{base}-")]
        if candidates and len(set(candidates)) == 1:
            return candidates[0]
        return None
    return _STEAM_LANGUAGE_MAP.get(locale)


@register
class SteamClient(ProviderClient):
    """Client for Steam Workshop mod metadata."""

    name: Provider = Provider.STEAM
    display_name: str = "Steam Workshop"
    colour: Colour = Colour("#1A9FFF", "#A1CD44", "#00E4E4", "#171D25", "#C5C3C0")
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

    async def get_user(self, user_id: str) -> Author:
        """Fetch Steam user metadata by SteamID.

        Args;
            user_id: Steam user identifier.

        Returns;
            A normalised Author instance.
        """
        steam_id = str(user_id).strip()
        if not steam_id:
            raise ValueError("Steam user id must be non-empty.")

        data = await self._get_json("ISteamUser/GetPlayerSummaries/v2/", params={"steamids": steam_id})
        response = data.get("response") if isinstance(data, dict) else None
        if not isinstance(response, dict):
            raise ProviderError(f"{self.name}: unexpected user response shape")

        players = response.get("players")
        if not isinstance(players, list):
            raise ProviderError(f"{self.name}: unexpected players payload")

        player: dict | None = None
        for entry in players:
            if not isinstance(entry, dict):
                continue
            entry_id = _coalesce(entry.get("steamid"), entry.get("id"))
            if entry_id is not None and str(entry_id) == steam_id:
                player = entry
                break

        if player is None:
            for entry in players:
                if isinstance(entry, dict):
                    player = entry
                    break

        if player is None:
            raise NotFound(f"{self.name}: user {steam_id!r} not found")

        resolved_id = _coalesce(player.get("steamid"), player.get("id"), steam_id)
        display_name = _coalesce(player.get("personaname"), player.get("realname"), resolved_id, steam_id)
        return Author(provider=Provider.STEAM, id=str(resolved_id), name=str(display_name))

    async def get_mod(
        self,
        mod_id: ModID,
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> Mod:
        """Fetch a single mod from Steam Workshop.

        Args;
            mod_id: Provider-specific mod identifier.
            locales: Optional locale tags to request translations for.
            author_resolution: Author enrichment toggle.

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

        name_translations: dict[LocaleTag, str] = {}
        description_translations: dict[LocaleTag, str] = {}
        locale_tags = _normalise_locales(locales)
        if locale_tags:
            requested: dict[str, list[str]] = {}
            for locale in locale_tags:
                language = _steam_language_for(locale)
                if not language:
                    continue
                requested.setdefault(language, []).append(locale)

            for language, tags in requested.items():
                translated_payload = dict(payload)
                translated_payload["language"] = language
                try:
                    translated = await self._post_json(
                        "ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                        data=translated_payload,
                    )
                except ModMuxError as exc:
                    log.debug("Steam localization fetch failed for %s: %s", language, exc)
                    continue
                translated_response = translated.get("response") if isinstance(translated, dict) else None
                if not isinstance(translated_response, dict):
                    continue
                translated_list = translated_response.get("publishedfiledetails")
                if not isinstance(translated_list, list) or not translated_list:
                    continue
                translated_details = translated_list[0]
                if not isinstance(translated_details, dict):
                    continue

                translated_title = _coalesce(translated_details.get("title"))
                translated_description = translated_details.get("description")

                for tag in tags:
                    if translated_title is not None:
                        name_translations[tag] = str(translated_title)
                    if translated_description is not None:
                        description_translations[tag] = str(translated_description)

        homepage = _coalesce(details.get("url"), details.get("file_url"), details.get("preview_url"))
        if homepage and not str(homepage).startswith(("http://", "https://")):
            homepage = None

        created_at = _parse_timestamp(details.get("time_created"))
        updated_at = _parse_timestamp(details.get("time_updated"))

        author_id = str(_coalesce(details.get("creator"), "unknown"))
        author = Author(provider=Provider.STEAM, id=author_id, name=author_id)
        should_enrich_author = resolve_toggle(author_resolution, default=False)
        if should_enrich_author and author_id != "unknown":
            try:
                author = await self.get_user(author_id)
            except ModMuxError as exc:
                log.debug("Steam user lookup failed for %s: %s", author_id, exc)

        tags = _extract_tags(details.get("tags"))

        game_id = _coalesce(mod_id.game, details.get("consumer_app_id"), details.get("creator_app_id"))
        mod_key = ModID(provider=Provider.STEAM, id=str(mod_id.id), game=str(game_id) if game_id is not None else None)

        name_value = LocalisedText(value=str(title), translations=name_translations)
        description_value = None
        if description is not None:
            description_value = LocalisedText(value=str(description), translations=description_translations)

        return Mod(
            provider=Provider.STEAM,
            id=mod_key,
            slug=None,
            name=name_value,
            description_md=description_value,
            author=author,
            homepage=cast(AnyHttpUrl | None, homepage),
            tags=tags,
            created_at=created_at,
            updated_at=updated_at,
            latest_version_id=None,
            raw=details,
        )
