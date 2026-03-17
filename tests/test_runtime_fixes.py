from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import auto_retrain
import db
import evaluator
import main
from executor import PolymarketExecutor


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
                        resolved_at, actual_entry_price, actual_entry_size_usd, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                        real_money, skipped, placed_at, outcome, label_applied_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                        1,
                        3_000,
                    ),
                )
                conn.commit()
                conn.close()

                with patch("auto_retrain.retrain_min_new_labels", return_value=1):
                    self.assertTrue(auto_retrain.should_retrain_early(None))
            finally:
                db.DB_PATH = original_db_path

    def test_startup_validation_reports_bad_numeric_config_cleanly(self) -> None:
        with patch("main.WATCHED_WALLETS", ["0xabc"]), patch("main.min_confidence", side_effect=main.ConfigError("MIN_CONFIDENCE must be numeric, got 'abc'")):
            with self.assertRaisesRegex(RuntimeError, "MIN_CONFIDENCE must be numeric, got 'abc'"):
                main._validate_startup()


if __name__ == "__main__":
    unittest.main()
