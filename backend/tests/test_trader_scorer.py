from __future__ import annotations

import math
import unittest

from kelly_watcher.engine.trader_scorer import TraderFeatures, TraderScorer, _to_float, _to_int


class TraderScorerTest(unittest.TestCase):
    def test_nonfinite_profile_values_score_conservatively(self) -> None:
        result = TraderScorer().score(
            TraderFeatures(
                win_rate=float("nan"),
                n_trades=float("nan"),
                consistency=float("inf"),
                account_age_d=float("inf"),
                volume_usd=0.0,
                avg_size_usd=0.0,
                diversity=float("inf"),
                conviction_ratio=float("inf"),
            )
        )

        self.assertTrue(math.isfinite(result["score"]))
        self.assertLessEqual(result["score"], 0.5)
        for value in result["components"].values():
            self.assertTrue(math.isfinite(value))

    def test_remote_numeric_coercion_rejects_nonfinite_values(self) -> None:
        self.assertEqual(_to_float("inf"), 0.0)
        self.assertEqual(_to_float(float("nan")), 0.0)
        self.assertEqual(_to_int("inf"), 0)
        self.assertEqual(_to_int(float("nan")), 0)


if __name__ == "__main__":
    unittest.main()
