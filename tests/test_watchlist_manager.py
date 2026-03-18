from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
from tracker import PolymarketTracker
from watchlist_manager import WatchlistManager, reactivate_wallet


class WatchlistManagerTest(unittest.TestCase):
    def test_watchlist_tiers_rank_wallets_by_copyability(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trader_cache (
                        trader_address, win_rate, n_trades, consistency, volume_usd, avg_size_usd,
                        diversity, account_age_d, wins, ties, realized_pnl_usd, avg_return,
                        open_positions, open_value_usd, open_pnl_usd, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        (
                            "0xhot",
                            0.72,
                            60,
                            0.8,
                            5_000.0,
                            40.0,
                            25,
                            120,
                            40,
                            2,
                            2_500.0,
                            0.14,
                            2,
                            120.0,
                            12.0,
                            1_700_000_000,
                        ),
                        (
                            "0xwarm",
                            0.58,
                            25,
                            0.2,
                            1_500.0,
                            30.0,
                            12,
                            90,
                            13,
                            1,
                            250.0,
                            0.03,
                            1,
                            40.0,
                            3.0,
                            1_700_000_000,
                        ),
                        (
                            "0xdisc",
                            0.49,
                            6,
                            -0.1,
                            300.0,
                            15.0,
                            4,
                            30,
                            3,
                            0,
                            -50.0,
                            -0.06,
                            0,
                            0.0,
                            0.0,
                            1_700_000_000,
                        ),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_cursors (wallet_address, last_source_ts, last_trade_ids_json, updated_at)
                    VALUES (?,?,?,?)
                    """,
                    (
                        ("0xhot", 1_700_000_100, "[]", 1_700_000_100),
                        ("0xwarm", 1_699_950_000, "[]", 1_699_950_000),
                        ("0xdisc", 1_699_000_000, "[]", 1_699_000_000),
                    ),
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=1
                ), patch("watchlist_manager.time.time", return_value=1_700_000_200):
                    manager = WatchlistManager(["0xhot", "0xwarm", "0xdisc"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xhot",))
                self.assertEqual(snapshot.warm, ("0xwarm",))
                self.assertEqual(snapshot.discovery, ("0xdisc",))
            finally:
                db.DB_PATH = original_db_path

    def test_watchlist_polling_cadence_splits_hot_warm_and_discovery(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trader_cache (
                        trader_address, win_rate, n_trades, consistency, volume_usd, avg_size_usd,
                        diversity, account_age_d, wins, ties, realized_pnl_usd, avg_return,
                        open_positions, open_value_usd, open_pnl_usd, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ("0xhot", 0.70, 50, 0.5, 0.0, 20.0, 10, 10, 35, 0, 500.0, 0.08, 1, 0.0, 0.0, 1_700_000_000),
                        ("0xwarm", 0.60, 20, 0.2, 0.0, 20.0, 10, 10, 10, 0, 100.0, 0.02, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xdisc", 0.50, 10, 0.0, 0.0, 20.0, 10, 10, 5, 0, 0.0, 0.0, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=1
                ), patch("watchlist_manager.warm_poll_interval_multiplier", return_value=2), patch(
                    "watchlist_manager.discovery_poll_interval_multiplier", return_value=3
                ), patch("watchlist_manager.time.time", return_value=1_700_000_200):
                    manager = WatchlistManager(["0xhot", "0xwarm", "0xdisc"])
                    self.assertEqual(manager.wallets_for_poll(), ["0xhot"])
                    self.assertEqual(manager.wallets_for_poll(), ["0xhot", "0xwarm"])
                    self.assertEqual(manager.wallets_for_poll(), ["0xhot", "0xdisc"])
            finally:
                db.DB_PATH = original_db_path

    def test_tracker_poll_empty_subset_does_not_fall_back_to_full_watchlist(self) -> None:
        tracker = PolymarketTracker(["0xhot", "0xwarm"])
        calls: list[str] = []
        tracker.get_wallet_trades = lambda address: calls.append(address) or []
        try:
            events = tracker.poll([])
        finally:
            tracker.close()

        self.assertEqual(events, [])
        self.assertEqual(calls, [])

    def test_inactive_wallets_are_auto_dropped_until_reactivated(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trader_cache (
                        trader_address, win_rate, n_trades, consistency, volume_usd, avg_size_usd,
                        diversity, account_age_d, wins, ties, realized_pnl_usd, avg_return,
                        open_positions, open_value_usd, open_pnl_usd, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ("0xactive", 0.70, 40, 0.4, 0.0, 20.0, 10, 10, 28, 0, 500.0, 0.08, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xstale", 0.65, 40, 0.4, 0.0, 20.0, 10, 10, 26, 0, 300.0, 0.05, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_cursors (wallet_address, last_source_ts, last_trade_ids_json, updated_at)
                    VALUES (?,?,?,?)
                    """,
                    (
                        ("0xactive", 1_700_000_000, "[]", 1_700_000_000),
                        ("0xstale", 1_699_980_000, "[]", 1_699_980_000),
                    ),
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=2), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=3600.0), patch(
                    "watchlist_manager.time.time", return_value=1_700_000_200
                ):
                    manager = WatchlistManager(["0xactive", "0xstale"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xactive",))
                self.assertEqual(snapshot.dropped, ("0xstale",))
                self.assertEqual(manager.active_wallets(), ["0xactive"])

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT status, status_reason FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xstale",),
                ).fetchone()
                conn.close()
                self.assertEqual(row["status"], "dropped")
                self.assertEqual(row["status_reason"], "inactive>1h")

                with patch("watchlist_manager.time.time", return_value=1_700_000_400):
                    self.assertTrue(reactivate_wallet("0xstale"))

                with patch("watchlist_manager.hot_wallet_count", return_value=2), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=3600.0), patch(
                    "watchlist_manager.time.time", return_value=1_700_000_500
                ):
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.dropped, ())
                self.assertEqual(snapshot.hot, ("0xactive", "0xstale"))
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
