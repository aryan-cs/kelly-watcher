from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import auto_retrain
import beliefs
import dedup
import db
import evaluator
import main
import tracker
from executor import PolymarketExecutor, log_trade
from market_scorer import MarketScorer, build_market_features
from trader_scorer import TraderScorer


class RuntimeFixesTest(unittest.TestCase):
    def test_live_account_equity_includes_open_positions(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor.get_usdc_balance = lambda: 80.0
        executor._fetch_live_positions = lambda: [
            {"currentValue": "25.5"},
            {"totalBought": "10", "cashPnl": "2"},
        ]

        with patch("executor.use_real_money", return_value=True):
            self.assertEqual(executor.get_account_equity_usd(), 117.5)

    def test_resolve_shadow_trades_labels_exited_rows_without_overwriting_realized_pnl(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at, exited_at,
                        resolved_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-1",
                        "market-1",
                        "Will it happen?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.40,
                        10.0,
                        0.75,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        1_700_000_100,
                        1_700_000_100,
                        0.40,
                        25.0,
                        10.0,
                        3.21,
                    ),
                )
                conn.commit()
                conn.close()

                market = {"closed": True, "winner": "yes"}
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, counterfactual_return,
                           shadow_pnl_usd, exited_at, resolved_at, label_applied_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-1",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "yes")
                self.assertAlmostEqual(float(row["counterfactual_return"]), 1.5, places=6)
                self.assertAlmostEqual(float(row["shadow_pnl_usd"]), 3.21, places=2)
                self.assertEqual(int(row["exited_at"]), 1_700_000_100)
                self.assertEqual(int(row["resolved_at"]), 1_700_000_100)
                self.assertGreater(int(row["label_applied_at"]), 0)
            finally:
                db.DB_PATH = original_db_path

    def test_early_retrain_counts_recent_labels_not_old_entry_times(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO model_history (
                        trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (2_000, 250, 0.2, 0.6, "[]", "model.joblib", 1),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd, resolved_at, label_applied_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-2",
                        "market-2",
                        "Old trade, new label",
                        "0xdef",
                        "yes",
                        "buy",
                        0.45,
                        12.0,
                        0.72,
                        0.08,
                        0,
                        0,
                        1_000,
                        0.45,
                        26.666667,
                        12.0,
                        2.5,
                        3_000,
                        3_000,
                    ),
                )
                conn.commit()
                conn.close()

                with patch("auto_retrain.retrain_min_new_labels", return_value=1):
                    self.assertTrue(auto_retrain.should_retrain_early(None))
            finally:
                db.DB_PATH = original_db_path

    def test_sync_belief_priors_expands_sql_contract_macros(self) -> None:
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
                        actual_entry_size_usd, shadow_pnl_usd, resolved_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "belief-1",
                        "market-1",
                        "Will it happen?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.4,
                        10.0,
                        0.7,
                        0.1,
                        0,
                        0,
                        1_700_000_000,
                        0.4,
                        25.0,
                        10.0,
                        1.5,
                        1_700_000_100,
                    ),
                )
                conn.commit()
                conn.close()

                beliefs.invalidate_belief_cache()
                applied = beliefs.sync_belief_priors()

                self.assertEqual(applied, 1)

                conn = db.get_conn()
                update_count = conn.execute("SELECT COUNT(*) AS n FROM belief_updates").fetchone()["n"]
                prior_count = conn.execute("SELECT COUNT(*) AS n FROM belief_priors").fetchone()["n"]
                conn.close()

                self.assertEqual(update_count, 1)
                self.assertGreater(prior_count, 0)
            finally:
                db.DB_PATH = original_db_path

    def test_startup_validation_reports_bad_numeric_config_cleanly(self) -> None:
        with patch("main.WATCHED_WALLETS", ["0xabc"]), patch("main.min_confidence", side_effect=main.ConfigError("MIN_CONFIDENCE must be numeric, got 'abc'")):
            with self.assertRaisesRegex(RuntimeError, "MIN_CONFIDENCE must be numeric, got 'abc'"):
                main._validate_startup()

    def test_partial_exit_keeps_remaining_shadow_position_and_realized_pnl(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                buy_event = SimpleNamespace(
                    action="buy",
                    token_id="token-1",
                    timestamp=1_700_000_000,
                    trader_name="Trader",
                    observed_at=1_700_000_005,
                    poll_started_at=1_700_000_004,
                    market_close_ts=1_700_010_000,
                    metadata_fetched_at=1_700_000_004,
                    orderbook_fetched_at=1_700_000_004,
                    source_ts_raw="1700000000",
                    shares=100.0,
                    size_usd=50.0,
                    question="Will it happen?",
                    raw_trade=None,
                    raw_market_metadata=None,
                    raw_orderbook=None,
                    snapshot=None,
                    trader_address="0xabc",
                )
                row_id = log_trade(
                    trade_id="buy-1",
                    market_id="market-1",
                    question="Will it happen?",
                    trader_address="0xabc",
                    side="yes",
                    price=0.5,
                    signal_size_usd=50.0,
                    confidence=0.7,
                    kelly_f=0.1,
                    real_money=False,
                    order_id=None,
                    skipped=False,
                    skip_reason=None,
                    actual_entry_price=0.5,
                    actual_entry_shares=100.0,
                    actual_entry_size_usd=50.0,
                    event=buy_event,
                    signal={"mode": "heuristic"},
                )
                conn = db.get_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
                    ("market-1", "yes", 50.0, 0.5, "token-1", 1_700_000_000, 0),
                )
                conn.commit()
                entry_row = conn.execute("SELECT * FROM trade_log WHERE id=?", (row_id,)).fetchone()
                conn.close()

                cache = dedup.DedupeCache()
                cache.load_from_db()
                executor = object.__new__(PolymarketExecutor)
                shares, exit_notional, pnl = executor._finalize_exit(
                    entries=[dict(entry_row)],
                    position={"market_id": "market-1", "token_id": "token-1", "side": "yes"},
                    real_money=False,
                    exit_trade_id="sell-1",
                    exit_price=0.6,
                    exit_fraction=0.4,
                    exit_shares=40.0,
                    exit_notional=24.0,
                    exit_reason="partial exit",
                    exit_order_id=None,
                    market_id="market-1",
                    trader_address="0xabc",
                    dedup=cache,
                    refresh_position_from_trade_log=True,
                )

                self.assertAlmostEqual(shares, 40.0, places=6)
                self.assertAlmostEqual(exit_notional, 24.0, places=6)
                self.assertAlmostEqual(pnl, 4.0, places=2)

                conn = db.get_conn()
                updated = conn.execute(
                    """
                    SELECT remaining_entry_shares, remaining_entry_size_usd, realized_exit_shares,
                           realized_exit_size_usd, realized_exit_pnl_usd, partial_exit_count, shadow_pnl_usd
                    FROM trade_log
                    WHERE id=?
                    """,
                    (row_id,),
                ).fetchone()
                position = conn.execute(
                    "SELECT size_usd, avg_price FROM positions WHERE market_id=? AND token_id=? AND real_money=0",
                    ("market-1", "token-1"),
                ).fetchone()
                conn.close()

                self.assertAlmostEqual(float(updated["remaining_entry_shares"]), 60.0, places=6)
                self.assertAlmostEqual(float(updated["remaining_entry_size_usd"]), 30.0, places=6)
                self.assertAlmostEqual(float(updated["realized_exit_shares"]), 40.0, places=6)
                self.assertAlmostEqual(float(updated["realized_exit_size_usd"]), 24.0, places=6)
                self.assertAlmostEqual(float(updated["realized_exit_pnl_usd"]), 4.0, places=6)
                self.assertEqual(int(updated["partial_exit_count"]), 1)
                self.assertIsNone(updated["shadow_pnl_usd"])
                self.assertAlmostEqual(float(position["size_usd"]), 30.0, places=6)
                self.assertAlmostEqual(float(position["avg_price"]), 0.5, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_resolved_shadow_trade_count_uses_fill_aware_realized_rows_only(self) -> None:
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
                        real_money, skipped, placed_at, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("skip-1", "m0", "Skipped", "0x1", "yes", "buy", 0.5, 10.0, 0.6, 0.1, 0, 0, 10, 1.0),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("open-1", "m1", "Open", "0x2", "yes", "buy", 0.5, 10.0, 0.6, 0.1, 0, 0, 11, 0.5, 20.0, 10.0),
                )
                conn.execute(
                    f"""
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("resolved-1", "m2", "Resolved", "0x3", "yes", "buy", 0.5, 10.0, 0.6, 0.1, 0, 0, 12, 0.5, 20.0, 10.0, 3.5),
                )
                conn.commit()
                conn.close()

                self.assertEqual(main._resolved_shadow_trade_count(), 1)
            finally:
                db.DB_PATH = original_db_path

    def test_tracker_cursor_and_stale_checks(self) -> None:
        stale_event = tracker.TradeEvent(
            trade_id="t-old",
            market_id="m1",
            question="Question",
            side="yes",
            action="buy",
            price=0.5,
            shares=10.0,
            size_usd=5.0,
            token_id="token-1",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=100,
            close_time="",
        )
        duplicate_event = tracker.TradeEvent(
            trade_id="t-1",
            market_id="m1",
            question="Question",
            side="yes",
            action="buy",
            price=0.5,
            shares=10.0,
            size_usd=5.0,
            token_id="token-1",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=200,
            close_time="",
        )
        new_same_ts = tracker.TradeEvent(
            trade_id="t-2",
            market_id="m1",
            question="Question",
            side="yes",
            action="buy",
            price=0.5,
            shares=10.0,
            size_usd=5.0,
            token_id="token-1",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=200,
            close_time="",
        )
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        tracker_obj.wallet_cursors = {"0xabc": tracker.WalletCursor(last_source_ts=200, last_trade_ids={"t-1"})}

        self.assertFalse(tracker_obj._is_new_for_wallet("0xabc", duplicate_event))
        self.assertTrue(tracker_obj._is_new_for_wallet("0xabc", new_same_ts))
        with patch("tracker.max_source_trade_age_seconds", return_value=30):
            self.assertTrue(tracker_obj._is_stale_event(stale_event, poll_started_at=200))
            self.assertFalse(tracker_obj._is_stale_event(new_same_ts, poll_started_at=220))

    def test_tracker_rejects_missing_timestamp_and_missing_price(self) -> None:
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        tracker_obj.client = object()
        tracker_obj.get_market_metadata = lambda _condition_id: (
            {
                "question": "Will it happen?",
                "endDate": "2030-01-01T00:00:00Z",
                "outcomes": '["Yes","No"]',
                "clobTokenIds": '["token-yes","token-no"]',
            },
            123,
        )

        raw_missing_timestamp = {
            "conditionId": "market-1",
            "side": "BUY",
            "asset": "token-yes",
            "size": 10,
            "price": 0.55,
        }
        raw_missing_price = {
            "conditionId": "market-1",
            "side": "BUY",
            "asset": "token-yes",
            "size": 10,
            "timestamp": 1_700_000_000,
        }

        with patch("tracker.hydrate_observed_identity", return_value="Trader"):
            self.assertIsNone(tracker_obj._parse_raw_trade(raw_missing_timestamp, "0xabc", 1_700_000_010))
            self.assertIsNone(tracker_obj._parse_raw_trade(raw_missing_price, "0xabc", 1_700_000_010))

    def test_tracker_resolves_outcome_from_metadata_token_map(self) -> None:
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        tracker_obj.client = object()
        tracker_obj.get_market_metadata = lambda _condition_id: (
            {
                "question": "Will it happen?",
                "endDate": "2030-01-01T00:00:00Z",
                "outcomes": '["Yes","No"]',
                "clobTokenIds": '["token-yes","token-no"]',
            },
            456,
        )
        raw = {
            "conditionId": "market-1",
            "side": "BUY",
            "asset": "token-no",
            "size": 12,
            "price": 0.42,
            "timestamp": 1_700_000_000,
            "title": "Will it happen?",
        }

        with patch("tracker.hydrate_observed_identity", return_value="Trader"):
            event = tracker_obj._parse_raw_trade(raw, "0xabc", 1_700_000_010)

        self.assertIsNotNone(event)
        self.assertEqual(event.side, "no")
        self.assertEqual(event.price, 0.42)
        self.assertEqual(event.timestamp, 1_700_000_000)

    def test_market_scorer_handles_missing_optional_features(self) -> None:
        snapshot = {
            "best_bid": 0.49,
            "best_ask": 0.51,
            "mid": 0.5,
            "volume_24h_usd": 1000.0,
            "oi_usd": 2500.0,
            "bid_depth_usd": 800.0,
            "ask_depth_usd": 700.0,
            "top_holder_pct": None,
            "price_history_1h": [],
        }
        features = build_market_features(snapshot, "2030-01-01T00:00:00Z", order_size_usd=25.0, execution_price=0.5)
        self.assertIsNotNone(features)
        self.assertIsNone(features.price_1h_ago)
        scorer = MarketScorer()
        score = scorer.score(features)
        self.assertGreaterEqual(score["score"], 0.0)
        self.assertLessEqual(score["score"], 1.0)

    def test_market_scorer_rejects_missing_or_bad_close_time(self) -> None:
        snapshot = {
            "best_bid": 0.49,
            "best_ask": 0.51,
            "mid": 0.5,
            "bid_depth_usd": 800.0,
            "ask_depth_usd": 700.0,
        }
        self.assertIsNone(build_market_features(snapshot, "", order_size_usd=25.0, execution_price=0.5))
        self.assertIsNone(
            build_market_features(snapshot, "not-a-timestamp", order_size_usd=25.0, execution_price=0.5)
        )

    def test_trader_score_win_rate_shrinks_small_samples(self) -> None:
        low_evidence = TraderScorer._score_win_rate(0.9, 2)
        high_evidence = TraderScorer._score_win_rate(0.9, 200)

        self.assertGreater(low_evidence, 0.5)
        self.assertLess(low_evidence, 0.9)
        self.assertGreater(high_evidence, low_evidence)


if __name__ == "__main__":
    unittest.main()
