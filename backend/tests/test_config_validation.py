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

    def test_default_discovery_poll_cadence_stays_inside_source_freshness_window(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value=None):
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(config.discovery_poll_interval_multiplier(), 12)
                self.assertLess(
                    config.poll_interval() * config.discovery_poll_interval_multiplier(),
                    config.max_source_trade_age_seconds(),
                )

    def test_invalid_profitability_duration_is_rejected_instead_of_silently_defaulted(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value=None):
            with patch.dict(os.environ, {"MAX_SOURCE_TRADE_AGE": "soon"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "MAX_SOURCE_TRADE_AGE must look like"):
                    config.max_source_trade_age_seconds()

            with patch.dict(os.environ, {"MAX_MARKET_HORIZON": "later"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "MAX_MARKET_HORIZON must look like"):
                    config.max_market_horizon_seconds()

    def test_negative_or_nonfinite_model_duration_is_rejected(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value=None):
            with patch.dict(os.environ, {"MODEL_MIN_TIME_TO_CLOSE": "-5m"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "MODEL_MIN_TIME_TO_CLOSE must be >= 0.0 seconds"):
                    config.model_min_time_to_close_seconds()

            with patch.dict(os.environ, {"MODEL_MIN_TIME_TO_CLOSE": "nan"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "MODEL_MIN_TIME_TO_CLOSE must be finite"):
                    config.model_min_time_to_close_seconds()

    def test_env_file_float_values_must_be_finite(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value="nan"):
            with self.assertRaisesRegex(config.ConfigError, "DATA_API_REQUEST_RATE_PER_SECOND must be finite"):
                config.data_api_request_rate_per_second()

    def test_model_gate_booleans_reject_malformed_values(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value="treu"):
            with self.assertRaisesRegex(config.ConfigError, "ALLOW_XGBOOST must be boolean"):
                config.allow_xgboost()

        with patch("kelly_watcher.config._get_env_file_value", return_value=None):
            with patch.dict(os.environ, {"ALLOW_HEURISTIC": "maybe"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "ALLOW_HEURISTIC must be boolean"):
                    config.allow_heuristic()

    def test_retrain_gate_config_rejects_malformed_values(self) -> None:
        with patch("kelly_watcher.config._get_env_file_value", return_value=None):
            with patch.dict(os.environ, {"RETRAIN_EARLY_CHECK_INTERVAL": "soon"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "RETRAIN_EARLY_CHECK_INTERVAL must look like"):
                    config.retrain_early_check_seconds()

            with patch.dict(os.environ, {"RETRAIN_EARLY_CHECK_INTERVAL": "30m"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "RETRAIN_EARLY_CHECK_INTERVAL must be >= 3600.0 seconds"):
                    config.retrain_early_check_seconds()

            with patch.dict(os.environ, {"RETRAIN_MIN_NEW_LABELS": "many"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "RETRAIN_MIN_NEW_LABELS must be an integer"):
                    config.retrain_min_new_labels()

            with patch.dict(os.environ, {"RETRAIN_MIN_SAMPLES": "0"}, clear=False):
                with self.assertRaisesRegex(config.ConfigError, "RETRAIN_MIN_SAMPLES must be >= 1"):
                    config.retrain_min_samples()


if __name__ == "__main__":
    unittest.main()
