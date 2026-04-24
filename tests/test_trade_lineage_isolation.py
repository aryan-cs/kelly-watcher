from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

import db
import evaluator
import wallet_trust
from economics import build_entry_economics
from executor import PolymarketExecutor, log_trade


def _insert_trade(
    conn,
    *,
    trade_id: str,
    trader_address: str,
    experiment_arm: str,
    shadow_pnl_usd: float,
    resolved_at: int,
) -> None:
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id,
            market_id,
            question,
            trader_address,
            side,
            source_action,
            price_at_signal,
            signal_size_usd,
            confidence,
            kelly_fraction,
            real_money,
            skipped,
            placed_at,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            remaining_entry_shares,
            remaining_entry_size_usd,
            remaining_source_shares,
            shadow_pnl_usd,
            actual_pnl_usd,
            outcome,
            resolved_at,
            experiment_arm
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_id,
            f"market-{trade_id}",
            "Question",
            trader_address,
            "yes",
            "buy",
            0.5,
            10.0,
            0.7,
            0.1,
            0,
            0,
            resolved_at - 10,
            0.5,
            20.0,
            10.0,
            20.0,
            10.0,
            20.0,
            shadow_pnl_usd,
            None,
            1,
            resolved_at,
            experiment_arm,
        ),
    )


class TradeLineageIsolationTest(TestCase):
    def _use_temp_db(self) -> tuple[Path, Path]:
        tmpdir = TemporaryDirectory()
        db_path = Path(tmpdir.name) / "trading.db"
        self.addCleanup(tmpdir.cleanup)
        return db_path, Path(tmpdir.name)

    def test_init_db_adds_trade_log_lineage_columns(self) -> None:
        db_path, _tmpdir = self._use_temp_db()
        original_db_path = db.DB_PATH
        try:
            db.DB_PATH = db_path
            conn = db.get_conn()
            conn.execute(
                """
                CREATE TABLE trade_log (
                    trade_id TEXT PRIMARY KEY,
                    placed_at INTEGER NOT NULL DEFAULT 0,
                    outcome INTEGER,
                    trader_address TEXT NOT NULL DEFAULT '',
                    real_money INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                INSERT INTO trade_log (trade_id, placed_at, outcome, trader_address, real_money, skipped)
                VALUES ('legacy-trade', 1, 1, '0xlegacy', 0, 0)
                """
            )
            conn.commit()
            conn.close()

            db.init_db()

            conn = db.get_conn()
            try:
                info = {row["name"] for row in conn.execute("PRAGMA table_info(trade_log)").fetchall()}
                self.assertTrue(
                    {
                        "segment_id",
                        "policy_id",
                        "policy_bundle_version",
                        "promotion_epoch_id",
                        "experiment_arm",
                        "expected_edge",
                        "expected_fill_cost_usd",
                        "expected_exit_fee_usd",
                        "expected_close_fixed_cost_usd",
                    }.issubset(info)
                )
                row = conn.execute(
                    """
                    SELECT segment_id, policy_id, policy_bundle_version, promotion_epoch_id,
                           experiment_arm, expected_edge, expected_fill_cost_usd,
                           expected_exit_fee_usd, expected_close_fixed_cost_usd
                    FROM trade_log
                    WHERE trade_id='legacy-trade'
                    """
                ).fetchone()
                self.assertIsNotNone(row)
                assert row is not None
                self.assertIsNone(row["segment_id"])
                self.assertIsNone(row["policy_id"])
                self.assertEqual(int(row["policy_bundle_version"]), 0)
                self.assertEqual(int(row["promotion_epoch_id"]), 0)
                self.assertEqual(str(row["experiment_arm"]), "champion")
                self.assertIsNone(row["expected_edge"])
                self.assertIsNone(row["expected_fill_cost_usd"])
                self.assertIsNone(row["expected_exit_fee_usd"])
                self.assertIsNone(row["expected_close_fixed_cost_usd"])
            finally:
                conn.close()
        finally:
            db.DB_PATH = original_db_path

    def test_challenger_rows_do_not_touch_shadow_balance_or_performance(self) -> None:
        db_path, _tmpdir = self._use_temp_db()
        original_db_path = db.DB_PATH
        try:
            db.DB_PATH = db_path
            db.init_db()

            conn = db.get_conn()
            try:
                _insert_trade(
                    conn,
                    trade_id="champion-trade",
                    trader_address="0xchampion",
                    experiment_arm="champion",
                    shadow_pnl_usd=12.5,
                    resolved_at=1_700_000_100,
                )
                _insert_trade(
                    conn,
                    trade_id="challenger-trade",
                    trader_address="0xchallenger",
                    experiment_arm="challenger",
                    shadow_pnl_usd=99.0,
                    resolved_at=1_700_000_200,
                )
                conn.commit()
            finally:
                conn.close()

            realized_pnl, remaining_cost = PolymarketExecutor._shadow_balance_components()
            self.assertAlmostEqual(realized_pnl, 12.5, places=6)
            self.assertAlmostEqual(remaining_cost, 0.0, places=6)

            report = evaluator.compute_performance_report("shadow")
            self.assertEqual(report["resolved"], 1)
            self.assertAlmostEqual(report["total_pnl_usd"], 12.5, places=6)
            self.assertEqual(report["top_traders"][0]["trader_address"], "0xchampion")
        finally:
            db.DB_PATH = original_db_path

    def test_wallet_trust_ignores_challenger_rows(self) -> None:
        db_path, _tmpdir = self._use_temp_db()
        original_db_path = db.DB_PATH
        try:
            db.DB_PATH = db_path
            db.init_db()

            conn = db.get_conn()
            try:
                _insert_trade(
                    conn,
                    trade_id="wallet-champion",
                    trader_address="0xwallet",
                    experiment_arm="champion",
                    shadow_pnl_usd=8.0,
                    resolved_at=1_700_000_300,
                )
                _insert_trade(
                    conn,
                    trade_id="wallet-challenger",
                    trader_address="0xwallet",
                    experiment_arm="challenger",
                    shadow_pnl_usd=44.0,
                    resolved_at=1_700_000_400,
                )
                conn.commit()
            finally:
                conn.close()

            state = wallet_trust.get_wallet_trust_state("0xwallet")
            self.assertEqual(state.observed_buy_count, 1)
            self.assertEqual(state.resolved_observed_buy_count, 1)
            self.assertEqual(state.resolved_copied_buy_count, 1)
            self.assertAlmostEqual(state.resolved_copied_avg_return or 0.0, 0.8, places=6)
        finally:
            db.DB_PATH = original_db_path

    def test_log_trade_persists_lineage_and_expected_cost_fields(self) -> None:
        db_path, _tmpdir = self._use_temp_db()
        original_db_path = db.DB_PATH
        try:
            db.DB_PATH = db_path
            db.init_db()

            event = SimpleNamespace(
                question="Question",
                token_id="token-1",
                action="buy",
                timestamp=1_700_000_000,
                source_ts_raw="1700000000",
                price=0.5,
                shares=20.0,
                size_usd=10.0,
                close_time="2026-04-13T12:00:00Z",
                snapshot=None,
                raw_trade=None,
                raw_market_metadata=None,
                raw_orderbook=None,
                trader_name="Trader",
            )
            entry_economics = build_entry_economics(
                gross_price=0.5,
                gross_shares=20.0,
                gross_spent_usd=10.0,
                fee_rate_bps=100,
                fixed_cost_usd=0.25,
                include_expected_exit_fee_in_sizing=True,
                expected_close_fixed_cost_usd=0.1,
            )

            row_id = log_trade(
                trade_id="lineage-trade",
                market_id="market-lineage",
                question="Question",
                trader_address="0xlineage",
                side="yes",
                price=0.5,
                signal_size_usd=10.0,
                confidence=0.68,
                kelly_f=0.1,
                real_money=False,
                order_id=None,
                skipped=False,
                skip_reason=None,
                actual_entry_price=entry_economics.effective_entry_price,
                actual_entry_shares=entry_economics.net_shares,
                actual_entry_size_usd=entry_economics.total_cost_usd,
                entry_economics=entry_economics,
                event=event,
                signal={
                    "mode": "heuristic",
                    "edge": 0.18,
                    "segment_id": "hot_short",
                    "policy_id": "shadow-runtime-segment-policy-v1",
                    "policy_bundle_version": 1,
                    "experiment_arm": "champion",
                },
            )

            conn = db.get_conn()
            try:
                row = conn.execute(
                    """
                    SELECT segment_id, policy_id, policy_bundle_version, promotion_epoch_id,
                           experiment_arm, expected_edge, expected_fill_cost_usd,
                           expected_exit_fee_usd, expected_close_fixed_cost_usd
                    FROM trade_log
                    WHERE id=?
                    """,
                    (row_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row["segment_id"], "hot_short")
                self.assertEqual(row["policy_id"], "shadow-runtime-segment-policy-v1")
                self.assertEqual(int(row["policy_bundle_version"]), 1)
                self.assertEqual(int(row["promotion_epoch_id"]), 0)
                self.assertEqual(row["experiment_arm"], "champion")
                self.assertAlmostEqual(
                    float(row["expected_edge"]),
                    round(0.68 - entry_economics.sizing_effective_price, 6),
                    places=6,
                )
                self.assertAlmostEqual(
                    float(row["expected_fill_cost_usd"]),
                    round(entry_economics.entry_fee_usd + entry_economics.fixed_cost_usd, 6),
                    places=6,
                )
                self.assertAlmostEqual(float(row["expected_exit_fee_usd"]), entry_economics.expected_exit_fee_usd, places=6)
                self.assertAlmostEqual(
                    float(row["expected_close_fixed_cost_usd"]),
                    entry_economics.expected_close_fixed_cost_usd,
                    places=6,
                )
            finally:
                conn.close()
        finally:
            db.DB_PATH = original_db_path
