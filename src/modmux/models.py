"""Pydantic models for providers and mod metadata."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class Provider(StrEnum):
    """Supported mod provider identifiers."""

    MODRINTH = "MODRINTH"
    CURSEFORGE = "CURSEFORGE"
    NEXUSMODS = "NEXUSMODS"
    WUBE = "WUBE"
    MODIO = "MODIO"
    STEAM = "STEAM"


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
    name: str
    author: Author
    summary: str | None = None

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
    name: str
    description_md: str | None = None
    author: Author
    homepage: AnyHttpUrl | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    latest_version_id: str | None = None
    latest_version: ModVersion | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
