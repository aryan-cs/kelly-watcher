from __future__ import annotations

import unittest

from kelly_watcher.engine.adaptive_entry_price import (
    EntryPriceEvidenceRow,
    derive_adaptive_entry_price_band,
)


class AdaptiveEntryPriceBandTest(unittest.TestCase):
    def test_selects_profitable_price_bands_from_resolved_evidence(self) -> None:
        now_ts = 1_800_000_000
        rows = (
            EntryPriceEvidenceRow(price=0.32, return_value=0.40, placed_at=now_ts - 100, source="executed"),
            EntryPriceEvidenceRow(price=0.38, return_value=0.30, placed_at=now_ts - 200, source="executed"),
            EntryPriceEvidenceRow(
                price=0.42,
                return_value=0.20,
                placed_at=now_ts - 300,
                source="counterfactual_outside_band",
            ),
            EntryPriceEvidenceRow(price=0.63, return_value=-0.20, placed_at=now_ts - 100, source="executed"),
            EntryPriceEvidenceRow(price=0.66, return_value=-0.10, placed_at=now_ts - 200, source="executed"),
            EntryPriceEvidenceRow(
                price=0.68,
                return_value=0.00,
                placed_at=now_ts - 300,
                source="counterfactual_outside_band",
            ),
            EntryPriceEvidenceRow(price=0.76, return_value=0.15, placed_at=now_ts - 100, source="executed"),
            EntryPriceEvidenceRow(price=0.83, return_value=0.20, placed_at=now_ts - 200, source="executed"),
            EntryPriceEvidenceRow(
                price=0.91,
                return_value=0.25,
                placed_at=now_ts - 300,
                source="counterfactual_outside_band",
            ),
        )

        decision = derive_adaptive_entry_price_band(
            rows=rows,
            base_min_price=0.65,
            base_max_price=0.75,
            min_samples=9,
            min_band_samples=2,
            min_avg_return=0.0,
            now_ts=now_ts,
            lookback_seconds=14 * 24 * 60 * 60,
        )

        self.assertEqual(decision.source, "adaptive")
        self.assertEqual(decision.allowed_bands, ("<0.45", ">=0.70"))
        self.assertAlmostEqual(decision.min_price, 0.01)
        self.assertAlmostEqual(decision.max_price, 1.0)

    def test_falls_back_until_enough_evidence_exists(self) -> None:
        now_ts = 1_800_000_000
        rows = (
            EntryPriceEvidenceRow(price=0.32, return_value=0.40, placed_at=now_ts - 100, source="executed"),
            EntryPriceEvidenceRow(price=0.76, return_value=0.15, placed_at=now_ts - 100, source="executed"),
        )

        decision = derive_adaptive_entry_price_band(
            rows=rows,
            base_min_price=0.65,
            base_max_price=0.75,
            min_samples=3,
            min_band_samples=2,
            min_avg_return=0.0,
            now_ts=now_ts,
            lookback_seconds=14 * 24 * 60 * 60,
        )

        self.assertEqual(decision.source, "fallback")
        self.assertEqual(decision.allowed_bands, ())
        self.assertAlmostEqual(decision.min_price, 0.65)
        self.assertAlmostEqual(decision.max_price, 0.75)


if __name__ == "__main__":
    unittest.main()
