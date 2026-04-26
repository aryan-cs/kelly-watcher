from __future__ import annotations

import os
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import kelly_watcher.config as config
import kelly_watcher.data.db as db
import kelly_watcher.engine.dedup as dedup
import kelly_watcher.engine.trader_scorer as trader_scorer
import kelly_watcher.runtime.evaluator as evaluator
import kelly_watcher.runtime.tracker as tracker
import kelly_watcher.research.train as train
from kelly_watcher.runtime.executor import LiveExchangeFill, PolymarketExecutor


class ProductionReadinessTest(unittest.TestCase):
    def test_shadow_buy_records_actual_spent_when_fill_has_dust_remaining(self) -> None:
        book = {
            "asks": [
                {"price": "0.50", "size": "9.99"},
            ]
        }

        fill, reason = PolymarketExecutor._simulate_shadow_buy(book, 5.0)

        self.assertIsNone(reason)
        self.assertIsNotNone(fill)
        assert fill is not None
        expected_spent = 0.50 * 9.99
        self.assertAlmostEqual(fill.spent_usd, expected_spent, places=6)
        self.assertAlmostEqual(fill.shares, 9.99, places=6)
        self.assertAlmostEqual(fill.avg_price, expected_spent / 9.99, places=6)

    def test_shadow_buy_rejects_non_finite_requested_size(self) -> None:
        book = {
            "asks": [
                {"price": "0.50", "size": "10"},
            ]
        }

        fill, reason = PolymarketExecutor._simulate_shadow_buy(book, float("nan"))

        self.assertIsNone(fill)
        self.assertIn("non-finite", str(reason))

    def test_shadow_sell_rejects_non_finite_share_size(self) -> None:
        book = {
            "bids": [
                {"price": "0.50", "size": "10"},
            ]
        }

        fill, reason = PolymarketExecutor._simulate_shadow_sell(book, float("nan"))

        self.assertIsNone(fill)
        self.assertIn("non-finite", str(reason))

    def test_orderbook_snapshot_uses_sorted_executable_levels(self) -> None:
        book = {
            "bids": [
                {"price": "0.20", "size": "100"},
                {"price": "0.40", "size": "50"},
                {"price": "0", "size": "999"},
                {"price": "0.45", "size": "0"},
            ],
            "asks": [
                {"price": "0.80", "size": "100"},
                {"price": "0.50", "size": "60"},
                {"price": "0.55", "size": "0"},
                {"price": "-0.10", "size": "999"},
            ],
        }

        snapshot = PolymarketExecutor._build_orderbook_snapshot(book)

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertAlmostEqual(snapshot["best_bid"], 0.40, places=6)
        self.assertAlmostEqual(snapshot["best_ask"], 0.50, places=6)
        self.assertAlmostEqual(snapshot["mid"], 0.45, places=6)
        self.assertAlmostEqual(snapshot["bid_depth_usd"], (0.40 * 50) + (0.20 * 100), places=6)
        self.assertAlmostEqual(snapshot["ask_depth_usd"], (0.50 * 60) + (0.80 * 100), places=6)

    def test_orderbook_snapshot_ignores_nonfinite_levels(self) -> None:
        book = {
            "bids": [
                {"price": "nan", "size": "100"},
                {"price": "inf", "size": "100"},
                {"price": "0.40", "size": "50"},
            ],
            "asks": [
                {"price": "-inf", "size": "100"},
                {"price": "1.20", "size": "100"},
                {"price": "0.55", "size": "50"},
            ],
        }

        snapshot = PolymarketExecutor._build_orderbook_snapshot(book)

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertAlmostEqual(snapshot["best_bid"], 0.40, places=6)
        self.assertAlmostEqual(snapshot["best_ask"], 0.55, places=6)

    def test_refresh_event_market_data_normalizes_cached_raw_orderbook(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor.get_fee_rate_bps = lambda token_id, market_meta=None: (0, None)
        executor.fetch_execution_orderbook = Mock(side_effect=AssertionError("fresh cached book should not refresh"))
        event = SimpleNamespace(
            token_id="token-1",
            snapshot={"best_bid": 0.20, "best_ask": 0.80, "mid": 0.50},
            raw_orderbook={
                "bids": [
                    {"price": "0.20", "size": "100"},
                    {"price": "0.42", "size": "50"},
                ],
                "asks": [
                    {"price": "0.70", "size": "100"},
                    {"price": "0.48", "size": "60"},
                ],
            },
            orderbook_fetched_at=int(time.time()),
            raw_market_metadata={},
        )

        with patch("kelly_watcher.runtime.executor.max_orderbook_staleness_seconds", return_value=3):
            ok, reason = executor.refresh_event_market_data(event)

        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertAlmostEqual(event.snapshot["best_bid"], 0.42, places=6)
        self.assertAlmostEqual(event.snapshot["best_ask"], 0.48, places=6)
        self.assertAlmostEqual(event.snapshot["mid"], 0.45, places=6)

    def test_live_entry_ensures_allowance_before_posting_order(self) -> None:
        ops: list[str] = []
        executor = object.__new__(PolymarketExecutor)
        executor.refresh_event_market_data = lambda event: (True, None)
        executor.get_fee_rate_bps = lambda token_id, market_meta=None: (0, None)
        executor._ensure_live_token_allowance = lambda token_id: ops.append(f"allow:{token_id}")
        executor._clob = SimpleNamespace(
            create_market_order=lambda order: ops.append("create") or order,
            post_order=lambda signed, order_type: ops.append("post") or {
                "success": True,
                "status": "matched",
                "orderID": "order-1",
                "makingAmount": "10.0",
                "takingAmount": "20.0",
            },
        )
        executor.get_usdc_balance = lambda: 100.0
        executor._measure_live_balance_change = lambda before, expect_increase=False: (before, 0.0)
        executor._sync_live_positions = lambda *args, **kwargs: None
        executor._reconcile_live_order_fill = lambda **kwargs: LiveExchangeFill(
            shares=20.0,
            notional_usd=10.0,
            avg_price=0.5,
            source="response",
        )
        dedup_cache = SimpleNamespace(
            confirm=lambda *args, **kwargs: None,
            mark_seen=lambda *args, **kwargs: None,
            release=lambda *args, **kwargs: None,
        )
        event = SimpleNamespace(
            question="Will it happen?",
            trader_address="0xabc",
            price=0.5,
            raw_orderbook=None,
        )
        market_f = SimpleNamespace()

        with patch("kelly_watcher.runtime.executor.log_trade", return_value=1), patch("kelly_watcher.runtime.executor.send_alert") as alert_mock:
            result = executor._execute_live(
                "trade-1",
                "market-1",
                "token-1",
                "yes",
                10.0,
                0.1,
                0.7,
                {"mode": "heuristic"},
                event,
                None,
                market_f,
                dedup_cache,
            )

        self.assertTrue(result.placed)
        self.assertEqual(ops[:3], ["allow:token-1", "create", "post"])
        self.assertEqual(sum(1 for op in ops if op.startswith("allow:")), 1)
        alert_mock.assert_called_once()
        self.assertEqual(alert_mock.call_args.kwargs["kind"], "buy")

    def test_live_entry_fok_cancellation_is_nonfatal(self) -> None:
        release = Mock()
        executor = object.__new__(PolymarketExecutor)
        executor.refresh_event_market_data = lambda event: (True, None)
        executor.get_fee_rate_bps = lambda token_id, market_meta=None: (0, None)
        executor._ensure_live_token_allowance = lambda token_id: None
        executor._clob = SimpleNamespace(
            create_market_order=lambda order: order,
            post_order=lambda signed, order_type: {
                "success": False,
                "status": "cancelled",
                "orderID": "order-cancelled",
            },
        )
        event = SimpleNamespace(
            question="Will it happen?",
            trader_address="0xabc",
            price=0.5,
            raw_orderbook=None,
        )
        market_f = SimpleNamespace()
        dedup_cache = SimpleNamespace(confirm=Mock(), mark_seen=Mock(), release=release)

        with patch("kelly_watcher.runtime.executor.log_trade") as log_trade_mock, patch("kelly_watcher.runtime.executor.send_alert") as alert_mock:
            result = executor._execute_live(
                "trade-1",
                "market-1",
                "token-1",
                "yes",
                10.0,
                0.1,
                0.7,
                {"mode": "heuristic"},
                event,
                None,
                market_f,
                dedup_cache,
            )

        self.assertFalse(result.placed)
        self.assertIn("FOK order cancelled", result.reason)
        release.assert_called_once_with("market-1", "token-1", "yes")
        log_trade_mock.assert_not_called()
        alert_mock.assert_not_called()

    def test_live_exit_commits_reconciled_fill_when_position_sync_stays_stale(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor.get_fee_rate_bps = lambda token_id, market_meta=None: (0, None)
        executor._ensure_live_token_allowance = lambda token_id: None
        executor._clob = SimpleNamespace(
            create_market_order=lambda order: order,
            post_order=lambda signed, order_type: {
                "success": True,
                "status": "matched",
                "orderID": "exit-1",
            },
        )
        executor.get_usdc_balance = lambda: 100.0
        executor._measure_live_balance_change = lambda before, expect_increase=False: (before, 12.0)
        executor._reconcile_live_order_fill = lambda **kwargs: LiveExchangeFill(
            shares=20.0,
            notional_usd=12.0,
            avg_price=0.6,
            source="trade_reconciliation",
        )
        executor._fetch_live_positions = lambda: [
            {
                "asset": "token-1",
                "conditionId": "market-1",
                "outcome": "yes",
                "size": "20.0",
                "avgPrice": "0.5",
                "totalBought": "10.0",
            }
        ]
        finalize_mock = Mock(return_value=(20.0, 12.0, 2.0))
        executor._finalize_exit = finalize_mock
        event = SimpleNamespace(
            question="Will it happen?",
            trader_address="0xabc",
            side="yes",
        )
        dedup_cache = SimpleNamespace(sync_positions_from_rows=lambda rows: None, release=Mock())

        with patch("kelly_watcher.runtime.executor.send_alert"), patch("kelly_watcher.runtime.executor.logger.warning"):
            result = executor._execute_live_exit(
                trade_id="trade-exit",
                market_id="market-1",
                token_id="token-1",
                event=event,
                dedup=dedup_cache,
                position={"token_id": "token-1", "side": "yes"},
                entries=[{"remaining_entry_shares": 20.0, "remaining_entry_size_usd": 10.0, "source_shares": 20.0}],
                exit_price=0.6,
                shares=20.0,
                exit_notional=12.0,
                pnl=2.0,
                exit_fraction=1.0,
            )

        self.assertTrue(result.placed)
        finalize_mock.assert_called_once()
        self.assertTrue(finalize_mock.call_args.kwargs["refresh_position_from_trade_log"])
        self.assertAlmostEqual(result.shares, 20.0, places=6)

    def test_live_exit_rejects_when_position_sync_fails_without_reconciled_fill(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._ensure_live_token_allowance = lambda token_id: None
        executor._clob = SimpleNamespace(
            create_market_order=lambda order: order,
            post_order=lambda signed, order_type: {
                "success": True,
                "status": "matched",
                "orderID": "exit-ambiguous",
            },
        )
        executor.get_usdc_balance = lambda: 100.0
        executor._measure_live_balance_change = lambda before, expect_increase=False: (before, 12.0)
        executor._reconcile_live_order_fill = lambda **kwargs: None
        executor._fetch_live_positions = lambda: None
        finalize_mock = Mock(return_value=(20.0, 12.0, 2.0))
        executor._finalize_exit = finalize_mock
        event = SimpleNamespace(
            question="Will it happen?",
            trader_address="0xabc",
            trader_name="Trader",
            side="yes",
            raw_market_metadata=None,
        )
        dedup_cache = SimpleNamespace(sync_positions_from_rows=Mock(), release=Mock())

        with patch("kelly_watcher.runtime.executor.send_alert") as alert_mock, patch(
            "kelly_watcher.runtime.executor.logger.error"
        ), patch("kelly_watcher.runtime.executor.time.sleep"):
            result = executor._execute_live_exit(
                trade_id="trade-exit",
                market_id="market-1",
                token_id="token-1",
                event=event,
                dedup=dedup_cache,
                position={"token_id": "token-1", "side": "yes"},
                entries=[{"remaining_entry_shares": 20.0, "remaining_entry_size_usd": 10.0, "source_shares": 20.0}],
                exit_price=0.6,
                shares=20.0,
                exit_notional=12.0,
                pnl=2.0,
                exit_fraction=1.0,
            )

        self.assertFalse(result.placed)
        self.assertIn("exit state ambiguous", result.reason)
        finalize_mock.assert_not_called()
        dedup_cache.release.assert_called_once_with("market-1", "token-1", "yes")
        dedup_cache.sync_positions_from_rows.assert_not_called()
        alert_mock.assert_called_once()

    def test_sports_helpers_read_event_level_fields_and_score_objects(self) -> None:
        snapshot = {
            "event": {
                "ended": True,
                "score": {
                    "status": "FINAL",
                    "homeTeam": {"name": "Home FC", "score": 2},
                    "awayTeam": {"name": "Away FC", "score": 1},
                },
            }
        }

        self.assertTrue(evaluator._sports_snapshot_is_ended(snapshot))
        self.assertEqual(
            evaluator._snapshot_teams(snapshot, None),
            [{"name": "Home FC", "score": 2.0}, {"name": "Away FC", "score": 1.0}],
        )
        self.assertEqual(
            evaluator._sports_event_slug(
                {"market_url": None},
                {"event": {"slug": "uel-home-away-2026-03-19"}},
            ),
            "uel-home-away-2026-03-19",
        )
        self.assertIn(
            "esports",
            evaluator._sports_route_candidates("cs-am-ast-2026-03-19", {"seriesSlug": "blast"}),
        )

    def test_cleanup_premature_resolutions_reopens_bad_sports_page_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, remaining_entry_shares, remaining_entry_size_usd,
                        outcome, market_resolved_outcome, resolution_json, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-1",
                        "market-1",
                        "Question",
                        "0xabc",
                        "yes",
                        "buy",
                        0.5,
                        10.0,
                        0.7,
                        0.1,
                        0,
                        0,
                        1_700_000_000,
                        0.5,
                        20.0,
                        10.0,
                        0.0,
                        0.0,
                        1,
                        "yes",
                        json.dumps({"source": "sports_page", "closed": True, "ended": False, "period": ""}),
                        10.0,
                    ),
                )
                conn.commit()
                conn.close()

                with patch("kelly_watcher.runtime.evaluator.invalidate_belief_cache") as invalidate_mock, patch(
                    "kelly_watcher.runtime.evaluator.sync_belief_priors", return_value=0
                ):
                    result = evaluator.cleanup_premature_resolutions(backup_path=Path(tmpdir) / "cleanup.bak")

                self.assertEqual(result["rows_cleaned"], 1)
                invalidate_mock.assert_called_once_with()

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT outcome, market_resolved_outcome, resolution_json FROM trade_log WHERE trade_id=?",
                    ("trade-1",),
                ).fetchone()
                conn.close()
                self.assertIsNone(row["outcome"])
                self.assertIsNone(row["market_resolved_outcome"])
                self.assertIsNone(row["resolution_json"])
            finally:
                db.DB_PATH = original_db_path

    def test_dedupe_shadow_rebuild_is_explicit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, token_id, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-1",
                        "market-1",
                        "Question",
                        "0xabc",
                        "yes",
                        "token-1",
                        "buy",
                        0.5,
                        10.0,
                        0.7,
                        0.1,
                        0,
                        0,
                        1_700_000_000,
                        0.5,
                        20.0,
                        10.0,
                    ),
                )
                conn.commit()
                conn.close()

                cache = dedup.DedupeCache()
                cache.load_from_db(rebuild_shadow_positions=False)
                self.assertEqual(cache.open_positions, {})

                cache.load_from_db(rebuild_shadow_positions=True)
                self.assertEqual(len(cache.open_positions), 1)
            finally:
                db.DB_PATH = original_db_path

    def test_tracker_advances_cursor_without_queueing_stale_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                tracker_obj = tracker.PolymarketTracker(["0xabc"])
                now_ts = 1_700_000_100
                tracker_obj.wallet_cursors = {"0xabc": tracker.WalletCursor()}
                tracker_obj._fetch_wallet_trades_batch = lambda wallets, limit=50: {
                    "0xabc": [
                        {
                            "id": "stale-1",
                            "conditionId": "market-old",
                            "side": "BUY",
                            "asset": "token-old",
                            "size": 10,
                            "price": 0.41,
                            "timestamp": now_ts - 120,
                        }
                    ]
                }

                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_seconds", return_value=30
                    ), patch("kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=30):
                        ingestion = tracker_obj.stage_source_events(["0xabc"], trade_limit=50)
                finally:
                    tracker_obj.close()

                self.assertEqual(ingestion.queued, 0)
                self.assertEqual(ingestion.stale, 1)
                conn = db.get_conn()
                try:
                    row = conn.execute(
                        "SELECT trade_id, status, source_ts FROM source_event_queue WHERE trade_id='stale-1'"
                    ).fetchone()
                finally:
                    conn.close()
                self.assertIsNone(row)
                self.assertEqual(tracker_obj.wallet_cursors["0xabc"].last_source_ts, now_ts - 120)
            finally:
                db.DB_PATH = original_db_path

    def test_tracker_expires_stale_pending_queue_rows_before_claiming(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, ?, '', ?, ?, ?, '{}', ?, 0, ?, ?, ?, '')
                        """,
                        [
                            ("old-1", "0xabc", "market-old", "token-old", now_ts - 120, "pending", now_ts - 120, now_ts - 120, now_ts - 120),
                            ("new-1", "0xabc", "market-new", "token-new", now_ts - 10, "pending", now_ts - 10, now_ts - 10, now_ts - 10),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                tracker_obj = tracker.PolymarketTracker(["0xabc"])
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_seconds", return_value=30
                    ), patch("kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=30):
                        rows = tracker_obj._claim_source_queue_rows(limit=10)
                finally:
                    tracker_obj.close()

                self.assertEqual([row["trade_id"] for row in rows], ["new-1"])
                conn = db.get_conn()
                try:
                    statuses = {
                        row["trade_id"]: row["status"]
                        for row in conn.execute("SELECT trade_id, status FROM source_event_queue").fetchall()
                    }
                finally:
                    conn.close()
                self.assertEqual(statuses["old-1"], "stale")
                self.assertEqual(statuses["new-1"], "processing")
            finally:
                db.DB_PATH = original_db_path

    def test_source_queue_claim_can_prioritize_hot_tier(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, ?, ?, ?, ?, ?, '{}', 'pending', 0, ?, ?, ?, '')
                        """,
                        [
                            ("warm-newer", "0xwarm", "warm", "market-warm", "token-warm", now_ts - 1, now_ts - 1, now_ts - 1, now_ts - 1),
                            ("hot-older", "0xhot", "hot", "market-hot", "token-hot", now_ts - 5, now_ts - 5, now_ts - 5, now_ts - 5),
                            ("discovery-newest", "0xdisc", "discovery", "market-disc", "token-disc", now_ts, now_ts, now_ts, now_ts),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                tracker_obj = tracker.PolymarketTracker(["0xhot", "0xwarm", "0xdisc"])
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=300
                    ):
                        hot_rows = tracker_obj._claim_source_queue_rows(limit=10, watch_tiers=("hot",))
                        remaining_rows = tracker_obj._claim_source_queue_rows(limit=10)
                finally:
                    tracker_obj.close()

                self.assertEqual([row["trade_id"] for row in hot_rows], ["hot-older"])
                self.assertEqual([row["trade_id"] for row in remaining_rows], ["warm-newer", "discovery-newest"])
            finally:
                db.DB_PATH = original_db_path

    def test_source_queue_claims_newest_valid_rows_within_tier_first(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, '0xhot', 'hot', ?, ?, ?, '{}', 'pending', 0, ?, ?, ?, '')
                        """,
                        [
                            ("hot-newest", "market-newest", "token-newest", now_ts - 5, now_ts - 5, now_ts - 5, now_ts - 5),
                            ("hot-oldest", "market-oldest", "token-oldest", now_ts - 25, now_ts - 25, now_ts - 25, now_ts - 25),
                            ("hot-middle", "market-middle", "token-middle", now_ts - 15, now_ts - 15, now_ts - 15, now_ts - 15),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=300
                    ):
                        rows = tracker_obj._claim_source_queue_rows(limit=2, watch_tiers=("hot",))
                finally:
                    tracker_obj.close()

                self.assertEqual([row["trade_id"] for row in rows], ["hot-newest", "hot-middle"])
            finally:
                db.DB_PATH = original_db_path

    def test_source_queue_claim_prioritizes_base_fresh_rows_under_stale_backlog(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, '0xhot', 'hot', ?, ?, ?, '{}', 'pending', 0, ?, ?, ?, '')
                        """,
                        [
                            ("older-backlog", "market-old", "token-old", now_ts - 70, now_ts - 70, now_ts - 70, now_ts - 70),
                            ("base-fresh-older", "market-new", "token-new", now_ts - 10, now_ts - 10, now_ts - 10, now_ts - 10),
                            ("base-fresh-newer", "market-newer", "token-newer", now_ts - 2, now_ts - 2, now_ts - 2, now_ts - 2),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_seconds", return_value=45
                    ), patch("kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=180):
                        rows = tracker_obj._claim_source_queue_rows(limit=1, watch_tiers=("hot",))
                finally:
                    tracker_obj.close()

                self.assertEqual([row["trade_id"] for row in rows], ["base-fresh-newer"])
                conn = db.get_conn()
                try:
                    statuses = {
                        row["trade_id"]: row["status"]
                        for row in conn.execute("SELECT trade_id, status FROM source_event_queue").fetchall()
                    }
                finally:
                    conn.close()
                self.assertEqual(statuses["base-fresh-newer"], "processing")
                self.assertEqual(statuses["base-fresh-older"], "pending")
                self.assertEqual(statuses["older-backlog"], "pending")
            finally:
                db.DB_PATH = original_db_path

    def test_source_queue_claim_prioritizes_pending_before_retryable_failed_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, '0xhot', 'hot', ?, ?, ?, '{}', ?, ?, ?, ?, ?, '')
                        """,
                        [
                            (
                                "failed-newer",
                                "market-failed",
                                "token-failed",
                                now_ts - 5,
                                "failed",
                                1,
                                now_ts - 5,
                                now_ts - 5,
                                now_ts - 30,
                            ),
                            (
                                "pending-older",
                                "market-pending",
                                "token-pending",
                                now_ts - 20,
                                "pending",
                                0,
                                now_ts - 20,
                                now_ts - 20,
                                now_ts - 20,
                            ),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=300
                    ):
                        rows = tracker_obj._claim_source_queue_rows(limit=2, watch_tiers=("hot",))
                finally:
                    tracker_obj.close()

                self.assertEqual([row["trade_id"] for row in rows], ["pending-older", "failed-newer"])
            finally:
                db.DB_PATH = original_db_path

    def test_source_queue_claim_respects_failed_retry_backoff(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, '0xhot', 'hot', ?, ?, ?, '{}', 'failed', 1, ?, ?, ?, 'temporary failure')
                        """,
                        [
                            (
                                "failed-recent",
                                "market-recent",
                                "token-recent",
                                now_ts - 5,
                                now_ts - 5,
                                now_ts - 5,
                                now_ts - 2,
                            ),
                            (
                                "failed-ready",
                                "market-ready",
                                "token-ready",
                                now_ts - 10,
                                now_ts - 10,
                                now_ts - 10,
                                now_ts - 30,
                            ),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=300
                    ):
                        rows = tracker_obj._claim_source_queue_rows(limit=10, watch_tiers=("hot",))
                finally:
                    tracker_obj.close()

                self.assertEqual([row["trade_id"] for row in rows], ["failed-ready"])
            finally:
                db.DB_PATH = original_db_path

    def test_source_queue_claim_uses_immediate_transaction(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                conn = db.get_conn()
                try:
                    conn.execute(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES ('claim-1', '0xhot', 'hot', 'market-1', 'token-1', ?, '{}', 'pending', 0, ?, ?, ?, '')
                        """,
                        (now_ts - 5, now_ts - 5, now_ts - 5, now_ts - 5),
                    )
                    conn.commit()
                finally:
                    conn.close()

                statements: list[str] = []

                def traced_conn():
                    traced = db.get_conn()
                    traced.set_trace_callback(lambda statement: statements.append(" ".join(statement.upper().split())))
                    return traced

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                try:
                    with patch("kelly_watcher.runtime.tracker.get_conn", side_effect=traced_conn), patch(
                        "kelly_watcher.runtime.tracker.time.time", return_value=now_ts
                    ), patch("kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=300):
                        rows = tracker_obj._claim_source_queue_rows(limit=1, watch_tiers=("hot",))
                finally:
                    tracker_obj.close()

                self.assertEqual([row["trade_id"] for row in rows], ["claim-1"])
                begin_index = next(index for index, statement in enumerate(statements) if statement == "BEGIN IMMEDIATE")
                select_index = next(
                    index
                    for index, statement in enumerate(statements)
                    if statement.startswith("SELECT *") and "FROM SOURCE_EVENT_QUEUE" in statement
                )
                update_index = next(
                    index
                    for index, statement in enumerate(statements)
                    if statement.startswith("UPDATE SOURCE_EVENT_QUEUE") and "STATUS='PROCESSING'" in statement
                )
                commit_index = next(
                    index
                    for index, statement in enumerate(statements)
                    if index > update_index and statement == "COMMIT"
                )
                self.assertLess(begin_index, select_index)
                self.assertLess(select_index, update_index)
                self.assertLess(update_index, commit_index)
            finally:
                db.DB_PATH = original_db_path

    def test_load_queued_events_marks_stale_after_metadata_before_orderbook_fetch(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                late_raw = {
                    "id": "late-near",
                    "conditionId": "market-near",
                    "side": "BUY",
                    "asset": "token-near",
                    "size": 10,
                    "price": 0.41,
                    "timestamp": now_ts - 60,
                    "outcome": "Yes",
                }
                far_raw = {
                    "id": "fresh-far",
                    "conditionId": "market-far",
                    "side": "BUY",
                    "asset": "token-far",
                    "size": 10,
                    "price": 0.42,
                    "timestamp": now_ts - 60,
                    "outcome": "Yes",
                }
                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, '0xhot', 'hot', ?, ?, ?, ?, 'pending', 0, ?, ?, ?, '')
                        """,
                        [
                            (
                                "late-near",
                                "market-near",
                                "token-near",
                                now_ts - 60,
                                json.dumps(late_raw),
                                now_ts - 55,
                                now_ts - 55,
                                now_ts - 55,
                            ),
                            (
                                "fresh-far",
                                "market-far",
                                "token-far",
                                now_ts - 60,
                                json.dumps(far_raw),
                                now_ts - 54,
                                now_ts - 54,
                                now_ts - 54,
                            ),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                def age_limit(market_close_ts, *, now_ts=None):
                    now_value = int(now_ts or 0)
                    if int(market_close_ts or 0) >= now_value + 3600:
                        return 180
                    return 45

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                tracker_obj._fetch_market_metadata_batch = Mock(
                    return_value={
                        "market-near": ({"question": "Near market"}, now_ts),
                        "market-far": ({"question": "Far market", "endDate": str(now_ts + 7200)}, now_ts),
                    }
                )
                tracker_obj._fetch_orderbook_snapshots_batch = Mock(
                    return_value={
                        "token-far": (
                            {"best_bid": 0.41, "best_ask": 0.43, "mid": 0.42},
                            {"bids": [], "asks": []},
                            now_ts,
                        )
                    }
                )

                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=180
                    ), patch("kelly_watcher.runtime.tracker.source_trade_age_limit_seconds", side_effect=age_limit):
                        events = tracker_obj.load_queued_events(limit=10, watch_tiers=("hot",))
                finally:
                    tracker_obj.close()

                self.assertEqual([event.trade_id for event in events], ["fresh-far"])
                tracker_obj._fetch_orderbook_snapshots_batch.assert_called_once_with(["token-far"])
                conn = db.get_conn()
                try:
                    statuses = {
                        row["trade_id"]: (row["status"], row["last_error"])
                        for row in conn.execute(
                            "SELECT trade_id, status, last_error FROM source_event_queue ORDER BY trade_id"
                        ).fetchall()
                    }
                finally:
                    conn.close()
                self.assertEqual(statuses["late-near"][0], "stale")
                self.assertIn("source trade stale before processing", statuses["late-near"][1])
                self.assertEqual(statuses["fresh-far"][0], "processing")
            finally:
                db.DB_PATH = original_db_path

    def test_load_queued_events_preserves_claim_priority_order(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100

                def raw_trade(trade_id: str, market_id: str, token_id: str, source_ts: int) -> dict[str, object]:
                    return {
                        "id": trade_id,
                        "conditionId": market_id,
                        "side": "BUY",
                        "asset": token_id,
                        "size": 10,
                        "price": 0.42,
                        "timestamp": source_ts,
                        "outcome": "Yes",
                    }

                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, '0xhot', 'hot', ?, ?, ?, ?, 'pending', 0, ?, ?, ?, '')
                        """,
                        [
                            (
                                "newer",
                                "market-newer",
                                "token-newer",
                                now_ts - 5,
                                json.dumps(raw_trade("newer", "market-newer", "token-newer", now_ts - 5)),
                                now_ts - 5,
                                now_ts - 5,
                                now_ts - 5,
                            ),
                            (
                                "older",
                                "market-older",
                                "token-older",
                                now_ts - 25,
                                json.dumps(raw_trade("older", "market-older", "token-older", now_ts - 25)),
                                now_ts - 25,
                                now_ts - 25,
                                now_ts - 25,
                            ),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                tracker_obj._fetch_market_metadata_batch = Mock(
                    return_value={
                        "market-newer": ({"question": "Newer market", "endDate": str(now_ts + 7200)}, now_ts),
                        "market-older": ({"question": "Older market", "endDate": str(now_ts + 7200)}, now_ts),
                    }
                )
                tracker_obj._fetch_orderbook_snapshots_batch = Mock(
                    return_value={
                        "token-newer": (
                            {"best_bid": 0.41, "best_ask": 0.43, "mid": 0.42},
                            {"bids": [], "asks": []},
                            now_ts,
                        ),
                        "token-older": (
                            {"best_bid": 0.41, "best_ask": 0.43, "mid": 0.42},
                            {"bids": [], "asks": []},
                            now_ts,
                        ),
                    }
                )
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=180
                    ):
                        events = tracker_obj.load_queued_events(limit=2, watch_tiers=("hot",))
                finally:
                    tracker_obj.close()

                self.assertEqual([event.trade_id for event in events], ["newer", "older"])
            finally:
                db.DB_PATH = original_db_path

    def test_iter_queued_event_batches_yields_each_enriched_chunk_in_claim_order(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100

                def raw_trade(trade_id: str, market_id: str, token_id: str, source_ts: int) -> dict[str, object]:
                    return {
                        "id": trade_id,
                        "conditionId": market_id,
                        "side": "BUY",
                        "asset": token_id,
                        "size": 10,
                        "price": 0.42,
                        "timestamp": source_ts,
                        "outcome": "Yes",
                    }

                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, '0xhot', 'hot', ?, ?, ?, ?, 'pending', 0, ?, ?, ?, '')
                        """,
                        [
                            (
                                "newer",
                                "market-newer",
                                "token-newer",
                                now_ts - 5,
                                json.dumps(raw_trade("newer", "market-newer", "token-newer", now_ts - 5)),
                                now_ts - 5,
                                now_ts - 5,
                                now_ts - 5,
                            ),
                            (
                                "older",
                                "market-older",
                                "token-older",
                                now_ts - 25,
                                json.dumps(raw_trade("older", "market-older", "token-older", now_ts - 25)),
                                now_ts - 25,
                                now_ts - 25,
                                now_ts - 25,
                            ),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                def metadata_batch(condition_ids):
                    return {
                        condition_id: (
                            {"question": condition_id, "endDate": str(now_ts + 7200)},
                            now_ts,
                        )
                        for condition_id in condition_ids
                    }

                def orderbook_batch(token_ids):
                    return {
                        token_id: (
                            {"best_bid": 0.41, "best_ask": 0.43, "mid": 0.42},
                            {"bids": [], "asks": []},
                            now_ts,
                        )
                        for token_id in token_ids
                    }

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                tracker_obj._fetch_market_metadata_batch = Mock(side_effect=metadata_batch)
                tracker_obj._fetch_orderbook_snapshots_batch = Mock(side_effect=orderbook_batch)
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=180
                    ):
                        batches = list(
                            tracker_obj.iter_queued_event_batches(
                                limit=2,
                                watch_tiers=("hot",),
                                batch_size=1,
                            )
                        )
                finally:
                    tracker_obj.close()

                self.assertEqual(
                    [[event.trade_id for event in batch] for batch in batches],
                    [["newer"], ["older"]],
                )
                self.assertEqual(
                    tracker_obj._fetch_market_metadata_batch.call_args_list[0].args[0],
                    ["market-newer"],
                )
                self.assertEqual(
                    tracker_obj._fetch_market_metadata_batch.call_args_list[1].args[0],
                    ["market-older"],
                )
                self.assertEqual(
                    tracker_obj._fetch_orderbook_snapshots_batch.call_args_list[0].args[0],
                    ["token-newer"],
                )
                self.assertEqual(
                    tracker_obj._fetch_orderbook_snapshots_batch.call_args_list[1].args[0],
                    ["token-older"],
                )
            finally:
                db.DB_PATH = original_db_path

    def test_load_queued_events_retries_when_metadata_unavailable(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                raw_trade = {
                    "id": "needs-meta",
                    "conditionId": "market-needs-meta",
                    "side": "BUY",
                    "asset": "token-needs-meta",
                    "size": 10,
                    "price": 0.42,
                    "timestamp": now_ts - 5,
                }
                conn = db.get_conn()
                try:
                    conn.execute(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, '0xhot', 'hot', ?, ?, ?, ?, 'pending', 0, ?, ?, ?, '')
                        """,
                        (
                            "needs-meta",
                            "market-needs-meta",
                            "token-needs-meta",
                            now_ts - 5,
                            json.dumps(raw_trade),
                            now_ts - 5,
                            now_ts - 5,
                            now_ts - 5,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                tracker_obj._fetch_market_metadata_batch = Mock(return_value={"market-needs-meta": ({}, 0)})
                tracker_obj.get_market_metadata = Mock(return_value=({}, 0))
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts):
                        events = tracker_obj.load_queued_events(limit=1, watch_tiers=("hot",))
                finally:
                    tracker_obj.close()

                self.assertEqual(events, [])
                conn = db.get_conn()
                try:
                    row = conn.execute(
                        "SELECT status, attempts, last_error FROM source_event_queue WHERE trade_id='needs-meta'"
                    ).fetchone()
                finally:
                    conn.close()
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row["status"], "failed")
                self.assertEqual(row["attempts"], 1)
                self.assertIn("metadata unavailable", row["last_error"])
            finally:
                db.DB_PATH = original_db_path

    def test_single_orderbook_enrichment_failure_isolated(self) -> None:
        tracker_obj = tracker.PolymarketTracker(["0xhot"])
        tracker_obj.get_orderbook_snapshot = Mock(side_effect=RuntimeError("bad orderbook"))
        try:
            result = tracker_obj._fetch_orderbook_snapshots_batch(["token-far"])
        finally:
            tracker_obj.close()

        self.assertEqual(result, {"token-far": (None, None, 0)})

    def test_dedup_confirm_can_merge_same_side_position_cost_basis(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                cache = dedup.DedupeCache()
                cache.confirm("market-1", "yes", 10.0, "token-1", 0.50, real_money=False, merge=True)
                cache.confirm("market-1", "yes", 6.0, "token-1", 0.60, real_money=False, merge=True)

                position = cache.get_position("market-1", "token-1", "yes")
                self.assertIsNotNone(position)
                assert position is not None
                expected_avg = 16.0 / ((10.0 / 0.50) + (6.0 / 0.60))
                self.assertAlmostEqual(float(position["size"]), 16.0, places=6)
                self.assertAlmostEqual(float(position["avg_price"]), expected_avg, places=6)

                conn = db.get_conn()
                try:
                    row = conn.execute(
                        """
                        SELECT size_usd, avg_price
                        FROM positions
                        WHERE market_id='market-1'
                          AND token_id='token-1'
                          AND side='yes'
                          AND real_money=0
                        """
                    ).fetchone()
                finally:
                    conn.close()
                self.assertIsNotNone(row)
                self.assertAlmostEqual(float(row["size_usd"]), 16.0, places=6)
                self.assertAlmostEqual(float(row["avg_price"]), expected_avg, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_trader_features_can_skip_remote_fetch_on_hot_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                with patch.object(
                    trader_scorer,
                    "_fetch_remote_trader_features",
                    side_effect=AssertionError("remote fetch must not run on hot path"),
                ):
                    features = trader_scorer.get_trader_features(
                        "0xabc",
                        25.0,
                        allow_remote=False,
                    )

                self.assertEqual(features.n_trades, 0)
                self.assertEqual(features.win_rate, 0.5)
                self.assertEqual(features.avg_size_usd, 25.0)
            finally:
                db.DB_PATH = original_db_path

    def test_load_queued_events_refills_after_stale_near_row_to_preserve_far_market_event(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                now_ts = 1_700_000_100
                stale_near_raw = {
                    "id": "stale-near",
                    "conditionId": "market-near",
                    "side": "BUY",
                    "asset": "token-near",
                    "size": 10,
                    "price": 0.41,
                    "timestamp": now_ts - 60,
                    "outcome": "Yes",
                }
                valid_far_raw = {
                    "id": "valid-far",
                    "conditionId": "market-far",
                    "side": "BUY",
                    "asset": "token-far",
                    "size": 10,
                    "price": 0.42,
                    "timestamp": now_ts - 60,
                    "outcome": "Yes",
                }
                conn = db.get_conn()
                try:
                    conn.executemany(
                        """
                        INSERT INTO source_event_queue (
                            trade_id, wallet_address, watch_tier, condition_id, token_id,
                            source_ts, source_trade_json, status, attempts, first_seen_at,
                            observed_at, updated_at, last_error
                        ) VALUES (?, '0xhot', 'hot', ?, ?, ?, ?, 'pending', 0, ?, ?, ?, '')
                        """,
                        [
                            (
                                "stale-near",
                                "market-near",
                                "token-near",
                                now_ts - 60,
                                json.dumps(stale_near_raw),
                                now_ts - 59,
                                now_ts - 59,
                                now_ts - 59,
                            ),
                            (
                                "valid-far",
                                "market-far",
                                "token-far",
                                now_ts - 60,
                                json.dumps(valid_far_raw),
                                now_ts - 58,
                                now_ts - 58,
                                now_ts - 58,
                            ),
                        ],
                    )
                    conn.commit()
                finally:
                    conn.close()

                def metadata_batch(condition_ids):
                    payloads = {
                        "market-near": ({"question": "Near market"}, now_ts),
                        "market-far": ({"question": "Far market", "endDate": str(now_ts + 7200)}, now_ts),
                    }
                    return {condition_id: payloads[condition_id] for condition_id in condition_ids}

                def age_limit(market_close_ts, *, now_ts=None):
                    now_value = int(now_ts or 0)
                    if int(market_close_ts or 0) >= now_value + 3600:
                        return 180
                    return 45

                tracker_obj = tracker.PolymarketTracker(["0xhot"])
                tracker_obj._fetch_market_metadata_batch = Mock(side_effect=metadata_batch)
                tracker_obj._fetch_orderbook_snapshots_batch = Mock(
                    return_value={
                        "token-far": (
                            {"best_bid": 0.41, "best_ask": 0.43, "mid": 0.42},
                            {"bids": [], "asks": []},
                            now_ts,
                        )
                    }
                )
                try:
                    with patch("kelly_watcher.runtime.tracker.time.time", return_value=now_ts), patch(
                        "kelly_watcher.runtime.tracker.max_source_trade_age_seconds", return_value=45
                    ), patch("kelly_watcher.runtime.tracker.max_source_trade_age_ceiling_seconds", return_value=180), patch(
                        "kelly_watcher.runtime.tracker.source_trade_age_limit_seconds", side_effect=age_limit
                    ):
                        events = tracker_obj.load_queued_events(limit=1, watch_tiers=("hot",))
                finally:
                    tracker_obj.close()

                self.assertEqual([event.trade_id for event in events], ["valid-far"])
                self.assertEqual(tracker_obj._fetch_market_metadata_batch.call_count, 2)
                tracker_obj._fetch_orderbook_snapshots_batch.assert_called_once_with(["token-far"])
                conn = db.get_conn()
                try:
                    statuses = {
                        row["trade_id"]: (row["status"], row["last_error"])
                        for row in conn.execute(
                            "SELECT trade_id, status, last_error FROM source_event_queue ORDER BY trade_id"
                        ).fetchall()
                    }
                finally:
                    conn.close()
                self.assertEqual(statuses["stale-near"][0], "stale")
                self.assertIn("source trade stale before processing", statuses["stale-near"][1])
                self.assertEqual(statuses["valid-far"][0], "processing")
            finally:
                db.DB_PATH = original_db_path

    def test_load_training_data_closes_connection_when_read_fails(self) -> None:
        class ClosingConnection:
            closed = False

            def close(self) -> None:
                self.closed = True

        conn = ClosingConnection()
        fake_numpy = ModuleType("numpy")
        fake_pandas = ModuleType("pandas")
        fake_pandas.read_sql_query = Mock(side_effect=RuntimeError("read failed"))
        with patch.dict("sys.modules", {"numpy": fake_numpy, "pandas": fake_pandas}), patch(
            "kelly_watcher.research.train.get_conn", return_value=conn
        ):
            with self.assertRaises(RuntimeError):
                train.load_training_data()

        self.assertTrue(conn.closed)

    def test_wallet_trade_pagination_continues_past_cursor_duplicate_page(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                tracker_obj = tracker.PolymarketTracker(["0xabc"])
                now_ts = 1_700_000_100
                requested_offsets: list[int] = []

                def fake_request_json(url, *, params=None, **kwargs):
                    requested_offsets.append(int((params or {}).get("offset") or 0))
                    offset = int((params or {}).get("offset") or 0)
                    if offset == 0:
                        return [
                            {"id": "seen-1", "timestamp": now_ts},
                            {"id": "seen-2", "timestamp": now_ts},
                        ], True
                    if offset == 2:
                        return [{"id": "unseen-peer", "timestamp": now_ts}], True
                    return [], True

                tracker_obj._request_json = fake_request_json
                try:
                    rows = tracker_obj.get_wallet_trades(
                        "0xabc",
                        limit=2,
                        cursor=tracker.WalletCursor(last_source_ts=now_ts, last_trade_ids={"seen-1", "seen-2"}),
                    )
                finally:
                    tracker_obj.close()

                self.assertEqual([row["id"] for row in rows], ["unseen-peer"])
                self.assertEqual(requested_offsets, [0, 2])
            finally:
                db.DB_PATH = original_db_path

    def test_load_training_data_sql_executes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                df = train.load_training_data()
                self.assertEqual(len(df), 0)
            finally:
                db.DB_PATH = original_db_path

    def test_watched_wallets_reads_environment_changes_each_call(self) -> None:
        with patch.dict(os.environ, {"WATCHED_WALLETS": "0x1,0x2"}, clear=False):
            self.assertEqual(config.watched_wallets(), ["0x1", "0x2"])
        with patch.dict(os.environ, {"WATCHED_WALLETS": "0x3"}, clear=False):
            self.assertEqual(config.watched_wallets(), ["0x3"])

    def test_intraday_market_metadata_cache_refreshes_early(self) -> None:
        tracker_obj = tracker.PolymarketTracker([])
        tracker_obj._market_metadata_cache["cond"] = (
            time.time() - 45,
            ({"conditionId": "cond", "endDate": "2026-03-19T18:00:00Z", "question": "stale"}, 111),
        )
        tracker_obj._request_json = lambda *args, **kwargs: (
            [{"conditionId": "cond", "endDate": "2026-03-19T18:00:00Z", "question": "fresh"}],
            True,
        )

        try:
            meta, fetched_at = tracker_obj.get_market_metadata("cond")
        finally:
            tracker_obj.close()

        self.assertEqual(meta["question"], "fresh")
        self.assertGreater(fetched_at, 0)


if __name__ == "__main__":
    unittest.main()
