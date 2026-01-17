"""Pydantic models for providers and mod metadata."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StringConstraints, model_validator


class Provider(StrEnum):
    """Supported mod provider identifiers."""

    MODRINTH = "MODRINTH"
    CURSEFORGE = "CURSEFORGE"
    NEXUSMODS = "NEXUSMODS"
    WUBE = "WUBE"
    MODIO = "MODIO"
    STEAM = "STEAM"


LocaleTag = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=50,
        pattern=r"^[A-Za-z0-9]+([_-][A-Za-z0-9]+)*$",
    ),
]


class LocalisedText(BaseModel):
    """Text with optional translations keyed by locale tags."""

    model_config = ConfigDict(frozen=True)

    value: str
    translations: dict[LocaleTag, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, value: object) -> object:
        if isinstance(value, str):
            return {"value": value}
        if isinstance(value, LocalisedText):
            return value
        return value

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash((self.value, tuple(sorted(self.translations.items()))))


class ProviderCreds(BaseModel):
    """Frozen credential model for provider authentication."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)
    provider: Provider

    def headers(self) -> dict[str, str]:
        """Return any HTTP headers needed for authentication.

        Returns;
            A mapping of header names to values.
        """
        return {}

    def params(self) -> dict[str, str]:
        """Return any query parameters needed for authentication.

        Returns;
            A mapping of parameter names to values.
        """
        return {}

    def format_base(self, base: str) -> str:
        """Return the base URL, optionally customised per credentials.

        Args;
            base: The default base URL.

        Returns;
            The base URL, possibly modified with user-specific data.
        """
        return base

    def __hash__(self) -> int:
        return hash((self.provider, self.headers(), self.params()))


class ModID(BaseModel):
    """Frozen provider-scoped mod identifier."""

    model_config = ConfigDict(frozen=True)

    provider: Provider
    id: str
    game: str | None = None

    def __hash__(self) -> int:
        return hash((self.provider, self.id, self.game))


class Author(BaseModel):
    """Frozen mod author metadata."""

    model_config = ConfigDict(frozen=True)

    provider: Provider
    id: str
    name: str

    def __hash__(self) -> int:
        return hash((self.provider, self.id, self.name))


class ModSummary(BaseModel):
    """Frozen summary representation for a mod."""

    model_config = ConfigDict(frozen=True)

    provider: Provider
    id: ModID
    slug: str | None = None
    name: LocalisedText
    author: Author
    summary: LocalisedText | None = None

    def __hash__(self) -> int:
        return hash((self.provider, self.id, self.slug, self.name, self.author, self.summary))


class Dependency(BaseModel):
    """A mod dependency constraint."""

    provider: Provider | None = None
    id: ModID
    version_req: str | None = None
    optional: bool = False

class FileAsset(BaseModel):
    """File metadata for mod releases."""

    file_id: str
    filename: str
    size_bytes: int | None = None


class ModVersion(BaseModel):
    """Release metadata for a mod version."""

    id: ModID
    name: str | None = None
    version: str | None = None
    changelog_md: str | None = None
    published_at: datetime | None = None
    game_versions: list[str] = Field(default_factory=list)
    loaders: list[str] = Field(default_factory=list)
    files: list[FileAsset] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Mod(BaseModel):
    """Full mod metadata."""

    provider: Provider
    id: ModID
    slug: str | None = None
    name: LocalisedText
    description_md: LocalisedText | None = None
    author: Author
    homepage: AnyHttpUrl | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    latest_version_id: str | None = None
    latest_version: ModVersion | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
