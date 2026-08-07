"""Logging helpers for ModMux."""

from __future__ import annotations

import logging
import re

PACKAGE_LOGGER_NAME = "modmux"


_SECRET_PATTERNS = [
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|token|key)\s*=\s*[^&\s,;]+"),
    re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[^\s,;]+"),
]


class RedactFilter(logging.Filter):
    """Filter that redacts likely secrets from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        for pat in _SECRET_PATTERNS:
            msg = pat.sub(_redact_secret, msg)
        record.msg = msg
        record.args = ()
        for attr in ("api_key", "access_token", "token", "key", "auth", "authorization"):
            if hasattr(record, attr):
                setattr(record, attr, "***")
        return True


def _redact_secret(match: re.Match[str]) -> str:
    value = match.group(0)
    if "bearer" in value.casefold():
        prefix, _, _ = value.rpartition(" ")
        return f"{prefix} ***"
    prefix, _, _ = value.partition("=")
    return f"{prefix}=***"


def _ensure_redact_filter(logger: logging.Logger) -> None:
    if not any(isinstance(log_filter, RedactFilter) for log_filter in logger.filters):
        logger.addFilter(RedactFilter())


def get_logger(name: str | None = None, /) -> logging.Logger:
    """Create a namespaced logger for ModMux.

    Args;
        name: Optional module or logger name. If provided, only the final segment is used.

    Returns;
        A logger named `modmux` or `modmux.<segment>`.
    """
    logger_name = PACKAGE_LOGGER_NAME + (f".{name.rsplit('.', 1)[-1]}" if name else "")
    logger = logging.getLogger(logger_name)
    _ensure_redact_filter(logger)
    return logger


_logger = get_logger()
_logger.addHandler(logging.NullHandler())
