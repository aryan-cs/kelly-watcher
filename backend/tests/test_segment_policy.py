from __future__ import annotations

import unittest

from kelly_watcher.engine.segment_policy import (
    SEGMENT_FALLBACK,
    SHORT_TIME_TO_CLOSE_BANDS,
    MID_TIME_TO_CLOSE_BANDS,
    LONG_TIME_TO_CLOSE_BANDS,
    WATCH_TIERS,
    segment_route_for_seconds,
    segment_route_for_trade,
)


class SegmentPolicyTest(unittest.TestCase):
    def test_watch_tier_and_horizon_map_to_full_fixed_segment_matrix(self) -> None:
        horizon_bands = {
            "short": SHORT_TIME_TO_CLOSE_BANDS[0],
            "mid": MID_TIME_TO_CLOSE_BANDS[0],
            "long": LONG_TIME_TO_CLOSE_BANDS[0],
        }

        for watch_tier in WATCH_TIERS:
            for horizon_bucket, time_to_close_band in horizon_bands.items():
                expected_segment_id = f"{watch_tier}_{horizon_bucket}"
                route = segment_route_for_trade(
                    watch_tier=watch_tier,
                    time_to_close_band=time_to_close_band,
                )
                with self.subTest(watch_tier=watch_tier, horizon_bucket=horizon_bucket):
                    self.assertEqual(route.segment_id, expected_segment_id)
                    self.assertEqual(route.watch_tier, watch_tier)
                    self.assertEqual(route.horizon_bucket, horizon_bucket)
                    self.assertFalse(route.fallback)

    def test_invalid_watch_tier_or_horizon_falls_back(self) -> None:
        route = segment_route_for_trade(watch_tier="cold", time_to_close_band="2h-12h")
        self.assertEqual(route.segment_id, SEGMENT_FALLBACK)
        self.assertTrue(route.fallback)

        seconds_route = segment_route_for_seconds(watch_tier="warm", time_to_close_seconds=6 * 3600)
        self.assertEqual(seconds_route.segment_id, "warm_mid")
        self.assertFalse(seconds_route.fallback)

        seconds_route = segment_route_for_seconds(watch_tier="warm", time_to_close_seconds=-1)
        self.assertEqual(seconds_route.segment_id, SEGMENT_FALLBACK)
        self.assertTrue(seconds_route.fallback)
