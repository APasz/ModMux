"""Provider discovery and registration helpers."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import TypeVar

from .._log import get_logger
from ..models import Provider
from ..providers._base import ProviderClient

log = get_logger(__name__)

T = TypeVar("T", bound=ProviderClient)

REGISTRY: dict[Provider, type[ProviderClient]] = {}


def load_providers(pkg: ModuleType) -> list[ModuleType]:
    """Import each module in a package and return the loaded modules.

    Args;
        pkg: Package module to scan for providers.

    Returns;
        A list of imported provider modules.

    Raises;
        ValueError: If the provided module is not a package.
    """
    log.debug("Loading providers from %s", pkg.__name__)
    if not hasattr(pkg, "__path__"):
        raise ValueError(f"{pkg.__name__!r} is not a package")
    prefix = pkg.__name__ + "."
    modules: list[ModuleType] = []
    for _, name, _ in pkgutil.iter_modules(pkg.__path__, prefix):
        mod = importlib.import_module(name)
        modules.append(mod)
    return modules


def register(cls: type[T], *, key: Provider | None = None) -> type[T]:
    """Register a provider client class under its Provider enum.

    Args;
        cls: The provider client class.
        key: Optional override for the provider key.

    Returns;
        The input class for decorator usage.

    Raises;
        TypeError: If the key is not a Provider enum.
        RuntimeError: If the key is already registered.
    """
    provider_key = key or getattr(cls, "name", cls.__name__)
    if not isinstance(provider_key, Provider):
        raise TypeError(f"Provider key must be a Provider enum, got {provider_key!r}")
    if provider_key in REGISTRY and REGISTRY[provider_key] is not cls:
        raise RuntimeError(f"Duplicate key {provider_key!r}")
    REGISTRY[provider_key] = cls
    log.debug("Registered: %s", provider_key)
    return cls
