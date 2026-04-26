from __future__ import annotations

import math
import unittest

from kelly_watcher.engine.features import build_feature_map
from kelly_watcher.engine.market_scorer import MarketFeatures
from kelly_watcher.engine.signal_engine import _model_feature_value, _sanitize_model_policy
from kelly_watcher.engine.trader_scorer import TraderFeatures


class FeatureMapTest(unittest.TestCase):
    def test_build_feature_map_converts_nonfinite_live_values_to_missing(self) -> None:
        trader_features = TraderFeatures(
            win_rate=float("nan"),
            n_trades=25,
            consistency=float("-inf"),
            account_age_d=120,
            volume_usd=float("inf"),
            avg_size_usd=40.0,
            diversity=8,
            conviction_ratio=1.2,
        )
        market_features = MarketFeatures(
            best_bid=0.49,
            best_ask=0.51,
            mid=0.50,
            execution_price=float("nan"),
            bid_depth_usd=float("inf"),
            ask_depth_usd=5_000.0,
            days_to_res=float("nan"),
            price_1h_ago=float("inf"),
            volume_24h_usd=float("inf"),
            volume_7d_avg_usd=18_000.0,
            oi_usd=float("-inf"),
            top_holder_pct=0.20,
            order_size_usd=10.0,
        )

        feature_map = build_feature_map(trader_features, market_features)

        self.assertIsNone(feature_map["f_trader_win_rate"])
        self.assertIsNone(feature_map["f_consistency"])
        self.assertIsNone(feature_map["f_trader_volume_usd"])
        self.assertIsNone(feature_map["f_days_to_res"])
        self.assertEqual(feature_map["f_price"], 0.50)
        self.assertAlmostEqual(feature_map["f_spread_pct"], 0.04, places=6)
        self.assertIsNone(feature_map["f_momentum_1h"])
        self.assertIsNone(feature_map["f_volume_24h_usd"])
        self.assertIsNone(feature_map["f_volume_trend"])
        self.assertIsNone(feature_map["f_oi_usd"])
        self.assertIsNone(feature_map["f_bid_depth_usd"])
        self.assertEqual(feature_map["f_ask_depth_usd"], 5_000.0)

    def test_model_feature_value_maps_nonfinite_values_to_nan(self) -> None:
        self.assertTrue(math.isnan(_model_feature_value(None)))
        self.assertTrue(math.isnan(_model_feature_value(float("inf"))))
        self.assertTrue(math.isnan(_model_feature_value("not-a-number")))
        self.assertEqual(_model_feature_value("0.42"), 0.42)

    def test_model_policy_sanitizes_edge_threshold_without_lowering_gate(self) -> None:
        self.assertEqual(_sanitize_model_policy({"edge_threshold": -0.10}), {"edge_threshold": 0.0})
        self.assertEqual(_sanitize_model_policy({"edge_threshold": float("nan")}), {"edge_threshold": 0.0})
        self.assertEqual(_sanitize_model_policy("not-a-policy"), {"edge_threshold": 0.0})
        self.assertEqual(_sanitize_model_policy({"edge_threshold": "0.025"}), {"edge_threshold": 0.025})


if __name__ == "__main__":
    unittest.main()
