from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kelly_watcher.engine.kelly import heuristic_size, kelly_size, size_signal


class HeuristicSizingTest(unittest.TestCase):
    def test_kelly_size_rejects_non_finite_confidence(self) -> None:
        sized = kelly_size(float("nan"), 0.5, 1000.0)

        self.assertEqual(sized["dollar_size"], 0.0)
        self.assertIn("non-finite confidence", sized["reason"])

    def test_kelly_size_clips_overconfident_input_before_sizing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAX_BET_FRACTION": "0.10",
                "MIN_CONFIDENCE": "0.60",
                "MIN_BET_USD": "1.00",
            },
            clear=False,
        ):
            sized = kelly_size(2.0, 0.5, 1000.0)

        self.assertEqual(sized["dollar_size"], 100.0)
        self.assertLessEqual(sized["full_kelly_f"], 1.0)

    def test_heuristic_size_rejects_non_finite_score(self) -> None:
        sized = heuristic_size(float("inf"), 1000.0)

        self.assertEqual(sized["dollar_size"], 0.0)
        self.assertIn("non-finite score", sized["reason"])

    def test_heuristic_size_expands_modest_edges_above_linear_curve(self) -> None:
        bankroll = 1000.0
        score = 0.63
        threshold = 0.60
        max_fraction = 0.10
        linear_size = round(bankroll * max_fraction * ((score - threshold) / (1.0 - threshold)), 2)

        with patch.dict(
            os.environ,
            {
                "MAX_BET_FRACTION": f"{max_fraction}",
                "MIN_CONFIDENCE": f"{threshold}",
                "MIN_BET_USD": "1.00",
            },
            clear=False,
        ):
            sized = heuristic_size(score, bankroll)

        self.assertGreater(sized["dollar_size"], linear_size)
        self.assertAlmostEqual(sized["heuristic_raw_edge"], 0.075, places=6)
        self.assertAlmostEqual(sized["heuristic_size_edge"], 0.27386, places=5)

    def test_heuristic_size_still_respects_max_fraction_cap(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAX_BET_FRACTION": "0.10",
                "MIN_CONFIDENCE": "0.60",
                "MIN_BET_USD": "1.00",
            },
            clear=False,
        ):
            sized = heuristic_size(1.0, 1000.0)

        self.assertEqual(sized["dollar_size"], 100.0)
        self.assertAlmostEqual(sized["kelly_f"], 0.10, places=6)

    def test_heuristic_size_rejects_fee_effective_price_outside_bounds(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAX_BET_FRACTION": "0.10",
                "MIN_CONFIDENCE": "0.60",
                "MIN_BET_USD": "1.00",
            },
            clear=False,
        ):
            sized = heuristic_size(
                1.0,
                1000.0,
                quoted_market_price=0.10,
                effective_market_price=1.01,
            )

        self.assertEqual(sized["dollar_size"], 0.0)
        self.assertIn("invalid effective market price", sized["reason"])

    def test_size_signal_rejects_invalid_effective_price_without_raising(self) -> None:
        sized = size_signal(
            0.90,
            0.50,
            1000.0,
            "heuristic",
            effective_market_price="not-a-price",
        )

        self.assertEqual(sized["dollar_size"], 0.0)
        self.assertIn("invalid effective market price", sized["reason"])


if __name__ == "__main__":
    unittest.main()
