from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import auto_retrain
import db


class RetrainRunHistoryTest(unittest.TestCase):
    def test_init_db_backfills_successful_model_history_into_retrain_runs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                with patch("db._repair_trade_log_market_urls", side_effect=sqlite3.DatabaseError("malformed")):
                    db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO model_history (
                        trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (1_700_000_050, 123, 0.11, 0.44, "[]", "model.joblib", 1),
                )
                conn.commit()
                conn.close()

                with patch("db._repair_trade_log_market_urls", side_effect=sqlite3.DatabaseError("malformed")):
                    db.init_db()

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT trigger, status, ok, deployed, sample_count, brier_score, log_loss
                    FROM retrain_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()

                self.assertEqual(row["trigger"], "backfill")
                self.assertEqual(row["status"], "deployed")
                self.assertEqual(int(row["ok"]), 1)
                self.assertEqual(int(row["deployed"]), 1)
                self.assertEqual(int(row["sample_count"]), 123)
                self.assertAlmostEqual(float(row["brier_score"]), 0.11, places=6)
                self.assertAlmostEqual(float(row["log_loss"]), 0.44, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_retrain_cycle_report_logs_skipped_attempt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                with patch(
                    "auto_retrain.read_shadow_evidence_epoch",
                    return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
                ), patch("auto_retrain.load_training_data", return_value=[object()] * 3), patch(
                    "auto_retrain.min_samples_required", return_value=5
                ), patch("auto_retrain.send_alert", return_value=None) as alert_mock, patch(
                    "auto_retrain.time.time", return_value=1_700_000_100
                ):
                    report = auto_retrain.retrain_cycle_report(object(), trigger="manual")

                self.assertEqual(report["status"], "skipped_not_enough_samples")
                alert_mock.assert_not_called()

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT trigger, status, deployed, sample_count, min_samples, started_at, finished_at
                    FROM retrain_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()

                self.assertEqual(row["trigger"], "manual")
                self.assertEqual(row["status"], "skipped_not_enough_samples")
                self.assertEqual(int(row["deployed"]), 0)
                self.assertEqual(int(row["sample_count"]), 3)
                self.assertEqual(int(row["min_samples"]), 5)
                self.assertEqual(int(row["started_at"]), 1_700_000_100)
                self.assertEqual(int(row["finished_at"]), 1_700_000_100)
            finally:
                db.DB_PATH = original_db_path

    def test_retrain_cycle_report_blocks_without_active_epoch(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                with patch(
                    "auto_retrain.read_shadow_evidence_epoch",
                    return_value={"shadow_evidence_epoch_started_at": 0},
                ), patch("auto_retrain.load_training_data") as load_mock, patch(
                    "auto_retrain.send_alert", return_value=None
                ), patch("auto_retrain.time.time", return_value=1_700_000_125):
                    report = auto_retrain.retrain_cycle_report(object(), trigger="scheduled")

                self.assertEqual(report["status"], "blocked_shadow_snapshot")
                self.assertFalse(report["ok"])
                load_mock.assert_not_called()

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT trigger, status, ok, deployed, sample_count, min_samples, started_at, finished_at, message
                    FROM retrain_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()

                self.assertEqual(row["trigger"], "scheduled")
                self.assertEqual(row["status"], "blocked_shadow_snapshot")
                self.assertEqual(int(row["ok"]), 0)
                self.assertEqual(int(row["deployed"]), 0)
                self.assertEqual(int(row["sample_count"]), 0)
                self.assertEqual(int(row["min_samples"]), 0)
                self.assertEqual(int(row["started_at"]), 1_700_000_125)
                self.assertEqual(int(row["finished_at"]), 1_700_000_125)
                self.assertIn("current evidence window is not active yet", row["message"])
            finally:
                db.DB_PATH = original_db_path

    def test_retrain_cycle_report_logs_failed_attempt_when_loading_training_data_raises(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                with patch(
                    "auto_retrain.read_shadow_evidence_epoch",
                    return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
                ), patch(
                    "auto_retrain.load_training_data",
                    side_effect=RuntimeError("training data unavailable"),
                ), patch("auto_retrain.send_alert", return_value=None), patch(
                    "auto_retrain.time.time", return_value=1_700_000_125
                ):
                    with self.assertRaisesRegex(RuntimeError, "training data unavailable"):
                        auto_retrain.retrain_cycle_report(object(), trigger="scheduled")

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT trigger, status, ok, deployed, sample_count, min_samples, started_at, finished_at, message
                    FROM retrain_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()

                self.assertEqual(row["trigger"], "scheduled")
                self.assertEqual(row["status"], "failed")
                self.assertEqual(int(row["ok"]), 0)
                self.assertEqual(int(row["deployed"]), 0)
                self.assertEqual(int(row["sample_count"]), 0)
                self.assertEqual(int(row["min_samples"]), 0)
                self.assertEqual(int(row["started_at"]), 1_700_000_125)
                self.assertEqual(int(row["finished_at"]), 1_700_000_125)
                self.assertIn("training data unavailable", row["message"])
            finally:
                db.DB_PATH = original_db_path

    def test_retrain_cycle_report_logs_completed_not_deployed_attempt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                metrics = {
                    "deployed": False,
                    "brier_score": 0.19,
                    "log_loss": 0.51,
                    "candidate_name": "xgb_balanced_seed42",
                    "candidate_count": 16,
                    "search_total_pnl": -1.25,
                    "val_selected_trades": 7,
                    "val_total_pnl": -2.0,
                    "challenger_shared_log_loss": 0.501,
                    "challenger_shared_brier_score": 0.177,
                    "incumbent_log_loss": 0.509,
                    "incumbent_brier_score": 0.175,
                }
                sample_rows = [object()] * 12
                with patch(
                    "auto_retrain.read_shadow_evidence_epoch",
                    return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
                ), patch("auto_retrain.load_training_data", return_value=sample_rows), patch(
                    "auto_retrain.min_samples_required", return_value=5
                ), patch("auto_retrain.train", return_value=metrics) as train_mock, patch(
                    "auto_retrain.send_alert", return_value=None
                ), patch("auto_retrain.time.time", return_value=1_700_000_200):
                    report = auto_retrain.retrain_cycle_report(object(), trigger="scheduled", started_at=1_700_000_150)

                self.assertEqual(report["status"], "completed_not_deployed")
                train_mock.assert_called_once_with(
                    sample_rows,
                    training_since_ts=1_700_000_400,
                    training_routed_only=True,
                )

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT trigger, status, deployed, brier_score, log_loss, candidate_name, candidate_count,
                           search_total_pnl, val_selected_trades, val_total_pnl,
                           challenger_shared_log_loss, challenger_shared_brier_score,
                           incumbent_log_loss, incumbent_brier_score
                    FROM retrain_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()

                self.assertEqual(row["trigger"], "scheduled")
                self.assertEqual(row["status"], "completed_not_deployed")
                self.assertEqual(int(row["deployed"]), 0)
                self.assertAlmostEqual(float(row["brier_score"]), 0.19, places=6)
                self.assertAlmostEqual(float(row["log_loss"]), 0.51, places=6)
                self.assertEqual(row["candidate_name"], "xgb_balanced_seed42")
                self.assertEqual(int(row["candidate_count"]), 16)
                self.assertAlmostEqual(float(row["search_total_pnl"]), -1.25, places=6)
                self.assertEqual(int(row["val_selected_trades"]), 7)
                self.assertAlmostEqual(float(row["val_total_pnl"]), -2.0, places=6)
                self.assertAlmostEqual(float(row["challenger_shared_log_loss"]), 0.501, places=6)
                self.assertAlmostEqual(float(row["challenger_shared_brier_score"]), 0.177, places=6)
                self.assertAlmostEqual(float(row["incumbent_log_loss"]), 0.509, places=6)
                self.assertAlmostEqual(float(row["incumbent_brier_score"]), 0.175, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_init_db_backfills_shared_holdout_metrics_from_existing_message(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO retrain_runs (
                        started_at, finished_at, trigger, status, ok, deployed,
                        sample_count, min_samples, brier_score, log_loss, message
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        1_700_000_100,
                        1_700_000_120,
                        "manual",
                        "completed_not_deployed",
                        0,
                        0,
                        3211,
                        200,
                        0.1799,
                        0.5219,
                        "\n".join(
                            [
                                "retrain rejected",
                                "model failed deployment checks",
                                "shared holdout ll/brier: 0.5219 / 0.1799",
                                "incumbent ll/brier: 0.5233 / 0.1785",
                            ]
                        ),
                    ),
                )
                conn.commit()
                conn.close()

                db.init_db()

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT challenger_shared_log_loss, challenger_shared_brier_score,
                           incumbent_log_loss, incumbent_brier_score
                    FROM retrain_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                conn.close()

                self.assertAlmostEqual(float(row["challenger_shared_log_loss"]), 0.5219, places=6)
                self.assertAlmostEqual(float(row["challenger_shared_brier_score"]), 0.1799, places=6)
                self.assertAlmostEqual(float(row["incumbent_log_loss"]), 0.5233, places=6)
                self.assertAlmostEqual(float(row["incumbent_brier_score"]), 0.1785, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_retrain_cycle_report_alerts_when_model_is_rejected(self) -> None:
        metrics = {
            "deployed": False,
            "brier_score": 0.19,
            "log_loss": 0.51,
            "val_selected_trades": 7,
            "val_total_pnl": -2.0,
        }

        with patch(
            "auto_retrain.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
        ), patch("auto_retrain.load_training_data", return_value=[object()] * 12), patch(
            "auto_retrain.min_samples_required", return_value=5
        ), patch("auto_retrain.train", return_value=metrics), patch(
            "auto_retrain.send_alert", return_value=None
        ) as alert_mock, patch("auto_retrain._record_retrain_run"):
            report = auto_retrain.retrain_cycle_report(object(), trigger="scheduled", started_at=1_700_000_150)

        self.assertEqual(report["status"], "completed_not_deployed")
        alert_mock.assert_called_once()
        self.assertIn("retrain rejected", alert_mock.call_args.args[0])
        self.assertEqual(alert_mock.call_args.kwargs["kind"], "retrain")

    def test_retrain_cycle_report_scopes_training_data_to_active_epoch_and_routed_rows(self) -> None:
        with patch(
            "auto_retrain.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
        ), patch(
            "auto_retrain.load_training_data",
            return_value=[object()] * 3,
        ) as load_mock, patch(
            "auto_retrain.min_samples_required",
            return_value=5,
        ), patch("auto_retrain.send_alert", return_value=None):
            report = auto_retrain.retrain_cycle_report(object(), trigger="manual", started_at=1_700_000_300)

        self.assertEqual(report["status"], "skipped_not_enough_samples")
        load_mock.assert_called_once_with(since_ts=1_700_000_400, routed_only=True)

    def test_retrain_cycle_report_scopes_training_data_to_latest_promotion_within_epoch(self) -> None:
        with patch(
            "auto_retrain.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
        ), patch(
            "auto_retrain._latest_applied_replay_promotion_at",
            return_value=1_700_000_900,
        ), patch(
            "auto_retrain.load_training_data",
            return_value=[object()] * 3,
        ) as load_mock, patch(
            "auto_retrain.min_samples_required",
            return_value=5,
        ), patch("auto_retrain.send_alert", return_value=None):
            report = auto_retrain.retrain_cycle_report(object(), trigger="manual", started_at=1_700_000_300)

        self.assertEqual(report["status"], "skipped_not_enough_samples")
        load_mock.assert_called_once_with(since_ts=1_700_000_900, routed_only=True)

    def test_retrain_cycle_report_alerts_when_model_is_accepted(self) -> None:
        metrics = {
            "deployed": True,
            "brier_score": 0.11,
            "log_loss": 0.44,
            "log_loss_base": 0.51,
            "val_selected_trades": 9,
            "val_total_pnl": 3.25,
            "edge_threshold": 0.02,
            "top_features": [("f_price", 0.42)],
        }
        engine = SimpleNamespace(reload_model=lambda: None)

        with patch(
            "auto_retrain.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
        ), patch("auto_retrain.load_training_data", return_value=[object()] * 12), patch(
            "auto_retrain.min_samples_required", return_value=5
        ), patch("auto_retrain.train", return_value=metrics), patch(
            "auto_retrain.check_calibration", return_value={"calibration_bins": [1, 2, 3]}
        ), patch("auto_retrain.send_alert", return_value=None) as alert_mock, patch("auto_retrain._record_retrain_run"):
            report = auto_retrain.retrain_cycle_report(engine, trigger="scheduled", started_at=1_700_000_150)

        self.assertEqual(report["status"], "deployed")
        alert_mock.assert_called_once()
        self.assertIn("retrain accepted", alert_mock.call_args.args[0])
        self.assertEqual(alert_mock.call_args.kwargs["kind"], "retrain")


if __name__ == "__main__":
    unittest.main()
