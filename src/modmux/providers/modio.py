"""mod.io provider integration."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

from httpx import AsyncClient
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr

from .._log import get_logger
from ..cache import ModioLookupCache
from ..models import Author, LocaleTag, LocalisedText, Mod, ModID, Provider, ProviderCreds
from ..modmux_errors import ModMuxError, NotFound, ProviderError
from ..toggles import ToggleMode, UndefinedType
from ..utils.discovery import register
from ._base import ProviderClient
from .colour import Colour

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


def _extract_items(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _mod_cache_key(game_id: str, value: str) -> str:
    return f"{game_id}:{value}"


def _normalise_locales(locales: list[LocaleTag] | None) -> list[str]:
    if not locales:
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for locale in locales:
        tag = str(locale).strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def _localised_text(value: str | None, translations: dict[LocaleTag, str]) -> LocalisedText | None:
    if value is None:
        return None
    return LocalisedText(value=value, translations=translations)


_MODIO_BATCH_SIZE = 100


@register
class ModioClient(ProviderClient):
    """Client for mod.io mod metadata."""

    name: Provider = Provider.MODIO
    display_name: str = "mod.io"
    colour: Colour = Colour("#07C1D8", "#FFFFFF", "#F5F7FA")
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

    def _require_access(self) -> None:
        if not self.creds or not self.creds.user_id:
            raise ValueError("mod.io requires a user id for API access.")

    async def _resolve_game_id(self, game: str) -> str:
        game_id = str(game).strip()
        if _is_numeric(game_id):
            return game_id
        if self.cache:
            cached_id = await self.cache.game_slug_to_id.get(game_id)
            if cached_id is not None:
                return str(cached_id)

        game_data = await self._get_json("games", params={"name_id": game_id, "limit": 1})
        game_item = _extract_first_item(game_data)
        if not game_item or game_item.get("id") is None:
            raise NotFound(f"{self.name}: game {game!r} not found")

        resolved_id = str(game_item["id"])
        game_slug = _coalesce(game_item.get("name_id"), game_item.get("slug"))
        if self.cache and game_slug is not None:
            await self.cache.game_slug_to_id.set(str(game_slug), resolved_id)
            await self.cache.game_id_to_slug.set(resolved_id, str(game_slug))
        return resolved_id

    async def _resolve_mod_id(self, game_id: str, mod_value: str) -> str:
        resolved_value = str(mod_value).strip()
        if _is_numeric(resolved_value):
            return resolved_value
        if self.cache:
            cached_id = await self.cache.mod_slug_to_id.get(_mod_cache_key(game_id, resolved_value))
            if cached_id is not None:
                return str(cached_id)

        mod_data = await self._get_json(f"games/{game_id}/mods", params={"name_id": resolved_value, "limit": 1})
        mod_item = _extract_first_item(mod_data)
        if not mod_item or mod_item.get("id") is None:
            raise NotFound(f"{self.name}: mod {mod_value!r} not found")

        resolved_id = str(mod_item["id"])
        mod_slug = _coalesce(mod_item.get("name_id"), mod_item.get("slug"))
        if self.cache and mod_slug is not None:
            key = _mod_cache_key(game_id, str(mod_slug))
            await self.cache.mod_slug_to_id.set(key, resolved_id)
            await self.cache.mod_id_to_slug.set(_mod_cache_key(game_id, resolved_id), str(mod_slug))
        return resolved_id

    async def _fetch_mods_batch(
        self,
        game_id: str,
        mod_ids: Sequence[str],
        *,
        locale: str | None = None,
    ) -> dict[str, dict[str, object]]:
        mods_by_id: dict[str, dict[str, object]] = {}
        if not mod_ids:
            return mods_by_id

        headers: dict[str, str] | None = None
        if locale is not None:
            headers = {"Accept-Language": locale}

        for start in range(0, len(mod_ids), _MODIO_BATCH_SIZE):
            batch = list(mod_ids[start : start + _MODIO_BATCH_SIZE])
            data = await self._get_json(
                f"games/{game_id}/mods",
                params={"id-in": ",".join(batch), "limit": len(batch)},
                headers=headers,
            )
            if isinstance(data, dict) and (data.get("error") or data.get("error_ref")):
                raise ProviderError(f"{self.name}: {data.get('error') or data.get('error_ref')}")
            for payload in _extract_items(data):
                mod_value = _coalesce(payload.get("id"))
                if mod_value is None:
                    continue
                mods_by_id[str(mod_value)] = payload

        return mods_by_id

    async def _update_cache_from_payload(self, game_id: str, payload: Mapping[str, object]) -> None:
        if not self.cache:
            return
        payload_slug = _coalesce(payload.get("name_id"), payload.get("slug"))
        payload_id = _coalesce(payload.get("id"))
        if payload_slug is None or payload_id is None:
            return
        slug = str(payload_slug)
        mod_value = str(payload_id)
        await self.cache.mod_slug_to_id.set(_mod_cache_key(game_id, slug), mod_value)
        await self.cache.mod_id_to_slug.set(_mod_cache_key(game_id, mod_value), slug)

    def _build_mod(
        self,
        requested: ModID,
        *,
        game_id: str,
        mod_value: str,
        payload: Mapping[str, object],
        name_translations: dict[LocaleTag, str],
        description_translations: dict[LocaleTag, str],
    ) -> Mod:
        name = _coalesce(payload.get("name"), payload.get("mod_name"), requested.id)
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

        resolved_game_id = _coalesce(payload.get("game_id"), game_id)
        mod_key = ModID(
            provider=Provider.MODIO,
            id=str(_coalesce(payload.get("id"), mod_value)),
            game=str(resolved_game_id),
        )
        author = Author(provider=Provider.MODIO, id=str(author_id), name=str(author_name), raw=dict(submitted_by))

        return Mod(
            provider=Provider.MODIO,
            id=mod_key,
            slug=str(slug) if slug is not None else None,
            name=LocalisedText(value=str(name), translations=name_translations),
            description_md=_localised_text(description, description_translations),
            author=author,
            homepage=cast(AnyHttpUrl | None, homepage),
            tags=tags,
            created_at=created_at,
            updated_at=updated_at,
            latest_version_id=latest_version_id,
            raw=dict(payload),
        )

    async def get_mod(
        self,
        mod_id: ModID,
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> Mod:
        """Fetch a single mod from mod.io.

        Args;
            mod_id: Provider-specific mod identifier.
            locales: Optional locale tags to request translations for.
            author_resolution: Author enrichment toggle.

        Returns;
            A normalised Mod instance.

        Raises;
            ValueError: If the game id is missing from the ModID or user id is missing.
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
        """Fetch multiple mods from mod.io."""
        del author_resolution

        requests = list(mod_ids)
        if not requests:
            return []

        self._require_access()

        resolved_requests: list[tuple[ModID, str, str]] = []
        grouped_ids: dict[str, list[str]] = {}
        for mod_id in requests:
            if not mod_id.game:
                raise ValueError("mod.io requires ModID.game (game id).")
            game_id = await self._resolve_game_id(str(mod_id.game))
            mod_value = await self._resolve_mod_id(game_id, str(mod_id.id))
            resolved_requests.append((mod_id, game_id, mod_value))
            grouped_ids.setdefault(game_id, [])
            if mod_value not in grouped_ids[game_id]:
                grouped_ids[game_id].append(mod_value)

        payloads_by_game: dict[str, dict[str, dict[str, object]]] = {}
        for game_id, game_mod_ids in grouped_ids.items():
            payloads = await self._fetch_mods_batch(game_id, game_mod_ids)
            payloads_by_game[game_id] = payloads
            for payload in payloads.values():
                await self._update_cache_from_payload(game_id, payload)

        name_translations: dict[tuple[str, str], dict[LocaleTag, str]] = {}
        description_translations: dict[tuple[str, str], dict[LocaleTag, str]] = {}
        locale_tags = _normalise_locales(locales)
        if locale_tags:
            for locale in locale_tags:
                for game_id, game_mod_ids in grouped_ids.items():
                    try:
                        translated_payloads = await self._fetch_mods_batch(game_id, game_mod_ids, locale=locale)
                    except ModMuxError as exc:
                        log.debug("mod.io localization fetch failed for %s: %s", locale, exc)
                        continue
                    for mod_value, translated_payload in translated_payloads.items():
                        translated_name = _coalesce(translated_payload.get("name"), translated_payload.get("mod_name"))
                        if translated_name is not None:
                            name_translations.setdefault((game_id, mod_value), {})[locale] = str(translated_name)
                        translated_description = _coalesce(
                            translated_payload.get("description"),
                            translated_payload.get("summary"),
                        )
                        if translated_description is not None:
                            description_translations.setdefault((game_id, mod_value), {})[locale] = str(
                                translated_description
                            )

        mods: list[Mod] = []
        for requested, game_id, mod_value in resolved_requests:
            payload = payloads_by_game.get(game_id, {}).get(mod_value)
            if payload is None:
                raise NotFound(f"{self.name}: mod {requested.id!r} not found")
            mods.append(
                self._build_mod(
                    requested,
                    game_id=game_id,
                    mod_value=mod_value,
                    payload=payload,
                    name_translations=name_translations.get((game_id, mod_value), {}),
                    description_translations=description_translations.get((game_id, mod_value), {}),
                )
            )

        return mods
