from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import kelly_watcher.config as config
from kelly_watcher.engine.market_scorer import MarketScorer, build_market_features


def _close_time_in(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


class MarketWindowAlignmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = {
            "best_bid": 0.59,
            "best_ask": 0.61,
            "mid": 0.60,
            "bid_depth_usd": 1_500.0,
            "ask_depth_usd": 1_500.0,
            "price_history_1h": [
                {"t": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()), "p": 0.58},
                {"t": int(datetime.now(timezone.utc).timestamp()), "p": 0.60},
            ],
            "volume_24h_usd": 15_000.0,
            "volume_7d_avg_usd": 8_000.0,
            "oi_usd": 40_000.0,
            "top_holder_pct": 0.12,
        }

    def test_market_window_blocks_trades_inside_min_execution_window(self) -> None:
        with patch.object(config, "ENV_PATH", Path("/tmp/kelly-watcher-no-env")), patch.dict(
            os.environ,
            {
                "MIN_EXECUTION_WINDOW": "45s",
                "MAX_MARKET_HORIZON": "6h",
                "POLL_INTERVAL_SECONDS": "2",
            },
            clear=False,
        ):
            features = build_market_features(
                self.snapshot,
                _close_time_in(30),
                order_size_usd=25.0,
                execution_price=0.61,
            )
            self.assertIsNotNone(features)
            result = MarketScorer().score(features)

        self.assertEqual(result["veto"], "expires in <45s")

    def test_market_window_accepts_medium_horizon_copyable_markets(self) -> None:
        with patch.object(config, "ENV_PATH", Path("/tmp/kelly-watcher-no-env")), patch.dict(
            os.environ,
            {
                "MIN_EXECUTION_WINDOW": "45s",
                "MAX_MARKET_HORIZON": "6h",
                "POLL_INTERVAL_SECONDS": "2",
            },
            clear=False,
        ):
            features = build_market_features(
                self.snapshot,
                _close_time_in(3 * 3600),
                order_size_usd=25.0,
                execution_price=0.61,
            )
            self.assertIsNotNone(features)
            result = MarketScorer().score(features)

        self.assertIsNone(result["veto"])
        self.assertGreater(result["score"], 0.0)

    def test_market_window_rejects_markets_beyond_strategy_horizon(self) -> None:
        with patch.object(config, "ENV_PATH", Path("/tmp/kelly-watcher-no-env")), patch.dict(
            os.environ,
            {
                "MIN_EXECUTION_WINDOW": "45s",
                "MAX_MARKET_HORIZON": "6h",
                "POLL_INTERVAL_SECONDS": "2",
            },
            clear=False,
        ):
            features = build_market_features(
                self.snapshot,
                _close_time_in(8 * 3600),
                order_size_usd=25.0,
                execution_price=0.61,
            )
            self.assertIsNotNone(features)
            result = MarketScorer().score(features)

        self.assertEqual(result["veto"], "beyond max horizon 6h")


if __name__ == "__main__":
    unittest.main()
