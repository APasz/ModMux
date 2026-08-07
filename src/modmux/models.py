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
    TRANSPORTFEVERNET = "TRANSPORTFEVERNET"


class DependencyRelation(StrEnum):
    """Supported dependency relationship types."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    INCOMPATIBLE = "incompatible"
    EMBEDDED = "embedded"
    TOOL = "tool"
    INCLUDED = "included"


class DownloadAccess(StrEnum):
    """Ways a caller can access a release file."""

    DIRECT = "direct"
    RESOLVABLE = "resolvable"
    WEB = "web"
    UNAVAILABLE = "unavailable"


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
        return hash(
            (
                self.provider,
                tuple(sorted(self.headers().items())),
                tuple(sorted(self.params().items())),
            )
        )


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
    raw: dict[str, Any] = Field(default_factory=dict)

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


class Dependency(BaseModel):
    """A mod dependency constraint."""

    provider: Provider | None = None
    id: ModID
    version_req: str | None = None
    relation: DependencyRelation = DependencyRelation.REQUIRED


class DownloadInfo(BaseModel):
    """Provider-supplied access details for a release file.

    Direct URLs can be short-lived and may still require caller credentials.
    Resolvable files need a provider-specific request before a URL can be used.
    Web URLs are intended to be opened in a browser or other provider-aware client.
    """

    model_config = ConfigDict(frozen=True)

    access: DownloadAccess
    url: AnyHttpUrl | None = None
    expires_at: datetime | None = None
    requires_authentication: bool = False

    @classmethod
    def direct(
        cls,
        url: str,
        *,
        expires_at: datetime | None = None,
        requires_authentication: bool = False,
    ) -> "DownloadInfo":
        """Build direct download access from a provider-supplied URL."""
        return cls.model_validate(
            {
                "access": DownloadAccess.DIRECT,
                "url": url,
                "expires_at": expires_at,
                "requires_authentication": requires_authentication,
            }
        )

    @classmethod
    def web(cls, url: str, *, requires_authentication: bool = False) -> "DownloadInfo":
        """Build browser-oriented download access from a provider-supplied URL."""
        return cls.model_validate(
            {
                "access": DownloadAccess.WEB,
                "url": url,
                "requires_authentication": requires_authentication,
            }
        )

    @model_validator(mode="after")
    def _validate_access_details(self) -> "DownloadInfo":
        if self.access in {DownloadAccess.DIRECT, DownloadAccess.WEB} and self.url is None:
            raise ValueError(f"{self.access} download access requires a URL")
        if self.access in {DownloadAccess.RESOLVABLE, DownloadAccess.UNAVAILABLE} and self.url is not None:
            raise ValueError(f"{self.access} download access cannot include a URL")
        if self.expires_at is not None and self.access is not DownloadAccess.DIRECT:
            raise ValueError("Only direct download URLs can have an expiry time")
        return self


def _unavailable_download_info() -> DownloadInfo:
    return DownloadInfo(access=DownloadAccess.UNAVAILABLE)


class FileAsset(BaseModel):
    """File metadata for mod releases."""

    file_id: str
    filename: str
    size_bytes: int | None = None
    download: DownloadInfo = Field(default_factory=_unavailable_download_info)


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
