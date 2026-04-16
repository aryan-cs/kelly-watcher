from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kelly_watcher.engine.adaptive_confidence import (
    BucketStats,
    CounterfactualRow,
    LocalCopyStats,
    derive_adaptive_floor,
)
from kelly_watcher.engine.kelly import heuristic_size


class AdaptiveConfidenceFloorTest(unittest.TestCase):
    def test_short_bucket_raises_floor_when_executed_returns_are_negative(self) -> None:
        decision = derive_adaptive_floor(
            base_floor=0.60,
            bucket="under_15m",
            bucket_stats=BucketStats(
                resolved_executed_count=8,
                resolved_executed_avg_return=-0.18,
                low_conf_samples=(),
            ),
        )

        self.assertEqual(decision.floor, 0.61)
        self.assertGreater(decision.adjustment, 0.0)

    def test_intrahour_bucket_can_lower_floor_on_good_counterfactual_evidence(self) -> None:
        samples = tuple(
            CounterfactualRow(confidence=confidence, won=won, counterfactual_return=ret)
            for confidence, won, ret in [
                (0.594, True, 0.40),
                (0.593, True, 0.62),
                (0.592, True, 0.32),
                (0.591, True, 0.28),
                (0.589, True, 0.54),
                (0.588, True, 0.47),
                (0.587, True, 0.51),
                (0.586, True, 0.23),
                (0.585, False, -1.0),
                (0.585, True, 0.36),
            ]
        )
        decision = derive_adaptive_floor(
            base_floor=0.60,
            bucket="15m_1h",
            bucket_stats=BucketStats(
                resolved_executed_count=6,
                resolved_executed_avg_return=0.01,
                low_conf_samples=samples,
            ),
        )

        self.assertEqual(decision.floor, 0.585)
        self.assertLess(decision.adjustment, 0.0)

    def test_medium_bucket_stays_at_base_when_live_bucket_returns_are_negative(self) -> None:
        samples = tuple(
            CounterfactualRow(confidence=confidence, won=True, counterfactual_return=0.25)
            for confidence in (0.594, 0.592, 0.591, 0.589, 0.588, 0.586, 0.585, 0.585)
        )
        decision = derive_adaptive_floor(
            base_floor=0.60,
            bucket="1h_6h",
            bucket_stats=BucketStats(
                resolved_executed_count=6,
                resolved_executed_avg_return=-0.12,
                low_conf_samples=samples,
            ),
        )

        self.assertEqual(decision.floor, 0.60)
        self.assertEqual(decision.adjustment, 0.0)

    def test_local_negative_feedback_raises_floor(self) -> None:
        decision = derive_adaptive_floor(
            base_floor=0.60,
            bucket="15m_1h",
            bucket_stats=BucketStats(
                resolved_executed_count=0,
                resolved_executed_avg_return=None,
                low_conf_samples=(),
            ),
            local_stats=LocalCopyStats(
                resolved_copied_count=4,
                copied_avg_return=-0.08,
                copied_win_rate=0.25,
            ),
        )

        self.assertEqual(decision.floor, 0.605)
        self.assertGreater(decision.adjustment, 0.0)

    def test_draggy_wallet_family_raises_floor_even_without_local_returns(self) -> None:
        decision = derive_adaptive_floor(
            base_floor=0.60,
            bucket="15m_1h",
            bucket_stats=BucketStats(
                resolved_executed_count=0,
                resolved_executed_avg_return=None,
                low_conf_samples=(),
            ),
            wallet_family="timing_sensitive",
        )

        self.assertEqual(decision.floor, 0.605)
        self.assertGreater(decision.adjustment, 0.0)
        self.assertIn("wallet family timing sensitive requires a higher confidence floor", decision.reasons)

    def test_liquidity_sensitive_wallet_family_gets_larger_floor_uplift(self) -> None:
        decision = derive_adaptive_floor(
            base_floor=0.60,
            bucket="1h_6h",
            bucket_stats=BucketStats(
                resolved_executed_count=0,
                resolved_executed_avg_return=None,
                low_conf_samples=(),
            ),
            wallet_family="liquidity_sensitive",
        )

        self.assertEqual(decision.floor, 0.61)
        self.assertGreater(decision.adjustment, 0.0)

    def test_heuristic_size_respects_min_confidence_override(self) -> None:
        with patch.dict(os.environ, {"MIN_CONFIDENCE": "0.60"}, clear=False):
            rejected = heuristic_size(0.59, 100.0)
            accepted = heuristic_size(0.59, 100.0, min_confidence_override=0.585)

        self.assertEqual(rejected["dollar_size"], 0.0)
        self.assertGreater(accepted["dollar_size"], 0.0)


if __name__ == "__main__":
    unittest.main()
