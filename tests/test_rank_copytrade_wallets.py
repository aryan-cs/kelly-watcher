from __future__ import annotations

import unittest

from rank_copytrade_wallets import (
    LeaderboardEntry,
    PerformanceMetrics,
    TradeTimingMetrics,
    build_ranked_wallet,
)


class RankCopytradeWalletsTest(unittest.TestCase):
    def test_build_ranked_wallet_accepts_active_profitable_wallet_with_good_lead_times(self) -> None:
        now_ts = 2_000_000_000
        entry = LeaderboardEntry(
            address="0xabc",
            username="steady_trader",
            rank=4,
            pnl_usd=25_000.0,
            volume_usd=140_000.0,
            verified=False,
        )
        performance = PerformanceMetrics(
            closed_positions=80,
            wins=52,
            ties=3,
            shrunk_win_rate=0.64,
            realized_pnl_usd=18_500.0,
            total_bought_usd=120_000.0,
            roi=0.154,
            avg_return=0.08,
            consistency=0.62,
            avg_position_size_usd=1_500.0,
            account_age_days=180,
        )
        timing = TradeTimingMetrics(
            last_trade_ts=now_ts - 1800,
            recent_trade_count=18,
            recent_buy_count=9,
            lead_sample_count=9,
            median_buy_lead_seconds=8 * 3600,
            p25_buy_lead_seconds=4 * 3600,
            late_buy_ratio=0.0,
        )

        ranked = build_ranked_wallet(
            entry,
            performance,
            timing,
            now_ts=now_ts,
            activity_window_days=3,
            min_closed_positions=25,
            min_recent_trades=10,
            min_recent_buys=5,
            min_lead_samples=5,
            min_median_lead_seconds=2 * 3600,
            max_late_buy_ratio=0.25,
            max_days_since_last_trade=7,
        )

        self.assertTrue(ranked.accepted)
        self.assertEqual(ranked.reject_reason, "")
        self.assertGreater(ranked.follow_score, 0.6)
        self.assertIn("medium-horizon", ranked.style)

    def test_build_ranked_wallet_rejects_late_entry_wallet_even_if_profitable(self) -> None:
        now_ts = 2_000_000_000
        entry = LeaderboardEntry(
            address="0xdef",
            username="too_late",
            rank=8,
            pnl_usd=40_000.0,
            volume_usd=210_000.0,
            verified=False,
        )
        performance = PerformanceMetrics(
            closed_positions=120,
            wins=90,
            ties=2,
            shrunk_win_rate=0.69,
            realized_pnl_usd=31_000.0,
            total_bought_usd=180_000.0,
            roi=0.172,
            avg_return=0.11,
            consistency=0.70,
            avg_position_size_usd=1_900.0,
            account_age_days=250,
        )
        timing = TradeTimingMetrics(
            last_trade_ts=now_ts - 900,
            recent_trade_count=22,
            recent_buy_count=12,
            lead_sample_count=12,
            median_buy_lead_seconds=15 * 60,
            p25_buy_lead_seconds=5 * 60,
            late_buy_ratio=0.75,
        )

        ranked = build_ranked_wallet(
            entry,
            performance,
            timing,
            now_ts=now_ts,
            activity_window_days=3,
            min_closed_positions=25,
            min_recent_trades=10,
            min_recent_buys=5,
            min_lead_samples=5,
            min_median_lead_seconds=2 * 3600,
            max_late_buy_ratio=0.25,
            max_days_since_last_trade=7,
        )

        self.assertFalse(ranked.accepted)
        self.assertIn("median_lead_too_short", ranked.reject_reason)
        self.assertIn("late_buy_ratio>25%", ranked.reject_reason)

    def test_build_ranked_wallet_prefers_more_active_wallet_when_quality_is_similar(self) -> None:
        now_ts = 2_000_000_000
        entry = LeaderboardEntry(
            address="0xflow",
            username="flow_trader",
            rank=5,
            pnl_usd=60_000.0,
            volume_usd=320_000.0,
            verified=False,
        )
        performance = PerformanceMetrics(
            closed_positions=70,
            wins=45,
            ties=4,
            shrunk_win_rate=0.63,
            realized_pnl_usd=24_000.0,
            total_bought_usd=155_000.0,
            roi=0.155,
            avg_return=0.09,
            consistency=0.60,
            avg_position_size_usd=1_700.0,
            account_age_days=210,
        )
        hyperactive = TradeTimingMetrics(
            last_trade_ts=now_ts - 900,
            recent_trade_count=24,
            recent_buy_count=12,
            lead_sample_count=10,
            median_buy_lead_seconds=7 * 3600,
            p25_buy_lead_seconds=3 * 3600,
            late_buy_ratio=0.05,
        )
        sleepy = TradeTimingMetrics(
            last_trade_ts=now_ts - (36 * 3600),
            recent_trade_count=6,
            recent_buy_count=3,
            lead_sample_count=10,
            median_buy_lead_seconds=7 * 3600,
            p25_buy_lead_seconds=3 * 3600,
            late_buy_ratio=0.05,
        )

        active_ranked = build_ranked_wallet(
            entry,
            performance,
            hyperactive,
            now_ts=now_ts,
            activity_window_days=3,
            min_closed_positions=25,
            min_recent_trades=5,
            min_recent_buys=2,
            min_lead_samples=5,
            min_median_lead_seconds=2 * 3600,
            max_late_buy_ratio=0.25,
            max_days_since_last_trade=7,
        )
        sleepy_ranked = build_ranked_wallet(
            entry,
            performance,
            sleepy,
            now_ts=now_ts,
            activity_window_days=3,
            min_closed_positions=25,
            min_recent_trades=5,
            min_recent_buys=2,
            min_lead_samples=5,
            min_median_lead_seconds=2 * 3600,
            max_late_buy_ratio=0.25,
            max_days_since_last_trade=7,
        )

        self.assertGreater(active_ranked.follow_score, sleepy_ranked.follow_score)
        self.assertIn("hyperactive", active_ranked.style)


if __name__ == "__main__":
    unittest.main()
