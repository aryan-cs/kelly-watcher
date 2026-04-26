from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import joblib
import numpy as np
import pandas as pd

import kelly_watcher.research.train as train
from kelly_watcher.engine.economic_model import transform_return_target


class DummyReturnModel:
    def __init__(self, confidences):
        self._confidences = np.asarray(confidences, dtype=float)

    def predict(self, X):
        prices = np.asarray(X[:, 0], dtype=float)
        expected_return = (self._confidences[: len(prices)] / prices) - 1.0
        transformed = [transform_return_target(value) for value in expected_return]
        return np.asarray(transformed, dtype=float)


class TrainingSearchTest(unittest.TestCase):
    def _comparison_frames(self):
        prices = np.array([0.4] * 24, dtype=float)
        outcomes = np.array([1] * 12 + [0] * 12, dtype=int)
        final_train_df = pd.DataFrame(
            {
                "f_price": np.array([0.4, 0.42, 0.38, 0.41, 0.39, 0.43], dtype=float),
                "effective_price": np.array([0.4, 0.42, 0.38, 0.41, 0.39, 0.43], dtype=float),
                train.OUTCOME_COL: np.array([1, 0, 1, 0, 1, 0], dtype=int),
                train.RETURN_COL: np.array([1.5, -1.0, 1.631579, -1.0, 1.564103, -1.0], dtype=float),
            }
        )
        holdout_df = pd.DataFrame(
            {
                "f_price": prices,
                "effective_price": prices,
                train.OUTCOME_COL: outcomes,
                train.RETURN_COL: np.where(outcomes == 1, 1.5, -1.0),
            }
        )
        return final_train_df, holdout_df

    def test_build_training_plan_creates_two_search_folds_plus_final_holdout(self) -> None:
        plan = train._build_training_plan(170)

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan.search_windows), 2)

        fold1, fold2 = plan.search_windows
        self.assertLess(fold1.train_end, fold1.cal_end)
        self.assertLess(fold1.cal_end, fold1.eval_end)
        self.assertEqual(fold2.train_end, fold1.eval_end)
        self.assertLess(fold2.train_end, fold2.cal_end)
        self.assertLess(fold2.cal_end, fold2.eval_end)
        self.assertEqual(fold2.eval_end, plan.final_train_end)
        self.assertLess(plan.final_train_end, plan.final_cal_end)
        self.assertEqual(plan.holdout_end, 170)

    def test_build_training_plan_rejects_short_history(self) -> None:
        self.assertIsNone(train._build_training_plan(100))

    def test_aggregate_search_reports_marks_profitable_candidate_as_passed(self) -> None:
        profitable_preds = np.array([0.82] * 6 + [0.18] * 6, dtype=float)
        profitable_prices = np.array([0.40] * 12, dtype=float)
        profitable_outcomes = np.array([1] * 6 + [0] * 6, dtype=int)
        fold_reports = [
            {
                "n_eval": 12,
                "log_loss": 0.22,
                "log_loss_base": 0.61,
                "brier_score": 0.09,
                "brier_base": 0.24,
                "preds": profitable_preds,
                "prices": profitable_prices,
                "outcomes": profitable_outcomes,
                "returns": np.where(profitable_outcomes == 1, 1.5, -1.0),
            },
            {
                "n_eval": 12,
                "log_loss": 0.24,
                "log_loss_base": 0.63,
                "brier_score": 0.10,
                "brier_base": 0.25,
                "preds": profitable_preds,
                "prices": profitable_prices,
                "outcomes": profitable_outcomes,
                "returns": np.where(profitable_outcomes == 1, 1.5, -1.0),
            },
        ]

        report = train._aggregate_search_reports(fold_reports)

        self.assertTrue(report["search_beats_baseline"])
        self.assertTrue(report["search_passed"])
        self.assertGreater(report["search_selected_trades"], 0)
        self.assertGreater(report["search_total_pnl"], 0)

    def test_candidate_rank_key_prefers_search_pass_then_metrics(self) -> None:
        losing_but_passed = {
            "search_passed": True,
            "search_beats_baseline": True,
            "search_total_pnl": 1.0,
            "search_avg_pnl": 0.1,
            "search_log_loss": 0.3,
            "search_brier_score": 0.1,
            "search_selected_trades": 8,
        }
        stronger_but_failed = {
            "search_passed": False,
            "search_beats_baseline": True,
            "search_total_pnl": 9.0,
            "search_avg_pnl": 0.4,
            "search_log_loss": 0.1,
            "search_brier_score": 0.05,
            "search_selected_trades": 12,
        }

        self.assertGreater(
            train._candidate_rank_key(losing_but_passed),
            train._candidate_rank_key(stronger_but_failed),
        )

    def test_should_deploy_candidate_uses_standard_gate_when_all_checks_pass(self) -> None:
        deployable, mode = train._should_deploy_candidate(
            best_candidate={
                "search_passed": True,
                "search_selected_trades": 24,
                "search_total_pnl": 9.0,
                "search_avg_pnl": 0.3,
            },
            holdout_report={
                "selected_trades": 28,
                "total_pnl": 5.0,
                "avg_pnl": 0.18,
            },
            beats_baseline=True,
            incumbent_present=True,
            incumbent_runtime_compatible=True,
            beats_incumbent=True,
        )

        self.assertTrue(deployable)
        self.assertEqual(mode, "standard")

    def test_should_deploy_candidate_rejects_recovery_without_search_gate(self) -> None:
        deployable, mode = train._should_deploy_candidate(
            best_candidate={
                "search_passed": False,
                "search_selected_trades": 30,
                "search_total_pnl": 12.0,
                "search_avg_pnl": 0.4,
            },
            holdout_report={
                "selected_trades": 30,
                "total_pnl": 18.7,
                "avg_pnl": 0.62,
            },
            beats_baseline=False,
            incumbent_present=True,
            incumbent_runtime_compatible=False,
            beats_incumbent=True,
        )

        self.assertFalse(deployable)
        self.assertEqual(mode, "rejected")

    def test_should_deploy_candidate_allows_standard_gate_recovery_for_incompatible_incumbent(self) -> None:
        deployable, mode = train._should_deploy_candidate(
            best_candidate={
                "search_passed": True,
                "search_selected_trades": 24,
                "search_total_pnl": 9.0,
                "search_avg_pnl": 0.3,
            },
            holdout_report={
                "selected_trades": 27,
                "total_pnl": 8.5,
                "avg_pnl": 0.31,
            },
            beats_baseline=True,
            incumbent_present=True,
            incumbent_runtime_compatible=False,
            beats_incumbent=False,
        )

        self.assertTrue(deployable)
        self.assertEqual(mode, "recovery_incompatible_incumbent_standard_gate")

    def test_should_deploy_candidate_does_not_use_recovery_without_incumbent(self) -> None:
        deployable, mode = train._should_deploy_candidate(
            best_candidate={
                "search_passed": False,
                "search_selected_trades": 30,
                "search_total_pnl": 12.0,
                "search_avg_pnl": 0.4,
            },
            holdout_report={
                "selected_trades": 30,
                "total_pnl": 18.7,
                "avg_pnl": 0.62,
            },
            beats_baseline=False,
            incumbent_present=False,
            incumbent_runtime_compatible=False,
            beats_incumbent=True,
        )

        self.assertFalse(deployable)
        self.assertEqual(mode, "rejected")

    def test_artifact_runtime_compatible_rejects_untrusted_training_provenance(self) -> None:
        artifact = {
            "data_contract_version": train.DATA_CONTRACT_VERSION,
            "label_mode": train.MODEL_LABEL_MODE,
            "training_scope": "current_evidence_window",
            "training_since_ts": 1_700_000_400,
            "training_routed_only": "false",
            "training_provenance_trusted": "true",
        }

        with mock.patch(
            "kelly_watcher.research.train.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
        ):
            self.assertFalse(train._artifact_runtime_compatible(artifact))

    def test_training_provenance_treats_string_false_routed_only_as_untrusted(self) -> None:
        provenance = train._training_provenance_payload(
            since_ts=1_700_000_400,
            routed_only="false",  # type: ignore[arg-type]
        )

        self.assertEqual(provenance["training_scope"], "since_ts")
        self.assertFalse(provenance["training_routed_only"])
        self.assertFalse(provenance["training_provenance_trusted"])
        self.assertEqual(
            provenance["training_provenance_block_reason"],
            "artifact training scope included non-routed shadow history",
        )

    def test_artifact_runtime_compatible_accepts_current_routed_provenance(self) -> None:
        artifact = {
            "data_contract_version": train.DATA_CONTRACT_VERSION,
            "label_mode": train.MODEL_LABEL_MODE,
            "training_scope": "current_evidence_window",
            "training_since_ts": 1_700_000_500,
            "training_routed_only": True,
            "training_provenance_trusted": True,
        }

        with mock.patch(
            "kelly_watcher.research.train.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
        ):
            self.assertTrue(train._artifact_runtime_compatible(artifact))

    def test_select_prediction_path_does_not_switch_on_final_holdout_metrics(self) -> None:
        calibrated = {"log_loss": 0.91, "brier_score": 0.31}
        raw = {"log_loss": 0.62, "brier_score": 0.22}
        calibrator_obj = object()

        report, path, calibrator, method = train._select_prediction_path(
            calibrated_report=calibrated,
            raw_report=raw,
            probability_calibrator=calibrator_obj,
            calibration_method="sigmoid",
        )

        self.assertIs(report, calibrated)
        self.assertEqual(path, "calibrated")
        self.assertIs(calibrator, calibrator_obj)
        self.assertEqual(method, "sigmoid")

    def test_select_prediction_path_uses_raw_when_no_calibrator_was_trained(self) -> None:
        calibrated = {"log_loss": 0.60, "brier_score": 0.24}
        raw = {"log_loss": 0.58, "brier_score": 0.26}

        report, path, calibrator, method = train._select_prediction_path(
            calibrated_report=calibrated,
            raw_report=raw,
            probability_calibrator=None,
            calibration_method="sigmoid",
        )

        self.assertIs(report, raw)
        self.assertEqual(path, "raw")
        self.assertIsNone(calibrator)
        self.assertEqual(method, "identity")

    def test_train_keeps_search_selected_calibration_path_without_holdout_switching(self) -> None:
        rows = 30
        df = pd.DataFrame(
            {
                "label_ts": np.arange(rows, dtype=float),
                "effective_price": np.full(rows, 0.5, dtype=float),
                "f_price": np.linspace(0.4, 0.6, rows),
                train.LABEL_COL: np.linspace(-0.1, 0.1, rows),
                train.OUTCOME_COL: np.array([0, 1] * (rows // 2), dtype=int),
                train.RETURN_COL: np.linspace(-0.2, 0.2, rows),
                train.SAMPLE_WEIGHT_COL: np.ones(rows, dtype=float),
                "skipped": np.zeros(rows, dtype=bool),
            }
        )
        calibrated_report = {
            "n_eval": 10,
            "log_loss": 0.91,
            "log_loss_base": 0.80,
            "brier_score": 0.31,
            "brier_base": 0.25,
            "beats_baseline": False,
            "selected_trades": 21,
            "total_pnl": 5.0,
            "avg_pnl": 0.2,
            "win_rate": 0.55,
            "edge_threshold": 0.04,
            "preds": np.full(10, 0.6, dtype=float),
        }
        raw_report = calibrated_report | {
            "log_loss": 0.62,
            "brier_score": 0.22,
            "beats_baseline": True,
            "preds": np.full(10, 0.55, dtype=float),
        }
        candidate = {
            "name": "stub",
            "backend": "stub_backend",
            "search_edge_threshold": 0.04,
            "search_log_loss": 0.60,
            "search_log_loss_base": 0.70,
            "search_brier_score": 0.20,
            "search_brier_base": 0.24,
            "search_beats_baseline": True,
            "search_passed": True,
            "search_selected_trades": 12,
            "search_total_pnl": 3.0,
            "search_avg_pnl": 0.25,
            "search_win_rate": 0.58,
        }

        with mock.patch("kelly_watcher.research.train.min_samples_required", return_value=1), mock.patch(
            "kelly_watcher.research.train._select_feature_cols", return_value=["f_price"]
        ), mock.patch(
            "kelly_watcher.research.train._build_training_plan",
            return_value=train.TrainingPlan(
                search_windows=(),
                final_train_end=10,
                final_cal_end=20,
                holdout_end=30,
            ),
        ), mock.patch("kelly_watcher.research.train._candidate_specs", return_value=[{"name": "stub"}]), mock.patch(
            "kelly_watcher.research.train._evaluate_candidate_spec", return_value=candidate
        ), mock.patch(
            "kelly_watcher.research.train._fit_calibrated_model",
            return_value={
                "base_model": object(),
                "probability_calibrator": object(),
                "backend": "stub_backend",
                "requested_calibration_mode": "auto",
                "calibration_method": "sigmoid",
            },
        ), mock.patch(
            "kelly_watcher.research.train._evaluate_window",
            side_effect=[calibrated_report, raw_report],
        ), mock.patch("kelly_watcher.research.train._feature_ranking", return_value={"f_price": 1.0}), mock.patch(
            "kelly_watcher.research.train._load_model_artifact", return_value=None
        ), mock.patch(
            "kelly_watcher.research.train._compare_against_incumbent",
            return_value={"incumbent_present": False, "beats_incumbent": True},
        ), mock.patch(
            "kelly_watcher.research.train._should_deploy_candidate", return_value=(False, "rejected")
        ):
            metrics = train.train(df)

        self.assertEqual(metrics["selected_prediction_path"], "calibrated")
        self.assertEqual(metrics["calibration_method"], "sigmoid")
        self.assertEqual(metrics["log_loss"], round(calibrated_report["log_loss"], 4))
        self.assertEqual(metrics["brier_score"], round(calibrated_report["brier_score"], 4))

    def test_score_predictions_handles_single_class_eval_windows(self) -> None:
        outcomes = np.array([1, 1, 1, 1, 1], dtype=int)
        preds = np.array([0.72, 0.75, 0.78, 0.81, 0.76], dtype=float)
        prices = np.array([0.55, 0.56, 0.57, 0.58, 0.59], dtype=float)

        report = train._score_predictions(
            preds=preds,
            outcomes=outcomes,
            prices=prices,
            returns=np.array([(1 - price) / price for price in prices], dtype=float),
            baseline_rate=1.0,
        )

        self.assertEqual(report["n_eval"], 5)
        self.assertIn("log_loss", report)
        self.assertIn("brier_score", report)

    def test_score_predictions_can_use_preselected_edge_threshold_without_holdout_tuning(self) -> None:
        outcomes = np.array([0] * 10 + [1] * 10, dtype=int)
        prices = np.array([0.50] * 20, dtype=float)
        preds = np.array([0.51] * 10 + [0.80] * 10, dtype=float)

        optimized = train._score_predictions(
            preds=preds,
            outcomes=outcomes,
            prices=prices,
            returns=np.where(outcomes == 1, 1.0, -1.0),
            baseline_rate=0.5,
        )
        fixed = train._score_predictions(
            preds=preds,
            outcomes=outcomes,
            prices=prices,
            returns=np.where(outcomes == 1, 1.0, -1.0),
            baseline_rate=0.5,
            fixed_edge_threshold=0.0,
        )

        self.assertGreater(optimized["edge_threshold"], 0.0)
        self.assertEqual(fixed["edge_threshold"], 0.0)
        self.assertEqual(fixed["selected_trades"], 20)
        self.assertLess(fixed["total_pnl"], optimized["total_pnl"])

    def test_score_predictions_uses_realized_returns_for_decision_pnl(self) -> None:
        outcomes = np.array([1, 1, 1, 1, 1], dtype=int)
        prices = np.array([0.50, 0.50, 0.50, 0.50, 0.50], dtype=float)
        preds = np.array([0.80, 0.81, 0.82, 0.83, 0.84], dtype=float)
        realized_returns = np.array([-0.10, -0.20, -0.30, -0.40, -0.50], dtype=float)

        report = train._score_predictions(
            preds=preds,
            outcomes=outcomes,
            prices=prices,
            returns=realized_returns,
            baseline_rate=1.0,
            fixed_edge_threshold=0.0,
        )

        self.assertEqual(report["selected_trades"], 5)
        self.assertAlmostEqual(report["total_pnl"], -1.5, places=6)
        self.assertAlmostEqual(report["avg_pnl"], -0.3, places=6)

    def test_fit_calibrated_model_falls_back_to_identity_when_calibration_window_is_single_class(self) -> None:
        class StubRegressor:
            def fit(self, X, y, sample_weight=None):
                return self

            def predict(self, X):
                return np.full(len(X), transform_return_target(0.15), dtype=float)

        train_df = pd.DataFrame(
            {
                "f_price": np.array([0.42, 0.47, 0.53, 0.58], dtype=float),
                train.LABEL_COL: np.array([transform_return_target(0.10), transform_return_target(0.12), transform_return_target(0.08), transform_return_target(0.09)], dtype=float),
                train.OUTCOME_COL: np.array([1, 1, 1, 1], dtype=int),
                train.SAMPLE_WEIGHT_COL: np.ones(4, dtype=float),
                "effective_price": np.array([0.42, 0.47, 0.53, 0.58], dtype=float),
            }
        )
        cal_df = pd.DataFrame(
            {
                "f_price": np.array([0.44, 0.49, 0.51], dtype=float),
                train.LABEL_COL: np.array([transform_return_target(0.11), transform_return_target(0.10), transform_return_target(0.12)], dtype=float),
                train.OUTCOME_COL: np.array([1, 1, 1], dtype=int),
                train.SAMPLE_WEIGHT_COL: np.ones(3, dtype=float),
                "effective_price": np.array([0.44, 0.49, 0.51], dtype=float),
            }
        )

        with mock.patch("kelly_watcher.research.train._build_regressor", return_value=(StubRegressor(), "hist_gradient_boosting")):
            fit_result = train._fit_calibrated_model(
                spec={"backend": "hist_gradient_boosting", "params": {}, "calibration_mode": "auto"},
                train_df=train_df,
                cal_df=cal_df,
                feature_cols=["f_price"],
            )

        self.assertIsNotNone(fit_result)
        assert fit_result is not None
        self.assertEqual(fit_result["calibration_method"], "identity")
        self.assertIsNone(fit_result["probability_calibrator"])

    def test_fit_probability_calibrator_rejects_unknown_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported calibration mode"):
            train._fit_probability_calibrator(
                np.array([0.35, 0.65], dtype=float),
                np.array([0, 1], dtype=int),
                np.ones(2, dtype=float),
                requested_mode="magic",
            )

    def test_dump_model_artifact_atomic_writes_loadable_artifact(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.joblib"

            train._dump_model_artifact_atomic({"value": 7}, model_path)

            self.assertEqual(joblib.load(model_path)["value"], 7)
            self.assertFalse(list(model_path.parent.glob("*.tmp")))

    def test_dump_model_artifact_atomic_preserves_existing_artifact_on_failure(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.joblib"
            model_path.write_text("existing-artifact", encoding="utf-8")

            with mock.patch("joblib.dump", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    train._dump_model_artifact_atomic({"value": 8}, model_path)

            self.assertEqual(model_path.read_text(encoding="utf-8"), "existing-artifact")
            self.assertFalse(list(model_path.parent.glob("*.tmp")))

    def test_compare_against_incumbent_passes_when_no_model_is_live(self) -> None:
        final_train_df, holdout_df = self._comparison_frames()

        report = train._compare_against_incumbent(
            incumbent_artifact=None,
            final_train_df=final_train_df,
            holdout_df=holdout_df,
            challenger_model=DummyReturnModel([0.8] * 12 + [0.2] * 12),
            challenger_feature_cols=["f_price"],
            challenger_probability_calibrator=None,
            challenger_prediction_mode="expected_return",
        )

        self.assertFalse(report["incumbent_present"])
        self.assertTrue(report["beats_incumbent"])

    def test_compare_against_incumbent_requires_shared_holdout_improvement(self) -> None:
        final_train_df, holdout_df = self._comparison_frames()

        report = train._compare_against_incumbent(
            incumbent_artifact={
                "model": DummyReturnModel([0.65] * 12 + [0.35] * 12),
                "probability_calibrator": None,
                "prediction_mode": "expected_return",
                "feature_cols": ["f_price"],
                "path": "model.joblib",
            },
            final_train_df=final_train_df,
            holdout_df=holdout_df,
            challenger_model=DummyReturnModel([0.8] * 12 + [0.2] * 12),
            challenger_feature_cols=["f_price"],
            challenger_probability_calibrator=None,
            challenger_prediction_mode="expected_return",
        )

        self.assertTrue(report["incumbent_present"])
        self.assertTrue(report["beats_incumbent"])
        self.assertLess(report["challenger_shared_log_loss"], report["incumbent_log_loss"])
        self.assertLess(report["challenger_shared_brier_score"], report["incumbent_brier_score"])

    def test_compare_against_incumbent_rejects_non_improving_challenger(self) -> None:
        final_train_df, holdout_df = self._comparison_frames()

        report = train._compare_against_incumbent(
            incumbent_artifact={
                "model": DummyReturnModel([0.8] * 12 + [0.2] * 12),
                "probability_calibrator": None,
                "prediction_mode": "expected_return",
                "feature_cols": ["f_price"],
                "path": "model.joblib",
            },
            final_train_df=final_train_df,
            holdout_df=holdout_df,
            challenger_model=DummyReturnModel([0.65] * 12 + [0.35] * 12),
            challenger_feature_cols=["f_price"],
            challenger_probability_calibrator=None,
            challenger_prediction_mode="expected_return",
        )

        self.assertFalse(report["beats_incumbent"])
        self.assertIn("challenger did not beat the deployed model", report["reject_reason"])

    def test_compare_against_incumbent_rejects_lower_shared_pnl_challenger(self) -> None:
        final_train_df, holdout_df = self._comparison_frames()
        challenger_report = {
            "n_eval": len(holdout_df),
            "log_loss": 0.20,
            "brier_score": 0.08,
            "total_pnl": 1.0,
            "avg_pnl": 0.10,
            "selected_trades": 10,
        }
        incumbent_report = {
            "n_eval": len(holdout_df),
            "log_loss": 0.25,
            "brier_score": 0.10,
            "total_pnl": 2.0,
            "avg_pnl": 0.20,
            "selected_trades": 10,
        }

        with mock.patch(
            "kelly_watcher.research.train._evaluate_prediction_report",
            side_effect=[challenger_report, incumbent_report],
        ):
            report = train._compare_against_incumbent(
                incumbent_artifact={
                    "model": object(),
                    "probability_calibrator": None,
                    "prediction_mode": "expected_return",
                    "feature_cols": ["f_price"],
                    "path": "model.joblib",
                },
                final_train_df=final_train_df,
                holdout_df=holdout_df,
                challenger_model=object(),
                challenger_feature_cols=["f_price"],
                challenger_probability_calibrator=None,
                challenger_prediction_mode="expected_return",
            )

        self.assertFalse(report["beats_incumbent"])
        self.assertIn("regressed selected-trade P&L", report["reject_reason"])


if __name__ == "__main__":
    unittest.main()
