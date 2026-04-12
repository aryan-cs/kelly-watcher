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

    def test_main_can_aggregate_multiple_time_windows(self) -> None:
        calls: list[tuple[int | None, int | None]] = []

        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            calls.append((start_ts, end_ts))
            pnl = 20.0 if start_ts == 1 else -5.0
            return {
                "run_id": len(calls),
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": pnl,
                "max_drawdown_pct": 0.04 if pnl > 0 else 0.08,
                "accepted_count": 6,
                "resolved_count": 6,
                "rejected_count": 1,
                "unresolved_count": 0,
                "trade_count": 7,
                "win_rate": 2 / 3 if pnl > 0 else 1 / 3,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60]}),
            "--window-days",
            "30",
            "--window-count",
            "2",
            "--min-positive-windows",
            "1",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=5_184_000),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["candidate_count"], 1)
        self.assertEqual(payload["best_feasible"]["result"]["window_count"], 2)
        self.assertEqual(payload["best_feasible"]["result"]["positive_window_count"], 1)
        self.assertEqual(payload["best_feasible"]["result"]["total_pnl_usd"], 15.0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], (1, 2_592_001))
        self.assertEqual(calls[1], (2_592_001, 5_184_001))

    def test_main_can_penalize_window_instability(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                pnl = 100.0 if start_ts == 1 else -20.0
            else:
                pnl = 40.0 if start_ts == 1 else 35.0
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": pnl,
                "max_drawdown_pct": 0.06,
                "accepted_count": 10,
                "resolved_count": 10,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--window-days",
            "30",
            "--window-count",
            "2",
            "--window-stddev-penalty",
            "1.0",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=5_184_000),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.6)
        self.assertIn("windows 2/2+", stderr.getvalue())

    def test_main_can_reject_bad_worst_window(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            pnl = -25.0 if min_conf >= 0.65 and start_ts != 1 else 20.0
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": pnl,
                "max_drawdown_pct": 0.05 if pnl > 0 else 0.14,
                "accepted_count": 10,
                "resolved_count": 10,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--window-days",
            "30",
            "--window-count",
            "2",
            "--min-worst-window-pnl-usd",
            "-10",
            "--max-worst-window-drawdown-pct",
            "0.10",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=5_184_000),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        self.assertEqual(rejected["constraint_failures"], ["worst_window_pnl_usd", "worst_window_drawdown_pct"])
        self.assertIn("reject worst_window_pnl_usd,worst_window_drawdown_pct", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
