from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import train
from economic_model import transform_return_target


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
            }
        )
        holdout_df = pd.DataFrame(
            {
                "f_price": prices,
                "effective_price": prices,
                train.OUTCOME_COL: outcomes,
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

    def test_select_prediction_path_prefers_raw_when_calibration_is_worse(self) -> None:
        calibrated = {"log_loss": 0.91, "brier_score": 0.31}
        raw = {"log_loss": 0.62, "brier_score": 0.22}

        report, path, calibrator, method = train._select_prediction_path(
            calibrated_report=calibrated,
            raw_report=raw,
            probability_calibrator=object(),
            calibration_method="sigmoid",
        )

        self.assertIs(report, raw)
        self.assertEqual(path, "raw")
        self.assertIsNone(calibrator)
        self.assertEqual(method, "identity")

    def test_select_prediction_path_keeps_calibrated_when_it_is_not_worse_on_both_metrics(self) -> None:
        calibrated = {"log_loss": 0.60, "brier_score": 0.24}
        raw = {"log_loss": 0.58, "brier_score": 0.26}
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


if __name__ == "__main__":
    unittest.main()
