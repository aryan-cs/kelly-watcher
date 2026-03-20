from __future__ import annotations

import unittest

import numpy as np

import train


class TrainingSearchTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
