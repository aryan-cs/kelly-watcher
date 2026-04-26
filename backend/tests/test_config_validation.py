from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kelly_watcher import config


class ConfigValidationTest(unittest.TestCase):
    def test_profitability_risk_fractions_are_bounded(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value=None):
            with patch.dict(os.environ, {"MAX_BET_FRACTION": "1.25"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "MAX_BET_FRACTION must be <= 1.0"):
                    config.max_bet_fraction()

            with patch.dict(os.environ, {"MIN_CONFIDENCE": "-0.01"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "MIN_CONFIDENCE must be >= 0.0"):
                    config.min_confidence()

    def test_invalid_poll_interval_is_rejected_instead_of_silently_defaulted(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value=None):
            with patch.dict(os.environ, {"POLL_INTERVAL_SECONDS": "fast"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "POLL_INTERVAL_SECONDS must be numeric"):
                    config.poll_interval()

    def test_invalid_profitability_duration_is_rejected_instead_of_silently_defaulted(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value=None):
            with patch.dict(os.environ, {"MAX_SOURCE_TRADE_AGE": "soon"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "MAX_SOURCE_TRADE_AGE must look like"):
                    config.max_source_trade_age_seconds()

            with patch.dict(os.environ, {"MAX_MARKET_HORIZON": "later"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "MAX_MARKET_HORIZON must look like"):
                    config.max_market_horizon_seconds()

    def test_env_file_float_values_must_be_finite(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value="nan"):
            with self.assertRaisesRegex(config.ConfigError, "DATA_API_REQUEST_RATE_PER_SECOND must be finite"):
                config.data_api_request_rate_per_second()


if __name__ == "__main__":
    unittest.main()
