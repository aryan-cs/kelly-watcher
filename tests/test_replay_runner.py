from __future__ import annotations

import json
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import kelly_watcher.research.replay_runner as replay_runner


class ReplayRunnerTest(unittest.TestCase):
    def test_main_keeps_json_stdout_and_writes_segment_summary_to_stderr(self) -> None:
        result = {
            "run_id": 7,
            "segment_leaders": {
                "signal_mode": {
                    "best": {
                        "segment_value": "heuristic",
                        "total_pnl_usd": 12.5,
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "win_rate": 0.75,
                    },
                    "worst": {
                        "segment_value": "xgboost",
                        "total_pnl_usd": -3.0,
                        "accepted_count": 2,
                        "resolved_count": 2,
                        "win_rate": 0.5,
                    },
                }
            },
        }

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(replay_runner, "run_replay", return_value=result) as run_replay_mock,
            patch("sys.argv", ["replay_runner.py"]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_runner.main()

        self.assertEqual(run_replay_mock.call_count, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["result"]["run_id"], 7)
        self.assertIn("Replay segment leaders:", stderr.getvalue())
        self.assertIn("signal_mode: best heuristic (+12.500", stderr.getvalue())
        self.assertIn("worst xgboost (-3.000", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
