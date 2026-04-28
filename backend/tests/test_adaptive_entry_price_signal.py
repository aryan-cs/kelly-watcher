from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import kelly_watcher.engine.signal_engine as signal_engine
from kelly_watcher.engine.adaptive_entry_price import AdaptiveEntryPriceDecision


class AdaptiveEntryPriceSignalTest(unittest.TestCase):
    def test_signal_engine_uses_adaptive_heuristic_entry_price_band(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            "kelly_watcher.engine.signal_engine.model_path",
            return_value="/tmp/kelly-watcher-missing-model.joblib",
        ):
            engine = signal_engine.SignalEngine()

        market_features = SimpleNamespace(execution_price=0.42, mid=0.42, days_to_res=0.5)
        belief = SimpleNamespace(
            adjusted_confidence=0.7,
            prior_confidence=0.5,
            blend=0.0,
            evidence=0,
        )
        adaptive_floor = SimpleNamespace(floor=0.55, as_dict=lambda: {"floor": 0.55})
        adaptive_entry_price = AdaptiveEntryPriceDecision(
            enabled=True,
            source="adaptive",
            min_price=0.01,
            max_price=0.45,
            allowed_bands=("<0.45",),
            base_min_price=0.65,
            base_max_price=0.75,
            sample_count=20,
            min_samples=12,
            min_band_samples=2,
            min_avg_return=0.0,
            reasons=("test adaptive band",),
            band_stats=(),
        )

        with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch(
            "kelly_watcher.engine.signal_engine.adjust_heuristic_confidence",
            return_value=belief,
        ), patch(
            "kelly_watcher.engine.signal_engine.adaptive_min_confidence_for_signal",
            return_value=adaptive_floor,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_entry_price",
            return_value=0.65,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_max_entry_price",
            return_value=0.75,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_time_to_close_seconds",
            return_value=0.0,
        ), patch(
            "kelly_watcher.engine.signal_engine.adaptive_heuristic_entry_price_band",
            return_value=adaptive_entry_price,
        ):
            result = engine._evaluate_heuristic(
                SimpleNamespace(),
                market_features,
                {"score": 0.8, "veto": None},
            )

        self.assertTrue(result["passed"])
        self.assertEqual(result["entry_price_band"], "<0.45")
        self.assertEqual(result["adaptive_entry_price"]["source"], "adaptive")
        self.assertEqual(result["adaptive_allowed_entry_price_bands"], ["<0.45"])


if __name__ == "__main__":
    unittest.main()
