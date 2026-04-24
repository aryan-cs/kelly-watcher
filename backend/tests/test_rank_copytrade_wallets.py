from __future__ import annotations

import unittest

from kelly_watcher.tools.rank_copytrade_wallets import (
    LeaderboardEntry,
    LocalCopyMetrics,
    PerformanceMetrics,
    TradeTimingMetrics,
    build_ranked_wallet,
)


class RankCopytradeWalletsTest(unittest.TestCase):
    def rank_wallet(
        self,
        entry: LeaderboardEntry,
        performance: PerformanceMetrics,
        timing: TradeTimingMetrics,
        *,
        now_ts: int,
        **overrides,
    ):
        params = {
            "now_ts": now_ts,
            "activity_window_days": 3,
            "min_closed_positions": 25,
            "min_recent_trades": 5,
            "min_recent_buys": 2,
            "min_lead_samples": 5,
            "min_median_lead_seconds": 60 * 60,
            "max_median_lead_seconds": 6 * 60 * 60,
            "min_p25_lead_seconds": 20 * 60,
            "max_late_buy_ratio": 0.20,
            "max_days_since_last_trade": 7,
            "min_avg_buy_size_usd": 75.0,
            "min_large_buy_count": 2,
            "min_conviction_buy_ratio": 0.25,
            "large_buy_threshold_usd": 100.0,
            "local_copy_metrics": None,
            "min_local_resolved_copies": 3,
            "min_local_copy_avg_return": 0.0,
        }
        params.update(overrides)
        return build_ranked_wallet(entry, performance, timing, **params)

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
            recent_buy_volume_usd=2_160.0,
            avg_recent_buy_size_usd=240.0,
            large_buy_count=6,
            large_buy_ratio=0.667,
            conviction_buy_count=5,
            conviction_buy_ratio=0.556,
            lead_sample_count=9,
            median_buy_lead_seconds=4 * 3600,
            p25_buy_lead_seconds=2 * 3600,
            late_buy_ratio=0.0,
        )

        ranked = self.rank_wallet(
            entry,
            performance,
            timing,
            now_ts=now_ts,
            min_recent_trades=10,
            min_recent_buys=5,
        )

        self.assertTrue(ranked.accepted)
        self.assertEqual(ranked.reject_reason, "")
        self.assertGreater(ranked.follow_score, 0.6)
        self.assertIn("short-horizon", ranked.style)

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
            recent_buy_volume_usd=2_400.0,
            avg_recent_buy_size_usd=200.0,
            large_buy_count=6,
            large_buy_ratio=0.5,
            conviction_buy_count=2,
            conviction_buy_ratio=0.167,
            lead_sample_count=12,
            median_buy_lead_seconds=15 * 60,
            p25_buy_lead_seconds=5 * 60,
            late_buy_ratio=0.75,
        )

        ranked = self.rank_wallet(
            entry,
            performance,
            timing,
            now_ts=now_ts,
            min_recent_trades=10,
            min_recent_buys=5,
        )

        self.assertFalse(ranked.accepted)
        self.assertIn("median_lead_too_short", ranked.reject_reason)
        self.assertIn("p25_lead_too_short", ranked.reject_reason)
        self.assertIn("late_buy_ratio>20%", ranked.reject_reason)

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
            recent_buy_volume_usd=2_400.0,
            avg_recent_buy_size_usd=200.0,
            large_buy_count=6,
            large_buy_ratio=0.5,
            conviction_buy_count=5,
            conviction_buy_ratio=0.417,
            lead_sample_count=10,
            median_buy_lead_seconds=4 * 3600,
            p25_buy_lead_seconds=2 * 3600,
            late_buy_ratio=0.05,
        )
        sleepy = TradeTimingMetrics(
            last_trade_ts=now_ts - (36 * 3600),
            recent_trade_count=6,
            recent_buy_count=3,
            recent_buy_volume_usd=450.0,
            avg_recent_buy_size_usd=150.0,
            large_buy_count=2,
            large_buy_ratio=0.667,
            conviction_buy_count=2,
            conviction_buy_ratio=0.667,
            lead_sample_count=10,
            median_buy_lead_seconds=4 * 3600,
            p25_buy_lead_seconds=2 * 3600,
            late_buy_ratio=0.05,
        )

        active_ranked = self.rank_wallet(
            entry,
            performance,
            hyperactive,
            now_ts=now_ts,
        )
        sleepy_ranked = self.rank_wallet(
            entry,
            performance,
            sleepy,
            now_ts=now_ts,
        )

        self.assertGreater(active_ranked.follow_score, sleepy_ranked.follow_score)
        self.assertIn("hyperactive", active_ranked.style)

    def test_build_ranked_wallet_prefers_bigger_more_conviction_buys_when_quality_is_similar(self) -> None:
        now_ts = 2_000_000_000
        entry = LeaderboardEntry(
            address="0xconv",
            username="conviction_trader",
            rank=7,
            pnl_usd=42_000.0,
            volume_usd=250_000.0,
            verified=False,
        )
        performance = PerformanceMetrics(
            closed_positions=60,
            wins=37,
            ties=3,
            shrunk_win_rate=0.62,
            realized_pnl_usd=15_000.0,
            total_bought_usd=95_000.0,
            roi=0.158,
            avg_return=0.09,
            consistency=0.58,
            avg_position_size_usd=1_300.0,
            account_age_days=150,
        )
        high_copyability = TradeTimingMetrics(
            last_trade_ts=now_ts - 1200,
            recent_trade_count=16,
            recent_buy_count=8,
            recent_buy_volume_usd=2_000.0,
            avg_recent_buy_size_usd=250.0,
            large_buy_count=5,
            large_buy_ratio=0.625,
            conviction_buy_count=5,
            conviction_buy_ratio=0.625,
            lead_sample_count=8,
            median_buy_lead_seconds=4 * 3600,
            p25_buy_lead_seconds=2 * 3600,
            late_buy_ratio=0.05,
        )
        low_copyability = TradeTimingMetrics(
            last_trade_ts=now_ts - 1200,
            recent_trade_count=16,
            recent_buy_count=8,
            recent_buy_volume_usd=320.0,
            avg_recent_buy_size_usd=40.0,
            large_buy_count=0,
            large_buy_ratio=0.0,
            conviction_buy_count=1,
            conviction_buy_ratio=0.125,
            lead_sample_count=8,
            median_buy_lead_seconds=4 * 3600,
            p25_buy_lead_seconds=2 * 3600,
            late_buy_ratio=0.05,
        )

        strong_ranked = self.rank_wallet(
            entry,
            performance,
            high_copyability,
            now_ts=now_ts,
        )
        weak_ranked = self.rank_wallet(
            entry,
            performance,
            low_copyability,
            now_ts=now_ts,
        )

        self.assertTrue(strong_ranked.accepted)
        self.assertFalse(weak_ranked.accepted)
        self.assertGreater(strong_ranked.copyability_score, weak_ranked.copyability_score)
        self.assertGreater(strong_ranked.follow_score, weak_ranked.follow_score)

    def test_build_ranked_wallet_rejects_wallet_with_bad_local_copy_feedback(self) -> None:
        now_ts = 2_000_000_000
        entry = LeaderboardEntry(
            address="0xlocal",
            username="burned_us",
            rank=9,
            pnl_usd=55_000.0,
            volume_usd=210_000.0,
            verified=False,
        )
        performance = PerformanceMetrics(
            closed_positions=90,
            wins=58,
            ties=4,
            shrunk_win_rate=0.65,
            realized_pnl_usd=22_000.0,
            total_bought_usd=140_000.0,
            roi=0.157,
            avg_return=0.09,
            consistency=0.61,
            avg_position_size_usd=1_450.0,
            account_age_days=220,
        )
        timing = TradeTimingMetrics(
            last_trade_ts=now_ts - 900,
            recent_trade_count=18,
            recent_buy_count=8,
            recent_buy_volume_usd=2_000.0,
            avg_recent_buy_size_usd=250.0,
            large_buy_count=4,
            large_buy_ratio=0.5,
            conviction_buy_count=3,
            conviction_buy_ratio=0.375,
            lead_sample_count=8,
            median_buy_lead_seconds=3 * 3600,
            p25_buy_lead_seconds=90 * 60,
            late_buy_ratio=0.0,
        )

        ranked = self.rank_wallet(
            entry,
            performance,
            timing,
            now_ts=now_ts,
            local_copy_metrics=LocalCopyMetrics(
                resolved_copied_count=4,
                copied_win_rate=0.25,
                copied_avg_return=-0.18,
                copied_pnl_usd=-1.72,
            ),
        )

        self.assertFalse(ranked.accepted)
        self.assertIn("local_copy_avg_return<0%", ranked.reject_reason)


if __name__ == "__main__":
    unittest.main()
