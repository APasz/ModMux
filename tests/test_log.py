from __future__ import annotations

import io
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modmux._log import get_logger


class TestLogging(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger = get_logger("logging_test")
        self.previous_handlers = self.logger.handlers[:]
        self.previous_propagate = self.logger.propagate
        self.previous_level = self.logger.level
        self.logger.handlers.clear()
        self.logger.addHandler(self.handler)
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)

    def tearDown(self) -> None:
        self.logger.handlers.clear()
        self.logger.handlers.extend(self.previous_handlers)
        self.logger.propagate = self.previous_propagate
        self.logger.setLevel(self.previous_level)

    def test_redacts_parameterised_messages_without_breaking_interpolation(self) -> None:
        self.logger.warning("request failed: api_key=%s token=%s", "api-secret", "token-secret")

        message = self.stream.getvalue().strip()

        self.assertEqual(message, "request failed: api_key=*** token=***")

    def test_redacts_authorization_and_steam_query_key(self) -> None:
        self.logger.warning("Authorization: Bearer bearer-secret; key=steam-secret")

        message = self.stream.getvalue().strip()

        self.assertEqual(message, "Authorization: Bearer ***; key=***")

    def test_redacts_sensitive_extras(self) -> None:
        self.handler.setFormatter(logging.Formatter("%(api_key)s %(access_token)s %(token)s %(key)s"))

        self.logger.warning(
            "request failed",
            extra={
                "api_key": "api-secret",
                "access_token": "access-secret",
                "token": "token-secret",
                "key": "key-secret",
            },
        )

        self.assertEqual(self.stream.getvalue().strip(), "*** *** *** ***")
