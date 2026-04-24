from __future__ import annotations

import os
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import kelly_watcher.config as config
import kelly_watcher.data.db as db
import kelly_watcher.engine.dedup as dedup
import kelly_watcher.runtime.evaluator as evaluator
import kelly_watcher.runtime.tracker as tracker
import kelly_watcher.research.train as train
from kelly_watcher.runtime.executor import LiveExchangeFill, PolymarketExecutor


class ProductionReadinessTest(unittest.TestCase):
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
        self.assertAlmostEqual(result.shares, 20.0, places=6)

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

    def test_tracker_stages_stale_rows_instead_of_dropping_before_ledger(self) -> None:
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
                    ):
                        ingestion = tracker_obj.stage_source_events(["0xabc"], trade_limit=50)
                finally:
                    tracker_obj.close()

                self.assertEqual(ingestion.queued, 1)
                conn = db.get_conn()
                try:
                    row = conn.execute(
                        "SELECT trade_id, status, source_ts FROM source_event_queue WHERE trade_id='stale-1'"
                    ).fetchone()
                finally:
                    conn.close()
                self.assertIsNotNone(row)
                self.assertEqual(row["status"], "pending")
                self.assertEqual(row["source_ts"], now_ts - 120)
                self.assertEqual(tracker_obj.wallet_cursors["0xabc"].last_source_ts, now_ts - 120)
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
