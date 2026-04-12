from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import replay_search


class ReplaySearchTest(unittest.TestCase):
    def test_main_ranks_grid_candidates_and_keeps_json_on_stdout(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run_replay(*, policy, db_path=None, label="", notes=""):
            payload = policy.as_dict()
            calls.append(payload)
            total_pnl = float(payload["min_confidence"]) * 100.0 - float(payload["max_bet_fraction"]) * 200.0
            return {
                "run_id": len(calls),
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": float(payload["max_bet_fraction"]),
                "accepted_count": 10,
                "win_rate": float(payload["min_confidence"]),
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps(
                {
                    "min_confidence": [0.55, 0.65],
                    "max_bet_fraction": [0.02, 0.05],
                }
            ),
            "--drawdown-penalty",
            "1.0",
            "--top",
            "2",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["candidate_count"], 4)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        self.assertEqual(payload["ranked"][0]["overrides"]["max_bet_fraction"], 0.02)
        self.assertIn("Replay sweep top candidates:", stderr.getvalue())
        self.assertEqual(len(calls), 4)

    def test_main_filters_infeasible_candidates_from_best_feasible_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes=""):
            payload = policy.as_dict()
            min_conf = float(payload["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 80.0,
                    "max_drawdown_pct": 0.18,
                    "accepted_count": 4,
                    "resolved_count": 4,
                    "win_rate": 0.80,
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 60.0,
                "max_drawdown_pct": 0.05,
                "accepted_count": 12,
                "resolved_count": 12,
                "win_rate": 0.62,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--drawdown-penalty",
            "0.01",
            "--max-drawdown-pct",
            "0.10",
            "--min-accepted-count",
            "5",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["feasible_count"], 1)
        self.assertEqual(payload["rejected_count"], 1)
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        self.assertEqual(payload["ranked"][0]["constraint_failures"], ["accepted_count", "max_drawdown_pct"])
        self.assertIn("Replay sweep rejected candidates:", stderr.getvalue())
        self.assertIn("reject accepted_count,max_drawdown_pct", stderr.getvalue())

    def test_load_grid_rejects_unknown_policy_keys(self) -> None:
        class Args:
            grid_file = ""
            grid_json = '{"not_a_real_key":[1,2]}'

        with self.assertRaisesRegex(ValueError, "Unknown replay policy key"):
            replay_search._load_grid(Args())


if __name__ == "__main__":
    unittest.main()
