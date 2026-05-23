"""transportfever.net provider integration."""

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlsplit

from httpx import AsyncClient
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError

from ..models import (
    Author,
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

_DEFAULT_DOWNLOAD_PREFIX_URL = "https://www.transportfever.net/filebase/entry-download/"
_DEFAULT_ENTRY_PREFIX_URL = "https://www.transportfever.net/filebase/entry/"
_ENTRY_PATH = "filebase/entry/{entry_id}/"
_TERMS_PATH = "filebase/terms-of-use/"
_SITE_TITLE_SUFFIX = " - Transport Fever Community"
_REDIRECT_STATUS_CODES: tuple[int, ...] = (301, 302, 303, 307, 308)


class TransportfeverGame(StrEnum):
    """Supported transportfever.net CommonAPI repositories."""

    TPF1 = "tpf1"
    TPF2 = "tpf2"


_REPO_PATHS: dict[TransportfeverGame, str] = {
    TransportfeverGame.TPF1: "filebase/repos/tpf1.json",
    TransportfeverGame.TPF2: "filebase/repos/tpf2.json",
}
_DEFAULT_REPOSITORY_ORDER: tuple[TransportfeverGame, ...] = (TransportfeverGame.TPF2, TransportfeverGame.TPF1)

_GAME_ALIASES: dict[str, TransportfeverGame] = {
    "1": TransportfeverGame.TPF1,
    "tf1": TransportfeverGame.TPF1,
    "tpf1": TransportfeverGame.TPF1,
    "transportfever": TransportfeverGame.TPF1,
    "transportfever1": TransportfeverGame.TPF1,
    "transport-fever": TransportfeverGame.TPF1,
    "transport-fever-1": TransportfeverGame.TPF1,
    "2": TransportfeverGame.TPF2,
    "tf2": TransportfeverGame.TPF2,
    "tpf2": TransportfeverGame.TPF2,
    "transportfever2": TransportfeverGame.TPF2,
    "transport-fever-2": TransportfeverGame.TPF2,
}


class TransportfeverRepoInfo(BaseModel):
    """Repository metadata emitted by the CommonAPI repository endpoint."""

    model_config = ConfigDict(extra="ignore")

    format: str | None = None
    version: int | None = None
    name: str | None = None
    download_prefix_url: str = _DEFAULT_DOWNLOAD_PREFIX_URL
    entry_prefix_url: str = _DEFAULT_ENTRY_PREFIX_URL
    utc_time: int | None = None
    complete: bool = True


class TransportfeverFileEntry(BaseModel):
    """Single file record in the transportfever.net repository."""

    model_config = ConfigDict(extra="ignore")

    name: str
    author: str
    modid: str
    version: str | None = None
    download: str | None = None
    download_size: int | None = None
    utc_changed: int | None = None
    entryurl: str | None = None


class TransportfeverRepository(BaseModel):
    """Transport Fever CommonAPI repository payload."""

    model_config = ConfigDict(extra="ignore")

    repo: TransportfeverRepoInfo
    files: list[TransportfeverFileEntry] = Field(default_factory=list)


def _normalise_game(value: str | None) -> TransportfeverGame:
    if value is None or not value.strip():
        return TransportfeverGame.TPF2
    cleaned = value.strip().lower().replace("_", "-")
    try:
        return _GAME_ALIASES[cleaned]
    except KeyError as exc:
        raise ValueError("transportfever.net ModID.game must be 'tpf1' or 'tpf2'.") from exc


class _IncompleteRepositoryError(ProviderError):
    """Raised when a CommonAPI repository explicitly reports an incomplete update."""


class TransportfeverHtmlFile(BaseModel):
    """File metadata parsed from a live filebase entry page."""

    model_config = ConfigDict(frozen=True)

    file_id: str
    filename: str
    download_url: str


class TransportfeverHtmlEntry(BaseModel):
    """Partial metadata parsed from a live filebase entry page."""

    model_config = ConfigDict(frozen=True)

    entry_id: str
    name: str
    author_id: str
    author_name: str
    homepage: str
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version: str | None = None
    files: list[TransportfeverHtmlFile] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


def _coalesce(*values: object) -> object | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _parse_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return datetime.fromtimestamp(float(cleaned), tz=UTC)
        except ValueError:
            cleaned = cleaned.replace("Z", "+00:00") if cleaned.endswith("Z") else cleaned
            try:
                return datetime.fromisoformat(cleaned)
            except ValueError:
                return None
    return None


def _join_url(prefix: str, suffix: object | None) -> str | None:
    if suffix is None:
        return None
    cleaned_suffix = str(suffix).strip()
    if not cleaned_suffix:
        return None
    if cleaned_suffix.startswith(("http://", "https://")):
        return cleaned_suffix
    return f"{prefix.rstrip('/')}/{cleaned_suffix.lstrip('/')}"


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _attrs_to_dict(attrs: Sequence[tuple[str, str | None]]) -> dict[str, str]:
    return {name.lower(): value for name, value in attrs if value is not None}


def _class_contains(attrs: dict[str, str], class_name: str) -> bool:
    return class_name in attrs.get("class", "").split()


def _site_title(value: str | None) -> str | None:
    if value is None:
        return None
    title = _normalise_text(value)
    if title.endswith(_SITE_TITLE_SUFFIX):
        title = title[: -len(_SITE_TITLE_SUFFIX)].strip()
    return title or None


def _leading_numeric_id(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip().strip("/")
    if not cleaned:
        return None
    match = re.match(r"^(\d+)(?:\D.*)?$", cleaned)
    return match.group(1) if match is not None else None


def _entry_id_from_entryurl(value: object | None) -> str | None:
    if value is None:
        return None
    segments = [segment for segment in str(value).split("/") if segment]
    if not segments:
        return None
    return _leading_numeric_id(segments[0])


def _is_terms_page(html: str) -> bool:
    return 'id="tpl_filebase_termsOfUse"' in html or 'data-template="termsOfUse"' in html


def _is_redirect_status(status_code: int) -> bool:
    return status_code in _REDIRECT_STATUS_CODES


def _file_id_from_download(value: object | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(str(value))
    file_ids = parse_qs(parsed.query).get("fileID")
    if file_ids:
        return file_ids[0]
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts:
        return _leading_numeric_id(path_parts[-1]) or path_parts[-1]
    return None


def _query_file_id(value: str) -> str | None:
    return _file_id_from_download(value) if "fileID=" in value else None


def _filename_for(entry: TransportfeverFileEntry, file_id: str) -> str:
    version = str(entry.version).strip() if entry.version is not None else ""
    if version:
        return f"{entry.modid}_{version}_{file_id}"
    return f"{entry.modid}_{file_id}"


def _entry_sort_key(entry: TransportfeverFileEntry) -> tuple[int, str, str]:
    return (
        entry.utc_changed if entry.utc_changed is not None else -1,
        entry.version or "",
        _file_id_from_download(entry.download) or "",
    )


def _entry_matches(entry: TransportfeverFileEntry, key: str) -> bool:
    return entry.modid == key or _entry_id_from_entryurl(entry.entryurl) == key


def _is_entry_id(value: object) -> bool:
    return _leading_numeric_id(value) == str(value).strip()


def _related_entries(
    entries: Sequence[TransportfeverFileEntry],
    selected: TransportfeverFileEntry,
    requested_key: str,
) -> list[TransportfeverFileEntry]:
    selected_entry_id = _entry_id_from_entryurl(selected.entryurl)
    selected_modid = selected.modid
    related: list[TransportfeverFileEntry] = []
    for entry in entries:
        entry_id = _entry_id_from_entryurl(entry.entryurl)
        if requested_key == selected_modid and entry.modid == selected_modid:
            related.append(entry)
            continue
        if selected_entry_id is not None and entry_id == selected_entry_id:
            related.append(entry)
    return related


def _raw_file_entry(
    entry: TransportfeverFileEntry,
    *,
    download_prefix_url: str,
    entry_prefix_url: str,
) -> dict[str, Any]:
    raw = entry.model_dump()
    raw["download_url"] = _join_url(download_prefix_url, entry.download)
    raw["entry_url"] = _join_url(entry_prefix_url, entry.entryurl)
    return raw


class _TransportfeverEntryHtmlParser(HTMLParser):
    """Small parser for WoltLab/VieCode filebase entry metadata."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.body_id: str | None = None
        self.meta: dict[str, str] = {}
        self.hidden_inputs: dict[str, str] = {}
        self.canonical_url: str | None = None
        self.object_id: str | None = None
        self.author_id: str | None = None
        self.files: dict[str, TransportfeverHtmlFile] = {}
        self.definitions: dict[str, str] = {}

        self._title_parts: list[str] = []
        self._content_title_parts: list[str] = []
        self._author_name_parts: list[str] = []
        self._dt_parts: list[str] = []
        self._dd_parts: list[str] = []
        self._file_text_parts: list[str] = []

        self._title_depth = 0
        self._content_title_depth = 0
        self._author_depth = 0
        self._author_name_depth = 0
        self._dt_depth = 0
        self._dd_depth = 0
        self._file_link_depth = 0

        self._current_dt: str | None = None
        self._current_file_href: str | None = None
        self._current_file_title: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = _attrs_to_dict(attrs)

        if tag == "body":
            self.body_id = attr_map.get("id")
        elif tag == "meta":
            self._handle_meta(attr_map)
        elif tag == "input" and attr_map.get("type") == "hidden":
            name = attr_map.get("name")
            value = attr_map.get("value")
            if name is not None and value is not None:
                self.hidden_inputs[name] = value
        elif tag == "link" and attr_map.get("rel") == "canonical":
            self.canonical_url = attr_map.get("href")
        elif tag == "article" and _class_contains(attr_map, "filebaseEntry"):
            self.object_id = attr_map.get("data-object-id") or self.object_id

        self._start_text_collections(tag, attr_map)

        href = attr_map.get("href")
        if tag == "a" and href is not None and "/filebase/entry-download/" in href:
            self._file_link_depth = 1
            self._current_file_href = href
            self._current_file_title = attr_map.get("title")
            self._file_text_parts = []
        elif self._file_link_depth > 0:
            self._file_link_depth += 1

    def _start_text_collections(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == "title":
            self._title_depth = 1
        elif self._title_depth > 0:
            self._title_depth += 1

        if tag == "h1" and _class_contains(attrs, "contentTitle"):
            self._content_title_depth = 1
        elif self._content_title_depth > 0:
            self._content_title_depth += 1

        itemprop = attrs.get("itemprop")
        if itemprop == "author":
            self._author_depth = 1
        elif self._author_depth > 0:
            self._author_depth += 1
        if self._author_depth > 0 and itemprop == "name":
            self._author_name_depth = 1
        elif self._author_name_depth > 0:
            self._author_name_depth += 1
        if self._author_depth > 0 and tag == "a" and _class_contains(attrs, "userLink"):
            self.author_id = attrs.get("data-user-id") or self.author_id

        if tag == "dt":
            self._dt_depth = 1
            self._dt_parts = []
        elif self._dt_depth > 0:
            self._dt_depth += 1

        if tag == "dd":
            self._dd_depth = 1
            self._dd_parts = []
        elif self._dd_depth > 0:
            self._dd_depth += 1

    def _handle_meta(self, attrs: dict[str, str]) -> None:
        key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
        content = attrs.get("content")
        if key is not None and content is not None:
            self.meta[key] = content

    def handle_data(self, data: str) -> None:
        if self._title_depth > 0:
            self._title_parts.append(data)
        if self._content_title_depth > 0:
            self._content_title_parts.append(data)
        if self._author_name_depth > 0:
            self._author_name_parts.append(data)
        if self._dt_depth > 0:
            self._dt_parts.append(data)
        if self._dd_depth > 0:
            self._dd_parts.append(data)
        if self._file_link_depth > 0:
            self._file_text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._file_link_depth > 0:
            self._file_link_depth -= 1
            if self._file_link_depth == 0:
                self._add_current_file()

        if self._dd_depth > 0:
            self._dd_depth -= 1
            if self._dd_depth == 0 and self._current_dt is not None:
                dd_value = _normalise_text(" ".join(self._dd_parts))
                if dd_value:
                    self.definitions[self._current_dt] = dd_value
        if self._dt_depth > 0:
            self._dt_depth -= 1
            if self._dt_depth == 0:
                dt_value = _normalise_text(" ".join(self._dt_parts))
                self._current_dt = dt_value or None

        if self._author_name_depth > 0:
            self._author_name_depth -= 1
        if self._author_depth > 0:
            self._author_depth -= 1
        if self._content_title_depth > 0:
            self._content_title_depth -= 1
        if self._title_depth > 0:
            self._title_depth -= 1

        if tag == "dl":
            self._current_dt = None

    def _add_current_file(self) -> None:
        href = self._current_file_href
        if href is None:
            return
        file_id = _query_file_id(href)
        if file_id is None:
            return

        link_text = _normalise_text(" ".join(self._file_text_parts))
        filename = _normalise_text(self._current_file_title or link_text or file_id)
        existing = self.files.get(file_id)
        if existing is not None and existing.filename != file_id:
            return

        self.files[file_id] = TransportfeverHtmlFile(file_id=file_id, filename=filename, download_url=href)

    def to_entry(self, fallback_entry_id: str) -> TransportfeverHtmlEntry | None:
        name = _site_title(self.meta.get("og:title")) or _site_title(" ".join(self._title_parts))
        name = name or _site_title(" ".join(self._content_title_parts))
        if name is None:
            return None

        author_name = _normalise_text(" ".join(self._author_name_parts)) or "unknown"
        homepage = self.canonical_url or self.meta.get("og:url") or f"{_DEFAULT_ENTRY_PREFIX_URL}{fallback_entry_id}/"
        entry_id = self.object_id or fallback_entry_id
        description = self.meta.get("og:description") or self.meta.get("description")
        description = _normalise_text(description) if description is not None else None
        version = self.definitions.get("Aktuelle Version") or self.definitions.get("Current Version")

        return TransportfeverHtmlEntry(
            entry_id=entry_id,
            name=name,
            author_id=self.author_id or author_name,
            author_name=author_name,
            homepage=homepage,
            description=description,
            created_at=_parse_datetime(self.meta.get("datePublished")),
            updated_at=_parse_datetime(self.meta.get("dateModified")),
            version=version,
            files=list(self.files.values()),
            raw={
                "body_id": self.body_id,
                "canonical_url": self.canonical_url,
                "meta": dict(self.meta),
                "definitions": dict(self.definitions),
            },
        )


@register
class TransportfevernetClient(ProviderClient):
    """Client for transportfever.net filebase metadata."""

    name: Provider = Provider.TRANSPORTFEVERNET
    display_name: str = "transportfever.net"
    colour: Colour = Colour("#3F6EA8", "#FFFFFF", "#E4EEF7")
    base = "https://www.transportfever.net"
    domains = ("transportfever.net",)

    def __init__(self, creds: ProviderCreds | None = None, *, http: AsyncClient, cache: object | None = None) -> None:
        super().__init__(creds, http=http, cache=cache)

    @classmethod
    def parse_url(cls, url: str) -> ModID | None:
        parts = urlsplit(cls._normalise_url(url))
        if not cls._match_domain(parts.hostname):
            return None

        segments = cls._path_segments(parts.path)
        entry_id = cls._entry_id_from_segments(segments)
        if entry_id is None and parts.query:
            entry_id = cls._entry_id_from_segments(cls._path_segments(unquote(parts.query)))
        if entry_id is None:
            return None
        return ModID(provider=Provider.TRANSPORTFEVERNET, id=entry_id)

    @staticmethod
    def _entry_id_from_segments(segments: Sequence[str]) -> str | None:
        lowered = [segment.lower() for segment in segments]
        if "entry" not in lowered:
            return None
        entry_index = lowered.index("entry")
        if entry_index + 1 >= len(segments):
            return None
        return _leading_numeric_id(segments[entry_index + 1])

    @staticmethod
    def _candidate_games(mod_id: ModID) -> tuple[TransportfeverGame, ...]:
        if mod_id.game is not None:
            return (_normalise_game(mod_id.game),)
        return _DEFAULT_REPOSITORY_ORDER

    @staticmethod
    def _response_game(mod_id: ModID, game: TransportfeverGame) -> TransportfeverGame | None:
        return game if mod_id.game is not None else None

    async def _fetch_cached_repository(
        self,
        game: TransportfeverGame,
        repositories: dict[TransportfeverGame, TransportfeverRepository],
    ) -> TransportfeverRepository:
        repository = repositories.get(game)
        if repository is None:
            repository = await self._fetch_repository(game)
            repositories[game] = repository
        return repository

    async def _fetch_repository(self, game: TransportfeverGame) -> TransportfeverRepository:
        payload = await self._get_json(_REPO_PATHS[game])
        try:
            repository = TransportfeverRepository.model_validate(payload)
        except ValidationError as exc:
            raise ProviderError(f"{self.name}: unexpected repository response shape") from exc
        if not repository.repo.complete:
            raise _IncompleteRepositoryError(f"{self.name}: repository update is marked incomplete")
        return repository

    async def _fetch_entry_page(self, entry_id: str) -> str:
        path = _ENTRY_PATH.format(entry_id=entry_id)
        response = await self._get(path, allowed_status_codes=_REDIRECT_STATUS_CODES)
        html = response.text
        if _is_redirect_status(response.status_code) and not html.strip():
            location = response.headers.get("Location")
            if location:
                response = await self._get(location, allowed_status_codes=_REDIRECT_STATUS_CODES)
                html = response.text
        if not _is_terms_page(html):
            return html

        await self._accept_terms(html, fallback_redirect=f"/{path}")
        response = await self._get(path, allowed_status_codes=_REDIRECT_STATUS_CODES)
        html = response.text
        if _is_redirect_status(response.status_code) and not html.strip():
            location = response.headers.get("Location")
            if location:
                response = await self._get(location, allowed_status_codes=_REDIRECT_STATUS_CODES)
                html = response.text
        if _is_terms_page(html):
            raise ProviderError(f"{self.name}: filebase terms gate could not be accepted")
        return html

    async def _accept_terms(self, html: str, *, fallback_redirect: str) -> None:
        parser = _TransportfeverEntryHtmlParser()
        parser.feed(html)
        token = parser.hidden_inputs.get("t")
        if token is None:
            raise ProviderError(f"{self.name}: filebase terms page is missing a token")
        redirect = parser.hidden_inputs.get("redirect") or fallback_redirect

        await self._post(_TERMS_PATH, data={"redirect": redirect, "t": token}, follow_redirects=True)

    async def _fetch_html_entry(
        self,
        mod_id: ModID,
        *,
        game: TransportfeverGame | None,
        not_found: NotFound,
    ) -> Mod:
        entry_id = str(mod_id.id).strip()
        if not entry_id.isdigit():
            raise not_found

        entry = await self._fetch_html_entry_data(entry_id)
        return self._build_html_mod(entry, game=game)

    async def _fetch_html_entry_data(self, entry_id: str) -> TransportfeverHtmlEntry:
        html = await self._fetch_entry_page(entry_id)
        parser = _TransportfeverEntryHtmlParser()
        parser.feed(html)
        entry = parser.to_entry(entry_id)
        if entry is None:
            raise ProviderError(f"{self.name}: entry page did not contain mod metadata")
        return entry

    async def _resolve_mod(
        self,
        mod_id: ModID,
        repositories: dict[TransportfeverGame, TransportfeverRepository],
    ) -> Mod:
        not_found: NotFound | None = None
        incomplete_repo_error: _IncompleteRepositoryError | None = None

        for requested_game in self._candidate_games(mod_id):
            try:
                repository = await self._fetch_cached_repository(requested_game, repositories)
                selected, related = self._select_entry(repository, mod_id)
            except NotFound as exc:
                not_found = exc
                continue
            except _IncompleteRepositoryError as exc:
                if not _is_entry_id(mod_id.id):
                    raise
                incomplete_repo_error = exc
                not_found = NotFound(f"{self.name}: mod {mod_id.id!r} not found")
                continue

            return self._build_mod(
                selected,
                related,
                repository.repo,
                game=self._response_game(mod_id, requested_game),
            )

        if not_found is None:
            raise NotFound(f"{self.name}: mod {mod_id.id!r} not found")

        try:
            return await self._fetch_html_entry(
                mod_id,
                game=_normalise_game(mod_id.game) if mod_id.game is not None else None,
                not_found=not_found,
            )
        except NotFound:
            if incomplete_repo_error is not None:
                raise incomplete_repo_error
            raise

    def _select_entry(
        self,
        repository: TransportfeverRepository,
        mod_id: ModID,
    ) -> tuple[TransportfeverFileEntry, list[TransportfeverFileEntry]]:
        requested_key = str(mod_id.id).strip()
        if not requested_key:
            raise ValueError("transportfever.net mod id must be non-empty.")

        matches = [entry for entry in repository.files if _entry_matches(entry, requested_key)]
        if not matches:
            raise NotFound(f"{self.name}: mod {requested_key!r} not found")

        selected = max(matches, key=_entry_sort_key)
        related = _related_entries(repository.files, selected, requested_key)
        return selected, related or matches

    def _build_latest_version(
        self,
        mod_key: ModID,
        selected: TransportfeverFileEntry,
        related: Sequence[TransportfeverFileEntry],
        repo: TransportfeverRepoInfo,
    ) -> ModVersion:
        selected_version = selected.version
        version_entries = [
            entry for entry in related if entry.version == selected_version and entry.modid == selected.modid
        ]
        if not version_entries:
            version_entries = [selected]

        files: list[FileAsset] = []
        for entry in version_entries:
            file_id = str(_coalesce(_file_id_from_download(entry.download), entry.version, entry.modid))
            files.append(
                FileAsset(
                    file_id=file_id,
                    filename=_filename_for(entry, file_id),
                    size_bytes=entry.download_size,
                )
            )

        return ModVersion(
            id=mod_key,
            name=selected.version,
            version=selected.version,
            published_at=_parse_datetime(selected.utc_changed),
            files=files,
            raw={
                "selected": _raw_file_entry(
                    selected,
                    download_prefix_url=repo.download_prefix_url,
                    entry_prefix_url=repo.entry_prefix_url,
                ),
                "files": [
                    _raw_file_entry(
                        entry,
                        download_prefix_url=repo.download_prefix_url,
                        entry_prefix_url=repo.entry_prefix_url,
                    )
                    for entry in version_entries
                ],
            },
        )

    def _build_mod(
        self,
        selected: TransportfeverFileEntry,
        related: Sequence[TransportfeverFileEntry],
        repo: TransportfeverRepoInfo,
        *,
        game: TransportfeverGame | None,
    ) -> Mod:
        entry_id = _entry_id_from_entryurl(selected.entryurl)
        resolved_id = str(_coalesce(entry_id, selected.modid))
        mod_key = ModID(provider=Provider.TRANSPORTFEVERNET, id=resolved_id, game=game.value if game else None)
        author_name = str(_coalesce(selected.author, "unknown"))
        homepage = _join_url(repo.entry_prefix_url, selected.entryurl)
        latest_version = self._build_latest_version(mod_key, selected, related, repo)

        timestamps = [
            parsed for parsed in (_parse_datetime(entry.utc_changed) for entry in related) if parsed is not None
        ]
        created_at = min(timestamps) if timestamps else None
        updated_at = max(timestamps) if timestamps else None

        return Mod(
            provider=Provider.TRANSPORTFEVERNET,
            id=mod_key,
            slug=selected.modid,
            name=LocalisedText(value=selected.name),
            author=Author(
                provider=Provider.TRANSPORTFEVERNET,
                id=author_name,
                name=author_name,
                raw={"author": selected.author},
            ),
            homepage=cast(AnyHttpUrl | None, homepage),
            created_at=created_at,
            updated_at=updated_at,
            latest_version_id=selected.version,
            latest_version=latest_version,
            raw={
                "repo": repo.model_dump(),
                "selected": _raw_file_entry(
                    selected,
                    download_prefix_url=repo.download_prefix_url,
                    entry_prefix_url=repo.entry_prefix_url,
                ),
                "matches": [
                    _raw_file_entry(
                        entry,
                        download_prefix_url=repo.download_prefix_url,
                        entry_prefix_url=repo.entry_prefix_url,
                    )
                    for entry in related
                ],
            },
        )

    def _build_html_mod(self, entry: TransportfeverHtmlEntry, *, game: TransportfeverGame | None) -> Mod:
        mod_key = ModID(provider=Provider.TRANSPORTFEVERNET, id=entry.entry_id, game=game.value if game else None)
        latest_files = [
            FileAsset(file_id=file.file_id, filename=file.filename) for file in entry.files if file.filename.strip()
        ]
        latest_version = None
        if entry.version is not None or latest_files:
            latest_version = ModVersion(
                id=mod_key,
                name=entry.version,
                version=entry.version,
                published_at=entry.updated_at or entry.created_at,
                files=latest_files,
                raw={
                    "source": "html_fallback",
                    "files": [file.model_dump() for file in entry.files],
                },
            )

        return Mod(
            provider=Provider.TRANSPORTFEVERNET,
            id=mod_key,
            name=LocalisedText(value=entry.name),
            description_md=LocalisedText(value=entry.description) if entry.description is not None else None,
            author=Author(
                provider=Provider.TRANSPORTFEVERNET,
                id=entry.author_id,
                name=entry.author_name,
                raw={"source": "html_fallback"},
            ),
            homepage=cast(AnyHttpUrl | None, entry.homepage),
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            latest_version_id=entry.version,
            latest_version=latest_version,
            raw={
                "source": "html_fallback",
                **entry.raw,
            },
        )

    async def get_mod(
        self,
        mod_id: ModID,
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> Mod:
        """Fetch a single mod from the transportfever.net repository."""
        mods = await self.get_mods([mod_id], locales=locales, author_resolution=author_resolution)
        return mods[0]

    async def get_mods(
        self,
        mod_ids: Sequence[ModID],
        *,
        locales: list[LocaleTag] | None = None,
        author_resolution: ToggleMode | bool | UndefinedType = ToggleMode.AUTO,
    ) -> list[Mod]:
        """Fetch multiple mods from the transportfever.net repository."""
        del locales, author_resolution

        requests = list(mod_ids)
        if not requests:
            return []

        repositories: dict[TransportfeverGame, TransportfeverRepository] = {}
        mods: list[Mod] = []
        for mod_id in requests:
            mods.append(await self._resolve_mod(mod_id, repositories))
        return mods
