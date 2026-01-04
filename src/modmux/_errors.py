"""Project-specific exception types."""


class ModMuxError(Exception):
    """Base exception for ModMux errors."""


class NotFound(ModMuxError):
    """Raised when a requested resource cannot be found."""


class AuthError(ModMuxError):
    """Raised when authentication or authorisation fails."""


class RateLimited(ModMuxError):
    """Raised when a provider indicates rate limiting."""


class ProviderError(ModMuxError):
    """Raised for provider-specific failures."""
