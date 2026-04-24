from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
from tracker import PolymarketTracker
from watchlist_manager import (
    DISCOVERY_WALLET_TRADE_FETCH_LIMIT,
    HOT_WALLET_TRADE_FETCH_LIMIT,
    WARM_WALLET_TRADE_FETCH_LIMIT,
    WatchlistManager,
    reactivate_wallet,
)


def insert_logged_trade(
    conn,
    trader_address: str,
    placed_at: int,
    *,
    trade_id: str | None = None,
    skipped: bool = False,
    skip_reason: str | None = None,
    market_veto: str | None = None,
    actual_entry_price: float | None = None,
    actual_entry_shares: float | None = None,
    actual_entry_size_usd: float | None = None,
    shadow_pnl_usd: float | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id,
            market_id,
            trader_address,
            side,
            source_action,
            price_at_signal,
            signal_size_usd,
            confidence,
            kelly_fraction,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            shadow_pnl_usd,
            market_veto,
            skipped,
            skip_reason,
            placed_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_id or f"{trader_address}:{placed_at}",
            f"market:{trader_address}",
            trader_address,
            "yes",
            "buy",
            0.55,
            1.0,
            0.60,
            0.01,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            shadow_pnl_usd,
            market_veto,
            1 if skipped else 0,
            skip_reason,
            placed_at,
        ),
    )


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
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.time.time", return_value=1_700_000_200
                ):
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
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.time.time", return_value=1_700_000_200
                ):
                    manager = WatchlistManager(["0xhot", "0xwarm", "0xdisc"])
                    self.assertEqual(manager.wallets_for_poll(), ["0xhot"])
                    self.assertEqual(manager.wallets_for_poll(), ["0xhot", "0xwarm"])
                    self.assertEqual(manager.wallets_for_poll(), ["0xhot", "0xdisc"])
            finally:
                db.DB_PATH = original_db_path

    def test_uncopyable_skip_penalty_pushes_veto_heavy_wallet_below_similar_peer(self) -> None:
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
                        ("0xcopyable", 0.64, 45, 0.3, 1_500.0, 40.0, 10, 10, 29, 0, 300.0, 0.04, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xuncopyable", 0.64, 45, 0.3, 1_500.0, 40.0, 10, 10, 29, 0, 300.0, 0.04, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                for index in range(18):
                    insert_logged_trade(conn, "0xcopyable", 1_700_000_000 + index)
                for index in range(18):
                    insert_logged_trade(
                        conn,
                        "0xuncopyable",
                        1_700_000_000 + index,
                        skipped=True,
                        market_veto="beyond max horizon 6h",
                        skip_reason="market resolves too far out, beyond the 6h maximum horizon",
                    )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=1
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch("watchlist_manager.wallet_performance_drop_min_trades", return_value=999), patch(
                    "watchlist_manager.wallet_uncopyable_drop_min_buys", return_value=999
                ), patch("watchlist_manager.wallet_uncopyable_penalty_min_buys", return_value=12), patch(
                    "watchlist_manager.wallet_uncopyable_penalty_weight", return_value=0.25
                ), patch("watchlist_manager.time.time", return_value=1_700_000_200):
                    manager = WatchlistManager(["0xcopyable", "0xuncopyable"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xcopyable",))
                self.assertEqual(snapshot.warm, ("0xuncopyable",))
            finally:
                db.DB_PATH = original_db_path

    def test_local_copied_performance_can_outrank_stronger_public_profile(self) -> None:
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
                        ("0xflashy", 0.72, 60, 0.5, 4_000.0, 50.0, 15, 100, 43, 1, 1_200.0, 0.08, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xsteady", 0.60, 38, 0.3, 1_200.0, 30.0, 10, 80, 23, 1, 150.0, 0.02, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                for index in range(14):
                    insert_logged_trade(
                        conn,
                        "0xflashy",
                        1_700_000_000 + index,
                        actual_entry_price=0.50,
                        actual_entry_shares=2.0,
                        actual_entry_size_usd=1.0,
                        shadow_pnl_usd=-0.20,
                    )
                for index in range(12):
                    insert_logged_trade(
                        conn,
                        "0xsteady",
                        1_700_000_100 + index,
                        actual_entry_price=0.50,
                        actual_entry_shares=2.0,
                        actual_entry_size_usd=1.0,
                        shadow_pnl_usd=0.10,
                    )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=1
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch("watchlist_manager.wallet_performance_drop_min_trades", return_value=999), patch(
                    "watchlist_manager.wallet_uncopyable_drop_min_buys", return_value=999
                ), patch("watchlist_manager.wallet_local_drop_min_resolved_copied_buys", return_value=999
                ), patch("watchlist_manager.time.time", return_value=1_700_000_500):
                    manager = WatchlistManager(["0xflashy", "0xsteady"])
                    snapshot = manager.refresh(run_auto_drop=False)

                self.assertEqual(snapshot.hot, ("0xsteady",))
                self.assertEqual(snapshot.warm, ("0xflashy",))
            finally:
                db.DB_PATH = original_db_path

    def test_tracker_poll_empty_subset_does_not_fall_back_to_full_watchlist(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                tracker = PolymarketTracker(["0xhot", "0xwarm"])
                calls: list[str] = []
                tracker.get_wallet_trades = lambda address: calls.append(address) or []
                try:
                    events = tracker.poll([])
                finally:
                    tracker.close()

                self.assertEqual(events, [])
                self.assertEqual(calls, [])
            finally:
                db.DB_PATH = original_db_path

    def test_poll_batches_apply_tier_specific_trade_limits(self) -> None:
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
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.time.time", return_value=1_700_000_200
                ):
                    manager = WatchlistManager(["0xhot", "0xwarm", "0xdisc"])

                    first = manager.poll_batches()
                    second = manager.poll_batches()
                    third = manager.poll_batches()

                self.assertEqual([(batch.wallets, batch.trade_limit) for batch in first], [(("0xhot",), HOT_WALLET_TRADE_FETCH_LIMIT)])
                self.assertEqual(
                    [(batch.wallets, batch.trade_limit) for batch in second],
                    [
                        (("0xhot",), HOT_WALLET_TRADE_FETCH_LIMIT),
                        (("0xwarm",), WARM_WALLET_TRADE_FETCH_LIMIT),
                    ],
                )
                self.assertEqual(
                    [(batch.wallets, batch.trade_limit) for batch in third],
                    [
                        (("0xhot",), HOT_WALLET_TRADE_FETCH_LIMIT),
                        (("0xdisc",), DISCOVERY_WALLET_TRADE_FETCH_LIMIT),
                    ],
                )
            finally:
                db.DB_PATH = original_db_path

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
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    (
                        ("0xactive", 1_699_970_000, 1_699_970_000),
                        ("0xstale", 1_699_970_000, 1_699_970_000),
                    ),
                )
                insert_logged_trade(conn, "0xactive", 1_700_000_000)
                insert_logged_trade(conn, "0xstale", 1_699_980_000)
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

    def test_never_seen_wallets_can_drop_after_tracking_window(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trader_cache (
                        trader_address, win_rate, n_trades, consistency, volume_usd, avg_size_usd,
                        diversity, account_age_d, wins, ties, realized_pnl_usd, avg_return,
                        open_positions, open_value_usd, open_pnl_usd, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("0xquiet", 0.55, 12, 0.1, 0.0, 20.0, 6, 10, 7, 0, 25.0, 0.01, 0, 0.0, 0.0, 1_700_000_000),
                )
                conn.execute(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    ("0xquiet", 1_699_990_000, 1_699_990_000),
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=3600.0), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch("watchlist_manager.time.time", return_value=1_700_000_200):
                    manager = WatchlistManager(["0xquiet"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ())
                self.assertEqual(snapshot.dropped, ("0xquiet",))

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT status_reason FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xquiet",),
                ).fetchone()
                conn.close()
                self.assertEqual(row["status_reason"], "inactive>1h")
            finally:
                db.DB_PATH = original_db_path

    def test_positive_best_wallet_is_protected_from_auto_drop(self) -> None:
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
                        ("0xbest", 0.20, 80, -0.5, 0.0, 20.0, 10, 10, 16, 0, -250.0, -0.08, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xweak", 0.18, 80, -0.5, 0.0, 20.0, 10, 10, 14, 0, -300.0, -0.09, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    (
                        ("0xbest", 1_699_970_000, 1_699_970_000),
                        ("0xweak", 1_699_970_000, 1_699_970_000),
                    ),
                )
                insert_logged_trade(
                    conn,
                    "0xbest",
                    1_699_990_000,
                    actual_entry_price=0.55,
                    actual_entry_shares=1.818,
                    actual_entry_size_usd=1.0,
                    shadow_pnl_usd=2.50,
                )
                insert_logged_trade(conn, "0xweak", 1_699_990_000)
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=2), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=3600.0), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch("watchlist_manager.wallet_performance_drop_min_trades", return_value=40), patch(
                    "watchlist_manager.wallet_performance_drop_max_win_rate", return_value=0.40
                ), patch("watchlist_manager.wallet_performance_drop_max_avg_return", return_value=-0.03), patch(
                    "watchlist_manager.wallet_uncopyable_drop_min_buys", return_value=999
                ), patch("watchlist_manager.time.time", return_value=1_700_000_200):
                    manager = WatchlistManager(["0xbest", "0xweak"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xbest",))
                self.assertEqual(snapshot.dropped, ("0xweak",))

                conn = db.get_conn()
                best_row = conn.execute(
                    "SELECT status FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xbest",),
                ).fetchone()
                weak_row = conn.execute(
                    "SELECT status, status_reason FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xweak",),
                ).fetchone()
                conn.close()

                self.assertEqual(best_row["status"], "active")
                self.assertEqual(weak_row["status"], "dropped")
            finally:
                db.DB_PATH = original_db_path

    def test_recent_raw_cursor_activity_prevents_inactivity_drop_without_logged_trades(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trader_cache (
                        trader_address, win_rate, n_trades, consistency, volume_usd, avg_size_usd,
                        diversity, account_age_d, wins, ties, realized_pnl_usd, avg_return,
                        open_positions, open_value_usd, open_pnl_usd, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("0xcursoronly", 0.55, 20, 0.1, 0.0, 20.0, 6, 10, 10, 0, 25.0, 0.01, 0, 0.0, 0.0, 1_700_000_000),
                )
                conn.execute(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    ("0xcursoronly", 1_699_990_000, 1_699_990_000),
                )
                conn.execute(
                    """
                    INSERT INTO wallet_cursors (wallet_address, last_source_ts, last_trade_ids_json, updated_at)
                    VALUES (?,?,?,?)
                    """,
                    ("0xcursoronly", 1_700_000_150, "[]", 1_700_000_150),
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=3600.0), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch("watchlist_manager.time.time", return_value=1_700_000_200):
                    manager = WatchlistManager(["0xcursoronly"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xcursoronly",))
                self.assertEqual(snapshot.dropped, ())
            finally:
                db.DB_PATH = original_db_path

    def test_profitable_local_wallet_is_protected_from_inactivity_drop(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trader_cache (
                        trader_address, win_rate, n_trades, consistency, volume_usd, avg_size_usd,
                        diversity, account_age_d, wins, ties, realized_pnl_usd, avg_return,
                        open_positions, open_value_usd, open_pnl_usd, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("0xprofit", 0.55, 30, 0.1, 0.0, 20.0, 6, 10, 16, 0, 50.0, 0.01, 0, 0.0, 0.0, 1_700_000_000),
                )
                conn.execute(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    ("0xprofit", 1_699_990_000, 1_699_990_000),
                )
                insert_logged_trade(
                    conn,
                    "0xprofit",
                    1_699_990_000,
                    actual_entry_price=0.50,
                    actual_entry_shares=2.0,
                    actual_entry_size_usd=1.0,
                    shadow_pnl_usd=0.75,
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager._protected_best_wallets", return_value=set()), patch(
                    "watchlist_manager.wallet_inactivity_limit_seconds", return_value=3600.0
                ), patch("watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.time.time", return_value=1_700_000_200
                ):
                    manager = WatchlistManager(["0xprofit"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xprofit",))
                self.assertEqual(snapshot.dropped, ())
            finally:
                db.DB_PATH = original_db_path

    def test_underperforming_wallets_are_auto_dropped_after_minimum_sample(self) -> None:
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
                        ("0xgood", 0.61, 55, 0.2, 0.0, 20.0, 10, 10, 34, 0, 300.0, 0.03, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xbad", 0.34, 60, -0.2, 0.0, 20.0, 10, 10, 20, 0, -250.0, -0.08, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    (
                        ("0xgood", 1_700_000_000, 1_700_000_000),
                        ("0xbad", 1_700_000_050, 1_700_000_050),
                    ),
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=2), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch(
                    "watchlist_manager.wallet_performance_drop_min_trades", return_value=40
                ), patch("watchlist_manager.wallet_performance_drop_max_win_rate", return_value=0.40), patch(
                    "watchlist_manager.wallet_performance_drop_max_avg_return", return_value=-0.03
                ), patch("watchlist_manager.time.time", return_value=1_700_000_200):
                    manager = WatchlistManager(["0xgood", "0xbad"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xgood",))
                self.assertEqual(snapshot.dropped, ("0xbad",))

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT status, status_reason FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xbad",),
                ).fetchone()
                conn.close()
                self.assertEqual(row["status"], "dropped")
                self.assertIn("poor_perf", row["status_reason"])

                with patch("watchlist_manager.time.time", return_value=1_700_000_300):
                    self.assertTrue(reactivate_wallet("0xbad"))

                with patch("watchlist_manager.hot_wallet_count", return_value=2), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch(
                    "watchlist_manager.wallet_performance_drop_min_trades", return_value=40
                ), patch("watchlist_manager.wallet_performance_drop_max_win_rate", return_value=0.40), patch(
                    "watchlist_manager.wallet_performance_drop_max_avg_return", return_value=-0.03
                ), patch("watchlist_manager.time.time", return_value=1_700_000_320):
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.dropped, ())
                self.assertEqual(snapshot.hot, ("0xgood", "0xbad"))

                conn = db.get_conn()
                insert_logged_trade(conn, "0xbad", 1_700_000_450)
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=2), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch(
                    "watchlist_manager.wallet_performance_drop_min_trades", return_value=40
                ), patch("watchlist_manager.wallet_performance_drop_max_win_rate", return_value=0.40), patch(
                    "watchlist_manager.wallet_performance_drop_max_avg_return", return_value=-0.03
                ), patch("watchlist_manager.time.time", return_value=1_700_000_500):
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.dropped, ("0xbad",))
            finally:
                db.DB_PATH = original_db_path

    def test_local_underperforming_wallets_are_auto_dropped_after_bad_copied_results(self) -> None:
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
                        ("0xgood", 0.62, 42, 0.2, 0.0, 20.0, 10, 10, 26, 0, 250.0, 0.03, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xloser", 0.66, 48, 0.3, 0.0, 20.0, 10, 10, 31, 0, 500.0, 0.04, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    (
                        ("0xgood", 1_700_000_000, 1_700_000_000),
                        ("0xloser", 1_700_000_000, 1_700_000_000),
                    ),
                )
                for index in range(8):
                    insert_logged_trade(
                        conn,
                        "0xgood",
                        1_700_000_100 + index,
                        actual_entry_price=0.50,
                        actual_entry_shares=2.0,
                        actual_entry_size_usd=1.0,
                        shadow_pnl_usd=0.08,
                    )
                for index in range(6):
                    insert_logged_trade(
                        conn,
                        "0xloser",
                        1_700_000_200 + index,
                        actual_entry_price=0.50,
                        actual_entry_shares=2.0,
                        actual_entry_size_usd=1.0,
                        shadow_pnl_usd=-0.20,
                    )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=2), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch("watchlist_manager.wallet_performance_drop_min_trades", return_value=999), patch(
                    "watchlist_manager.wallet_uncopyable_drop_min_buys", return_value=999
                ), patch("watchlist_manager.wallet_local_drop_min_resolved_copied_buys", return_value=10), patch(
                    "watchlist_manager.wallet_local_drop_max_avg_return", return_value=-0.08
                ), patch("watchlist_manager.wallet_local_drop_max_total_pnl_usd", return_value=0.0), patch(
                    "watchlist_manager.time.time", return_value=1_700_000_500
                ):
                    manager = WatchlistManager(["0xgood", "0xloser"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xgood",))
                self.assertEqual(snapshot.dropped, ("0xloser",))

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT status, status_reason FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xloser",),
                ).fetchone()
                metrics_row = conn.execute(
                    """
                    SELECT recent_resolved_copied_count, recent_resolved_copied_total_pnl_usd, local_drop_ready
                    FROM wallet_policy_metrics
                    WHERE wallet_address=?
                    """,
                    ("0xloser",),
                ).fetchone()
                conn.close()

                self.assertEqual(row["status"], "dropped")
                self.assertIn("local_recent", row["status_reason"])
                self.assertEqual(int(metrics_row["recent_resolved_copied_count"] or 0), 6)
                self.assertEqual(int(metrics_row["local_drop_ready"] or 0), 1)
                self.assertAlmostEqual(float(metrics_row["recent_resolved_copied_total_pnl_usd"] or 0.0), -1.2, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_uncopyable_wallets_are_auto_dropped_after_large_structural_skip_sample(self) -> None:
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
                        ("0xusable", 0.60, 40, 0.2, 0.0, 20.0, 10, 10, 24, 0, 200.0, 0.03, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xbot", 0.67, 55, 0.2, 0.0, 20.0, 10, 10, 36, 0, 600.0, 0.05, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    (
                        ("0xusable", 1_700_000_000, 1_700_000_000),
                        ("0xbot", 1_700_000_000, 1_700_000_000),
                    ),
                )
                for index in range(12):
                    insert_logged_trade(
                        conn,
                        "0xusable",
                        1_700_000_000 + index,
                        actual_entry_price=0.55,
                        actual_entry_shares=1.818,
                        actual_entry_size_usd=1.0,
                        shadow_pnl_usd=0.10,
                    )
                for index in range(24):
                    insert_logged_trade(
                        conn,
                        "0xbot",
                        1_700_000_000 + index,
                        skipped=True,
                        market_veto="beyond max horizon 6h",
                        skip_reason="market resolves too far out, beyond the 6h maximum horizon",
                    )
                for index in range(2):
                    insert_logged_trade(
                        conn,
                        "0xbot",
                        1_700_000_100 + index,
                        actual_entry_price=0.50,
                        actual_entry_shares=2.0,
                        actual_entry_size_usd=1.0,
                        shadow_pnl_usd=-0.05,
                    )
                for index in range(5):
                    insert_logged_trade(
                        conn,
                        f"0xchamp{index}",
                        1_700_000_050 + index,
                        actual_entry_price=0.50,
                        actual_entry_shares=2.0,
                        actual_entry_size_usd=1.0,
                        shadow_pnl_usd=1.0 + index,
                    )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=2), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch("watchlist_manager.wallet_performance_drop_min_trades", return_value=999), patch(
                    "watchlist_manager.wallet_uncopyable_drop_min_buys", return_value=20
                ), patch("watchlist_manager.wallet_uncopyable_drop_max_skip_rate", return_value=0.75), patch(
                    "watchlist_manager.wallet_uncopyable_drop_max_resolved_copied", return_value=3
                ), patch("watchlist_manager.time.time", return_value=1_700_000_400):
                    manager = WatchlistManager(["0xusable", "0xbot"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xusable",))
                self.assertEqual(snapshot.dropped, ("0xbot",))

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT status_reason FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xbot",),
                ).fetchone()
                conn.close()
                self.assertIn("uncopyable", row["status_reason"])
            finally:
                db.DB_PATH = original_db_path

    def test_profitable_high_skip_wallet_is_not_auto_dropped_for_uncopyable_skip_rate(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                tracked_wallets = ["0xbot", "0xchamp0", "0xchamp1", "0xchamp2", "0xchamp3", "0xchamp4"]
                conn.executemany(
                    """
                    INSERT INTO trader_cache (
                        trader_address, win_rate, n_trades, consistency, volume_usd, avg_size_usd,
                        diversity, account_age_d, wins, ties, realized_pnl_usd, avg_return,
                        open_positions, open_value_usd, open_pnl_usd, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ("0xbot", 0.67, 55, 0.2, 0.0, 20.0, 10, 10, 36, 0, 600.0, 0.05, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xchamp0", 0.70, 60, 0.4, 0.0, 20.0, 10, 10, 40, 0, 900.0, 0.08, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xchamp1", 0.69, 58, 0.4, 0.0, 20.0, 10, 10, 39, 0, 850.0, 0.08, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xchamp2", 0.68, 56, 0.4, 0.0, 20.0, 10, 10, 38, 0, 800.0, 0.07, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xchamp3", 0.67, 54, 0.4, 0.0, 20.0, 10, 10, 37, 0, 750.0, 0.07, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xchamp4", 0.66, 52, 0.4, 0.0, 20.0, 10, 10, 36, 0, 700.0, 0.06, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    tuple((wallet, 1_700_000_000, 1_700_000_000) for wallet in tracked_wallets),
                )
                for index in range(24):
                    insert_logged_trade(
                        conn,
                        "0xbot",
                        1_700_000_000 + index,
                        skipped=True,
                        market_veto="beyond max horizon 6h",
                        skip_reason="market resolves too far out, beyond the 6h maximum horizon",
                    )
                for index in range(2):
                    insert_logged_trade(
                        conn,
                        "0xbot",
                        1_700_000_100 + index,
                        actual_entry_price=0.50,
                        actual_entry_shares=2.0,
                        actual_entry_size_usd=1.0,
                        shadow_pnl_usd=0.05,
                    )
                for index in range(5):
                    insert_logged_trade(
                        conn,
                        f"0xchamp{index}",
                        1_700_000_200 + index,
                        actual_entry_price=0.50,
                        actual_entry_shares=2.0,
                        actual_entry_size_usd=1.0,
                        shadow_pnl_usd=1.0 + index,
                    )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=6), patch(
                    "watchlist_manager.warm_wallet_count", return_value=0
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=float("inf")
                ), patch("watchlist_manager.wallet_performance_drop_min_trades", return_value=999), patch(
                    "watchlist_manager.wallet_uncopyable_drop_min_buys", return_value=20
                ), patch("watchlist_manager.wallet_uncopyable_drop_max_skip_rate", return_value=0.75), patch(
                    "watchlist_manager.wallet_uncopyable_drop_max_resolved_copied", return_value=3
                ), patch("watchlist_manager.time.time", return_value=1_700_000_400):
                    manager = WatchlistManager(tracked_wallets)
                    snapshot = manager.refresh()

                self.assertNotIn("0xbot", snapshot.dropped)

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT status, status_reason FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xbot",),
                ).fetchone()
                conn.close()
                self.assertEqual(row["status"], "active")
                self.assertIsNone(row["status_reason"])
            finally:
                db.DB_PATH = original_db_path

    def test_profitable_discovery_wallet_is_not_slow_dropped(self) -> None:
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
                        ("0xhot", 0.70, 50, 0.4, 0.0, 20.0, 10, 10, 35, 0, 500.0, 0.08, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xwarm", 0.60, 20, 0.2, 0.0, 20.0, 10, 10, 10, 0, 100.0, 0.02, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xprofit", 0.48, 8, -0.1, 0.0, 20.0, 10, 10, 4, 0, 10.0, -0.01, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    ("0xprofit", 1_699_990_000, 1_699_990_000),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_cursors (wallet_address, last_source_ts, last_trade_ids_json, updated_at)
                    VALUES (?,?,?,?)
                    """,
                    (
                        ("0xhot", 1_700_000_100, "[]", 1_700_000_100),
                        ("0xwarm", 1_699_999_000, "[]", 1_699_999_000),
                        ("0xprofit", 1_700_000_120, "[]", 1_700_000_120),
                    ),
                )
                for index in range(20):
                    insert_logged_trade(
                        conn,
                        "0xprofit",
                        1_700_000_000 + index,
                        skipped=True,
                        market_veto="beyond max horizon 6h",
                        skip_reason="market resolves too far out, beyond the 6h maximum horizon",
                    )
                insert_logged_trade(
                    conn,
                    "0xprofit",
                    1_700_000_150,
                    actual_entry_price=0.50,
                    actual_entry_shares=2.0,
                    actual_entry_size_usd=1.0,
                    shadow_pnl_usd=0.25,
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=1
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=3600.0
                ), patch("watchlist_manager.wallet_performance_drop_min_trades", return_value=999), patch(
                    "watchlist_manager.wallet_uncopyable_drop_min_buys", return_value=999
                ), patch("watchlist_manager.time.time", return_value=1_700_000_200):
                    manager = WatchlistManager(["0xhot", "0xwarm", "0xprofit"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xhot",))
                self.assertEqual(snapshot.warm, ("0xwarm",))
                self.assertEqual(snapshot.discovery, ("0xprofit",))
                self.assertEqual(snapshot.dropped, ())

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT status, status_reason FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xprofit",),
                ).fetchone()
                conn.close()
                self.assertEqual(row["status"], "active")
                self.assertIsNone(row["status_reason"])
            finally:
                db.DB_PATH = original_db_path

    def test_slow_wallets_are_auto_dropped_after_tracking_age_limit(self) -> None:
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
                        ("0xhot", 0.70, 50, 0.4, 0.0, 20.0, 10, 10, 35, 0, 500.0, 0.08, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xwarm", 0.60, 20, 0.2, 0.0, 20.0, 10, 10, 10, 0, 100.0, 0.02, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xslow", 0.50, 8, 0.0, 0.0, 20.0, 10, 10, 4, 0, 0.0, 0.0, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_cursors (wallet_address, last_source_ts, last_trade_ids_json, updated_at)
                    VALUES (?,?,?,?)
                    """,
                    (
                        ("0xhot", 1_700_000_100, "[]", 1_700_000_100),
                        ("0xwarm", 1_699_999_000, "[]", 1_699_999_000),
                        ("0xslow", 1_699_990_000, "[]", 1_699_990_000),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    ("0xslow", 1_699_990_000, 1_699_990_000),
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=1
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=3600.0
                ), patch("watchlist_manager.wallet_performance_drop_min_trades", return_value=999), patch(
                    "watchlist_manager.time.time", return_value=1_700_000_200
                ):
                    manager = WatchlistManager(["0xhot", "0xwarm", "0xslow"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xhot",))
                self.assertEqual(snapshot.warm, ("0xwarm",))
                self.assertEqual(snapshot.dropped, ("0xslow",))

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT status_reason FROM wallet_watch_state WHERE wallet_address=?",
                    ("0xslow",),
                ).fetchone()
                conn.close()
                self.assertEqual(row["status_reason"], "slow>1h")
            finally:
                db.DB_PATH = original_db_path

    def test_recent_source_activity_prevents_slow_drop_after_tracking_age_limit(self) -> None:
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
                        ("0xhot", 0.70, 50, 0.4, 0.0, 20.0, 10, 10, 35, 0, 500.0, 0.08, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xwarm", 0.60, 20, 0.2, 0.0, 20.0, 10, 10, 10, 0, 100.0, 0.02, 0, 0.0, 0.0, 1_700_000_000),
                        ("0xslow", 0.50, 8, 0.0, 0.0, 20.0, 10, 10, 4, 0, 0.0, 0.0, 0, 0.0, 0.0, 1_700_000_000),
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO wallet_cursors (wallet_address, last_source_ts, last_trade_ids_json, updated_at)
                    VALUES (?,?,?,?)
                    """,
                    (
                        ("0xhot", 1_700_000_100, "[]", 1_700_000_100),
                        ("0xwarm", 1_699_999_000, "[]", 1_699_999_000),
                        ("0xslow", 1_699_999_800, "[]", 1_699_999_800),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, 'active', ?, ?)
                    """,
                    ("0xslow", 1_699_990_000, 1_699_990_000),
                )
                conn.commit()
                conn.close()

                with patch("watchlist_manager.hot_wallet_count", return_value=1), patch(
                    "watchlist_manager.warm_wallet_count", return_value=1
                ), patch("watchlist_manager.wallet_inactivity_limit_seconds", return_value=float("inf")), patch(
                    "watchlist_manager.wallet_slow_drop_max_tracking_age_seconds", return_value=3600.0
                ), patch("watchlist_manager.wallet_performance_drop_min_trades", return_value=999), patch(
                    "watchlist_manager.time.time", return_value=1_700_000_200
                ):
                    manager = WatchlistManager(["0xhot", "0xwarm", "0xslow"])
                    snapshot = manager.refresh()

                self.assertEqual(snapshot.hot, ("0xhot",))
                self.assertEqual(snapshot.warm, ("0xwarm",))
                self.assertEqual(snapshot.discovery, ("0xslow",))
                self.assertEqual(snapshot.dropped, ())
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
