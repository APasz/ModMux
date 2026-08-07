"""Modrinth provider integration."""

import json
from collections.abc import Sequence
from typing import cast
from urllib.parse import urlsplit

from httpx import AsyncClient
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr

from .._log import get_logger
from ..models import (
    Author,
    Dependency,
    DependencyRelation,
    DownloadAccess,
    DownloadInfo,
    FileAsset,
    LocaleTag,
    LocalisedText,
    Mod,
    ModID,
    ModVersion,
    Provider,
    ProviderCreds,
)
from ..modmux_errors import ModMuxError, NotFound, ProviderError
from ..toggles import ToggleMode, UndefinedType, resolve_toggle
from ..utils.discovery import register
from ._base import ProviderClient
from ._helpers import clean_http_url, coalesce, extract_tags, parse_timestamp
from .colour import Colour

log = get_logger(__name__)


class ModrinthCreds(ProviderCreds):
    """Credential model for Modrinth API access."""

    provider: Provider = Provider.MODRINTH
    api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("token", "key", "api_key"))

    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": self.api_key.get_secret_value()}


def _relation_from_dependency_type(value: object) -> DependencyRelation | None:
    if not isinstance(value, str):
        return None
    match value:
        case "required":
            return DependencyRelation.REQUIRED
        case "optional":
            return DependencyRelation.OPTIONAL
        case "incompatible":
            return DependencyRelation.INCOMPATIBLE
        case "embedded":
            return DependencyRelation.EMBEDDED
        case _:
            return None


def _extract_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(entry) for entry in value if isinstance(entry, str)]


@register
class ModrinthClient(ProviderClient):
    """Client for Modrinth mod metadata."""

    name: Provider = Provider.MODRINTH
    display_name: str = "Modrinth"
    colour: Colour = Colour("#1bd96a", "#F8F9FA", "#000000")
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

    def _author_from_members(self, team_id: str | None, members: object) -> Author:
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
                author_id = coalesce(user.get("id"), user.get("user_id"), chosen.get("user_id"), team_id, "unknown")
                author_name = coalesce(user.get("username"), user.get("name"), author_id, "unknown")
                return Author(provider=Provider.MODRINTH, id=str(author_id), name=str(author_name), raw=dict(chosen))

        fallback_id = team_id or "unknown"
        fallback_raw: dict[str, object] = {}
        if team_id is not None:
            fallback_raw = {"team_id": team_id}
        return Author(provider=Provider.MODRINTH, id=str(fallback_id), name=str(fallback_id), raw=fallback_raw)

    def _build_latest_version(self, mod_key: ModID, payload: object) -> ModVersion | None:
        if not isinstance(payload, dict):
            return None

        version_id = payload.get("id")
        version_name = coalesce(payload.get("name"), payload.get("version_number"), version_id)
        if version_name is None:
            return None

        files: list[FileAsset] = []
        raw_files = payload.get("files")
        if isinstance(raw_files, list):
            for file_payload in raw_files:
                if not isinstance(file_payload, dict):
                    continue
                filename = file_payload.get("filename")
                if not isinstance(filename, str):
                    continue
                size = file_payload.get("size")
                download_url = clean_http_url(file_payload.get("url"))
                download = (
                    DownloadInfo.direct(download_url)
                    if download_url is not None
                    else DownloadInfo(access=DownloadAccess.UNAVAILABLE)
                )
                files.append(
                    FileAsset(
                        file_id=str(coalesce(file_payload.get("url"), filename)),
                        filename=filename,
                        size_bytes=size if isinstance(size, int) else None,
                        download=download,
                    )
                )

        dependencies: list[Dependency] = []
        raw_dependencies = payload.get("dependencies")
        if isinstance(raw_dependencies, list):
            for dependency_payload in raw_dependencies:
                if not isinstance(dependency_payload, dict):
                    continue
                project_id = dependency_payload.get("project_id")
                relation = _relation_from_dependency_type(dependency_payload.get("dependency_type"))
                if project_id is None or relation is None:
                    continue
                dependency_version_id = dependency_payload.get("version_id")
                dependencies.append(
                    Dependency(
                        provider=Provider.MODRINTH,
                        id=ModID(provider=Provider.MODRINTH, id=str(project_id)),
                        version_req=str(dependency_version_id) if dependency_version_id is not None else None,
                        relation=relation,
                    )
                )

        changelog = payload.get("changelog")
        if changelog is not None:
            changelog = str(changelog)

        return ModVersion(
            id=mod_key,
            name=str(version_name),
            version=str(coalesce(payload.get("version_number"), version_id, version_name)),
            changelog_md=cast(str | None, changelog),
            published_at=parse_timestamp(payload.get("date_published")),
            game_versions=_extract_string_list(payload.get("game_versions")),
            loaders=_extract_string_list(payload.get("loaders")),
            files=files,
            dependencies=dependencies,
            raw=dict(payload),
        )

    def _build_mod(
        self,
        requested: ModID,
        payload: dict[str, object],
        author: Author,
        latest_version: ModVersion | None,
    ) -> Mod:
        project_id = str(coalesce(payload.get("id"), requested.id))
        slug = coalesce(payload.get("slug"), payload.get("id"))
        name = coalesce(payload.get("title"), payload.get("name"), payload.get("slug"), requested.id)
        description = coalesce(payload.get("body"), payload.get("description"), payload.get("summary"))
        if description is not None:
            description = str(description)

        created_at = parse_timestamp(coalesce(payload.get("published"), payload.get("date_created")))
        updated_at = parse_timestamp(coalesce(payload.get("updated"), payload.get("date_modified")))

        tags = extract_tags(payload.get("categories"))
        versions = payload.get("versions")
        latest_version_id = None
        if isinstance(versions, list) and versions:
            latest_version_id = str(versions[0])

        homepage = clean_http_url(
            coalesce(
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

    async def get_mod(
        self,
        mod_id: ModID,
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> Mod:
        """Fetch a single mod from Modrinth.

        Args;
            mod_id: Provider-specific mod identifier.
            locales: Optional locale tags to request translations for.
            author_resolution: Generic tri-state author enrichment toggle.

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
        """Fetch multiple mods from Modrinth."""
        del locales

        requests = list(mod_ids)
        if not requests:
            return []

        requested_ids = [str(mod_id.id) for mod_id in requests]
        payloads = await self._get_json("projects", params={"ids": json.dumps(requested_ids)})
        if not isinstance(payloads, list):
            raise ProviderError(f"{self.name}: unexpected response shape")

        payload_by_request: dict[str, dict[str, object]] = {}
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            project_id = coalesce(payload.get("id"))
            slug = coalesce(payload.get("slug"))
            if project_id is not None and str(project_id) in requested_ids:
                payload_by_request[str(project_id)] = payload
            if slug is not None and str(slug) in requested_ids:
                payload_by_request[str(slug)] = payload

        authors_by_request: dict[str, Author] = {}
        should_enrich_author = resolve_toggle(author_resolution, default=False)
        if should_enrich_author:
            team_ids: list[str] = []
            for requested in requests:
                payload = payload_by_request.get(str(requested.id))
                if not isinstance(payload, dict):
                    continue
                team_id = coalesce(payload.get("team"), payload.get("team_id"))
                if team_id is not None:
                    team_value = str(team_id)
                    if team_value not in team_ids:
                        team_ids.append(team_value)

            if team_ids:
                try:
                    members_payload = await self._get_json("teams", params={"ids": json.dumps(team_ids)})
                    if not isinstance(members_payload, list):
                        raise ProviderError(f"{self.name}: unexpected team response shape")
                    authors_by_team = {
                        team_id: self._author_from_members(team_id, members)
                        for team_id, members in zip(team_ids, members_payload, strict=False)
                    }
                except ModMuxError as exc:
                    log.debug("Failed to fetch Modrinth team members: %s", exc)
                    authors_by_team = {}

                for requested in requests:
                    payload = payload_by_request.get(str(requested.id))
                    if not isinstance(payload, dict):
                        continue
                    team_id = coalesce(payload.get("team"), payload.get("team_id"))
                    if team_id is not None:
                        author = authors_by_team.get(str(team_id))
                        if author is not None:
                            authors_by_request[str(requested.id)] = author

        latest_version_ids: list[str] = []
        for requested in requests:
            payload = payload_by_request.get(str(requested.id))
            if not isinstance(payload, dict):
                continue
            raw_versions = payload.get("versions")
            if not isinstance(raw_versions, list) or not raw_versions:
                continue
            latest_version_id = raw_versions[0]
            if latest_version_id is None:
                continue
            version_value = str(latest_version_id)
            if version_value not in latest_version_ids:
                latest_version_ids.append(version_value)

        latest_versions_by_id: dict[str, ModVersion] = {}
        if latest_version_ids:
            try:
                versions_payload = await self._get_json("versions", params={"ids": json.dumps(latest_version_ids)})
                if not isinstance(versions_payload, list):
                    raise ProviderError(f"{self.name}: unexpected version response shape")
                for version_payload in versions_payload:
                    if not isinstance(version_payload, dict):
                        continue
                    version_id = version_payload.get("id")
                    project_id = version_payload.get("project_id")
                    if version_id is None or project_id is None:
                        continue
                    latest_version = self._build_latest_version(
                        ModID(provider=Provider.MODRINTH, id=str(project_id)),
                        version_payload,
                    )
                    if latest_version is not None:
                        latest_versions_by_id[str(version_id)] = latest_version
            except ModMuxError as exc:
                log.debug("Failed to fetch Modrinth versions: %s", exc)

        mods: list[Mod] = []
        for requested in requests:
            payload = payload_by_request.get(str(requested.id))
            if not isinstance(payload, dict):
                raise NotFound(f"{self.name}: mod {requested.id!r} not found")
            fallback_team_id = coalesce(payload.get("team"), payload.get("team_id"))
            author = authors_by_request.get(str(requested.id))
            if author is None:
                author = self._author_from_members(
                    str(fallback_team_id) if fallback_team_id is not None else None,
                    None,
                )
            raw_versions = payload.get("versions")
            latest_version = None
            if isinstance(raw_versions, list) and raw_versions:
                latest_version_id = raw_versions[0]
                if latest_version_id is not None:
                    latest_version = latest_versions_by_id.get(str(latest_version_id))
            mods.append(self._build_mod(requested, payload, author, latest_version))
        return mods
