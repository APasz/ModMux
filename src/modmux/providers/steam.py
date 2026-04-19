"""Steam Workshop provider integration."""

from collections.abc import Sequence
from datetime import UTC, datetime
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
        return datetime.fromtimestamp(value, tz=UTC)
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

_STEAM_BATCH_SIZE = 100


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
        return Author(provider=Provider.STEAM, id=str(resolved_id), name=str(display_name), raw=player)

    def _build_details_payload(self, mod_ids: Sequence[str], *, language: str | None = None) -> dict[str, str]:
        payload: dict[str, str] = {"itemcount": str(len(mod_ids))}
        for index, mod_id in enumerate(mod_ids):
            payload[f"publishedfileids[{index}]"] = mod_id
        if language is not None:
            payload["language"] = language
        return payload

    async def _fetch_details_batch(
        self,
        mod_ids: Sequence[str],
        *,
        language: str | None = None,
    ) -> dict[str, dict[str, object]]:
        if not mod_ids:
            return {}

        details_by_id: dict[str, dict[str, object]] = {}
        for start in range(0, len(mod_ids), _STEAM_BATCH_SIZE):
            batch = list(mod_ids[start : start + _STEAM_BATCH_SIZE])
            data = await self._post_json(
                "ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                data=self._build_details_payload(batch, language=language),
            )
            response = data.get("response") if isinstance(data, dict) else None
            if not isinstance(response, dict):
                raise ProviderError(f"{self.name}: unexpected response shape")

            details_list = response.get("publishedfiledetails")
            if not isinstance(details_list, list):
                raise ProviderError(f"{self.name}: missing workshop details")

            for index, details in enumerate(details_list):
                if not isinstance(details, dict):
                    raise ProviderError(f"{self.name}: unexpected workshop payload")
                requested_id = batch[index] if index < len(batch) else ""
                resolved_id = str(_coalesce(details.get("publishedfileid"), requested_id))
                if not resolved_id:
                    raise ProviderError(f"{self.name}: workshop response missing published file id")
                details_by_id[resolved_id] = details

        return details_by_id

    async def _fetch_users_batch(self, user_ids: Sequence[str]) -> dict[str, Author]:
        authors: dict[str, Author] = {}
        if not user_ids:
            return authors

        for start in range(0, len(user_ids), _STEAM_BATCH_SIZE):
            batch = list(user_ids[start : start + _STEAM_BATCH_SIZE])
            data = await self._get_json("ISteamUser/GetPlayerSummaries/v2/", params={"steamids": ",".join(batch)})
            response = data.get("response") if isinstance(data, dict) else None
            if not isinstance(response, dict):
                raise ProviderError(f"{self.name}: unexpected user response shape")

            players = response.get("players")
            if not isinstance(players, list):
                raise ProviderError(f"{self.name}: unexpected players payload")

            for player in players:
                if not isinstance(player, dict):
                    continue
                resolved_id = _coalesce(player.get("steamid"), player.get("id"))
                if resolved_id is None:
                    continue
                author_id = str(resolved_id)
                display_name = _coalesce(player.get("personaname"), player.get("realname"), author_id)
                authors[author_id] = Author(
                    provider=Provider.STEAM,
                    id=author_id,
                    name=str(display_name),
                    raw=player,
                )

        return authors

    def _build_mod(
        self,
        requested: ModID,
        details: dict[str, object],
        *,
        name_translations: dict[LocaleTag, str],
        description_translations: dict[LocaleTag, str],
        authors: dict[str, Author],
    ) -> Mod:
        result = details.get("result")
        if result is not None:
            if isinstance(result, bool):
                result_code = int(result)
            elif isinstance(result, int):
                result_code = result
            elif isinstance(result, float):
                result_code = int(result)
            elif isinstance(result, str):
                try:
                    result_code = int(result)
                except ValueError as exc:
                    raise ProviderError(f"{self.name}: unexpected workshop result {result!r}") from exc
            else:
                raise ProviderError(f"{self.name}: unexpected workshop result {result!r}")
            if result_code != 1:
                if result_code == 9:
                    raise NotFound(f"{self.name}: workshop item {requested.id!r} not found")
                raise ProviderError(f"{self.name}: workshop result={result_code}")

        title = _coalesce(details.get("title"), requested.id)
        description = details.get("description")
        if description is not None:
            description = str(description)

        homepage = _coalesce(details.get("url"), details.get("file_url"), details.get("preview_url"))
        if homepage and not str(homepage).startswith(("http://", "https://")):
            homepage = None

        created_at = _parse_timestamp(details.get("time_created"))
        updated_at = _parse_timestamp(details.get("time_updated"))

        author_id = str(_coalesce(details.get("creator"), "unknown"))
        author_raw: dict[str, object] = {}
        creator = details.get("creator")
        if creator is not None:
            author_raw = {"creator": creator}
        author = authors.get(
            author_id,
            Author(provider=Provider.STEAM, id=author_id, name=author_id, raw=author_raw),
        )

        tags = _extract_tags(details.get("tags"))

        game_id = _coalesce(requested.game, details.get("consumer_app_id"), details.get("creator_app_id"))
        mod_key = ModID(
            provider=Provider.STEAM,
            id=str(requested.id),
            game=str(game_id) if game_id is not None else None,
        )

        description_value = None
        if description is not None:
            description_value = LocalisedText(value=description, translations=description_translations)

        return Mod(
            provider=Provider.STEAM,
            id=mod_key,
            slug=None,
            name=LocalisedText(value=str(title), translations=name_translations),
            description_md=description_value,
            author=author,
            homepage=cast(AnyHttpUrl | None, homepage),
            tags=tags,
            created_at=created_at,
            updated_at=updated_at,
            latest_version_id=None,
            raw=dict(details),
        )

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
        mods = await self.get_mods([mod_id], locales=locales, author_resolution=author_resolution)
        return mods[0]

    async def get_mods(
        self,
        mod_ids: Sequence[ModID],
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> list[Mod]:
        """Fetch multiple mods from Steam Workshop."""
        requests = list(mod_ids)
        if not requests:
            return []

        requested_ids = [str(mod_id.id) for mod_id in requests]
        details_by_id = await self._fetch_details_batch(requested_ids)

        name_translations: dict[str, dict[LocaleTag, str]] = {}
        description_translations: dict[str, dict[LocaleTag, str]] = {}
        locale_tags = _normalise_locales(locales)
        if locale_tags:
            requested_languages: dict[str, list[str]] = {}
            for locale in locale_tags:
                language = _steam_language_for(locale)
                if not language:
                    continue
                requested_languages.setdefault(language, []).append(locale)

            for language, tags in requested_languages.items():
                try:
                    translated_by_id = await self._fetch_details_batch(requested_ids, language=language)
                except ModMuxError as exc:
                    log.debug("Steam localization fetch failed for %s: %s", language, exc)
                    continue
                for mod_id, translated in translated_by_id.items():
                    translated_title = _coalesce(translated.get("title"))
                    translated_description = translated.get("description")
                    for tag in tags:
                        if translated_title is not None:
                            name_translations.setdefault(mod_id, {})[tag] = str(translated_title)
                        if translated_description is not None:
                            description_translations.setdefault(mod_id, {})[tag] = str(translated_description)

        authors: dict[str, Author] = {}
        should_enrich_author = resolve_toggle(author_resolution, default=False)
        if should_enrich_author:
            author_ids = sorted(
                {
                    str(_coalesce(details.get("creator"), "unknown"))
                    for details in details_by_id.values()
                    if str(_coalesce(details.get("creator"), "unknown")) != "unknown"
                }
            )
            if author_ids:
                try:
                    authors = await self._fetch_users_batch(author_ids)
                except ModMuxError as exc:
                    log.debug("Steam bulk user lookup failed: %s", exc)

        mods: list[Mod] = []
        for requested in requests:
            details = details_by_id.get(str(requested.id))
            if details is None:
                raise NotFound(f"{self.name}: workshop item {requested.id!r} not found")
            mods.append(
                self._build_mod(
                    requested,
                    details,
                    name_translations=name_translations.get(str(requested.id), {}),
                    description_translations=description_translations.get(str(requested.id), {}),
                    authors=authors,
                )
            )
        return mods
