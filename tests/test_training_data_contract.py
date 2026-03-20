from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import auto_retrain
import db
import train
from economic_model import COUNTERFACTUAL_SAMPLE_WEIGHT, EXECUTED_SAMPLE_WEIGHT, transform_return_target


class TrainingDataContractTest(unittest.TestCase):
    def test_load_training_data_includes_trainable_skips_but_excludes_policy_skips(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        shadow_pnl_usd, resolved_at, label_applied_at, counterfactual_return
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "accepted-win",
                            "market-1",
                            "Accepted trade",
                            "0xaaa",
                            "yes",
                            "buy",
                            0.40,
                            10.0,
                            0.71,
                            0.10,
                            0,
                            0,
                            None,
                            1_700_000_000,
                            0.40,
                            25.0,
                            10.0,
                            5.0,
                            1_700_000_100,
                            1_700_000_100,
                            1.5,
                        ),
                        (
                            "skip-trainable-win",
                            "market-2",
                            "Skipped confidence reject that would have won",
                            "0xbbb",
                            "yes",
                            "buy",
                            0.45,
                            12.0,
                            0.59,
                            0.08,
                            0,
                            1,
                            "signal confidence was 59.0%, below the 60.0% minimum",
                            1_700_000_010,
                            None,
                            None,
                            None,
                            None,
                            1_700_000_110,
                            1_700_000_110,
                            1.222222,
                        ),
                        (
                            "skip-trainable-loss",
                            "market-3",
                            "Skipped confidence reject that would have lost",
                            "0xccc",
                            "yes",
                            "buy",
                            0.53,
                            8.0,
                            0.58,
                            0.05,
                            0,
                            1,
                            "confidence was 58.0%, below the 60.0% minimum needed to place a trade",
                            1_700_000_020,
                            None,
                            None,
                            None,
                            None,
                            1_700_000_120,
                            1_700_000_120,
                            -1.0,
                        ),
                        (
                            "skip-policy-win",
                            "market-4",
                            "Skipped policy reject that should stay out of training",
                            "0xddd",
                            "yes",
                            "buy",
                            0.35,
                            6.0,
                            0.66,
                            0.07,
                            0,
                            1,
                            "too close to resolution, less than 45 seconds remained to place the trade",
                            1_700_000_030,
                            None,
                            None,
                            None,
                            None,
                            1_700_000_130,
                            1_700_000_130,
                            1.857143,
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                df = train.load_training_data()

                self.assertEqual(
                    list(df["trade_id"]),
                    ["accepted-win", "skip-trainable-win", "skip-trainable-loss"],
                )
                returns = {row.trade_id: float(row.economic_return) for row in df.itertuples(index=False)}
                self.assertAlmostEqual(returns["accepted-win"], 0.5, places=6)
                self.assertAlmostEqual(returns["skip-trainable-win"], 1.222222, places=6)
                self.assertAlmostEqual(returns["skip-trainable-loss"], -1.0, places=6)

                labels = {row.trade_id: float(row.label) for row in df.itertuples(index=False)}
                self.assertAlmostEqual(labels["accepted-win"], transform_return_target(0.5), places=6)
                self.assertAlmostEqual(labels["skip-trainable-win"], transform_return_target(1.222222), places=6)
                self.assertAlmostEqual(labels["skip-trainable-loss"], transform_return_target(-1.0), places=6)

                outcomes = {row.trade_id: int(row.outcome_label) for row in df.itertuples(index=False)}
                self.assertEqual(outcomes["accepted-win"], 1)
                self.assertEqual(outcomes["skip-trainable-win"], 1)
                self.assertEqual(outcomes["skip-trainable-loss"], 0)

                weights = {row.trade_id: float(row.sample_weight) for row in df.itertuples(index=False)}
                self.assertAlmostEqual(weights["accepted-win"], EXECUTED_SAMPLE_WEIGHT, places=6)
                self.assertAlmostEqual(weights["skip-trainable-win"], COUNTERFACTUAL_SAMPLE_WEIGHT, places=6)
                self.assertAlmostEqual(weights["skip-trainable-loss"], COUNTERFACTUAL_SAMPLE_WEIGHT, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_early_retrain_counts_trainable_skipped_labels_only(self) -> None:
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
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        resolved_at, label_applied_at, counterfactual_return
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "eligible-new-skip",
                            "market-2",
                            "Eligible skipped label",
                            "0xdef",
                            "yes",
                            "buy",
                            0.45,
                            12.0,
                            0.59,
                            0.08,
                            0,
                            1,
                            "signal confidence was 59.0%, below the 60.0% minimum",
                            1_000,
                            1_500,
                            3_000,
                            1.222222,
                        ),
                        (
                            "ineligible-new-skip",
                            "market-3",
                            "Ineligible skipped label",
                            "0x987",
                            "yes",
                            "buy",
                            0.35,
                            8.0,
                            0.67,
                            0.07,
                            0,
                            1,
                            "too close to resolution, less than 45 seconds remained to place the trade",
                            1_001,
                            1_600,
                            3_100,
                            1.857143,
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                with patch("auto_retrain.retrain_min_new_labels", return_value=1):
                    self.assertTrue(auto_retrain.should_retrain_early(None))
                with patch("auto_retrain.retrain_min_new_labels", return_value=2):
                    self.assertFalse(auto_retrain.should_retrain_early(None))
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
