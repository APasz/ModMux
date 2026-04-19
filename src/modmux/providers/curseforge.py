"""CurseForge provider integration."""

from collections.abc import Mapping, Sequence
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
from ..modmux_errors import NotFound, ProviderError
from ..toggles import ToggleMode, UndefinedType
from ..utils.discovery import register
from ._base import ProviderClient
from .colour import Colour

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


def _relation_from_file_type(value: object | None) -> DependencyRelation | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)):
        return None
    try:
        relation_type = int(value)
    except (TypeError, ValueError):
        return None
    relation_by_type = {
        1: DependencyRelation.EMBEDDED,
        2: DependencyRelation.OPTIONAL,
        3: DependencyRelation.REQUIRED,
        4: DependencyRelation.TOOL,
        5: DependencyRelation.INCOMPATIBLE,
        6: DependencyRelation.INCLUDED,
    }
    return relation_by_type.get(relation_type)


def _pick_latest_file(latest_files: object) -> Mapping[str, object] | None:
    if not isinstance(latest_files, list):
        return None

    best_file: Mapping[str, object] | None = None
    best_timestamp: datetime | None = None
    for entry in latest_files:
        if not isinstance(entry, dict):
            continue
        file_timestamp = _parse_timestamp(entry.get("fileDate"))
        if best_file is None:
            best_file = entry
            best_timestamp = file_timestamp
            continue
        if file_timestamp is not None and (best_timestamp is None or file_timestamp > best_timestamp):
            best_file = entry
            best_timestamp = file_timestamp
    return best_file


@register
class CurseforgeClient(ProviderClient):
    """Client for CurseForge mod metadata."""

    name: Provider = Provider.CURSEFORGE
    display_name: str = "CurseForge"
    colour: Colour = Colour("#F58220", "#FFFFFF", "#222222")
    base = "https://api.curseforge.com/v1"
    creds_model = CurseforgeCreds
    domains = ("curseforge.com",)

    def __init__(self, creds: CurseforgeCreds | None, *, http: AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)
        self._game_slug_cache: dict[str, str] = {}

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

    async def _resolve_game_id(self, game: str) -> str:
        game_value = str(game).strip()
        if game_value.isdigit():
            return game_value

        cached = self._game_slug_cache.get(game_value)
        if cached is not None:
            return cached

        normalised_game = game_value.casefold()
        index = 0
        page_size = 50
        total_count: int | None = None
        while total_count is None or index < total_count:
            data = await self._get_json("games", params={"index": index, "pageSize": page_size})
            payloads = data.get("data") if isinstance(data, dict) else None
            pagination = data.get("pagination") if isinstance(data, dict) else None
            if not isinstance(payloads, list):
                raise ProviderError(f"{self.name}: unexpected games response shape")
            if not isinstance(pagination, dict):
                raise ProviderError(f"{self.name}: missing games pagination")

            result_count = pagination.get("resultCount")
            total_raw = pagination.get("totalCount")
            if not isinstance(result_count, int) or not isinstance(total_raw, int):
                raise ProviderError(f"{self.name}: unexpected games pagination")
            total_count = total_raw

            for payload in payloads:
                if not isinstance(payload, dict):
                    continue
                slug = _coalesce(payload.get("slug"))
                if slug is None or str(slug).casefold() != normalised_game:
                    continue
                game_id = payload.get("id")
                if game_id is None:
                    raise ProviderError(f"{self.name}: game payload missing id")
                resolved_id = str(game_id)
                self._game_slug_cache[game_value] = resolved_id
                self._game_slug_cache[str(slug)] = resolved_id
                return resolved_id

            if result_count <= 0:
                break
            index += result_count

        raise NotFound(f"{self.name}: game {game!r} not found")

    def _build_latest_version(self, mod_key: ModID, latest_file: Mapping[str, object] | None) -> ModVersion | None:
        if latest_file is None:
            return None

        file_id = latest_file.get("id")
        file_name = _coalesce(latest_file.get("fileName"), latest_file.get("displayName"))
        if file_id is None and file_name is None:
            return None

        files: list[FileAsset] = []
        if file_id is not None and file_name is not None:
            file_length = latest_file.get("fileLength")
            size_bytes = file_length if isinstance(file_length, int) else None
            files.append(
                FileAsset(
                    file_id=str(file_id),
                    filename=str(file_name),
                    size_bytes=size_bytes,
                )
            )

        dependencies: list[Dependency] = []
        raw_dependencies = latest_file.get("dependencies")
        if isinstance(raw_dependencies, list):
            for entry in raw_dependencies:
                if not isinstance(entry, dict):
                    continue
                dependency_mod_id = entry.get("modId")
                relation = _relation_from_file_type(entry.get("relationType"))
                if dependency_mod_id is None or relation is None:
                    continue
                dependency_id = ModID(
                    provider=Provider.CURSEFORGE,
                    id=str(dependency_mod_id),
                    game=mod_key.game,
                )
                dependencies.append(
                    Dependency(
                        provider=Provider.CURSEFORGE,
                        id=dependency_id,
                        relation=relation,
                    )
                )

        raw_game_versions = latest_file.get("gameVersions")
        game_versions = [str(version) for version in raw_game_versions if isinstance(version, str)] if isinstance(
            raw_game_versions, list
        ) else []

        return ModVersion(
            id=mod_key,
            name=str(_coalesce(latest_file.get("displayName"), file_name, file_id)) if (file_name or file_id) else None,
            version=str(file_id) if file_id is not None else None,
            published_at=_parse_timestamp(latest_file.get("fileDate")),
            game_versions=game_versions,
            files=files,
            dependencies=dependencies,
            raw=dict(latest_file),
        )

    async def _resolve_mod_id(self, mod_id: ModID) -> tuple[str, str | None]:
        mod_value = str(mod_id.id).strip()
        if mod_value.isdigit():
            game_id = None
            if mod_id.game is not None:
                game_id = await self._resolve_game_id(str(mod_id.game))
            return mod_value, game_id
        if not mod_id.game:
            raise ValueError("CurseForge slug lookup requires ModID.game (game id).")
        game_id = await self._resolve_game_id(str(mod_id.game))
        search = await self._get_json(
            "mods/search",
            params={"gameId": game_id, "slug": mod_value, "pageSize": 1},
        )
        search_data = search.get("data") if isinstance(search, dict) else None
        if not isinstance(search_data, list) or not search_data:
            raise NotFound(f"{self.name}: mod {mod_id.id!r} not found")
        first = search_data[0]
        if not isinstance(first, dict) or first.get("id") is None:
            raise ProviderError(f"{self.name}: unexpected search response")
        return str(first["id"]), game_id

    def _build_mod(self, requested: ModID, payload: dict[str, object], mod_value: str) -> Mod:
        name = _coalesce(payload.get("name"), payload.get("slug"), requested.id)
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
        author_raw: dict[str, object] = {}
        if isinstance(authors, list) and authors:
            first = authors[0]
            if isinstance(first, dict):
                author_id = str(_coalesce(first.get("id"), first.get("userId"), author_id))
                author_name = str(_coalesce(first.get("name"), first.get("username"), author_id))
                author_raw = dict(first)

        game_id = _coalesce(payload.get("gameId"), requested.game)
        mod_key = ModID(
            provider=Provider.CURSEFORGE,
            id=str(payload.get("id", mod_value)),
            game=str(game_id) if game_id else None,
        )
        author = Author(provider=Provider.CURSEFORGE, id=str(author_id), name=str(author_name), raw=author_raw)
        latest_file = _pick_latest_file(payload.get("latestFiles"))
        latest_version = self._build_latest_version(mod_key, latest_file)
        latest_version_id = latest_version.version if latest_version is not None else None

        return Mod(
            provider=Provider.CURSEFORGE,
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

    async def _get_mod_single(self, mod_id: ModID) -> Mod:
        mod_value, _ = await self._resolve_mod_id(mod_id)
        data = await self._get_json(f"mods/{mod_value}")
        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            raise ProviderError(f"{self.name}: unexpected response shape")
        return self._build_mod(mod_id, payload, mod_value)

    async def get_mod(
        self,
        mod_id: ModID,
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> Mod:
        """Fetch a single mod from CurseForge.

        Args;
            mod_id: Provider-specific mod identifier.
            locales: Optional locale tags to request translations for.
            author_resolution: Author enrichment toggle.

        Returns;
            A normalised Mod instance.

        Raises;
            ValueError: If a slug is provided without a game id.
        """
        del locales, author_resolution
        return await self._get_mod_single(mod_id)

    async def get_mods(
        self,
        mod_ids: Sequence[ModID],
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> list[Mod]:
        """Fetch multiple mods from CurseForge."""
        requests = list(mod_ids)
        if not requests:
            return []

        fallback_indexes: list[int] = []
        grouped: dict[str, list[tuple[int, ModID, str]]] = {}
        for index, mod_id in enumerate(requests):
            mod_value = str(mod_id.id).strip()
            if mod_value.isdigit() and mod_id.game is None:
                fallback_indexes.append(index)
                continue
            resolved_id, game_id = await self._resolve_mod_id(mod_id)
            if game_id is None:
                fallback_indexes.append(index)
                continue
            grouped.setdefault(str(game_id), []).append((index, mod_id, resolved_id))

        resolved_results: dict[int, Mod] = {}
        for game_id, entries in grouped.items():
            unique_ids: list[int] = []
            for _, _, resolved_id in entries:
                value = int(resolved_id)
                if value not in unique_ids:
                    unique_ids.append(value)

            data = await self._post_json("mods", json={"modIds": unique_ids})
            payloads = data.get("data") if isinstance(data, dict) else None
            if not isinstance(payloads, list):
                raise ProviderError(f"{self.name}: unexpected response shape")

            payload_by_id: dict[str, dict[str, object]] = {}
            for payload in payloads:
                if not isinstance(payload, dict):
                    continue
                payload_id = payload.get("id")
                if payload_id is not None:
                    payload_by_id[str(payload_id)] = payload

            for index, requested, resolved_id in entries:
                payload = payload_by_id.get(resolved_id)
                if payload is None:
                    raise NotFound(f"{self.name}: mod {requested.id!r} not found")
                resolved_results[index] = self._build_mod(requested, payload, resolved_id)

        if fallback_indexes:
            for index in fallback_indexes:
                resolved_results[index] = await self._get_mod_single(requests[index])

        return [resolved_results[index] for index in range(len(requests))]
