"""Logging helpers for ModMux."""

from __future__ import annotations

import logging
import re

PACKAGE_LOGGER_NAME = "modmux"


def get_logger(name: str | None = None, /) -> logging.Logger:
    """Create a namespaced logger for ModMux.

    Args;
        name: Optional module or logger name. If provided, only the final segment is used.

    Returns;
        A logger named `modmux` or `modmux.<segment>`.
    """
    sub_logger = logging.getLogger(f"{PACKAGE_LOGGER_NAME}" + (f".{name.rsplit('.', 1)[-1]}" if name else ""))
    # sub_logger.addFilter(RedactFilter())
    return sub_logger


_SECRET_PATTERNS = [
    re.compile(r"(?i)api[_-]?key\s*=\s*([A-Za-z0-9._-]+)"),
    re.compile(r"(?i)authorization:\s*bearer\s+([A-Za-z0-9._-]+)"),
]


class RedactFilter(logging.Filter):
    """Filter that redacts likely secrets from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Scrub message text
        msg = str(record.getMessage())
        for pat in _SECRET_PATTERNS:
            msg = pat.sub(lambda m: m.group(0).replace(m.group(1), "***"), msg)
        record.msg = msg
        # Scrub common extras if present
        for attr in ("token", "auth", "authorization"):
            if hasattr(record, attr):
                setattr(record, attr, "***")
        return True


_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
_logger.addHandler(logging.NullHandler())
_logger.addFilter(RedactFilter())
