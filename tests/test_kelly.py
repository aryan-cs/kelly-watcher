from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kelly_watcher.engine.kelly import heuristic_size, size_signal


class HeuristicSizingTest(unittest.TestCase):
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

    def test_xgboost_sizing_uses_quoted_market_price_even_when_effective_price_differs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAX_BET_FRACTION": "0.10",
                "MIN_CONFIDENCE": "0.55",
                "MIN_BET_USD": "1.00",
            },
            clear=False,
        ):
                quoted = size_signal(0.64, 0.60, 1000.0, "xgboost")
                fee_aware = size_signal(
                    0.64,
                    0.60,
                    1000.0,
                    "xgboost",
                    effective_market_price=0.63,
                )

        self.assertEqual(quoted["dollar_size"], fee_aware["dollar_size"])
        self.assertEqual(quoted["kelly_f"], fee_aware["kelly_f"])


if __name__ == "__main__":
    unittest.main()
