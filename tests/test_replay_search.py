from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import db
import replay_search


class ReplaySearchTest(unittest.TestCase):
    def test_db_init_db_backfills_replay_search_run_columns_for_dashboard_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                try:
                    columns = {
                        str(row["name"])
                        for row in conn.execute("PRAGMA table_info(replay_search_runs)").fetchall()
                    }
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path

        self.assertIn("current_candidate_constraint_failures_json", columns)
        self.assertIn("current_candidate_result_json", columns)
        self.assertIn("pause_guard_penalty", columns)
        self.assertIn("resolved_share_penalty", columns)
        self.assertIn("worst_window_resolved_share_penalty", columns)
        self.assertIn("mode_resolved_share_penalty", columns)
        self.assertIn("mode_worst_window_resolved_share_penalty", columns)
        self.assertIn("mode_loss_penalty", columns)
        self.assertIn("mode_inactivity_penalty", columns)
        self.assertIn("wallet_concentration_penalty", columns)
        self.assertIn("market_concentration_penalty", columns)
        self.assertIn("entry_price_band_concentration_penalty", columns)
        self.assertIn("time_to_close_band_concentration_penalty", columns)

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
        self.assertEqual(payload["current_candidate"]["index"], 0)
        self.assertFalse(payload["current_candidate_matches_grid"])
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        self.assertEqual(payload["ranked"][0]["overrides"]["max_bet_fraction"], 0.02)
        self.assertEqual(payload["ranked"][0]["config"]["MIN_CONFIDENCE"], 0.65)
        self.assertEqual(payload["ranked"][0]["config"]["MAX_BET_FRACTION"], 0.02)
        self.assertIn("Replay sweep top candidates:", stderr.getvalue())
        self.assertEqual(len(calls), 5)

    def test_main_single_window_materializes_window_activity_fields(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes=""):
            min_conf = float(policy.as_dict()["min_confidence"])
            return {
                "run_id": 1,
                "total_pnl_usd": 12.0 if min_conf >= 0.65 else 10.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 5,
                "resolved_count": 5,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 5,
                "win_rate": 0.6,
            }

        stdout = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        result = payload["best_feasible"]["result"]
        self.assertEqual(result["window_count"], 1)
        self.assertEqual(result["positive_window_count"], 1)
        self.assertEqual(result["negative_window_count"], 0)
        self.assertEqual(result["active_window_count"], 1)
        self.assertEqual(result["inactive_window_count"], 0)

    def test_score_breakdown_ignores_disabled_scorer_inactivity(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 0,
                        "resolved_count": 0,
                        "trade_count": 0,
                        "total_pnl_usd": 0.0,
                        "inactive_window_count": 2,
                    }
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.25,
            allow_heuristic=True,
            allow_xgboost=False,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_inactivity_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_ignores_disabled_scorer_losses(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "trade_count": 5,
                        "total_pnl_usd": -12.0,
                    }
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_loss_penalty=1.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=False,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_loss_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_ignores_resolved_share_penalty_without_accepted_trades(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 0,
                "resolved_count": 0,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.5,
            worst_window_resolved_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["resolved_share_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_ignores_worst_window_resolved_share_penalty_without_accepted_trades(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 0,
                "resolved_count": 0,
                "worst_window_resolved_share": 0.0,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.5,
            mode_resolved_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["worst_window_resolved_share_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_ignores_mode_resolved_share_penalty_without_mode_activity(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 8,
                "resolved_count": 8,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 0,
                        "resolved_count": 0,
                        "trade_count": 0,
                        "total_pnl_usd": 0.0,
                    }
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            mode_resolved_share_penalty=0.5,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_resolved_share_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_ignores_mode_worst_window_resolved_share_penalty_without_mode_activity(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 8,
                "resolved_count": 8,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 0,
                        "resolved_count": 0,
                        "trade_count": 0,
                        "total_pnl_usd": 0.0,
                        "worst_window_resolved_share": 0.0,
                    }
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.5,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_worst_window_resolved_share_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_uses_active_mode_worst_window_resolved_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 8,
                "resolved_count": 8,
                "window_count": 2,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": 20.0,
                        "inactive_window_count": 1,
                        "worst_window_resolved_share": 0.0,
                        "worst_active_window_resolved_share": 1.0,
                    }
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.5,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=False,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_worst_window_resolved_share_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_constraint_failures_ignores_disabled_scorer_guards(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "win_rate": 0.625,
                "max_drawdown_pct": 0.04,
                "worst_window_pnl_usd": 40.0,
                "worst_window_drawdown_pct": 0.04,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 8,
                        "resolved_count": 8,
                        "trade_count": 8,
                        "total_pnl_usd": 40.0,
                        "win_count": 5,
                        "positive_window_count": 1,
                    }
                },
            },
            allow_heuristic=False,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=1,
            min_xgboost_accepted_count=1,
            min_heuristic_resolved_count=1,
            min_xgboost_resolved_count=1,
            min_heuristic_win_rate=0.5,
            min_xgboost_win_rate=0.5,
            min_heuristic_resolved_share=0.5,
            min_xgboost_resolved_share=0.5,
            min_heuristic_pnl_usd=1.0,
            min_xgboost_pnl_usd=1.0,
            min_heuristic_worst_window_pnl_usd=1.0,
            min_xgboost_worst_window_pnl_usd=1.0,
            min_heuristic_worst_window_resolved_share=0.5,
            min_xgboost_worst_window_resolved_share=0.5,
            min_heuristic_positive_window_count=1,
            min_xgboost_positive_window_count=1,
            max_heuristic_inactive_window_count=0,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.5,
            min_xgboost_accepted_share=0.5,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
        )

        self.assertEqual(failures, [])

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
            if min_conf >= 0.60:
                return {
                    "run_id": 1,
                    "total_pnl_usd": 60.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 12,
                    "resolved_count": 12,
                    "win_rate": 0.62,
                }
            return {
                "run_id": 0,
                "total_pnl_usd": 40.0,
                "max_drawdown_pct": 0.05,
                "accepted_count": 12,
                "resolved_count": 12,
                "win_rate": 0.58,
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
        self.assertEqual(payload["current_candidate"]["overrides"], {})
        self.assertEqual(payload["best_feasible_config"]["MIN_CONFIDENCE"], 0.6)
        self.assertEqual(payload["best_vs_current_pnl_usd"], 20.0)
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

    def test_main_supports_list_valued_segment_filter_overrides(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes=""):
            horizon_bands = tuple(policy.as_dict()["allowed_time_to_close_bands"])
            pnl = 70.0 if horizon_bands == ("2h-12h",) else 30.0
            return {
                "run_id": 1,
                "total_pnl_usd": pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps(
                {
                    "allowed_time_to_close_bands": [
                        ["<=5m"],
                        ["2h-12h"],
                    ]
                }
            ),
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["best_feasible"]["overrides"]["allowed_time_to_close_bands"], ["2h-12h"])
        self.assertEqual(payload["best_feasible"]["result"]["total_pnl_usd"], 70.0)
        self.assertEqual(payload["best_feasible_config"]["ALLOWED_TIME_TO_CLOSE_BANDS"], "2h-12h")
        self.assertIn("allowed_time_to_close_bands=['2h-12h']", stderr.getvalue())

    def test_main_maps_global_entry_band_overrides_into_config_payload(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes=""):
            entry_bands = tuple(policy.as_dict()["allowed_entry_price_bands"])
            pnl = 80.0 if entry_bands == (">=0.70",) else 25.0
            return {
                "run_id": 1,
                "total_pnl_usd": pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps(
                {
                    "allowed_entry_price_bands": [
                        ["0.60-0.69"],
                        [">=0.70"],
                    ]
                }
            ),
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["best_feasible"]["overrides"]["allowed_entry_price_bands"], [">=0.70"])
        self.assertEqual(payload["best_feasible_config"]["ALLOWED_ENTRY_PRICE_BANDS"], ">=0.70")
        self.assertIn("allowed_entry_price_bands=['>=0.70']", stderr.getvalue())

    def test_main_maps_scorer_toggle_overrides_into_config_payload(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes=""):
            allow_heuristic = bool(policy.as_dict()["allow_heuristic"])
            pnl = 75.0 if not allow_heuristic else 20.0
            return {
                "run_id": 1,
                "total_pnl_usd": pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps(
                {
                    "allow_heuristic": [True, False],
                }
            ),
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["best_feasible"]["overrides"]["allow_heuristic"], False)
        self.assertEqual(payload["best_feasible_config"]["ALLOW_HEURISTIC"], False)
        self.assertIn("allow_heuristic=False", stderr.getvalue())

    def test_main_supports_mode_specific_horizon_overrides(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes=""):
            min_horizon = int(policy.as_dict()["heuristic_min_time_to_close_seconds"])
            pnl = 90.0 if min_horizon == 3600 else 45.0
            return {
                "run_id": 1,
                "total_pnl_usd": pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps(
                {
                    "heuristic_min_time_to_close_seconds": [0, 3600],
                }
            ),
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["best_feasible"]["overrides"]["heuristic_min_time_to_close_seconds"], 3600)
        self.assertEqual(payload["best_feasible_config"]["HEURISTIC_MIN_TIME_TO_CLOSE"], "1h")
        self.assertIn("heuristic_min_time_to_close_seconds=3600", stderr.getvalue())

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
                "signal_mode_summary": {
                    "heuristic": {
                        "trade_count": 4,
                        "accepted_count": 4 if pnl > 0 else 2,
                        "resolved_count": 4 if pnl > 0 else 2,
                        "total_pnl_usd": 18.0 if pnl > 0 else -6.0,
                        "win_count": 3 if pnl > 0 else 1,
                    },
                    "model": {
                        "trade_count": 3,
                        "accepted_count": 2 if pnl > 0 else 3,
                        "resolved_count": 2 if pnl > 0 else 3,
                        "total_pnl_usd": 2.0 if pnl > 0 else 1.0,
                        "win_count": 1 if pnl > 0 else 1,
                    },
                },
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
        self.assertEqual(payload["best_feasible"]["result"]["signal_mode_summary"]["heuristic"]["accepted_count"], 6)
        self.assertEqual(payload["best_feasible"]["result"]["signal_mode_summary"]["xgboost"]["accepted_count"], 5)
        self.assertEqual(payload["best_feasible"]["result"]["signal_mode_summary"]["xgboost"]["win_count"], 2)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0], (1, 2_592_001))
        self.assertEqual(calls[1], (2_592_001, 5_184_001))
        self.assertEqual(calls[2], (1, 2_592_001))
        self.assertEqual(calls[3], (2_592_001, 5_184_001))

    def test_main_can_require_mode_specific_accepted_counts(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 70.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 12,
                    "resolved_count": 12,
                    "win_rate": 0.7,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 18.0, "win_count": 3},
                        "xgboost": {"accepted_count": 7, "resolved_count": 7, "trade_count": 7, "total_pnl_usd": 52.0, "win_count": 5},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 80.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 12,
                "resolved_count": 12,
                "win_rate": 0.68,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 12, "resolved_count": 12, "trade_count": 12, "total_pnl_usd": 80.0, "win_count": 8},
                    "xgboost": {"accepted_count": 0, "resolved_count": 0, "trade_count": 0, "total_pnl_usd": 0.0, "win_count": 0},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--min-heuristic-accepted-count",
            "4",
            "--min-xgboost-accepted-count",
            "4",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["xgboost_accepted_count"])
        self.assertEqual(payload["constraints"]["min_heuristic_accepted_count"], 4)
        self.assertEqual(payload["constraints"]["min_xgboost_accepted_count"], 4)
        self.assertIn("modes heur 5 (42%) / xgb 7 (58%)", stderr.getvalue())

    def test_main_can_require_mode_specific_accepted_shares(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 68.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "win_rate": 0.7,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 16.0, "win_count": 3},
                        "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": 52.0, "win_count": 4},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 82.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.68,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 8, "resolved_count": 8, "trade_count": 8, "total_pnl_usd": 64.0, "win_count": 6},
                    "xgboost": {"accepted_count": 2, "resolved_count": 2, "trade_count": 2, "total_pnl_usd": 18.0, "win_count": 1},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--max-heuristic-accepted-share",
            "0.60",
            "--min-xgboost-accepted-share",
            "0.40",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["heuristic_accepted_share", "xgboost_accepted_share"])
        self.assertEqual(payload["constraints"]["max_heuristic_accepted_share"], 0.6)
        self.assertEqual(payload["constraints"]["min_xgboost_accepted_share"], 0.4)
        self.assertIn("modes heur 4 (40%) / xgb 6 (60%)", stderr.getvalue())

    def test_main_can_limit_pause_guard_reject_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 62.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 1,
                    "trade_count": 10,
                    "win_rate": 0.625,
                    "reject_reason_summary": {"daily_loss_guard": 1},
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 14.0, "win_count": 2},
                        "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 48.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 70.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 7,
                "resolved_count": 7,
                "rejected_count": 3,
                "trade_count": 10,
                "win_rate": 4 / 7,
                "reject_reason_summary": {"daily_loss_guard": 2, "live_drawdown_guard": 1},
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 24.0, "win_count": 3},
                    "xgboost": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 46.0, "win_count": 1},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--max-pause-guard-reject-share",
            "0.20",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["pause_guard_reject_share"])
        self.assertEqual(payload["constraints"]["max_pause_guard_reject_share"], 0.2)
        self.assertIn("pause 10%", stderr.getvalue())

    def test_main_can_limit_top_trader_concentration(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 66.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "win_rate": 0.6,
                    "trader_concentration": {
                        "trader_count": 4,
                        "top_accepted_trader_address": "0xbbb",
                        "top_accepted_count": 4,
                        "top_accepted_share": 0.40,
                        "top_accepted_total_pnl_usd": 18.0,
                        "top_abs_pnl_trader_address": "0xccc",
                        "top_abs_pnl_usd": 30.0,
                        "top_abs_pnl_share": 0.45,
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 74.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
                "trader_concentration": {
                    "trader_count": 2,
                    "top_accepted_trader_address": "0xaaa",
                    "top_accepted_count": 7,
                    "top_accepted_share": 0.70,
                    "top_accepted_total_pnl_usd": 52.0,
                    "top_abs_pnl_trader_address": "0xaaa",
                    "top_abs_pnl_usd": 56.0,
                    "top_abs_pnl_share": 0.80,
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--max-top-trader-accepted-share",
            "0.60",
            "--max-top-trader-abs-pnl-share",
            "0.60",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["top_trader_accepted_share", "top_trader_abs_pnl_share"])
        self.assertEqual(payload["constraints"]["max_top_trader_accepted_share"], 0.6)
        self.assertEqual(payload["constraints"]["max_top_trader_abs_pnl_share"], 0.6)
        self.assertIn("wallet n 40%", stderr.getvalue())
        self.assertIn("wallet pnl 45%", stderr.getvalue())

    def test_main_can_limit_top_market_concentration(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 64.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "win_rate": 0.6,
                    "market_concentration": {
                        "market_count": 4,
                        "top_accepted_market_id": "market-b",
                        "top_accepted_count": 4,
                        "top_accepted_share": 0.40,
                        "top_accepted_total_pnl_usd": 16.0,
                        "top_abs_pnl_market_id": "market-c",
                        "top_abs_pnl_usd": 28.0,
                        "top_abs_pnl_share": 0.45,
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 78.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
                "market_concentration": {
                    "market_count": 2,
                    "top_accepted_market_id": "market-a",
                    "top_accepted_count": 7,
                    "top_accepted_share": 0.70,
                    "top_accepted_total_pnl_usd": 50.0,
                    "top_abs_pnl_market_id": "market-a",
                    "top_abs_pnl_usd": 58.0,
                    "top_abs_pnl_share": 0.82,
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--max-top-market-accepted-share",
            "0.60",
            "--max-top-market-abs-pnl-share",
            "0.60",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["top_market_accepted_share", "top_market_abs_pnl_share"])
        self.assertEqual(payload["constraints"]["max_top_market_accepted_share"], 0.6)
        self.assertEqual(payload["constraints"]["max_top_market_abs_pnl_share"], 0.6)
        self.assertIn("market n 40%", stderr.getvalue())
        self.assertIn("market pnl 45%", stderr.getvalue())

    def test_main_can_limit_top_entry_price_band_concentration(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 60.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "win_rate": 0.6,
                    "entry_price_band_concentration": {
                        "entry_price_band_count": 3,
                        "top_accepted_entry_price_band": "0.60-0.69",
                        "top_accepted_count": 4,
                        "top_accepted_share": 0.40,
                        "top_accepted_total_pnl_usd": 18.0,
                        "top_abs_pnl_entry_price_band": ">=0.70",
                        "top_abs_pnl_usd": 26.0,
                        "top_abs_pnl_share": 0.45,
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 72.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
                "entry_price_band_concentration": {
                    "entry_price_band_count": 2,
                    "top_accepted_entry_price_band": "0.60-0.69",
                    "top_accepted_count": 7,
                    "top_accepted_share": 0.70,
                    "top_accepted_total_pnl_usd": 50.0,
                    "top_abs_pnl_entry_price_band": "0.60-0.69",
                    "top_abs_pnl_usd": 57.0,
                    "top_abs_pnl_share": 0.79,
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--max-top-entry-price-band-accepted-share",
            "0.60",
            "--max-top-entry-price-band-abs-pnl-share",
            "0.60",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["top_entry_price_band_accepted_share", "top_entry_price_band_abs_pnl_share"])
        self.assertEqual(payload["constraints"]["max_top_entry_price_band_accepted_share"], 0.6)
        self.assertEqual(payload["constraints"]["max_top_entry_price_band_abs_pnl_share"], 0.6)
        self.assertIn("band n 40%", stderr.getvalue())
        self.assertIn("band pnl 45%", stderr.getvalue())

    def test_main_can_limit_top_time_to_close_band_concentration(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 58.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "win_rate": 0.6,
                    "time_to_close_band_concentration": {
                        "time_to_close_band_count": 4,
                        "top_accepted_time_to_close_band": "2h-12h",
                        "top_accepted_count": 4,
                        "top_accepted_share": 0.40,
                        "top_accepted_total_pnl_usd": 16.0,
                        "top_abs_pnl_time_to_close_band": "12h-1d",
                        "top_abs_pnl_usd": 25.0,
                        "top_abs_pnl_share": 0.45,
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 70.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
                "time_to_close_band_concentration": {
                    "time_to_close_band_count": 2,
                    "top_accepted_time_to_close_band": "2h-12h",
                    "top_accepted_count": 7,
                    "top_accepted_share": 0.70,
                    "top_accepted_total_pnl_usd": 48.0,
                    "top_abs_pnl_time_to_close_band": "2h-12h",
                    "top_abs_pnl_usd": 56.0,
                    "top_abs_pnl_share": 0.80,
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--max-top-time-to-close-band-accepted-share",
            "0.60",
            "--max-top-time-to-close-band-abs-pnl-share",
            "0.60",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["top_time_to_close_band_accepted_share", "top_time_to_close_band_abs_pnl_share"])
        self.assertEqual(payload["constraints"]["max_top_time_to_close_band_accepted_share"], 0.6)
        self.assertEqual(payload["constraints"]["max_top_time_to_close_band_abs_pnl_share"], 0.6)
        self.assertIn("hzn n 40%", stderr.getvalue())
        self.assertIn("hzn pnl 45%", stderr.getvalue())

    def test_main_can_require_minimum_distinct_concentration_counts(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 58.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 12,
                    "resolved_count": 12,
                    "win_rate": 0.6,
                    "trader_concentration": {
                        "trader_count": 4,
                        "top_accepted_share": 0.35,
                        "top_abs_pnl_share": 0.30,
                    },
                    "market_concentration": {
                        "market_count": 4,
                        "top_accepted_share": 0.40,
                        "top_abs_pnl_share": 0.33,
                    },
                    "entry_price_band_concentration": {
                        "entry_price_band_count": 3,
                        "top_accepted_share": 0.45,
                        "top_abs_pnl_share": 0.40,
                    },
                    "time_to_close_band_concentration": {
                        "time_to_close_band_count": 4,
                        "top_accepted_share": 0.35,
                        "top_abs_pnl_share": 0.31,
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 64.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 12,
                "resolved_count": 12,
                "win_rate": 0.6,
                "trader_concentration": {
                    "trader_count": 2,
                    "top_accepted_share": 0.50,
                    "top_abs_pnl_share": 0.48,
                },
                "market_concentration": {
                    "market_count": 2,
                    "top_accepted_share": 0.50,
                    "top_abs_pnl_share": 0.47,
                },
                "entry_price_band_concentration": {
                    "entry_price_band_count": 2,
                    "top_accepted_share": 0.50,
                    "top_abs_pnl_share": 0.49,
                },
                "time_to_close_band_concentration": {
                    "time_to_close_band_count": 2,
                    "top_accepted_share": 0.55,
                    "top_abs_pnl_share": 0.50,
                },
            }

        stdout = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--min-trader-count",
            "3",
            "--min-market-count",
            "3",
            "--min-entry-price-band-count",
            "3",
            "--min-time-to-close-band-count",
            "3",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(
            rejected["constraint_failures"],
            ["trader_count", "market_count", "entry_price_band_count", "time_to_close_band_count"],
        )
        self.assertEqual(payload["constraints"]["min_trader_count"], 3)
        self.assertEqual(payload["constraints"]["min_market_count"], 3)
        self.assertEqual(payload["constraints"]["min_entry_price_band_count"], 3)
        self.assertEqual(payload["constraints"]["min_time_to_close_band_count"], 3)

    def test_main_can_require_global_active_windows(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 18.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 6,
                        "win_rate": 4 / 6,
                    }
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 0.0,
                    "max_drawdown_pct": 0.0,
                    "accepted_count": 0,
                    "resolved_count": 0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "win_rate": None,
                }
            return {
                "run_id": 3 if start_ts == 1 else 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 10.0 if start_ts == 1 else 8.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 6,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 4 / 6,
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
            "--min-active-windows",
            "2",
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
        self.assertEqual(rejected["constraint_failures"], ["active_window_count"])
        self.assertEqual(rejected["result"]["active_window_count"], 1)
        self.assertEqual(rejected["result"]["inactive_window_count"], 1)
        self.assertEqual(payload["constraints"]["min_active_windows"], 2)
        self.assertIn("reject active_window_count", stderr.getvalue())

    def test_main_uses_worst_active_window_counts_for_distinct_concentration_floors(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 34.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 8,
                        "resolved_count": 8,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 8,
                        "win_rate": 0.625,
                        "trader_concentration": {"trader_count": 4, "top_accepted_share": 0.35, "top_abs_pnl_share": 0.32},
                        "market_concentration": {"market_count": 4, "top_accepted_share": 0.40, "top_abs_pnl_share": 0.34},
                        "entry_price_band_concentration": {"entry_price_band_count": 3, "top_accepted_share": 0.45, "top_abs_pnl_share": 0.38},
                        "time_to_close_band_concentration": {"time_to_close_band_count": 4, "top_accepted_share": 0.36, "top_abs_pnl_share": 0.30},
                    }
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 30.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "trader_concentration": {"trader_count": 2, "top_accepted_share": 0.50, "top_abs_pnl_share": 0.46},
                    "market_concentration": {"market_count": 2, "top_accepted_share": 0.48, "top_abs_pnl_share": 0.44},
                    "entry_price_band_concentration": {"entry_price_band_count": 2, "top_accepted_share": 0.52, "top_abs_pnl_share": 0.47},
                    "time_to_close_band_concentration": {"time_to_close_band_count": 2, "top_accepted_share": 0.50, "top_abs_pnl_share": 0.45},
                }
            return {
                "run_id": 3 if start_ts == 1 else 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 26.0 if start_ts == 1 else 24.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 8,
                "resolved_count": 8,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "trader_concentration": {"trader_count": 3, "top_accepted_share": 0.40, "top_abs_pnl_share": 0.36},
                "market_concentration": {"market_count": 3, "top_accepted_share": 0.42, "top_abs_pnl_share": 0.37},
                "entry_price_band_concentration": {"entry_price_band_count": 3, "top_accepted_share": 0.43, "top_abs_pnl_share": 0.39},
                "time_to_close_band_concentration": {"time_to_close_band_count": 3, "top_accepted_share": 0.41, "top_abs_pnl_share": 0.35},
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
            "--min-trader-count",
            "3",
            "--min-market-count",
            "3",
            "--min-entry-price-band-count",
            "3",
            "--min-time-to-close-band-count",
            "3",
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
        self.assertEqual(
            rejected["constraint_failures"],
            ["trader_count", "market_count", "entry_price_band_count", "time_to_close_band_count"],
        )
        self.assertEqual(rejected["result"]["trader_concentration"]["trader_count"], 2)
        self.assertEqual(rejected["result"]["trader_concentration"]["peak_trader_count"], 4)
        self.assertEqual(rejected["result"]["market_concentration"]["market_count"], 2)
        self.assertEqual(rejected["result"]["market_concentration"]["peak_market_count"], 4)
        self.assertEqual(rejected["result"]["entry_price_band_concentration"]["entry_price_band_count"], 2)
        self.assertEqual(rejected["result"]["entry_price_band_concentration"]["peak_entry_price_band_count"], 3)
        self.assertEqual(rejected["result"]["time_to_close_band_concentration"]["time_to_close_band_count"], 2)
        self.assertEqual(rejected["result"]["time_to_close_band_concentration"]["peak_time_to_close_band_count"], 4)
        self.assertIn("reject trader_count,market_count,entry_price_band_count,time_to_close_band_count", stderr.getvalue())

    def test_main_can_penalize_pause_guard_reject_share_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 68.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 1,
                    "trade_count": 10,
                    "win_rate": 0.625,
                    "reject_reason_summary": {"daily_loss_guard": 1},
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 18.0, "win_count": 2},
                        "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 50.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 70.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 7,
                "resolved_count": 7,
                "rejected_count": 3,
                "trade_count": 10,
                "win_rate": 4 / 7,
                "reject_reason_summary": {"daily_loss_guard": 2, "live_drawdown_guard": 1},
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 28.0, "win_count": 3},
                    "xgboost": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 42.0, "win_count": 1},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--pause-guard-penalty",
            "1.0",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["pause_guard_penalty"], 1.0)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        self.assertLess(payload["ranked"][0]["score"], 68.0)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertGreater(payload["ranked"][0]["score"], rejected["score"])
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertEqual(best_breakdown["score_usd"], payload["ranked"][0]["score"])
        self.assertGreater(best_breakdown["pause_guard_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["pause_guard_penalty_usd"], best_breakdown["pause_guard_penalty_usd"])
        self.assertIn("pause 10%", stderr.getvalue())

    def test_main_can_penalize_wallet_and_market_concentration_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 68.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "trader_concentration": {
                        "trader_count": 4,
                        "top_accepted_share": 0.35,
                        "top_abs_pnl_share": 0.40,
                    },
                    "market_concentration": {
                        "market_count": 4,
                        "top_accepted_share": 0.30,
                        "top_abs_pnl_share": 0.35,
                    },
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 18.0, "win_count": 2},
                        "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 50.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 74.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "rejected_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "trader_concentration": {
                    "trader_count": 2,
                    "top_accepted_share": 0.75,
                    "top_abs_pnl_share": 0.80,
                },
                "market_concentration": {
                    "market_count": 2,
                    "top_accepted_share": 0.70,
                    "top_abs_pnl_share": 0.75,
                },
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 28.0, "win_count": 3},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 46.0, "win_count": 2},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--wallet-concentration-penalty",
            "0.5",
            "--market-concentration-penalty",
            "0.5",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["wallet_concentration_penalty"], 0.5)
        self.assertEqual(payload["market_concentration_penalty"], 0.5)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(best_breakdown["wallet_concentration_penalty_usd"], 0.0)
        self.assertGreater(best_breakdown["market_concentration_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["wallet_concentration_penalty_usd"], best_breakdown["wallet_concentration_penalty_usd"])
        self.assertGreater(rejected_breakdown["market_concentration_penalty_usd"], best_breakdown["market_concentration_penalty_usd"])

    def test_main_can_penalize_entry_band_and_horizon_concentration_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 68.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "entry_price_band_concentration": {
                        "entry_price_band_count": 3,
                        "top_accepted_share": 0.30,
                        "top_abs_pnl_share": 0.35,
                    },
                    "time_to_close_band_concentration": {
                        "time_to_close_band_count": 4,
                        "top_accepted_share": 0.32,
                        "top_abs_pnl_share": 0.36,
                    },
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 18.0, "win_count": 2},
                        "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 50.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 74.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "rejected_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "entry_price_band_concentration": {
                    "entry_price_band_count": 2,
                    "top_accepted_share": 0.78,
                    "top_abs_pnl_share": 0.80,
                },
                "time_to_close_band_concentration": {
                    "time_to_close_band_count": 2,
                    "top_accepted_share": 0.74,
                    "top_abs_pnl_share": 0.76,
                },
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 28.0, "win_count": 3},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 46.0, "win_count": 2},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--entry-price-band-concentration-penalty",
            "0.5",
            "--time-to-close-band-concentration-penalty",
            "0.5",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["entry_price_band_concentration_penalty"], 0.5)
        self.assertEqual(payload["time_to_close_band_concentration_penalty"], 0.5)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(best_breakdown["entry_price_band_concentration_penalty_usd"], 0.0)
        self.assertGreater(best_breakdown["time_to_close_band_concentration_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["entry_price_band_concentration_penalty_usd"], best_breakdown["entry_price_band_concentration_penalty_usd"])
        self.assertGreater(rejected_breakdown["time_to_close_band_concentration_penalty_usd"], best_breakdown["time_to_close_band_concentration_penalty_usd"])

    def test_main_can_penalize_losing_scorer_paths_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 68.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 18.0, "win_count": 2},
                        "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 50.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 74.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "rejected_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": -20.0, "win_count": 1},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 94.0, "win_count": 4},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--mode-loss-penalty",
            "1.0",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["mode_loss_penalty"], 1.0)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertEqual(best_breakdown["mode_loss_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["mode_loss_penalty_usd"], 0.0)

    def test_main_can_penalize_low_resolved_share_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 68.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 18.0, "win_count": 2},
                        "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 50.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 74.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 4,
                "rejected_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 2, "trade_count": 4, "total_pnl_usd": 28.0, "win_count": 3},
                    "xgboost": {"accepted_count": 4, "resolved_count": 2, "trade_count": 4, "total_pnl_usd": 46.0, "win_count": 2},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--resolved-share-penalty",
            "0.1",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["resolved_share_penalty"], 0.1)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertEqual(best_breakdown["resolved_share_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["resolved_share_penalty_usd"], 0.0)

    def test_main_can_penalize_low_mode_resolved_share_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 70.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 6,
                    "rejected_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 3, "trade_count": 4, "total_pnl_usd": 24.0, "win_count": 3},
                        "xgboost": {"accepted_count": 4, "resolved_count": 3, "trade_count": 4, "total_pnl_usd": 46.0, "win_count": 2},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 74.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 6,
                "rejected_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 24.0, "win_count": 3},
                    "xgboost": {"accepted_count": 4, "resolved_count": 2, "trade_count": 4, "total_pnl_usd": 50.0, "win_count": 2},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--mode-resolved-share-penalty",
            "0.1",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["mode_resolved_share_penalty"], 0.1)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(best_breakdown["mode_resolved_share_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["mode_resolved_share_penalty_usd"], best_breakdown["mode_resolved_share_penalty_usd"])

    def test_main_mode_loss_penalty_ignores_disabled_scorer_paths(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            allow_heuristic = bool(policy.as_dict()["allow_heuristic"])
            return {
                "run_id": 1 if allow_heuristic else 2,
                "total_pnl_usd": 40.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "rejected_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": -18.0, "win_count": 1},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 58.0, "win_count": 4},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"allow_heuristic": [True, False]}),
            "--mode-loss-penalty",
            "1.0",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["ranked"][0]["overrides"]["allow_heuristic"], False)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["allow_heuristic"] is True)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertEqual(best_breakdown["mode_loss_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["mode_loss_penalty_usd"], 0.0)
        self.assertGreater(payload["ranked"][0]["score"], rejected["score"])
        self.assertIn("allow_heuristic=False", stderr.getvalue())

    def test_main_mode_specific_constraints_ignore_disabled_scorer_paths(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            allow_heuristic = bool(policy.as_dict()["allow_heuristic"])
            if allow_heuristic:
                return {
                    "run_id": 1,
                    "total_pnl_usd": 22.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 8.0, "win_count": 2},
                        "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 14.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 2,
                "total_pnl_usd": 60.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "rejected_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "xgboost": {"accepted_count": 8, "resolved_count": 8, "trade_count": 8, "total_pnl_usd": 60.0, "win_count": 5},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"allow_heuristic": [True, False]}),
            "--min-heuristic-accepted-count",
            "1",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["allow_heuristic"], False)
        best_row = payload["ranked"][0]
        self.assertEqual(best_row["constraint_failures"], [])
        rejected = next(row for row in payload["ranked"] if row["overrides"]["allow_heuristic"] is True)
        self.assertEqual(rejected["constraint_failures"], [])
        self.assertIn("allow_heuristic=False", stderr.getvalue())

    def test_main_can_penalize_scorer_inactivity_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    return {
                        "run_id": 2,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 78.0,
                        "max_drawdown_pct": 0.04,
                        "accepted_count": 8,
                        "resolved_count": 8,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 8,
                        "win_rate": 0.625,
                        "signal_mode_summary": {
                            "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 24.0, "win_count": 2},
                            "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 54.0, "win_count": 3},
                        },
                    }
                return {
                    "run_id": 3,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 72.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 6,
                    "resolved_count": 6,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 6,
                    "win_rate": 4 / 6,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": 72.0, "win_count": 4},
                    },
                }
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 69.0 if start_ts == 1 else 67.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 21.0 if start_ts == 1 else 19.0, "win_count": 2},
                    "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 48.0, "win_count": 3},
                },
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
            "--mode-inactivity-penalty",
            "0.02",
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
        self.assertEqual(payload["mode_inactivity_penalty"], 0.02)
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertEqual(best_breakdown["mode_inactivity_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["mode_inactivity_penalty_usd"], 0.0)
        self.assertGreater(best_breakdown["score_usd"], rejected_breakdown["score_usd"])
        self.assertIn("min_confidence=0.65", stderr.getvalue())

    def test_main_can_penalize_fully_absent_scorer_inactivity_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 40.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 8, "resolved_count": 8, "trade_count": 8, "total_pnl_usd": 40.0, "win_count": 5},
                    },
                }
            return {
                "run_id": 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 34.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 10.0, "win_count": 2},
                    "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 24.0, "win_count": 3},
                },
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
            "--mode-inactivity-penalty",
            "0.02",
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
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertEqual(best_breakdown["mode_inactivity_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["mode_inactivity_penalty_usd"], 0.0)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["inactive_window_count"], 2)

    def test_main_can_require_mode_specific_resolved_counts_and_win_rates(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 66.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "win_rate": 0.7,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 12.0, "win_count": 2, "win_rate": 0.5},
                        "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": 54.0, "win_count": 4, "win_rate": 2 / 3},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 72.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 28.0, "win_count": 3, "win_rate": 0.6},
                    "xgboost": {"accepted_count": 5, "resolved_count": 3, "trade_count": 5, "total_pnl_usd": 44.0, "win_count": 1, "win_rate": 1 / 3},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--min-heuristic-resolved-count",
            "4",
            "--min-xgboost-resolved-count",
            "4",
            "--min-heuristic-win-rate",
            "0.5",
            "--min-xgboost-win-rate",
            "0.5",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["xgboost_resolved_count", "xgboost_win_rate"])
        self.assertEqual(payload["constraints"]["min_heuristic_resolved_count"], 4)
        self.assertEqual(payload["constraints"]["min_xgboost_resolved_count"], 4)
        self.assertEqual(payload["constraints"]["min_heuristic_win_rate"], 0.5)
        self.assertEqual(payload["constraints"]["min_xgboost_win_rate"], 0.5)

    def test_main_can_require_mode_specific_resolved_shares(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 64.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 3, "trade_count": 4, "total_pnl_usd": 16.0, "win_count": 2},
                        "xgboost": {"accepted_count": 6, "resolved_count": 5, "trade_count": 6, "total_pnl_usd": 48.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 70.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 7,
                "win_rate": 4 / 7,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 30.0, "win_count": 3},
                    "xgboost": {"accepted_count": 5, "resolved_count": 2, "trade_count": 5, "total_pnl_usd": 40.0, "win_count": 1},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--min-heuristic-resolved-share",
            "0.75",
            "--min-xgboost-resolved-share",
            "0.75",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["xgboost_resolved_share"])
        self.assertEqual(payload["constraints"]["min_heuristic_resolved_share"], 0.75)
        self.assertEqual(payload["constraints"]["min_xgboost_resolved_share"], 0.75)

    def test_main_can_require_mode_specific_pnl_floors(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 62.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "win_rate": 0.6,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 10.0, "win_count": 2},
                        "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": 52.0, "win_count": 4},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 70.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 18.0, "win_count": 2},
                    "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": -8.0, "win_count": 4},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--min-heuristic-pnl-usd",
            "0",
            "--min-xgboost-pnl-usd",
            "0",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["xgboost_total_pnl_usd"])
        self.assertEqual(payload["constraints"]["min_heuristic_pnl_usd"], 0.0)
        self.assertEqual(payload["constraints"]["min_xgboost_pnl_usd"], 0.0)

    def test_main_can_require_global_total_pnl_floor(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 12.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "win_rate": 0.6,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 4.0, "win_count": 2},
                        "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": 8.0, "win_count": 4},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": -6.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 2.0, "win_count": 2},
                    "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": -8.0, "win_count": 4},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--min-total-pnl-usd",
            "0",
            "--min-heuristic-pnl-usd",
            "-1000000000",
            "--min-xgboost-pnl-usd",
            "-1000000000",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["total_pnl_usd"])
        self.assertEqual(payload["constraints"]["min_total_pnl_usd"], 0.0)

    def test_main_can_require_global_resolved_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                resolved_count = 8
            else:
                resolved_count = 7
            return {
                "run_id": 1,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 10,
                "resolved_count": resolved_count,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": min(4, resolved_count),
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": max(resolved_count - 4, 0),
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                        "win_count": 4,
                    },
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--min-resolved-share",
            "0.75",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["resolved_share"])
        self.assertEqual(payload["constraints"]["min_resolved_share"], 0.75)

    def test_main_can_require_global_worst_window_resolved_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                resolved_count = 10
            elif min_conf >= 0.65:
                resolved_count = 8
            else:
                resolved_count = 4
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 10,
                "resolved_count": resolved_count,
                "rejected_count": 0,
                "unresolved_count": max(10 - resolved_count, 0),
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": min(4, resolved_count),
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": max(resolved_count - 4, 0),
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                        "win_count": 4,
                    },
                },
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
            "--min-worst-window-resolved-share",
            "0.75",
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
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["worst_window_resolved_share"])
        self.assertEqual(rejected["result"]["worst_window_resolved_share"], 0.4)
        self.assertEqual(payload["constraints"]["min_worst_window_resolved_share"], 0.75)
        self.assertIn("reject worst_window_resolved_share", stderr.getvalue())

    def test_main_can_penalize_low_worst_window_resolved_share_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                resolved_count = 10
            elif min_conf >= 0.65:
                resolved_count = 8
            else:
                resolved_count = 4
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0 if min_conf >= 0.65 else 14.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 10,
                "resolved_count": resolved_count,
                "rejected_count": 0,
                "unresolved_count": max(10 - resolved_count, 0),
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": min(4, resolved_count),
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": max(resolved_count - 4, 0),
                        "trade_count": 6,
                        "total_pnl_usd": 8.0 if min_conf >= 0.65 else 10.0,
                        "win_count": 4,
                    },
                },
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
            "--worst-window-resolved-share-penalty",
            "0.1",
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
        self.assertEqual(payload["worst_window_resolved_share_penalty"], 0.1)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(rejected_breakdown["worst_window_resolved_share_penalty_usd"], 0.0)
        self.assertGreater(
            rejected_breakdown["worst_window_resolved_share_penalty_usd"],
            best_breakdown["worst_window_resolved_share_penalty_usd"],
        )
        self.assertGreater(payload["ranked"][0]["score"], rejected["score"])
        self.assertIn("min_confidence=0.65", stderr.getvalue())

    def test_main_ignores_zero_activity_windows_for_global_worst_window_resolved_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                accepted_count = 10
                resolved_count = 10
            elif min_conf >= 0.65:
                accepted_count = 0
                resolved_count = 0
            else:
                accepted_count = 10
                resolved_count = 4
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": accepted_count,
                "resolved_count": resolved_count,
                "rejected_count": 0,
                "unresolved_count": max(accepted_count - resolved_count, 0),
                "trade_count": accepted_count,
                "win_rate": 0.6 if resolved_count > 0 else None,
                "signal_mode_summary": (
                    {
                        "heuristic": {
                            "accepted_count": accepted_count,
                            "resolved_count": resolved_count,
                            "trade_count": accepted_count,
                            "total_pnl_usd": 12.0,
                            "win_count": 2,
                        },
                    }
                    if accepted_count > 0 else {}
                ),
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
            "--min-worst-window-resolved-share",
            "0.75",
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
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        best = payload["best_feasible"]["result"]
        self.assertEqual(best["worst_window_resolved_share"], 0.0)
        self.assertEqual(best["worst_active_window_resolved_share"], 1.0)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["worst_window_resolved_share"])
        self.assertEqual(rejected["result"]["worst_active_window_resolved_share"], 0.4)

    def test_main_ignores_zero_activity_windows_for_global_worst_window_resolved_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                accepted_count = 10
                resolved_count = 10
            elif min_conf >= 0.65:
                accepted_count = 0
                resolved_count = 0
            else:
                accepted_count = 10
                resolved_count = 4
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0 if min_conf >= 0.65 else 14.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": accepted_count,
                "resolved_count": resolved_count,
                "rejected_count": 0,
                "unresolved_count": max(accepted_count - resolved_count, 0),
                "trade_count": accepted_count,
                "win_rate": 0.6 if resolved_count > 0 else None,
                "signal_mode_summary": (
                    {
                        "heuristic": {
                            "accepted_count": accepted_count,
                            "resolved_count": resolved_count,
                            "trade_count": accepted_count,
                            "total_pnl_usd": 12.0 if min_conf >= 0.65 else 14.0,
                            "win_count": 2,
                        },
                    }
                    if accepted_count > 0 else {}
                ),
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
            "--worst-window-resolved-share-penalty",
            "0.1",
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
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertEqual(best_breakdown["worst_window_resolved_share_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["worst_window_resolved_share_penalty_usd"], 0.0)

    def test_main_can_require_mode_specific_worst_window_pnl_floors(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                xgboost_window_pnl = 22.0 if start_ts == 1 else -18.0
                total_pnl = 32.0 if start_ts == 1 else 12.0
            else:
                xgboost_window_pnl = 6.0 if start_ts == 1 else -4.0
                total_pnl = 15.0 if start_ts == 1 else 14.0
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.05,
                "accepted_count": 10,
                "resolved_count": 10,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": total_pnl - xgboost_window_pnl, "win_count": 2},
                    "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": xgboost_window_pnl, "win_count": 4},
                },
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
            "--min-xgboost-worst-window-pnl-usd",
            "-10",
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
        self.assertEqual(rejected["constraint_failures"], ["xgboost_worst_window_pnl_usd"])
        self.assertEqual(payload["constraints"]["min_xgboost_worst_window_pnl_usd"], -10.0)
        self.assertIn("reject xgboost_worst_window_pnl_usd", stderr.getvalue())

    def test_main_can_require_mode_specific_worst_window_resolved_shares(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                xgboost_resolved = 6
            elif min_conf >= 0.65:
                xgboost_resolved = 5
            else:
                xgboost_resolved = 2
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.05,
                "accepted_count": 10,
                "resolved_count": 4 + xgboost_resolved,
                "rejected_count": 0,
                "unresolved_count": max(6 - xgboost_resolved, 0),
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": xgboost_resolved,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                        "win_count": 4,
                    },
                },
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
            "--min-xgboost-worst-window-resolved-share",
            "0.75",
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
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["xgboost_worst_window_resolved_share"])
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["worst_window_resolved_share"], 0.333333)
        self.assertEqual(payload["constraints"]["min_xgboost_worst_window_resolved_share"], 0.75)
        self.assertIn("reject xgboost_worst_window_resolved_share", stderr.getvalue())

    def test_main_ignores_inactive_mode_windows_for_mode_specific_worst_window_resolved_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    xgboost_resolved = 6
                else:
                    xgboost_resolved = 0
            else:
                if start_ts == 1:
                    xgboost_resolved = 6
                else:
                    xgboost_resolved = 2
            accepted_count = 4 + (6 if xgboost_resolved > 0 else 0)
            resolved_count = 4 + xgboost_resolved
            signal_mode_summary: dict[str, dict[str, float | int]] = {
                "heuristic": {
                    "accepted_count": 4,
                    "resolved_count": 4,
                    "trade_count": 4,
                    "total_pnl_usd": 4.0,
                    "win_count": 2,
                }
            }
            if xgboost_resolved > 0:
                signal_mode_summary["xgboost"] = {
                    "accepted_count": 6,
                    "resolved_count": xgboost_resolved,
                    "trade_count": 6,
                    "total_pnl_usd": 8.0,
                    "win_count": 4,
                }
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.05,
                "accepted_count": accepted_count,
                "resolved_count": resolved_count,
                "rejected_count": 0,
                "unresolved_count": max(accepted_count - resolved_count, 0),
                "trade_count": accepted_count,
                "win_rate": 0.6,
                "signal_mode_summary": signal_mode_summary,
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
            "--min-xgboost-worst-window-resolved-share",
            "0.75",
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
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        best = payload["best_feasible"]["result"]["signal_mode_summary"]["xgboost"]
        self.assertEqual(best["worst_window_resolved_share"], 0.0)
        self.assertEqual(best["worst_active_window_resolved_share"], 1.0)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(rejected["constraint_failures"], ["xgboost_worst_window_resolved_share"])
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["worst_active_window_resolved_share"], 0.333333)

    def test_main_can_penalize_low_mode_worst_window_resolved_share_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                xgboost_resolved = 6
            elif min_conf >= 0.65:
                xgboost_resolved = 5
            else:
                xgboost_resolved = 2
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0 if min_conf >= 0.65 else 14.0,
                "max_drawdown_pct": 0.05,
                "accepted_count": 10,
                "resolved_count": 4 + xgboost_resolved,
                "rejected_count": 0,
                "unresolved_count": max(6 - xgboost_resolved, 0),
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": xgboost_resolved,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0 if min_conf >= 0.65 else 10.0,
                        "win_count": 4,
                    },
                },
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
            "--mode-worst-window-resolved-share-penalty",
            "0.1",
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
        self.assertEqual(payload["mode_worst_window_resolved_share_penalty"], 0.1)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(best_breakdown["mode_worst_window_resolved_share_penalty_usd"], 0.0)
        self.assertGreater(
            rejected_breakdown["mode_worst_window_resolved_share_penalty_usd"],
            best_breakdown["mode_worst_window_resolved_share_penalty_usd"],
        )
        self.assertGreater(payload["ranked"][0]["score"], rejected["score"])
        self.assertIn("min_confidence=0.65", stderr.getvalue())

    def test_main_counts_missing_mode_windows_as_zero_activity_for_worst_window(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            if start_ts == 1:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 12.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 6,
                    "resolved_count": 6,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 6,
                    "win_rate": 4 / 6,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 2, "resolved_count": 2, "trade_count": 2, "total_pnl_usd": 7.0, "win_count": 1},
                        "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 5.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 9.0,
                "max_drawdown_pct": 0.02,
                "accepted_count": 5,
                "resolved_count": 5,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 5,
                "win_rate": 3 / 5,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 9.0, "win_count": 3},
                },
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
            "--min-xgboost-worst-window-pnl-usd",
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
        row = payload["ranked"][0]
        self.assertEqual(row["constraint_failures"], ["xgboost_worst_window_pnl_usd"])
        self.assertEqual(row["result"]["signal_mode_summary"]["xgboost"]["positive_window_count"], 1)
        self.assertEqual(row["result"]["signal_mode_summary"]["xgboost"]["negative_window_count"], 0)
        self.assertEqual(row["result"]["signal_mode_summary"]["xgboost"]["inactive_window_count"], 1)
        self.assertEqual(row["result"]["signal_mode_summary"]["xgboost"]["worst_window_pnl_usd"], 0.0)
        self.assertEqual(row["result"]["signal_mode_summary"]["xgboost"]["best_window_pnl_usd"], 5.0)
        self.assertIn("reject xgboost_worst_window_pnl_usd", stderr.getvalue())

    def test_main_can_limit_mode_inactive_windows(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 14.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 7,
                        "resolved_count": 7,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 7,
                        "win_rate": 4 / 7,
                        "signal_mode_summary": {
                            "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 6.0, "win_count": 2},
                            "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 8.0, "win_count": 2},
                        },
                    }
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 9.0,
                    "max_drawdown_pct": 0.02,
                    "accepted_count": 5,
                    "resolved_count": 5,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 5,
                    "win_rate": 3 / 5,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 9.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 3,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 11.0 if start_ts == 1 else 10.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 6,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 4 / 6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 2, "resolved_count": 2, "trade_count": 2, "total_pnl_usd": 3.0, "win_count": 1},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 8.0 if start_ts == 1 else 7.0, "win_count": 3},
                },
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
            "--max-xgboost-inactive-windows",
            "0",
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
        self.assertEqual(rejected["constraint_failures"], ["xgboost_inactive_window_count"])
        self.assertEqual(payload["constraints"]["max_xgboost_inactive_windows"], 0)
        self.assertIn("reject xgboost_inactive_window_count", stderr.getvalue())

    def test_main_counts_fully_absent_mode_windows_as_inactive(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 14.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 7,
                    "resolved_count": 7,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 7,
                    "win_rate": 4 / 7,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 7, "resolved_count": 7, "trade_count": 7, "total_pnl_usd": 14.0, "win_count": 4},
                    },
                }
            return {
                "run_id": 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 11.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 6,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 4 / 6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 2, "resolved_count": 2, "trade_count": 2, "total_pnl_usd": 3.0, "win_count": 1},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 8.0, "win_count": 3},
                },
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
            "--min-xgboost-worst-window-resolved-share",
            "0.75",
            "--max-xgboost-inactive-windows",
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
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        self.assertEqual(rejected["constraint_failures"], ["xgboost_inactive_window_count"])
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["accepted_count"], 0)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["inactive_window_count"], 2)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["worst_active_window_resolved_share"], 1.0)
        self.assertIn("reject xgboost_inactive_window_count", stderr.getvalue())

    def test_main_can_require_mode_specific_positive_windows(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                xgboost_window_pnl = 24.0 if start_ts == 1 else -6.0
                total_pnl = 34.0 if start_ts == 1 else 8.0
            else:
                xgboost_window_pnl = 12.0 if start_ts == 1 else 10.0
                total_pnl = 20.0 if start_ts == 1 else 18.0
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.05,
                "accepted_count": 10,
                "resolved_count": 10,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": total_pnl - xgboost_window_pnl, "win_count": 2},
                    "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": xgboost_window_pnl, "win_count": 4},
                },
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
            "--min-xgboost-positive-windows",
            "2",
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
        self.assertEqual(rejected["constraint_failures"], ["xgboost_positive_window_count"])
        self.assertEqual(payload["constraints"]["min_xgboost_positive_windows"], 2)
        self.assertIn("reject xgboost_positive_window_count", stderr.getvalue())

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

    def test_main_persists_search_runs_and_candidates(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 80.0,
                    "max_drawdown_pct": 0.18,
                    "accepted_count": 4,
                    "resolved_count": 4,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 4,
                    "win_rate": 0.80,
                    "trader_concentration": {"trader_count": 1, "top_accepted_share": 1.0, "top_abs_pnl_share": 1.0},
                    "market_concentration": {"market_count": 1, "top_accepted_share": 1.0, "top_abs_pnl_share": 1.0},
                    "signal_mode_summary": {"xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 80.0, "win_count": 3}},
                }
            if min_conf >= 0.60:
                return {
                    "run_id": 1,
                    "total_pnl_usd": 60.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 12,
                    "resolved_count": 12,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 12,
                    "win_rate": 0.62,
                    "trader_concentration": {"trader_count": 3, "top_accepted_share": 0.5, "top_abs_pnl_share": 0.5},
                    "market_concentration": {"market_count": 3, "top_accepted_share": 0.5, "top_abs_pnl_share": 0.5},
                    "signal_mode_summary": {"heuristic": {"accepted_count": 6, "resolved_count": 12, "trade_count": 12, "total_pnl_usd": 60.0, "win_count": 7}, "xgboost": {"accepted_count": 6, "resolved_count": 0, "trade_count": 0, "total_pnl_usd": 0.0, "win_count": 0}},
                }
            return {
                "run_id": 0,
                "total_pnl_usd": 40.0,
                "max_drawdown_pct": 0.05,
                "accepted_count": 12,
                "resolved_count": 12,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 12,
                "win_rate": 0.62,
                "trader_concentration": {"trader_count": 2, "top_accepted_share": 0.75, "top_abs_pnl_share": 0.75},
                "market_concentration": {"market_count": 2, "top_accepted_share": 0.75, "top_abs_pnl_share": 0.75},
                "signal_mode_summary": {"heuristic": {"accepted_count": 12, "resolved_count": 12, "trade_count": 12, "total_pnl_usd": 40.0, "win_count": 7}},
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--label-prefix",
                "persist",
                "--notes",
                "persisted run",
                "--grid-json",
                json.dumps({"min_confidence": [0.60, 0.65]}),
                "--min-accepted-count",
                "5",
                "--max-drawdown-pct",
                "0.10",
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            payload = json.loads(stdout.getvalue())
            conn = sqlite3.connect(str(db_path))
            try:
                run_row = conn.execute(
                    """
                    SELECT label_prefix, candidate_count, feasible_count, rejected_count,
                           pause_guard_penalty, worst_window_resolved_share_penalty,
                           mode_loss_penalty, mode_worst_window_resolved_share_penalty,
                           wallet_concentration_penalty, market_concentration_penalty,
                           current_candidate_score, current_candidate_feasible,
                           current_candidate_total_pnl_usd, best_vs_current_pnl_usd,
                           best_feasible_candidate_index, best_feasible_total_pnl_usd,
                           current_candidate_constraint_failures_json, current_candidate_result_json,
                           constraints_json, notes
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_rows = conn.execute(
                    """
                    SELECT candidate_index, feasible, is_current_policy, constraint_failures_json, overrides_json, config_json, result_json
                    FROM replay_search_candidates
                    ORDER BY candidate_index ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(payload["search_run_id"], 1)
            self.assertEqual(run_row[:4], ("persist", 2, 1, 1))
            self.assertEqual(run_row[4], 0.0)
            self.assertEqual(run_row[5], 0.0)
            self.assertEqual(run_row[6], 0.0)
            self.assertEqual(run_row[7], 0.0)
            self.assertEqual(run_row[8], 0.0)
            self.assertEqual(run_row[9], 0.0)
            self.assertEqual(run_row[10], -110.0)
            self.assertEqual(run_row[11], 1)
            self.assertEqual(run_row[12], 40.0)
            self.assertEqual(run_row[13], 20.0)
            self.assertEqual(run_row[14], 1)
            self.assertEqual(run_row[15], 60.0)
            self.assertEqual(json.loads(run_row[16]), [])
            current_result_json = json.loads(run_row[17])
            self.assertEqual(current_result_json["signal_mode_summary"]["heuristic"]["accepted_count"], 12)
            self.assertEqual(current_result_json["trader_concentration"]["top_accepted_share"], 0.75)
            self.assertEqual(current_result_json["market_concentration"]["top_accepted_share"], 0.75)
            self.assertEqual(current_result_json["score_breakdown"]["score_usd"], -110.0)
            self.assertEqual(
                json.loads(run_row[18]),
                {
                    "max_drawdown_pct": 0.1,
                    "max_heuristic_accepted_share": 0.0,
                    "max_heuristic_inactive_windows": -1,
                    "max_inactive_windows": -1,
                    "max_pause_guard_reject_share": 0.0,
                    "max_top_market_accepted_share": 0.0,
                    "max_top_market_abs_pnl_share": 0.0,
                    "max_top_entry_price_band_accepted_share": 0.0,
                    "max_top_entry_price_band_abs_pnl_share": 0.0,
                    "max_top_time_to_close_band_accepted_share": 0.0,
                    "max_top_time_to_close_band_abs_pnl_share": 0.0,
                    "max_top_trader_accepted_share": 0.0,
                    "max_top_trader_abs_pnl_share": 0.0,
                    "max_worst_window_drawdown_pct": 0.0,
                    "max_xgboost_inactive_windows": -1,
                    "min_accepted_count": 5,
                    "min_active_windows": 0,
                    "min_entry_price_band_count": 0,
                    "min_heuristic_accepted_count": 0,
                    "min_heuristic_resolved_count": 0,
                    "min_heuristic_resolved_share": 0.0,
                    "min_heuristic_win_rate": 0.0,
                    "min_heuristic_pnl_usd": 0.0,
                    "min_heuristic_positive_windows": 0,
                    "min_heuristic_worst_window_pnl_usd": -1000000000.0,
                    "min_heuristic_worst_window_resolved_share": 0.0,
                    "min_market_count": 0,
                    "min_positive_windows": 0,
                    "min_resolved_count": 0,
                    "min_resolved_share": 0.0,
                    "min_total_pnl_usd": -1000000000.0,
                    "min_time_to_close_band_count": 0,
                    "min_trader_count": 0,
                    "min_win_rate": 0.0,
                    "min_worst_window_pnl_usd": -1000000000.0,
                    "min_worst_window_resolved_share": 0.0,
                    "min_xgboost_accepted_share": 0.0,
                    "min_xgboost_accepted_count": 0,
                    "min_xgboost_resolved_count": 0,
                    "min_xgboost_resolved_share": 0.0,
                    "min_xgboost_win_rate": 0.0,
                    "min_xgboost_pnl_usd": 0.0,
                    "min_xgboost_positive_windows": 0,
                    "min_xgboost_worst_window_pnl_usd": -1000000000.0,
                    "min_xgboost_worst_window_resolved_share": 0.0,
                },
            )
            self.assertEqual(run_row[19], "persisted run")
            self.assertEqual(payload["best_feasible_config"]["MIN_CONFIDENCE"], 0.6)
            self.assertEqual(len(candidate_rows), 3)
            self.assertEqual(candidate_rows[0][0:3], (0, 1, 1))
            self.assertEqual(json.loads(candidate_rows[0][3]), [])
            self.assertEqual(json.loads(candidate_rows[0][4]), {})
            self.assertEqual(json.loads(candidate_rows[0][5])["MIN_CONFIDENCE"], 0.55)
            self.assertEqual(json.loads(candidate_rows[0][6])["total_pnl_usd"], 40.0)
            self.assertEqual(json.loads(candidate_rows[0][6])["score_breakdown"]["score_usd"], -110.0)
            self.assertEqual(candidate_rows[1][0:3], (1, 1, 0))
            self.assertEqual(json.loads(candidate_rows[1][3]), [])
            self.assertEqual(json.loads(candidate_rows[1][4]), {"min_confidence": 0.6})
            self.assertEqual(json.loads(candidate_rows[1][5])["MIN_CONFIDENCE"], 0.6)
            self.assertEqual(json.loads(candidate_rows[1][6])["total_pnl_usd"], 60.0)
            self.assertEqual(json.loads(candidate_rows[1][6])["score_breakdown"]["score_usd"], -90.0)
            self.assertEqual(candidate_rows[2][0:3], (2, 0, 0))
            self.assertEqual(json.loads(candidate_rows[2][3]), ["accepted_count", "max_drawdown_pct"])
            self.assertEqual(json.loads(candidate_rows[2][4]), {"min_confidence": 0.65})
            self.assertEqual(json.loads(candidate_rows[2][5])["MIN_CONFIDENCE"], 0.65)
            self.assertEqual(json.loads(candidate_rows[2][6])["max_drawdown_pct"], 0.18)
            self.assertEqual(json.loads(candidate_rows[2][6])["score_breakdown"]["score_usd"], -460.0)

    def test_main_backfills_existing_search_tables_before_insert(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            return {
                "run_id": 1,
                "total_pnl_usd": 42.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {"heuristic": {"accepted_count": 8, "resolved_count": 8, "trade_count": 8, "total_pnl_usd": 42.0, "win_count": 5}},
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_existing.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.executescript(
                    """
                    CREATE TABLE replay_search_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        started_at INTEGER NOT NULL,
                        finished_at INTEGER NOT NULL,
                        label_prefix TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE replay_search_candidates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        replay_search_run_id INTEGER NOT NULL,
                        candidate_index INTEGER NOT NULL,
                        score REAL NOT NULL DEFAULT 0
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            conn = sqlite3.connect(str(db_path))
            try:
                run_columns = {row[1] for row in conn.execute("PRAGMA table_info(replay_search_runs)").fetchall()}
                candidate_columns = {row[1] for row in conn.execute("PRAGMA table_info(replay_search_candidates)").fetchall()}
                run_row = conn.execute(
                    """
                    SELECT candidate_count, feasible_count, rejected_count,
                           current_candidate_score, current_candidate_feasible,
                           current_candidate_total_pnl_usd, current_candidate_result_json,
                           best_feasible_score, best_feasible_total_pnl_usd
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_row = conn.execute(
                    """
                    SELECT feasible, is_current_policy, constraint_failures_json, config_json, result_json
                    FROM replay_search_candidates
                    WHERE is_current_policy=1
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertIn("constraints_json", run_columns)
            self.assertIn("current_candidate_result_json", run_columns)
            self.assertIn("best_feasible_total_pnl_usd", run_columns)
            self.assertIn("pause_guard_penalty", run_columns)
            self.assertIn("resolved_share_penalty", run_columns)
            self.assertIn("worst_window_resolved_share_penalty", run_columns)
            self.assertIn("mode_resolved_share_penalty", run_columns)
            self.assertIn("mode_worst_window_resolved_share_penalty", run_columns)
            self.assertIn("mode_loss_penalty", run_columns)
            self.assertIn("mode_inactivity_penalty", run_columns)
            self.assertIn("wallet_concentration_penalty", run_columns)
            self.assertIn("market_concentration_penalty", run_columns)
            self.assertIn("feasible", candidate_columns)
            self.assertIn("config_json", candidate_columns)
            self.assertIn("result_json", candidate_columns)
            self.assertEqual(run_row[0:6], (1, 1, 0, -78.0, 1, 42.0))
            self.assertEqual(json.loads(run_row[6])["signal_mode_summary"]["heuristic"]["accepted_count"], 8)
            self.assertEqual(run_row[7:9], (-78.0, 42.0))
            self.assertEqual(candidate_row[0], 1)
            self.assertEqual(candidate_row[1], 1)
            self.assertEqual(json.loads(candidate_row[2]), [])
            self.assertEqual(json.loads(candidate_row[3])["MIN_CONFIDENCE"], 0.55)
            self.assertEqual(json.loads(candidate_row[4])["total_pnl_usd"], 42.0)

    def test_main_persists_nonzero_concentration_penalties(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                return {
                    "run_id": 1,
                    "total_pnl_usd": 55.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 10,
                    "win_rate": 0.6,
                    "trader_concentration": {"trader_count": 4, "top_accepted_share": 0.4, "top_abs_pnl_share": 0.45},
                    "market_concentration": {"market_count": 5, "top_accepted_share": 0.35, "top_abs_pnl_share": 0.3},
                    "entry_price_band_concentration": {"entry_price_band_count": 3, "top_accepted_share": 0.25, "top_abs_pnl_share": 0.2},
                    "time_to_close_band_concentration": {"time_to_close_band_count": 3, "top_accepted_share": 0.30, "top_abs_pnl_share": 0.28},
                    "signal_mode_summary": {"xgboost": {"accepted_count": 10, "resolved_count": 10, "trade_count": 10, "total_pnl_usd": 55.0, "win_count": 6}},
                }
            return {
                "run_id": 0,
                "total_pnl_usd": 40.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 12,
                "resolved_count": 12,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 12,
                "win_rate": 7 / 12,
                "trader_concentration": {"trader_count": 2, "top_accepted_share": 0.75, "top_abs_pnl_share": 0.7},
                "market_concentration": {"market_count": 3, "top_accepted_share": 0.5, "top_abs_pnl_share": 0.55},
                "entry_price_band_concentration": {"entry_price_band_count": 2, "top_accepted_share": 0.7, "top_abs_pnl_share": 0.68},
                "time_to_close_band_concentration": {"time_to_close_band_count": 2, "top_accepted_share": 0.72, "top_abs_pnl_share": 0.74},
                "signal_mode_summary": {"heuristic": {"accepted_count": 12, "resolved_count": 12, "trade_count": 12, "total_pnl_usd": 40.0, "win_count": 7}},
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_penalties.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--wallet-concentration-penalty",
                "0.25",
                "--market-concentration-penalty",
                "0.10",
                "--entry-price-band-concentration-penalty",
                "0.15",
                "--time-to-close-band-concentration-penalty",
                "0.20",
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            conn = sqlite3.connect(str(db_path))
            try:
                run_row = conn.execute(
                    """
                    SELECT wallet_concentration_penalty, market_concentration_penalty,
                           entry_price_band_concentration_penalty, time_to_close_band_concentration_penalty,
                           current_candidate_result_json
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_rows = conn.execute(
                    """
                    SELECT is_current_policy, result_json
                    FROM replay_search_candidates
                    ORDER BY candidate_index ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertIsNotNone(run_row)
            self.assertEqual(run_row[0], 0.25)
            self.assertEqual(run_row[1], 0.10)
            self.assertEqual(run_row[2], 0.15)
            self.assertEqual(run_row[3], 0.20)
            current_result_json = json.loads(run_row[4])
            self.assertGreater(current_result_json["score_breakdown"]["wallet_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["market_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["entry_price_band_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["time_to_close_band_concentration_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["wallet_concentration_penalty_usd"],
                best_candidate_json["score_breakdown"]["wallet_concentration_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["market_concentration_penalty_usd"],
                best_candidate_json["score_breakdown"]["market_concentration_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["entry_price_band_concentration_penalty_usd"],
                best_candidate_json["score_breakdown"]["entry_price_band_concentration_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["time_to_close_band_concentration_penalty_usd"],
                best_candidate_json["score_breakdown"]["time_to_close_band_concentration_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_loss_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                return {
                    "run_id": 1,
                    "total_pnl_usd": 55.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 10,
                    "win_rate": 0.6,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 15.0, "win_count": 2},
                        "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": 40.0, "win_count": 4},
                    },
                }
            return {
                "run_id": 0,
                "total_pnl_usd": 40.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 12,
                "resolved_count": 12,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 12,
                "win_rate": 7 / 12,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": -12.0, "win_count": 2},
                    "xgboost": {"accepted_count": 6, "resolved_count": 6, "trade_count": 6, "total_pnl_usd": 52.0, "win_count": 5},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_loss.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--mode-loss-penalty",
                "0.5",
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            conn = sqlite3.connect(str(db_path))
            try:
                run_row = conn.execute(
                    """
                    SELECT mode_loss_penalty, current_candidate_result_json
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_rows = conn.execute(
                    """
                    SELECT is_current_policy, result_json
                    FROM replay_search_candidates
                    ORDER BY candidate_index ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertIsNotNone(run_row)
            self.assertEqual(run_row[0], 0.5)
            current_result_json = json.loads(run_row[1])
            self.assertGreater(current_result_json["score_breakdown"]["mode_loss_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_loss_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_loss_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_inactivity_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 60.0 if start_ts == 1 else 55.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 18.0 if start_ts == 1 else 16.0, "win_count": 2},
                        "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 42.0 if start_ts == 1 else 39.0, "win_count": 3},
                    },
                }
            if start_ts == 1:
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 52.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 7,
                    "resolved_count": 7,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 7,
                    "win_rate": 4 / 7,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 17.0, "win_count": 2},
                        "xgboost": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 35.0, "win_count": 2},
                    },
                }
            return {
                "run_id": 3,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 49.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 7,
                "resolved_count": 7,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 7,
                "win_rate": 4 / 7,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 7, "resolved_count": 7, "trade_count": 7, "total_pnl_usd": 49.0, "win_count": 4},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_inactivity.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--window-days",
                "30",
                "--window-count",
                "2",
                "--mode-inactivity-penalty",
                "0.25",
            ]
            with (
                patch.object(replay_search, "_latest_trade_ts", return_value=5_184_000),
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            conn = sqlite3.connect(str(db_path))
            try:
                run_row = conn.execute(
                    """
                    SELECT mode_inactivity_penalty, current_candidate_result_json
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_rows = conn.execute(
                    """
                    SELECT is_current_policy, result_json
                    FROM replay_search_candidates
                    ORDER BY candidate_index ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertIsNotNone(run_row)
            self.assertEqual(run_row[0], 0.25)
            current_result_json = json.loads(run_row[1])
            self.assertGreater(current_result_json["score_breakdown"]["mode_inactivity_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_inactivity_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_inactivity_penalty_usd"],
            )

    def test_main_persists_nonzero_resolved_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                return {
                    "run_id": 1,
                    "total_pnl_usd": 60.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 18.0, "win_count": 2},
                        "xgboost": {"accepted_count": 5, "resolved_count": 5, "trade_count": 5, "total_pnl_usd": 42.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 2,
                "total_pnl_usd": 62.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 4,
                "rejected_count": 0,
                "unresolved_count": 4,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 2, "trade_count": 4, "total_pnl_usd": 24.0, "win_count": 3},
                    "xgboost": {"accepted_count": 4, "resolved_count": 2, "trade_count": 4, "total_pnl_usd": 38.0, "win_count": 2},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_resolved_share_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--resolved-share-penalty",
                "0.25",
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            conn = sqlite3.connect(str(db_path))
            try:
                run_row = conn.execute(
                    """
                    SELECT resolved_share_penalty, current_candidate_result_json
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_rows = conn.execute(
                    """
                    SELECT is_current_policy, result_json
                    FROM replay_search_candidates
                    ORDER BY candidate_index ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertIsNotNone(run_row)
            self.assertEqual(run_row[0], 0.25)
            current_result_json = json.loads(run_row[1])
            self.assertGreater(current_result_json["score_breakdown"]["resolved_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["resolved_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["resolved_share_penalty_usd"],
            )

    def test_main_persists_nonzero_worst_window_resolved_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                resolved_count = 8
            elif min_conf >= 0.60:
                resolved_count = 8
            else:
                resolved_count = 4
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 60.0 if min_conf >= 0.60 else 62.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": resolved_count,
                "rejected_count": 0,
                "unresolved_count": max(8 - resolved_count, 0),
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 3,
                        "resolved_count": min(3, resolved_count),
                        "trade_count": 3,
                        "total_pnl_usd": 18.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": max(resolved_count - 3, 0),
                        "trade_count": 5,
                        "total_pnl_usd": 42.0 if min_conf >= 0.60 else 44.0,
                        "win_count": 3,
                    },
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_worst_window_resolved_share_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--window-days",
                "30",
                "--window-count",
                "2",
                "--worst-window-resolved-share-penalty",
                "0.25",
            ]
            with (
                patch.object(replay_search, "_latest_trade_ts", return_value=5_184_000),
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            conn = sqlite3.connect(str(db_path))
            try:
                run_row = conn.execute(
                    """
                    SELECT worst_window_resolved_share_penalty, current_candidate_result_json
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_rows = conn.execute(
                    """
                    SELECT is_current_policy, result_json
                    FROM replay_search_candidates
                    ORDER BY candidate_index ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertIsNotNone(run_row)
            self.assertEqual(run_row[0], 0.25)
            current_result_json = json.loads(run_row[1])
            self.assertGreater(current_result_json["score_breakdown"]["worst_window_resolved_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["worst_window_resolved_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["worst_window_resolved_share_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_resolved_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                return {
                    "run_id": 1,
                    "total_pnl_usd": 60.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 6,
                    "rejected_count": 0,
                    "unresolved_count": 2,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 3, "trade_count": 4, "total_pnl_usd": 18.0, "win_count": 2},
                        "xgboost": {"accepted_count": 4, "resolved_count": 3, "trade_count": 4, "total_pnl_usd": 42.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 2,
                "total_pnl_usd": 62.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 2,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 24.0, "win_count": 3},
                    "xgboost": {"accepted_count": 4, "resolved_count": 2, "trade_count": 4, "total_pnl_usd": 38.0, "win_count": 2},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_resolved_share_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--mode-resolved-share-penalty",
                "0.25",
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            conn = sqlite3.connect(str(db_path))
            try:
                run_row = conn.execute(
                    """
                    SELECT mode_resolved_share_penalty, current_candidate_result_json
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_rows = conn.execute(
                    """
                    SELECT is_current_policy, result_json
                    FROM replay_search_candidates
                    ORDER BY candidate_index ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertIsNotNone(run_row)
            self.assertEqual(run_row[0], 0.25)
            current_result_json = json.loads(run_row[1])
            self.assertGreater(current_result_json["score_breakdown"]["mode_resolved_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_resolved_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_resolved_share_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_worst_window_resolved_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                xgboost_resolved = 4
            elif min_conf >= 0.60:
                xgboost_resolved = 4
            else:
                xgboost_resolved = 1
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 60.0 if min_conf >= 0.60 else 62.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 3 + xgboost_resolved,
                "rejected_count": 0,
                "unresolved_count": max(5 - xgboost_resolved, 0),
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 3,
                        "resolved_count": 3,
                        "trade_count": 3,
                        "total_pnl_usd": 18.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": xgboost_resolved,
                        "trade_count": 5,
                        "total_pnl_usd": 42.0 if min_conf >= 0.60 else 44.0,
                        "win_count": 3,
                    },
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_worst_window_resolved_share_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--window-days",
                "30",
                "--window-count",
                "2",
                "--mode-worst-window-resolved-share-penalty",
                "0.25",
            ]
            with (
                patch.object(replay_search, "_latest_trade_ts", return_value=5_184_000),
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            conn = sqlite3.connect(str(db_path))
            try:
                run_row = conn.execute(
                    """
                    SELECT mode_worst_window_resolved_share_penalty, current_candidate_result_json
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_rows = conn.execute(
                    """
                    SELECT is_current_policy, result_json
                    FROM replay_search_candidates
                    ORDER BY candidate_index ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertIsNotNone(run_row)
            self.assertEqual(run_row[0], 0.25)
            current_result_json = json.loads(run_row[1])
            self.assertGreater(current_result_json["score_breakdown"]["mode_worst_window_resolved_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_worst_window_resolved_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_worst_window_resolved_share_penalty_usd"],
            )

    def test_main_dedupes_current_candidate_when_grid_matches_base_policy(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None):
            payload = policy.as_dict()
            calls.append(payload)
            return {
                "run_id": len(calls),
                "total_pnl_usd": 50.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 9,
                "resolved_count": 9,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 9,
                "win_rate": 2 / 3,
                "signal_mode_summary": {"xgboost": {"accepted_count": 9, "resolved_count": 9, "trade_count": 9, "total_pnl_usd": 50.0, "win_count": 6}},
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_dedupe.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--base-policy-json",
                json.dumps({"min_confidence": 0.60}),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

            payload = json.loads(stdout.getvalue())
            conn = sqlite3.connect(str(db_path))
            try:
                run_row = conn.execute(
                    """
                    SELECT candidate_count, feasible_count, rejected_count,
                           current_candidate_total_pnl_usd, current_candidate_result_json, best_vs_current_pnl_usd,
                           best_feasible_candidate_index
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_rows = conn.execute(
                    """
                    SELECT candidate_index, feasible, is_current_policy
                    FROM replay_search_candidates
                    ORDER BY candidate_index ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertTrue(payload["current_candidate_matches_grid"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(run_row[0:4], (1, 1, 0, 50.0))
            self.assertEqual(json.loads(run_row[4])["signal_mode_summary"]["xgboost"]["accepted_count"], 9)
            self.assertEqual(run_row[5:7], (0.0, 1))
            self.assertEqual(candidate_rows, [(1, 1, 0)])


if __name__ == "__main__":
    unittest.main()
