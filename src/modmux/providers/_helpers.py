"""Shared normalization helpers for provider payloads."""

from datetime import UTC, datetime


def coalesce(*values: object) -> object | None:
    """Return the first non-empty value from a provider payload."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def parse_timestamp(value: object | None) -> datetime | None:
    """Parse a Unix or ISO-8601 timestamp from a provider payload."""
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


def extract_tags(raw: object, *, keys: tuple[str, ...] = ("name", "tag")) -> list[str]:
    """Extract non-empty tag names from a provider payload."""
    if not isinstance(raw, list):
        return []

    tags: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            if entry.strip():
                tags.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        value = coalesce(*(entry.get(key) for key in keys))
        if value is not None:
            tags.append(str(value))
    return tags


def clean_http_url(value: object | None) -> str | None:
    """Return an absolute HTTP(S) URL, if present."""
    if value is None:
        return None
    url = str(value)
    if not url.startswith(("http://", "https://")):
        return None
    return url
