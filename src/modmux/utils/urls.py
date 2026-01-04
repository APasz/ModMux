"""URL parsing helpers."""

from __future__ import annotations

from .. import providers
from ..models import ModID
from .discovery import REGISTRY, load_providers


def parse_url(url: str) -> ModID | None:
    """Parse a provider URL into a ModID.

    Args;
        url: Provider URL to parse.

    Returns;
        A ModID instance if a provider recognises the URL.
    """
    if not url or not url.strip():
        return None
    load_providers(providers)
    cleaned = url.strip()
    for provider_cls in REGISTRY.values():
        mod_id = provider_cls.parse_url(cleaned)
        if mod_id is not None:
            return mod_id
    return None
