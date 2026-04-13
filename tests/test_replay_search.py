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
    def _constraint_defaults(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "allow_heuristic": True,
            "allow_xgboost": True,
            "min_accepted_count": 0,
            "min_resolved_count": 0,
            "min_resolved_share": 0.0,
            "min_resolved_size_share": 0.0,
            "min_win_rate": 0.0,
            "min_total_pnl_usd": -1_000_000_000.0,
            "max_drawdown_pct": 0.0,
            "max_open_exposure_share": 0.0,
            "min_worst_window_pnl_usd": -1_000_000_000.0,
            "min_worst_window_resolved_share": 0.0,
            "min_worst_window_resolved_size_share": 0.0,
            "max_worst_window_drawdown_pct": 0.0,
            "min_heuristic_accepted_count": 0,
            "min_xgboost_accepted_count": 0,
            "min_heuristic_resolved_count": 0,
            "min_xgboost_resolved_count": 0,
            "min_heuristic_win_rate": 0.0,
            "min_xgboost_win_rate": 0.0,
            "min_heuristic_resolved_share": 0.0,
            "min_xgboost_resolved_share": 0.0,
            "min_heuristic_resolved_size_share": 0.0,
            "min_xgboost_resolved_size_share": 0.0,
            "min_heuristic_pnl_usd": 0.0,
            "min_xgboost_pnl_usd": 0.0,
            "min_heuristic_worst_window_pnl_usd": -1_000_000_000.0,
            "min_xgboost_worst_window_pnl_usd": -1_000_000_000.0,
            "min_heuristic_worst_window_resolved_share": 0.0,
            "min_xgboost_worst_window_resolved_share": 0.0,
            "min_heuristic_worst_window_resolved_size_share": 0.0,
            "min_xgboost_worst_window_resolved_size_share": 0.0,
            "min_heuristic_positive_window_count": 0,
            "min_xgboost_positive_window_count": 0,
            "min_heuristic_worst_active_window_accepted_count": 0,
            "min_heuristic_worst_active_window_accepted_size_usd": 0.0,
            "min_xgboost_worst_active_window_accepted_count": 0,
            "min_xgboost_worst_active_window_accepted_size_usd": 0.0,
            "max_heuristic_inactive_window_count": -1,
            "max_xgboost_inactive_window_count": -1,
            "max_heuristic_accepted_share": 0.0,
            "max_heuristic_accepted_size_share": 0.0,
            "max_heuristic_active_window_accepted_share": 0.0,
            "max_heuristic_active_window_accepted_size_share": 0.0,
            "min_xgboost_accepted_share": 0.0,
            "min_xgboost_accepted_size_share": 0.0,
            "min_xgboost_active_window_accepted_share": 0.0,
            "min_xgboost_active_window_accepted_size_share": 0.0,
            "max_pause_guard_reject_share": 0.0,
            "max_daily_guard_window_share": 0.0,
            "max_live_guard_window_share": 0.0,
            "max_daily_guard_restart_window_share": 0.0,
            "max_live_guard_restart_window_share": 0.0,
            "min_active_window_count": 0,
            "max_inactive_window_count": -1,
            "min_accepted_window_count": 0,
            "min_accepted_window_share": 0.0,
            "max_non_accepting_active_window_streak": -1,
            "max_non_accepting_active_window_episodes": -1,
            "max_accepting_window_accepted_share": 0.0,
            "max_accepting_window_accepted_size_share": 0.0,
            "max_top_two_accepting_window_accepted_share": 0.0,
            "max_top_two_accepting_window_accepted_size_share": 0.0,
            "max_accepting_window_accepted_concentration_index": 0.0,
            "max_accepting_window_accepted_size_concentration_index": 0.0,
            "min_trader_count": 0,
            "min_market_count": 0,
            "min_entry_price_band_count": 0,
            "min_time_to_close_band_count": 0,
            "max_top_trader_accepted_share": 0.0,
            "max_top_trader_abs_pnl_share": 0.0,
            "max_top_trader_size_share": 0.0,
            "max_top_market_accepted_share": 0.0,
            "max_top_market_abs_pnl_share": 0.0,
            "max_top_market_size_share": 0.0,
            "max_top_entry_price_band_accepted_share": 0.0,
            "max_top_entry_price_band_abs_pnl_share": 0.0,
            "max_top_entry_price_band_size_share": 0.0,
            "max_top_time_to_close_band_accepted_share": 0.0,
            "max_top_time_to_close_band_abs_pnl_share": 0.0,
            "max_top_time_to_close_band_size_share": 0.0,
            "min_worst_active_window_accepted_count": 0,
            "min_worst_active_window_accepted_size_usd": 0.0,
            "max_window_end_open_exposure_share": 0.0,
            "max_avg_window_end_open_exposure_share": 0.0,
            "max_carry_window_share": 0.0,
            "max_carry_restart_window_share": 0.0,
            "min_heuristic_accepted_windows": 0,
            "min_xgboost_accepted_windows": 0,
            "min_heuristic_accepted_window_share": 0.0,
            "min_xgboost_accepted_window_share": 0.0,
            "max_heuristic_non_accepting_active_window_streak": -1,
            "max_xgboost_non_accepting_active_window_streak": -1,
            "max_heuristic_non_accepting_active_window_episodes": -1,
            "max_xgboost_non_accepting_active_window_episodes": -1,
            "max_heuristic_accepting_window_accepted_share": 0.0,
            "max_heuristic_accepting_window_accepted_size_share": 0.0,
            "max_xgboost_accepting_window_accepted_share": 0.0,
            "max_xgboost_accepting_window_accepted_size_share": 0.0,
            "max_heuristic_top_two_accepting_window_accepted_share": 0.0,
            "max_heuristic_top_two_accepting_window_accepted_size_share": 0.0,
            "max_xgboost_top_two_accepting_window_accepted_share": 0.0,
            "max_xgboost_top_two_accepting_window_accepted_size_share": 0.0,
            "max_heuristic_accepting_window_accepted_concentration_index": 0.0,
            "max_heuristic_accepting_window_accepted_size_concentration_index": 0.0,
            "max_xgboost_accepting_window_accepted_concentration_index": 0.0,
            "max_xgboost_accepting_window_accepted_size_concentration_index": 0.0,
        }
        values.update(overrides)
        return values

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
        self.assertIn("request_token", columns)
        self.assertIn("pause_guard_penalty", columns)
        self.assertIn("daily_guard_window_penalty", columns)
        self.assertIn("live_guard_window_penalty", columns)
        self.assertIn("daily_guard_restart_window_penalty", columns)
        self.assertIn("live_guard_restart_window_penalty", columns)
        self.assertIn("open_exposure_penalty", columns)
        self.assertIn("window_end_open_exposure_penalty", columns)
        self.assertIn("avg_window_end_open_exposure_penalty", columns)
        self.assertIn("carry_window_penalty", columns)
        self.assertIn("carry_restart_window_penalty", columns)
        self.assertIn("resolved_share_penalty", columns)
        self.assertIn("resolved_size_share_penalty", columns)
        self.assertIn("worst_window_resolved_share_penalty", columns)
        self.assertIn("worst_window_resolved_size_share_penalty", columns)
        self.assertIn("mode_resolved_share_penalty", columns)
        self.assertIn("mode_resolved_size_share_penalty", columns)
        self.assertIn("mode_worst_window_resolved_share_penalty", columns)
        self.assertIn("mode_worst_window_resolved_size_share_penalty", columns)
        self.assertIn("worst_active_window_accepted_penalty", columns)
        self.assertIn("worst_active_window_accepted_size_penalty", columns)
        self.assertIn("mode_worst_active_window_accepted_penalty", columns)
        self.assertIn("mode_worst_active_window_accepted_size_penalty", columns)
        self.assertIn("mode_loss_penalty", columns)
        self.assertIn("mode_inactivity_penalty", columns)
        self.assertIn("mode_accepted_window_count_penalty", columns)
        self.assertIn("mode_accepted_window_share_penalty", columns)
        self.assertIn("mode_non_accepting_active_window_streak_penalty", columns)
        self.assertIn("mode_non_accepting_active_window_episode_penalty", columns)
        self.assertIn("mode_accepting_window_accepted_share_penalty", columns)
        self.assertIn("mode_accepting_window_accepted_size_share_penalty", columns)
        self.assertIn("mode_top_two_accepting_window_accepted_share_penalty", columns)
        self.assertIn("mode_top_two_accepting_window_accepted_size_share_penalty", columns)
        self.assertIn("mode_accepting_window_accepted_concentration_index_penalty", columns)
        self.assertIn("mode_accepting_window_accepted_size_concentration_index_penalty", columns)
        self.assertIn("window_inactivity_penalty", columns)
        self.assertIn("accepted_window_count_penalty", columns)
        self.assertIn("accepted_window_share_penalty", columns)
        self.assertIn("non_accepting_active_window_episode_penalty", columns)
        self.assertIn("accepting_window_accepted_share_penalty", columns)
        self.assertIn("accepting_window_accepted_size_share_penalty", columns)
        self.assertIn("top_two_accepting_window_accepted_share_penalty", columns)
        self.assertIn("top_two_accepting_window_accepted_size_share_penalty", columns)
        self.assertIn("accepting_window_accepted_concentration_index_penalty", columns)
        self.assertIn("accepting_window_accepted_size_concentration_index_penalty", columns)
        self.assertIn("wallet_count_penalty", columns)
        self.assertIn("market_count_penalty", columns)
        self.assertIn("entry_price_band_count_penalty", columns)
        self.assertIn("time_to_close_band_count_penalty", columns)
        self.assertIn("wallet_concentration_penalty", columns)
        self.assertIn("market_concentration_penalty", columns)
        self.assertIn("entry_price_band_concentration_penalty", columns)
        self.assertIn("time_to_close_band_concentration_penalty", columns)
        self.assertIn("wallet_size_concentration_penalty", columns)
        self.assertIn("market_size_concentration_penalty", columns)
        self.assertIn("entry_price_band_size_concentration_penalty", columns)
        self.assertIn("time_to_close_band_size_concentration_penalty", columns)

    def test_db_init_db_backfills_replay_run_open_exposure_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                try:
                    columns = {
                        str(row["name"])
                        for row in conn.execute("PRAGMA table_info(replay_runs)").fetchall()
                    }
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path

        self.assertIn("peak_open_exposure_usd", columns)
        self.assertIn("max_open_exposure_share", columns)
        self.assertIn("window_end_open_exposure_usd", columns)
        self.assertIn("window_end_open_exposure_share", columns)
        self.assertIn("window_end_live_guard_triggered", columns)
        self.assertIn("window_end_daily_guard_triggered", columns)

    def test_main_ranks_grid_candidates_and_keeps_json_on_stdout(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
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

    def test_evaluate_candidate_threads_continuity_state_between_windows(self) -> None:
        seen_initial_states: list[dict[str, object] | None] = []

        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            seen_initial_states.append(initial_state)
            if start_ts == 0:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "initial_bankroll_usd": 1000.0,
                    "final_equity_usd": 1000.0,
                    "final_bankroll_usd": 900.0,
                    "peak_equity_usd": 1000.0,
                    "min_equity_usd": 1000.0,
                    "total_pnl_usd": 0.0,
                    "max_drawdown_pct": 0.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 100.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                "rejected_count": 0,
                "unresolved_count": 1,
                "trade_count": 1,
                "win_rate": None,
                "window_end_open_exposure_usd": 100.0,
                "window_end_open_exposure_share": 0.1,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 1,
                        "accepted_size_usd": 100.0,
                        "resolved_count": 0,
                        "resolved_size_usd": 0.0,
                        "trade_count": 1,
                        "total_pnl_usd": 0.0,
                        "win_count": 0,
                    }
                },
                "continuity_state": {
                    "realized_pnl_usd": 0.0,
                    "open_positions": [
                        {
                            "close_ts": 150,
                                "market_id": "market-a",
                                "trader_address": "0xcarry",
                                "size_usd": 100.0,
                                "pnl_usd": 20.0,
                            }
                        ],
                        "live_guard_triggered": False,
                        "live_guard_start_equity": 1000.0,
                        "daily_guard_day_key": "",
                        "daily_guard_locked": False,
                        "daily_guard_start_equity": 1000.0,
                    },
                }
            return {
                "run_id": 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "initial_bankroll_usd": 1000.0,
                "final_equity_usd": 1020.0,
                "final_bankroll_usd": 1020.0,
                "peak_equity_usd": 1020.0,
                "min_equity_usd": 1000.0,
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 0,
                "accepted_size_usd": 0.0,
                "resolved_count": 1,
                "resolved_size_usd": 100.0,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 0,
                "win_rate": 1.0,
                "window_end_open_exposure_usd": 0.0,
                "window_end_open_exposure_share": 0.0,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 0,
                        "accepted_size_usd": 0.0,
                        "resolved_count": 1,
                        "resolved_size_usd": 100.0,
                        "trade_count": 0,
                        "total_pnl_usd": 20.0,
                        "win_count": 1,
                    }
                },
                "continuity_state": {
                    "realized_pnl_usd": 20.0,
                    "open_positions": [],
                    "live_guard_triggered": False,
                    "live_guard_start_equity": 1000.0,
                    "daily_guard_day_key": "",
                    "daily_guard_locked": False,
                    "daily_guard_start_equity": 1000.0,
                },
            }

        with patch.object(replay_search, "run_replay", side_effect=fake_run_replay):
            result = replay_search._evaluate_candidate(
                policy=replay_search.ReplayPolicy.default(),
                db_path=None,
                label="continuity",
                notes="",
                windows=[(0, 100), (100, 200)],
            )

        self.assertIsNone(seen_initial_states[0])
        self.assertIsNotNone(seen_initial_states[1])
        self.assertEqual(
            seen_initial_states[1]["open_positions"][0]["market_id"],
            "market-a",
        )
        self.assertEqual(result["accepted_count"], 1)
        self.assertEqual(result["resolved_count"], 1)
        self.assertEqual(result["unresolved_count"], 0)
        self.assertEqual(result["active_window_count"], 2)
        self.assertAlmostEqual(result["total_pnl_usd"], 20.0)
        self.assertEqual(result["signal_mode_summary"]["heuristic"]["inactive_window_count"], 0)
        self.assertEqual(result["signal_mode_summary"]["heuristic"]["positive_window_count"], 1)

    def test_main_does_not_count_carry_only_open_mode_window_as_inactive(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            if start_ts == 1:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "initial_bankroll_usd": 1000.0,
                    "final_equity_usd": 1000.0,
                    "final_bankroll_usd": 900.0,
                    "peak_equity_usd": 1000.0,
                    "min_equity_usd": 1000.0,
                    "total_pnl_usd": 0.0,
                    "max_drawdown_pct": 0.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 100.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 1,
                    "win_rate": None,
                    "window_end_open_exposure_usd": 100.0,
                    "window_end_open_exposure_share": 0.1,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 1,
                            "accepted_size_usd": 100.0,
                            "resolved_count": 0,
                            "resolved_size_usd": 0.0,
                            "trade_count": 1,
                            "total_pnl_usd": 0.0,
                            "win_count": 0,
                        }
                    },
                    "window_end_signal_mode_exposure": {
                        "heuristic": {
                            "open_count": 1,
                            "open_size_usd": 100.0,
                        }
                    },
                    "continuity_state": {
                        "realized_pnl_usd": 0.0,
                        "open_positions": [
                            {
                                "close_ts": 250,
                                "market_id": "market-a",
                                "trader_address": "0xcarry",
                                "size_usd": 100.0,
                                "pnl_usd": 20.0,
                            }
                        ],
                        "live_guard_triggered": False,
                        "live_guard_start_equity": 1000.0,
                        "daily_guard_day_key": "",
                        "daily_guard_locked": False,
                        "daily_guard_start_equity": 1000.0,
                    },
                }
            return {
                "run_id": 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "initial_bankroll_usd": 1000.0,
                "final_equity_usd": 1000.0,
                "final_bankroll_usd": 900.0,
                "peak_equity_usd": 1000.0,
                "min_equity_usd": 1000.0,
                "total_pnl_usd": 0.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 0,
                "accepted_size_usd": 0.0,
                "resolved_count": 0,
                "resolved_size_usd": 0.0,
                "rejected_count": 0,
                "unresolved_count": 1,
                "trade_count": 0,
                "win_rate": None,
                "window_end_open_exposure_usd": 100.0,
                "window_end_open_exposure_share": 0.1,
                "signal_mode_summary": {},
                "window_end_signal_mode_exposure": {
                    "heuristic": {
                        "open_count": 1,
                        "open_size_usd": 100.0,
                    }
                },
                "continuity_state": {
                    "realized_pnl_usd": 0.0,
                    "open_positions": [
                        {
                            "close_ts": 250,
                            "market_id": "market-a",
                            "trader_address": "0xcarry",
                            "size_usd": 100.0,
                            "pnl_usd": 20.0,
                        }
                    ],
                    "live_guard_triggered": False,
                    "live_guard_start_equity": 1000.0,
                    "daily_guard_day_key": "",
                    "daily_guard_locked": False,
                    "daily_guard_start_equity": 1000.0,
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
            "--max-heuristic-inactive-windows",
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
        ranked = payload["ranked"][0]
        self.assertEqual(ranked["constraint_failures"], [])
        self.assertEqual(ranked["result"]["signal_mode_summary"]["heuristic"]["inactive_window_count"], 0)
        self.assertNotIn("reject heuristic_inactive_window_count", stderr.getvalue())

    def test_main_does_not_count_carry_only_mode_resolution_as_inactive(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            if start_ts == 1:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "initial_bankroll_usd": 1000.0,
                    "final_equity_usd": 1000.0,
                    "final_bankroll_usd": 900.0,
                    "peak_equity_usd": 1000.0,
                    "min_equity_usd": 1000.0,
                    "total_pnl_usd": 0.0,
                    "max_drawdown_pct": 0.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 100.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 1,
                    "win_rate": None,
                    "window_end_open_exposure_usd": 100.0,
                    "window_end_open_exposure_share": 0.1,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 1,
                            "accepted_size_usd": 100.0,
                            "resolved_count": 0,
                            "resolved_size_usd": 0.0,
                            "trade_count": 1,
                            "total_pnl_usd": 0.0,
                            "win_count": 0,
                        }
                    },
                    "continuity_state": {
                        "realized_pnl_usd": 0.0,
                        "open_positions": [
                            {
                                "close_ts": 100,
                                "market_id": "market-a",
                                "trader_address": "0xcarry",
                                "size_usd": 100.0,
                                "pnl_usd": 20.0,
                            }
                        ],
                        "live_guard_triggered": False,
                        "live_guard_start_equity": 1000.0,
                        "daily_guard_day_key": "",
                        "daily_guard_locked": False,
                        "daily_guard_start_equity": 1000.0,
                    },
                }
            return {
                "run_id": 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "initial_bankroll_usd": 1000.0,
                "final_equity_usd": 1020.0,
                "final_bankroll_usd": 1020.0,
                "peak_equity_usd": 1020.0,
                "min_equity_usd": 1000.0,
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 0,
                "accepted_size_usd": 0.0,
                "resolved_count": 1,
                "resolved_size_usd": 100.0,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 0,
                "win_rate": 1.0,
                "window_end_open_exposure_usd": 0.0,
                "window_end_open_exposure_share": 0.0,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 0,
                        "accepted_size_usd": 0.0,
                        "resolved_count": 1,
                        "resolved_size_usd": 100.0,
                        "trade_count": 0,
                        "total_pnl_usd": 20.0,
                        "win_count": 1,
                    }
                },
                "continuity_state": {
                    "realized_pnl_usd": 20.0,
                    "open_positions": [],
                    "live_guard_triggered": False,
                    "live_guard_start_equity": 1000.0,
                    "daily_guard_day_key": "",
                    "daily_guard_locked": False,
                    "daily_guard_start_equity": 1000.0,
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
            "--max-heuristic-inactive-windows",
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
        ranked = payload["ranked"][0]
        self.assertEqual(ranked["constraint_failures"], [])
        self.assertEqual(ranked["result"]["signal_mode_summary"]["heuristic"]["inactive_window_count"], 0)
        self.assertNotIn("reject heuristic_inactive_window_count", stderr.getvalue())

    def test_main_single_window_materializes_window_activity_fields(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
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
            "--base-policy-json",
            json.dumps({"allow_heuristic": False}),
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

    def test_active_window_count_uses_exact_single_window_resolution_fallback(self) -> None:
        self.assertEqual(
            replay_search._active_window_count(
                {
                    "window_count": 1,
                    "accepted_count": 0,
                    "resolved_count": 1,
                    "resolved_size_usd": 100.0,
                    "total_pnl_usd": 20.0,
                }
            ),
            1,
        )

    def test_active_window_count_uses_exact_single_window_carry_fallback(self) -> None:
        self.assertEqual(
            replay_search._active_window_count(
                {
                    "window_count": 1,
                    "accepted_count": 0,
                    "resolved_count": 0,
                    "window_end_open_exposure_usd": 100.0,
                }
            ),
            1,
        )

    def test_signal_mode_summary_materializes_worst_active_window_accepted_count(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": 9.0,
                        "win_count": 2,
                    }
                }
            }
        )

        self.assertEqual(summary["xgboost"]["worst_active_window_accepted_count"], 4)
        self.assertEqual(summary["xgboost"]["worst_accepting_window_accepted_count"], 4)

    def test_signal_mode_summary_materializes_worst_active_window_accepted_size_usd(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 96.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 96.0,
                        "trade_count": 4,
                        "total_pnl_usd": 9.0,
                        "win_count": 2,
                    }
                }
            }
        )

        self.assertEqual(summary["xgboost"]["worst_active_window_accepted_size_usd"], 96.0)
        self.assertEqual(summary["xgboost"]["worst_accepting_window_accepted_size_usd"], 96.0)

    def test_signal_mode_summary_fails_closed_on_legacy_multi_window_mode_worst_active_depth(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "window_count": 4,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 96.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 96.0,
                        "trade_count": 4,
                        "total_pnl_usd": 9.0,
                    }
                },
            }
        )

        self.assertIsNone(summary["xgboost"]["worst_active_window_accepted_count"])
        self.assertIsNone(summary["xgboost"]["worst_accepting_window_accepted_count"])
        self.assertIsNone(summary["xgboost"]["worst_active_window_accepted_size_usd"])
        self.assertIsNone(summary["xgboost"]["worst_accepting_window_accepted_size_usd"])

    def test_with_window_activity_fields_materializes_worst_accepting_window_aliases(self) -> None:
        enriched = replay_search._with_window_activity_fields(
            {
                "accepted_count": 3,
                "accepted_size_usd": 75.0,
                "resolved_count": 3,
                "resolved_size_usd": 75.0,
                "trade_count": 3,
                "total_pnl_usd": 5.0,
                "initial_bankroll_usd": 1000.0,
            }
        )

        self.assertEqual(enriched["worst_active_window_accepted_count"], 3)
        self.assertEqual(enriched["worst_accepting_window_accepted_count"], 3)
        self.assertEqual(enriched["worst_active_window_accepted_size_usd"], 75.0)
        self.assertEqual(enriched["worst_accepting_window_accepted_size_usd"], 75.0)

    def test_with_worst_window_resolved_share_fails_closed_on_legacy_multi_window_payload(self) -> None:
        enriched = replay_search._with_worst_window_resolved_share(
            {
                "window_count": 4,
                "accepted_count": 6,
                "resolved_count": 6,
                "accepted_size_usd": 120.0,
                "resolved_size_usd": 120.0,
            }
        )

        self.assertEqual(enriched["worst_window_resolved_share"], 0.0)
        self.assertEqual(enriched["worst_active_window_resolved_share"], 0.0)
        self.assertEqual(enriched["worst_window_resolved_size_share"], 0.0)
        self.assertEqual(enriched["worst_active_window_resolved_size_share"], 0.0)

    def test_with_worst_window_resolved_share_uses_exact_single_window_payload(self) -> None:
        enriched = replay_search._with_worst_window_resolved_share(
            {
                "window_count": 1,
                "accepted_count": 6,
                "resolved_count": 3,
                "accepted_size_usd": 120.0,
                "resolved_size_usd": 60.0,
            }
        )

        self.assertEqual(enriched["worst_window_resolved_share"], 0.5)
        self.assertEqual(enriched["worst_active_window_resolved_share"], 0.5)
        self.assertEqual(enriched["worst_window_resolved_size_share"], 0.5)
        self.assertEqual(enriched["worst_active_window_resolved_size_share"], 0.5)

    def test_signal_mode_summary_materializes_accepted_window_count(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 96.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 96.0,
                        "trade_count": 4,
                        "total_pnl_usd": 9.0,
                        "win_count": 2,
                    }
                }
            }
        )

        self.assertEqual(summary["xgboost"]["accepted_window_count"], 1)

    def test_signal_mode_summary_fails_closed_on_legacy_multi_window_mode_worst_coverage(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "window_count": 4,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 96.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 96.0,
                        "trade_count": 4,
                        "total_pnl_usd": 9.0,
                    }
                },
            }
        )

        self.assertEqual(summary["xgboost"]["worst_window_resolved_share"], 0.0)
        self.assertEqual(summary["xgboost"]["worst_active_window_resolved_share"], 0.0)
        self.assertEqual(summary["xgboost"]["worst_window_resolved_size_share"], 0.0)
        self.assertEqual(summary["xgboost"]["worst_active_window_resolved_size_share"], 0.0)

    def test_signal_mode_summary_fails_closed_on_legacy_multi_window_worst_window_pnl(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "window_count": 4,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": 9.0,
                    }
                },
            }
        )

        self.assertEqual(summary["xgboost"]["worst_window_pnl_usd"], 0.0)
        self.assertEqual(summary["xgboost"]["best_window_pnl_usd"], 9.0)

    def test_signal_mode_summary_fails_closed_on_legacy_multi_window_mode_positive_windows(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "window_count": 4,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": 9.0,
                    }
                },
            }
        )

        self.assertEqual(summary["xgboost"]["positive_window_count"], 0)
        self.assertEqual(summary["xgboost"]["negative_window_count"], 0)

    def test_signal_mode_summary_uses_exact_mode_mix_for_single_window_payload(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "window_count": 1,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "accepted_size_usd": 72.0,
                        "resolved_count": 6,
                        "resolved_size_usd": 72.0,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 48.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 48.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                },
            }
        )

        self.assertEqual(summary["heuristic"]["min_active_window_accepted_share"], 0.6)
        self.assertEqual(summary["heuristic"]["max_active_window_accepted_share"], 0.6)
        self.assertEqual(summary["heuristic"]["min_active_window_accepted_size_share"], 0.6)
        self.assertEqual(summary["heuristic"]["max_active_window_accepted_size_share"], 0.6)
        self.assertEqual(summary["xgboost"]["min_active_window_accepted_share"], 0.4)
        self.assertEqual(summary["xgboost"]["max_active_window_accepted_share"], 0.4)
        self.assertEqual(summary["xgboost"]["min_active_window_accepted_size_share"], 0.4)
        self.assertEqual(summary["xgboost"]["max_active_window_accepted_size_share"], 0.4)

    def test_signal_mode_summary_fails_closed_on_legacy_multi_window_mode_mix(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "accepted_size_usd": 72.0,
                        "resolved_count": 6,
                        "resolved_size_usd": 72.0,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 48.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 48.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                },
            }
        )

        self.assertEqual(summary["heuristic"]["min_active_window_accepted_share"], 0.0)
        self.assertEqual(summary["heuristic"]["max_active_window_accepted_share"], 1.0)
        self.assertEqual(summary["heuristic"]["min_active_window_accepted_size_share"], 0.0)
        self.assertEqual(summary["heuristic"]["max_active_window_accepted_size_share"], 1.0)
        self.assertEqual(summary["xgboost"]["min_active_window_accepted_share"], 0.0)
        self.assertEqual(summary["xgboost"]["max_active_window_accepted_share"], 1.0)
        self.assertEqual(summary["xgboost"]["min_active_window_accepted_size_share"], 0.0)
        self.assertEqual(summary["xgboost"]["max_active_window_accepted_size_share"], 1.0)

    def test_signal_mode_summary_fails_closed_on_legacy_multi_window_accepting_window_concentration(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "accepted_size_usd": 72.0,
                        "accepted_window_count": 3,
                        "resolved_count": 6,
                        "resolved_size_usd": 72.0,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                },
            }
        )

        self.assertEqual(summary["heuristic"]["max_accepting_window_accepted_share"], 1.0)
        self.assertEqual(summary["heuristic"]["max_accepting_window_accepted_size_share"], 1.0)
        self.assertEqual(summary["heuristic"]["top_two_accepting_window_accepted_share"], 1.0)
        self.assertEqual(summary["heuristic"]["top_two_accepting_window_accepted_size_share"], 1.0)
        self.assertEqual(summary["heuristic"]["accepting_window_accepted_concentration_index"], 1.0)
        self.assertEqual(summary["heuristic"]["accepting_window_accepted_size_concentration_index"], 1.0)

    def test_signal_mode_summary_fails_closed_on_legacy_multi_window_accept_counts(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "window_count": 4,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 96.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 96.0,
                        "inactive_window_count": 0,
                        "max_non_accepting_active_window_streak": 2,
                        "non_accepting_active_window_episode_count": 1,
                        "trade_count": 4,
                        "total_pnl_usd": 9.0,
                        "win_count": 2,
                    }
                }
            }
        )

        self.assertEqual(summary["xgboost"]["accepted_window_count"], 1)
        self.assertEqual(summary["xgboost"]["post_accept_active_window_count"], 3)

    def test_signal_mode_summary_materializes_mode_non_accepting_active_window_streak(self) -> None:
        summary = replay_search._signal_mode_summary(
            {
                "window_count": 2,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 96.0,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                        "resolved_count": 4,
                        "resolved_size_usd": 96.0,
                        "trade_count": 4,
                        "total_pnl_usd": 9.0,
                        "win_count": 2,
                    }
                }
            }
        )

        self.assertEqual(summary["xgboost"]["max_non_accepting_active_window_streak"], 1)

    def test_accepted_window_count_fallback_uses_accepted_count_for_multi_window_payload(self) -> None:
        self.assertEqual(
            replay_search._accepted_window_count(
                {
                    "window_count": 2,
                    "active_window_count": 2,
                    "accepted_count": 3,
                    "accepted_size_usd": 0.0,
                }
            ),
            1,
        )
        enriched = replay_search._with_window_activity_fields(
            {
                "window_count": 2,
                "active_window_count": 2,
                "inactive_window_count": 0,
                "accepted_count": 3,
                "accepted_size_usd": 0.0,
                "resolved_count": 3,
                "resolved_size_usd": 0.0,
                "trade_count": 3,
                "total_pnl_usd": 5.0,
                "initial_bankroll_usd": 1000.0,
            }
        )
        self.assertEqual(enriched["accepted_window_count"], 1)
        self.assertEqual(enriched["post_accept_active_window_count"], 2)

    def test_mode_accepted_window_count_fallback_fails_closed_on_legacy_multi_window_payload(self) -> None:
        signal_mode_summary = {
            "xgboost": {
                "accepted_count": 3,
                "accepted_size_usd": 72.0,
                "inactive_window_count": 0,
            }
        }

        self.assertEqual(
            replay_search._mode_accepted_window_count(signal_mode_summary, "xgboost", 3),
            1,
        )

    def test_with_window_activity_fields_conservatively_materializes_post_accept_count(self) -> None:
        enriched = replay_search._with_window_activity_fields(
            {
                "window_count": 5,
                "active_window_count": 5,
                "inactive_window_count": 0,
                "accepted_count": 3,
                "accepted_size_usd": 60.0,
                "resolved_count": 3,
                "resolved_size_usd": 60.0,
                "max_non_accepting_active_window_streak": 2,
                "non_accepting_active_window_episode_count": 1,
                "trade_count": 3,
                "total_pnl_usd": 5.0,
                "initial_bankroll_usd": 1000.0,
            }
        )

        self.assertEqual(enriched["accepted_window_count"], 1)
        self.assertEqual(enriched["post_accept_active_window_count"], 3)

    def test_aggregate_window_results_uses_stitched_max_drawdown_pct(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 90.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 90.0,
                    "total_pnl_usd": -10.0,
                    "max_drawdown_pct": 0.10,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "win_rate": 0.5,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 70.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 70.0,
                    "total_pnl_usd": -30.0,
                    "max_drawdown_pct": 0.30,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "win_rate": 0.5,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["worst_window_drawdown_pct"], 0.3)
        self.assertEqual(result["max_drawdown_pct"], 0.37)

    def test_with_window_activity_fields_derives_window_end_open_exposure(self) -> None:
        result = replay_search._with_window_activity_fields(
            {
                "initial_bankroll_usd": 100.0,
                "final_equity_usd": 95.0,
                "final_bankroll_usd": 80.0,
                "accepted_count": 2,
                "resolved_count": 1,
            }
        )

        self.assertEqual(result["window_end_open_exposure_usd"], 15.0)
        self.assertAlmostEqual(result["window_end_open_exposure_share"], 15.0 / 95.0, places=6)
        self.assertEqual(result["max_window_end_open_exposure_usd"], 15.0)
        self.assertAlmostEqual(result["max_window_end_open_exposure_share"], 15.0 / 95.0, places=6)
        self.assertEqual(result["carry_window_count"], 1)
        self.assertEqual(result["carry_window_share"], 1.0)

        stressed = replay_search._with_window_activity_fields(
            {
                "initial_bankroll_usd": 100.0,
                "final_equity_usd": 0.0,
                "final_bankroll_usd": -10.0,
                "accepted_count": 1,
                "resolved_count": 0,
            }
        )

        self.assertEqual(stressed["window_end_open_exposure_usd"], 10.0)
        self.assertEqual(stressed["window_end_open_exposure_share"], 1.0)
        self.assertEqual(stressed["carry_window_count"], 1)
        self.assertEqual(stressed["carry_window_share"], 1.0)

        legacy_single_window = replay_search._with_window_activity_fields(
            {
                "initial_bankroll_usd": 100.0,
                "total_pnl_usd": 5.0,
                "final_bankroll_usd": 90.0,
                "accepted_count": 2,
                "resolved_count": 1,
            }
        )

        self.assertEqual(legacy_single_window["final_equity_usd"], 105.0)
        self.assertEqual(legacy_single_window["window_end_open_exposure_usd"], 15.0)
        self.assertAlmostEqual(legacy_single_window["window_end_open_exposure_share"], 15.0 / 105.0, places=6)
        self.assertEqual(legacy_single_window["carry_window_count"], 1)
        self.assertEqual(legacy_single_window["carry_window_share"], 1.0)

        legacy_null_final_equity = replay_search._with_window_activity_fields(
            {
                "initial_bankroll_usd": 100.0,
                "final_equity_usd": None,
                "total_pnl_usd": 5.0,
                "final_bankroll_usd": 90.0,
                "accepted_count": 2,
                "resolved_count": 1,
            }
        )

        self.assertEqual(legacy_null_final_equity["final_equity_usd"], 105.0)
        self.assertEqual(legacy_null_final_equity["window_end_open_exposure_usd"], 15.0)
        self.assertAlmostEqual(legacy_null_final_equity["window_end_open_exposure_share"], 15.0 / 105.0, places=6)
        self.assertEqual(legacy_null_final_equity["carry_window_count"], 1)
        self.assertEqual(legacy_null_final_equity["carry_window_share"], 1.0)

        legacy_null_carry_fields = replay_search._with_window_activity_fields(
            {
                "initial_bankroll_usd": 100.0,
                "final_equity_usd": 105.0,
                "final_bankroll_usd": 90.0,
                "accepted_count": 2,
                "resolved_count": 1,
                "window_end_open_exposure_usd": None,
                "window_end_open_exposure_share": None,
                "max_window_end_open_exposure_usd": None,
                "max_window_end_open_exposure_share": None,
                "avg_window_end_open_exposure_share": None,
                "carry_window_count": None,
                "carry_window_share": None,
            }
        )

        self.assertEqual(legacy_null_carry_fields["window_end_open_exposure_usd"], 15.0)
        self.assertAlmostEqual(legacy_null_carry_fields["window_end_open_exposure_share"], 15.0 / 105.0, places=6)
        self.assertEqual(legacy_null_carry_fields["max_window_end_open_exposure_usd"], 15.0)
        self.assertAlmostEqual(legacy_null_carry_fields["max_window_end_open_exposure_share"], 15.0 / 105.0, places=6)
        self.assertAlmostEqual(legacy_null_carry_fields["avg_window_end_open_exposure_share"], 15.0 / 105.0, places=6)
        self.assertEqual(legacy_null_carry_fields["carry_window_count"], 1)
        self.assertEqual(legacy_null_carry_fields["carry_window_share"], 1.0)

        live_guarded = replay_search._with_window_activity_fields(
            {
                "initial_bankroll_usd": 100.0,
                "final_equity_usd": 80.0,
                "accepted_count": 1,
                "resolved_count": 1,
                "window_end_live_guard_triggered": 1,
            }
        )

        self.assertEqual(live_guarded["live_guard_window_count"], 1)
        self.assertEqual(live_guarded["live_guard_window_share"], 1.0)

        legacy_null_activity_fields = replay_search._with_window_activity_fields(
            {
                "initial_bankroll_usd": 100.0,
                "final_equity_usd": 110.0,
                "final_bankroll_usd": 110.0,
                "total_pnl_usd": 10.0,
                "accepted_count": 2,
                "accepted_size_usd": 50.0,
                "resolved_count": 2,
                "resolved_size_usd": 50.0,
                "peak_open_exposure_usd": None,
                "max_open_exposure_share": None,
                "live_guard_window_count": None,
                "live_guard_window_share": None,
                "positive_window_count": None,
                "negative_window_count": None,
                "active_window_count": None,
                "inactive_window_count": None,
                "worst_active_window_accepted_count": None,
                "worst_active_window_accepted_size_usd": None,
                "worst_accepting_window_accepted_count": None,
                "worst_accepting_window_accepted_size_usd": None,
                "accepted_window_count": None,
                "max_accepting_window_accepted_share": None,
                "max_accepting_window_accepted_size_share": None,
                "top_two_accepting_window_accepted_share": None,
                "top_two_accepting_window_accepted_size_share": None,
                "accepting_window_accepted_concentration_index": None,
                "accepting_window_accepted_size_concentration_index": None,
                "window_end_live_guard_triggered": 1,
            }
        )

        self.assertEqual(legacy_null_activity_fields["peak_open_exposure_usd"], 0.0)
        self.assertEqual(legacy_null_activity_fields["max_open_exposure_share"], 0.0)
        self.assertEqual(legacy_null_activity_fields["live_guard_window_count"], 1)
        self.assertEqual(legacy_null_activity_fields["live_guard_window_share"], 1.0)
        self.assertEqual(legacy_null_activity_fields["positive_window_count"], 1)
        self.assertEqual(legacy_null_activity_fields["negative_window_count"], 0)
        self.assertEqual(legacy_null_activity_fields["active_window_count"], 1)
        self.assertEqual(legacy_null_activity_fields["inactive_window_count"], 0)
        self.assertEqual(legacy_null_activity_fields["worst_active_window_accepted_count"], 2)
        self.assertEqual(legacy_null_activity_fields["worst_active_window_accepted_size_usd"], 50.0)
        self.assertEqual(legacy_null_activity_fields["worst_accepting_window_accepted_count"], 2)
        self.assertEqual(legacy_null_activity_fields["worst_accepting_window_accepted_size_usd"], 50.0)
        self.assertEqual(legacy_null_activity_fields["accepted_window_count"], 1)
        self.assertEqual(legacy_null_activity_fields["max_accepting_window_accepted_share"], 1.0)
        self.assertEqual(legacy_null_activity_fields["max_accepting_window_accepted_size_share"], 1.0)
        self.assertEqual(legacy_null_activity_fields["top_two_accepting_window_accepted_share"], 1.0)
        self.assertEqual(legacy_null_activity_fields["top_two_accepting_window_accepted_size_share"], 1.0)
        self.assertEqual(legacy_null_activity_fields["accepting_window_accepted_concentration_index"], 1.0)
        self.assertEqual(legacy_null_activity_fields["accepting_window_accepted_size_concentration_index"], 1.0)

        daily_guarded = replay_search._with_window_activity_fields(
            {
                "initial_bankroll_usd": 100.0,
                "final_equity_usd": 80.0,
                "accepted_count": 1,
                "resolved_count": 1,
                "window_end_daily_guard_triggered": 1,
            }
        )

        self.assertEqual(daily_guarded["daily_guard_window_count"], 1)
        self.assertEqual(daily_guarded["daily_guard_window_share"], 1.0)

    def test_window_equity_summary_uses_equity_pnl_fallback_before_final_bankroll(self) -> None:
        start_equity, final_equity, peak_equity, min_equity = replay_search._window_equity_summary(
            {
                "initial_bankroll_usd": 100.0,
                "total_pnl_usd": 5.0,
                "final_bankroll_usd": 90.0,
            },
            default_start_equity=100.0,
        )

        self.assertEqual(start_equity, 100.0)
        self.assertEqual(final_equity, 105.0)
        self.assertEqual(peak_equity, 105.0)
        self.assertEqual(min_equity, 100.0)

    def test_single_window_share_helpers_use_exact_end_state_fallbacks(self) -> None:
        result = {
            "window_count": 1,
            "initial_bankroll_usd": 100.0,
            "total_pnl_usd": 5.0,
            "final_bankroll_usd": 90.0,
            "accepted_count": 2,
            "resolved_count": 2,
            "trade_count": 2,
            "window_end_live_guard_triggered": 1,
            "window_end_daily_guard_triggered": 1,
        }

        self.assertAlmostEqual(
            replay_search._avg_window_end_open_exposure_share(result),
            15.0 / 105.0,
            places=6,
        )
        self.assertEqual(replay_search._carry_window_share(result), 1.0)
        self.assertEqual(replay_search._live_guard_window_share(result), 1.0)
        self.assertEqual(replay_search._daily_guard_window_share(result), 1.0)

    def test_aggregate_window_results_tracks_max_window_end_open_exposure_share(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 10.0,
                    "window_end_open_exposure_share": 10.0 / 95.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 90.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 90.0,
                    "total_pnl_usd": -10.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 30.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 15.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 18.0,
                    "window_end_open_exposure_share": 0.2,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["max_window_end_open_exposure_usd"], 18.0)
        self.assertEqual(result["max_window_end_open_exposure_share"], 0.2)
        self.assertEqual(result["avg_window_end_open_exposure_share"], round(((10.0 / 95.0) + 0.2) / 2.0, 6))
        self.assertEqual(result["carry_window_count"], 2)
        self.assertEqual(result["carry_window_share"], 1.0)

    def test_aggregate_window_results_tracks_avg_window_end_open_exposure_share(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 10.0,
                    "window_end_open_exposure_share": 10.0 / 95.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.9,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["active_window_count"], 2)
        self.assertEqual(result["avg_window_end_open_exposure_share"], round((10.0 / 95.0) / 2.0, 6))

    def test_aggregate_window_results_uses_active_carry_only_windows_for_avg_carry_share(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 10.0,
                    "window_end_open_exposure_share": 10.0 / 95.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 95.0,
                    "final_equity_usd": 101.0,
                    "peak_equity_usd": 101.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": 6.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 5.0,
                    "window_end_open_exposure_share": 5.0 / 101.0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        expected_avg = round(((10.0 / 95.0) + (5.0 / 101.0)) / 2.0, 6)
        self.assertEqual(result["active_window_count"], 2)
        self.assertEqual(result["avg_window_end_open_exposure_share"], expected_avg)

    def test_aggregate_window_results_tracks_final_bankroll_separately_from_max_carry(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 20.0,
                    "window_end_open_exposure_share": 20.0 / 95.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 90.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 90.0,
                    "total_pnl_usd": -10.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 5.0,
                    "window_end_open_exposure_share": 5.0 / 90.0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["total_pnl_usd"], -15.0)
        self.assertEqual(result["final_equity_usd"], 85.0)
        self.assertEqual(result["window_end_open_exposure_usd"], 5.0)
        self.assertEqual(result["window_end_open_exposure_share"], round(5.0 / 85.0, 6))
        self.assertEqual(result["final_bankroll_usd"], 80.0)
        self.assertEqual(result["max_window_end_open_exposure_usd"], 20.0)
        self.assertEqual(result["max_window_end_open_exposure_share"], round(20.0 / 95.0, 6))

    def test_aggregate_window_results_tracks_carry_window_share(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 10.0,
                    "window_end_open_exposure_share": 10.0 / 95.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["carry_window_count"], 1)
        self.assertEqual(result["carry_window_share"], 0.5)

    def test_aggregate_window_results_tracks_live_guard_window_share(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_live_guard_triggered": 1,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_live_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_live_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["live_guard_window_count"], 1)
        self.assertEqual(result["live_guard_window_share"], 0.5)

    def test_aggregate_window_results_tracks_carry_restart_window_share(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 10.0,
                    "window_end_open_exposure_share": 10.0 / 95.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["carry_restart_window_count"], 1)
        self.assertEqual(result["carry_restart_window_opportunity_count"], 1)
        self.assertEqual(result["carry_restart_window_share"], 1.0)

    def test_aggregate_window_results_tracks_carry_restart_across_inactive_gap(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 10.0,
                    "window_end_open_exposure_share": 10.0 / 95.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["carry_restart_window_count"], 1)
        self.assertEqual(result["carry_restart_window_opportunity_count"], 1)
        self.assertEqual(result["carry_restart_window_share"], 1.0)

    def test_aggregate_window_results_tracks_carry_restart_on_resolution_only_window(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 10.0,
                    "window_end_open_exposure_share": 10.0 / 95.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 95.0,
                    "final_equity_usd": 101.0,
                    "peak_equity_usd": 101.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": 6.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["carry_restart_window_count"], 1)
        self.assertEqual(result["carry_restart_window_opportunity_count"], 1)
        self.assertEqual(result["carry_restart_window_share"], 1.0)

    def test_aggregate_window_results_tracks_daily_guard_restart_window_share(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_daily_guard_triggered": 1,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_daily_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_daily_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["daily_guard_restart_window_count"], 1)
        self.assertEqual(result["daily_guard_restart_window_opportunity_count"], 1)
        self.assertEqual(result["daily_guard_restart_window_share"], 1.0)

    def test_aggregate_window_results_tracks_daily_guard_restart_across_inactive_gap(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_daily_guard_triggered": 1,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_daily_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_daily_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["daily_guard_restart_window_count"], 1)
        self.assertEqual(result["daily_guard_restart_window_opportunity_count"], 1)
        self.assertEqual(result["daily_guard_restart_window_share"], 1.0)

    def test_aggregate_window_results_tracks_daily_guard_restart_on_resolution_only_window(
        self,
    ) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_daily_guard_triggered": 1,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 95.0,
                    "final_equity_usd": 101.0,
                    "peak_equity_usd": 101.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": 6.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_daily_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["daily_guard_restart_window_count"], 1)
        self.assertEqual(result["daily_guard_restart_window_opportunity_count"], 1)
        self.assertEqual(result["daily_guard_restart_window_share"], 1.0)

    def test_aggregate_window_results_tracks_live_guard_restart_window_share(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_live_guard_triggered": 1,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_live_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_live_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["live_guard_restart_window_count"], 1)
        self.assertEqual(result["live_guard_restart_window_opportunity_count"], 1)
        self.assertEqual(result["live_guard_restart_window_share"], 1.0)

    def test_aggregate_window_results_tracks_live_guard_restart_on_resolution_only_window(
        self,
    ) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_live_guard_triggered": 1,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 95.0,
                    "final_equity_usd": 101.0,
                    "peak_equity_usd": 101.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": 6.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_live_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["live_guard_restart_window_count"], 1)
        self.assertEqual(result["live_guard_restart_window_opportunity_count"], 1)
        self.assertEqual(result["live_guard_restart_window_share"], 1.0)

    def test_aggregate_window_results_tracks_accepting_windows_separately_from_active_windows(
        self,
    ) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 98.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 98.0,
                    "total_pnl_usd": -2.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 120.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 1,
                    "window_end_open_exposure_usd": 120.0,
                    "window_end_open_exposure_share": 1.0,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 1,
                            "accepted_size_usd": 120.0,
                            "resolved_count": 0,
                            "resolved_size_usd": 0.0,
                            "trade_count": 1,
                            "total_pnl_usd": -2.0,
                        }
                    },
                    "window_end_signal_mode_exposure": {
                        "heuristic": {"open_count": 1, "open_size_usd": 120.0}
                    },
                },
                {
                    "initial_bankroll_usd": 98.0,
                    "final_equity_usd": 101.0,
                    "peak_equity_usd": 101.0,
                    "min_equity_usd": 98.0,
                    "total_pnl_usd": 3.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 120.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 0,
                            "accepted_size_usd": 0.0,
                            "resolved_count": 1,
                            "resolved_size_usd": 120.0,
                            "trade_count": 0,
                            "total_pnl_usd": 3.0,
                        }
                    },
                },
                {
                    "initial_bankroll_usd": 101.0,
                    "final_equity_usd": 104.0,
                    "peak_equity_usd": 104.0,
                    "min_equity_usd": 101.0,
                    "total_pnl_usd": 3.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 40.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 40.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 1,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 1,
                            "accepted_size_usd": 40.0,
                            "resolved_count": 1,
                            "resolved_size_usd": 40.0,
                            "trade_count": 1,
                            "total_pnl_usd": 3.0,
                        }
                    },
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["active_window_count"], 3)
        self.assertEqual(result["accepted_window_count"], 2)
        self.assertEqual(result["signal_mode_summary"]["heuristic"]["accepted_window_count"], 2)

    def test_aggregate_window_results_tracks_top_two_accepting_window_concentration(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 5,
                    "accepted_size_usd": 50.0,
                    "resolved_count": 5,
                    "resolved_size_usd": 50.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 5,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 4,
                            "accepted_size_usd": 40.0,
                            "resolved_count": 4,
                            "resolved_size_usd": 40.0,
                            "trade_count": 4,
                            "total_pnl_usd": 1.0,
                        },
                        "xgboost": {
                            "accepted_count": 1,
                            "accepted_size_usd": 10.0,
                            "resolved_count": 1,
                            "resolved_size_usd": 10.0,
                            "trade_count": 1,
                            "total_pnl_usd": 1.0,
                        },
                    },
                },
                {
                    "initial_bankroll_usd": 102.0,
                    "final_equity_usd": 104.0,
                    "peak_equity_usd": 104.0,
                    "min_equity_usd": 102.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 3,
                    "accepted_size_usd": 30.0,
                    "resolved_count": 3,
                    "resolved_size_usd": 30.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 3,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 2,
                            "accepted_size_usd": 20.0,
                            "resolved_count": 2,
                            "resolved_size_usd": 20.0,
                            "trade_count": 2,
                            "total_pnl_usd": 1.0,
                        },
                        "xgboost": {
                            "accepted_count": 1,
                            "accepted_size_usd": 10.0,
                            "resolved_count": 1,
                            "resolved_size_usd": 10.0,
                            "trade_count": 1,
                            "total_pnl_usd": 1.0,
                        },
                    },
                },
                {
                    "initial_bankroll_usd": 104.0,
                    "final_equity_usd": 107.0,
                    "peak_equity_usd": 107.0,
                    "min_equity_usd": 104.0,
                    "total_pnl_usd": 3.0,
                    "accepted_count": 3,
                    "accepted_size_usd": 30.0,
                    "resolved_count": 3,
                    "resolved_size_usd": 30.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 3,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 1,
                            "accepted_size_usd": 10.0,
                            "resolved_count": 1,
                            "resolved_size_usd": 10.0,
                            "trade_count": 1,
                            "total_pnl_usd": 1.0,
                        },
                        "xgboost": {
                            "accepted_count": 2,
                            "accepted_size_usd": 20.0,
                            "resolved_count": 2,
                            "resolved_size_usd": 20.0,
                            "trade_count": 2,
                            "total_pnl_usd": 2.0,
                        },
                    },
                },
                {
                    "initial_bankroll_usd": 107.0,
                    "final_equity_usd": 108.0,
                    "peak_equity_usd": 108.0,
                    "min_equity_usd": 107.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 10.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 1,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 1,
                            "accepted_size_usd": 10.0,
                            "resolved_count": 1,
                            "resolved_size_usd": 10.0,
                            "trade_count": 1,
                            "total_pnl_usd": 1.0,
                        },
                    },
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertAlmostEqual(result["top_two_accepting_window_accepted_share"], 8.0 / 12.0, places=6)
        self.assertAlmostEqual(result["top_two_accepting_window_accepted_size_share"], 8.0 / 12.0, places=6)
        self.assertAlmostEqual(result["accepting_window_accepted_concentration_index"], 11.0 / 36.0, places=6)
        self.assertAlmostEqual(result["accepting_window_accepted_size_concentration_index"], 11.0 / 36.0, places=6)
        self.assertAlmostEqual(result["signal_mode_summary"]["heuristic"]["top_two_accepting_window_accepted_share"], 0.75, places=6)
        self.assertAlmostEqual(result["signal_mode_summary"]["heuristic"]["top_two_accepting_window_accepted_size_share"], 0.75, places=6)
        self.assertAlmostEqual(
            result["signal_mode_summary"]["heuristic"]["accepting_window_accepted_concentration_index"],
            11.0 / 32.0,
            places=6,
        )
        self.assertAlmostEqual(
            result["signal_mode_summary"]["heuristic"]["accepting_window_accepted_size_concentration_index"],
            11.0 / 32.0,
            places=6,
        )
        self.assertAlmostEqual(result["signal_mode_summary"]["xgboost"]["top_two_accepting_window_accepted_share"], 0.75, places=6)
        self.assertAlmostEqual(result["signal_mode_summary"]["xgboost"]["top_two_accepting_window_accepted_size_share"], 0.75, places=6)
        self.assertAlmostEqual(
            result["signal_mode_summary"]["xgboost"]["accepting_window_accepted_concentration_index"],
            0.375,
            places=6,
        )
        self.assertAlmostEqual(
            result["signal_mode_summary"]["xgboost"]["accepting_window_accepted_size_concentration_index"],
            0.375,
            places=6,
        )

    def test_aggregate_window_results_tracks_non_accepting_active_window_streak(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 101.0,
                    "peak_equity_usd": 101.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 1,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 101.0,
                    "final_equity_usd": 103.0,
                    "peak_equity_usd": 103.0,
                    "min_equity_usd": 101.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 103.0,
                    "final_equity_usd": 104.0,
                    "peak_equity_usd": 104.0,
                    "min_equity_usd": 103.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 104.0,
                    "final_equity_usd": 106.0,
                    "peak_equity_usd": 106.0,
                    "min_equity_usd": 104.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 30.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 30.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["active_window_count"], 4)
        self.assertEqual(result["accepted_window_count"], 2)
        self.assertEqual(result["max_non_accepting_active_window_streak"], 2)
        self.assertEqual(result["non_accepting_active_window_episode_count"], 1)

    def test_aggregate_window_results_tracks_non_accepting_active_window_episodes(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 101.0,
                    "peak_equity_usd": 101.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 1,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 101.0,
                    "final_equity_usd": 103.0,
                    "peak_equity_usd": 103.0,
                    "min_equity_usd": 101.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 103.0,
                    "final_equity_usd": 104.0,
                    "peak_equity_usd": 104.0,
                    "min_equity_usd": 103.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 10.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 1,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 104.0,
                    "final_equity_usd": 106.0,
                    "peak_equity_usd": 106.0,
                    "min_equity_usd": 104.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["active_window_count"], 4)
        self.assertEqual(result["accepted_window_count"], 2)
        self.assertEqual(result["max_non_accepting_active_window_streak"], 1)
        self.assertEqual(result["non_accepting_active_window_episode_count"], 2)

    def test_aggregate_window_results_ignores_leading_non_accepting_active_windows_for_droughts(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 101.0,
                    "peak_equity_usd": 101.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 101.0,
                    "final_equity_usd": 103.0,
                    "peak_equity_usd": 103.0,
                    "min_equity_usd": 101.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 1,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 103.0,
                    "final_equity_usd": 104.0,
                    "peak_equity_usd": 104.0,
                    "min_equity_usd": 103.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["active_window_count"], 3)
        self.assertEqual(result["accepted_window_count"], 1)
        self.assertEqual(result["post_accept_active_window_count"], 2)
        self.assertEqual(result["max_non_accepting_active_window_streak"], 1)
        self.assertEqual(result["non_accepting_active_window_episode_count"], 1)

    def test_aggregate_window_results_tracks_mode_non_accepting_active_window_episodes(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 1, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 1.0},
                        "xgboost": {"accepted_count": 1, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 1.0},
                    },
                },
                {
                    "initial_bankroll_usd": 102.0,
                    "final_equity_usd": 103.0,
                    "peak_equity_usd": 103.0,
                    "min_equity_usd": 102.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 10.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 0, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 0.4},
                        "xgboost": {"accepted_count": 1, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 0.6},
                    },
                },
                {
                    "initial_bankroll_usd": 103.0,
                    "final_equity_usd": 105.0,
                    "peak_equity_usd": 105.0,
                    "min_equity_usd": 103.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 1, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 1.0},
                        "xgboost": {"accepted_count": 1, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 1.0},
                    },
                },
                {
                    "initial_bankroll_usd": 105.0,
                    "final_equity_usd": 106.0,
                    "peak_equity_usd": 106.0,
                    "min_equity_usd": 105.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 10.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 0, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 0.4},
                        "xgboost": {"accepted_count": 1, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 0.6},
                    },
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["signal_mode_summary"]["heuristic"]["max_non_accepting_active_window_streak"], 1)
        self.assertEqual(result["signal_mode_summary"]["heuristic"]["non_accepting_active_window_episode_count"], 2)
        self.assertEqual(result["signal_mode_summary"]["xgboost"]["non_accepting_active_window_episode_count"], 0)

    def test_aggregate_window_results_ignores_leading_mode_non_accepting_active_windows_for_droughts(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 101.0,
                    "peak_equity_usd": 101.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 10.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 1,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 0, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 0.4},
                        "xgboost": {"accepted_count": 1, "resolved_count": 0, "trade_count": 1, "total_pnl_usd": 0.6},
                    },
                },
                {
                    "initial_bankroll_usd": 101.0,
                    "final_equity_usd": 103.0,
                    "peak_equity_usd": 103.0,
                    "min_equity_usd": 101.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 1,
                    "accepted_size_usd": 10.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 1,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 1, "resolved_count": 0, "trade_count": 1, "total_pnl_usd": 1.2},
                        "xgboost": {"accepted_count": 0, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 0.8},
                    },
                },
                {
                    "initial_bankroll_usd": 103.0,
                    "final_equity_usd": 104.0,
                    "peak_equity_usd": 104.0,
                    "min_equity_usd": 103.0,
                    "total_pnl_usd": 1.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 0, "resolved_count": 1, "trade_count": 0, "total_pnl_usd": 0.5},
                        "xgboost": {"accepted_count": 0, "resolved_count": 0, "trade_count": 0, "total_pnl_usd": 0.5},
                    },
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["signal_mode_summary"]["heuristic"]["accepted_window_count"], 1)
        self.assertEqual(result["signal_mode_summary"]["heuristic"]["post_accept_active_window_count"], 2)
        self.assertEqual(result["signal_mode_summary"]["heuristic"]["max_non_accepting_active_window_streak"], 1)
        self.assertEqual(result["signal_mode_summary"]["heuristic"]["non_accepting_active_window_episode_count"], 1)

    def test_aggregate_window_results_tracks_live_guard_restart_across_inactive_gap(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_live_guard_triggered": 1,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_live_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_live_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["live_guard_restart_window_count"], 1)
        self.assertEqual(result["live_guard_restart_window_opportunity_count"], 1)
        self.assertEqual(result["live_guard_restart_window_share"], 1.0)

    def test_aggregate_window_results_tracks_daily_guard_window_share(self) -> None:
        result = replay_search._aggregate_window_results(
            [
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 95.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 95.0,
                    "total_pnl_usd": -5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 1,
                    "resolved_size_usd": 10.0,
                    "rejected_count": 0,
                    "unresolved_count": 1,
                    "trade_count": 2,
                    "window_end_daily_guard_triggered": 1,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 102.0,
                    "peak_equity_usd": 102.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 2.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "window_end_daily_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 100.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 100.0,
                    "total_pnl_usd": 0.0,
                    "accepted_count": 0,
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0,
                    "resolved_size_usd": 0.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "window_end_daily_guard_triggered": 0,
                    "signal_mode_summary": {},
                },
            ],
            initial_bankroll_usd=100.0,
        )

        self.assertEqual(result["daily_guard_window_count"], 1)
        self.assertEqual(result["daily_guard_window_share"], 0.5)

    def test_main_uses_stitched_max_drawdown_pct_for_multi_window_replay(self) -> None:
        calls: list[tuple[int | None, int | None]] = []

        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            calls.append((start_ts, end_ts))
            if start_ts == 0:
                return {
                    "run_id": 1,
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 90.0,
                    "peak_equity_usd": 100.0,
                    "min_equity_usd": 90.0,
                    "total_pnl_usd": -10.0,
                    "max_drawdown_pct": 0.10,
                    "accepted_count": 2,
                    "accepted_size_usd": 20.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 20.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 2,
                    "win_rate": 0.5,
                }
            return {
                "run_id": 2,
                "initial_bankroll_usd": 100.0,
                "final_equity_usd": 70.0,
                "peak_equity_usd": 100.0,
                "min_equity_usd": 70.0,
                "total_pnl_usd": -30.0,
                "max_drawdown_pct": 0.30,
                "accepted_count": 2,
                "accepted_size_usd": 20.0,
                "resolved_count": 2,
                "resolved_size_usd": 20.0,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 2,
                "win_rate": 0.5,
            }

        stdout = io.StringIO()
        argv = [
            "replay_search.py",
            "--base-policy-json",
            json.dumps({"min_confidence": 0.60}),
            "--grid-json",
            json.dumps({"min_confidence": [0.60]}),
            "--window-days",
            "30",
            "--window-count",
            "2",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch.object(replay_search, "_latest_trade_ts", return_value=30 * 86400),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        result = payload["best_feasible"]["result"]
        self.assertEqual(result["worst_window_drawdown_pct"], 0.3)
        self.assertEqual(result["max_drawdown_pct"], 0.37)
        self.assertEqual(len(calls), 2)

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

    def test_score_breakdown_penalizes_global_window_inactivity(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "inactive_window_count": 1,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            window_inactivity_penalty=0.2,
        )

        self.assertEqual(breakdown["window_inactivity_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_penalizes_unproven_legacy_multi_window_worst_window_risk(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=2.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["worst_window_penalty_usd"], 6000.0)
        self.assertEqual(breakdown["score_usd"], -5980.0)

    def test_score_breakdown_penalizes_unproven_legacy_multi_window_negative_worst_window_risk(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": -12.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=2.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["worst_window_penalty_usd"], 6000.0)
        self.assertEqual(breakdown["score_usd"], -6012.0)

    def test_score_breakdown_penalizes_explicitly_unproven_global_worst_window_risk(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "accepted_count": 6,
                "resolved_count": 6,
                "worst_window_pnl_usd": 0.0,
                "has_proven_worst_window_pnl": False,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=2.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["worst_window_penalty_usd"], 6000.0)
        self.assertEqual(breakdown["score_usd"], -5980.0)

    def test_score_breakdown_penalizes_low_accepted_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 3,
                "accepted_window_count": 1,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            accepted_window_share_penalty=0.2,
        )

        self.assertEqual(breakdown["accepted_window_share_penalty_usd"], 400.0)
        self.assertEqual(breakdown["score_usd"], -380.0)

    def test_score_breakdown_penalizes_accepting_window_drought_streak(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 3,
                "accepted_window_count": 1,
                "max_non_accepting_active_window_streak": 2,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            non_accepting_active_window_streak_penalty=0.2,
        )

        self.assertEqual(breakdown["non_accepting_active_window_streak_penalty_usd"], 300.0)
        self.assertEqual(breakdown["score_usd"], -280.0)

        short_gap_breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 3,
                "accepted_window_count": 2,
                "max_non_accepting_active_window_streak": 1,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            non_accepting_active_window_streak_penalty=0.2,
        )

        self.assertEqual(short_gap_breakdown["non_accepting_active_window_streak_penalty_usd"], 0.0)
        self.assertEqual(short_gap_breakdown["score_usd"], 20.0)

        warmup_gap_breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 5,
                "active_window_count": 5,
                "accepted_window_count": 1,
                "post_accept_active_window_count": 3,
                "max_non_accepting_active_window_streak": 2,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            non_accepting_active_window_streak_penalty=0.2,
        )

        self.assertEqual(warmup_gap_breakdown["non_accepting_active_window_streak_penalty_usd"], 300.0)
        self.assertEqual(warmup_gap_breakdown["score_usd"], -280.0)

    def test_score_breakdown_penalizes_non_accepting_active_window_episodes(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 2,
                "max_non_accepting_active_window_streak": 1,
                "non_accepting_active_window_episode_count": 2,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            non_accepting_active_window_episode_penalty=0.2,
        )

        self.assertEqual(breakdown["non_accepting_active_window_episode_penalty_usd"], 200.0)
        self.assertEqual(breakdown["score_usd"], -180.0)

        single_episode_breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 2,
                "max_non_accepting_active_window_streak": 2,
                "non_accepting_active_window_episode_count": 1,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            non_accepting_active_window_episode_penalty=0.2,
        )

        self.assertEqual(single_episode_breakdown["non_accepting_active_window_episode_penalty_usd"], 0.0)
        self.assertEqual(single_episode_breakdown["score_usd"], 20.0)

        warmup_episode_breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 6,
                "active_window_count": 6,
                "accepted_window_count": 2,
                "post_accept_active_window_count": 4,
                "max_non_accepting_active_window_streak": 1,
                "non_accepting_active_window_episode_count": 2,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            non_accepting_active_window_episode_penalty=0.2,
        )

        self.assertEqual(warmup_episode_breakdown["non_accepting_active_window_episode_penalty_usd"], 200.0)
        self.assertEqual(warmup_episode_breakdown["score_usd"], -180.0)

    def test_score_breakdown_penalizes_low_accepted_window_count(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 3,
                "accepted_window_count": 1,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            accepted_window_count_penalty=0.2,
        )

        self.assertEqual(breakdown["accepted_window_count_penalty_usd"], 600.0)
        self.assertEqual(breakdown["score_usd"], -580.0)

    def test_score_breakdown_fails_closed_on_legacy_low_accepted_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_count": 6,
                "resolved_count": 6,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            accepted_window_share_penalty=0.2,
        )

        self.assertEqual(breakdown["accepted_window_share_penalty_usd"], 450.0)
        self.assertEqual(breakdown["score_usd"], -430.0)

    def test_score_breakdown_penalizes_accepting_window_trade_share_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "active_window_count": 3,
                "accepted_window_count": 2,
                "accepted_count": 8,
                "resolved_count": 8,
                "max_accepting_window_accepted_share": 0.8,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            accepting_window_accepted_share_penalty=0.1,
        )

        self.assertEqual(breakdown["accepting_window_accepted_share_penalty_usd"], 240.0)
        self.assertEqual(breakdown["score_usd"], -220.0)

    def test_score_breakdown_penalizes_accepting_window_size_share_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "active_window_count": 3,
                "accepted_window_count": 2,
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 200.0,
                "max_accepting_window_accepted_size_share": 0.9,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            accepting_window_accepted_size_share_penalty=0.1,
        )

        self.assertEqual(breakdown["accepting_window_accepted_size_share_penalty_usd"], 270.0)
        self.assertEqual(breakdown["score_usd"], -250.0)

    def test_score_breakdown_fails_closed_on_legacy_accepting_window_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 3,
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 200.0,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            accepting_window_accepted_share_penalty=0.1,
            accepting_window_accepted_size_share_penalty=0.2,
            top_two_accepting_window_accepted_share_penalty=0.05,
            top_two_accepting_window_accepted_size_share_penalty=0.15,
            accepting_window_accepted_concentration_index_penalty=0.03,
            accepting_window_accepted_size_concentration_index_penalty=0.04,
        )

        self.assertEqual(breakdown["accepting_window_accepted_share_penalty_usd"], 300.0)
        self.assertEqual(breakdown["accepting_window_accepted_size_share_penalty_usd"], 600.0)
        self.assertEqual(breakdown["top_two_accepting_window_accepted_share_penalty_usd"], 150.0)
        self.assertEqual(breakdown["top_two_accepting_window_accepted_size_share_penalty_usd"], 450.0)
        self.assertEqual(breakdown["accepting_window_accepted_concentration_index_penalty_usd"], 90.0)
        self.assertEqual(breakdown["accepting_window_accepted_size_concentration_index_penalty_usd"], 120.0)
        self.assertEqual(breakdown["score_usd"], -1690.0)

    def test_score_breakdown_penalizes_top_two_accepting_window_trade_share_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "active_window_count": 3,
                "accepted_window_count": 3,
                "accepted_count": 10,
                "resolved_count": 10,
                "top_two_accepting_window_accepted_share": 0.9,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            top_two_accepting_window_accepted_share_penalty=0.1,
        )

        self.assertEqual(breakdown["top_two_accepting_window_accepted_share_penalty_usd"], 270.0)
        self.assertEqual(breakdown["score_usd"], -250.0)

    def test_score_breakdown_penalizes_top_two_accepting_window_size_share_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "active_window_count": 3,
                "accepted_window_count": 3,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 200.0,
                "top_two_accepting_window_accepted_size_share": 0.8,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            top_two_accepting_window_accepted_size_share_penalty=0.2,
        )

        self.assertEqual(breakdown["top_two_accepting_window_accepted_size_share_penalty_usd"], 480.0)
        self.assertEqual(breakdown["score_usd"], -460.0)

    def test_score_breakdown_penalizes_accepting_window_trade_concentration_index(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "active_window_count": 3,
                "accepted_window_count": 3,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepting_window_accepted_concentration_index": 0.375,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            accepting_window_accepted_concentration_index_penalty=0.1,
        )

        self.assertEqual(breakdown["accepting_window_accepted_concentration_index_penalty_usd"], 112.5)
        self.assertEqual(breakdown["score_usd"], -92.5)

    def test_score_breakdown_penalizes_accepting_window_size_concentration_index(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "active_window_count": 3,
                "accepted_window_count": 3,
                "accepted_count": 10,
                "accepted_size_usd": 200.0,
                "resolved_count": 10,
                "resolved_size_usd": 200.0,
                "accepting_window_accepted_size_concentration_index": 0.32,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            accepting_window_accepted_size_concentration_index_penalty=0.2,
        )

        self.assertEqual(breakdown["accepting_window_accepted_size_concentration_index_penalty_usd"], 192.0)
        self.assertEqual(breakdown["score_usd"], -172.0)

    def test_score_breakdown_penalizes_low_mode_accepted_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                        "trade_count": 5,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "trade_count": 5,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_accepted_window_share_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_accepted_window_share_penalty_usd"], 300.0)
        self.assertEqual(breakdown["score_usd"], -280.0)

    def test_score_breakdown_fails_closed_on_legacy_low_mode_accepted_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "inactive_window_count": 0,
                        "trade_count": 5,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 4,
                        "inactive_window_count": 0,
                        "trade_count": 5,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_accepted_window_share_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_accepted_window_share_penalty_usd"], 450.0)
        self.assertEqual(breakdown["score_usd"], -430.0)

    def test_score_breakdown_penalizes_low_mode_accepted_window_count(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                        "trade_count": 5,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "trade_count": 5,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_accepted_window_count_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_accepted_window_count_penalty_usd"], 600.0)
        self.assertEqual(breakdown["score_usd"], -580.0)

    def test_score_breakdown_penalizes_mode_non_accepting_active_window_streak(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 1,
                        "inactive_window_count": 1,
                        "max_non_accepting_active_window_streak": 2,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 3,
                        "inactive_window_count": 1,
                        "max_non_accepting_active_window_streak": 1,
                        "trade_count": 6,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_non_accepting_active_window_streak_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_non_accepting_active_window_streak_penalty_usd"], 300.0)
        self.assertEqual(breakdown["score_usd"], -280.0)

        warmup_breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 5,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 1,
                        "post_accept_active_window_count": 3,
                        "inactive_window_count": 0,
                        "max_non_accepting_active_window_streak": 2,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 5,
                        "post_accept_active_window_count": 5,
                        "inactive_window_count": 0,
                        "max_non_accepting_active_window_streak": 0,
                        "trade_count": 6,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_non_accepting_active_window_streak_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(warmup_breakdown["mode_non_accepting_active_window_streak_penalty_usd"], 300.0)
        self.assertEqual(warmup_breakdown["score_usd"], -280.0)

    def test_score_breakdown_ignores_disabled_mode_non_accepting_active_window_streak(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 1,
                        "inactive_window_count": 1,
                        "max_non_accepting_active_window_streak": 2,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 3,
                        "inactive_window_count": 1,
                        "max_non_accepting_active_window_streak": 1,
                        "trade_count": 6,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_non_accepting_active_window_streak_penalty=0.2,
            allow_heuristic=False,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_non_accepting_active_window_streak_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_penalizes_mode_non_accepting_active_window_episodes(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 1,
                        "inactive_window_count": 1,
                        "max_non_accepting_active_window_streak": 1,
                        "non_accepting_active_window_episode_count": 2,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 2,
                        "inactive_window_count": 1,
                        "max_non_accepting_active_window_streak": 1,
                        "non_accepting_active_window_episode_count": 1,
                        "trade_count": 6,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_non_accepting_active_window_episode_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_non_accepting_active_window_episode_penalty_usd"], 300.0)
        self.assertEqual(breakdown["score_usd"], -280.0)

        warmup_breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 2,
                        "post_accept_active_window_count": 4,
                        "inactive_window_count": 0,
                        "max_non_accepting_active_window_streak": 1,
                        "non_accepting_active_window_episode_count": 2,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 6,
                        "post_accept_active_window_count": 6,
                        "inactive_window_count": 0,
                        "max_non_accepting_active_window_streak": 0,
                        "non_accepting_active_window_episode_count": 0,
                        "trade_count": 6,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_non_accepting_active_window_episode_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(warmup_breakdown["mode_non_accepting_active_window_episode_penalty_usd"], 200.0)
        self.assertEqual(warmup_breakdown["score_usd"], -180.0)

    def test_score_breakdown_ignores_disabled_mode_non_accepting_active_window_episodes(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 1,
                        "inactive_window_count": 1,
                        "max_non_accepting_active_window_streak": 1,
                        "non_accepting_active_window_episode_count": 2,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 2,
                        "inactive_window_count": 1,
                        "max_non_accepting_active_window_streak": 1,
                        "non_accepting_active_window_episode_count": 1,
                        "trade_count": 6,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_non_accepting_active_window_episode_penalty=0.2,
            allow_heuristic=False,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_non_accepting_active_window_episode_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_ignores_fully_inactive_mode_accepting_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 0,
                        "resolved_count": 0,
                        "accepted_window_count": 0,
                        "inactive_window_count": 2,
                        "trade_count": 0,
                        "total_pnl_usd": 0.0,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "trade_count": 5,
                        "total_pnl_usd": 20.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_accepted_window_share_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_accepted_window_share_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_penalizes_mode_accepting_window_trade_share_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "max_accepting_window_accepted_share": 0.75,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "max_accepting_window_accepted_share": 0.50,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_accepting_window_accepted_share_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_accepting_window_accepted_share_penalty_usd"], 450.0)
        self.assertEqual(breakdown["score_usd"], -430.0)

    def test_score_breakdown_penalizes_mode_accepting_window_size_share_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 200.0,
                        "resolved_size_usd": 200.0,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "max_accepting_window_accepted_size_share": 0.80,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 150.0,
                        "resolved_size_usd": 150.0,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "max_accepting_window_accepted_size_share": 0.50,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            mode_accepting_window_accepted_size_share_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_accepting_window_accepted_size_share_penalty_usd"], 480.0)
        self.assertEqual(breakdown["score_usd"], -460.0)

    def test_score_breakdown_penalizes_mode_top_two_accepting_window_trade_share_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 3,
                        "inactive_window_count": 0,
                        "top_two_accepting_window_accepted_share": 0.75,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_window_count": 3,
                        "inactive_window_count": 0,
                        "top_two_accepting_window_accepted_share": 0.5,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_top_two_accepting_window_accepted_share_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_top_two_accepting_window_accepted_share_penalty_usd"], 450.0)
        self.assertEqual(breakdown["score_usd"], -430.0)

    def test_score_breakdown_penalizes_mode_top_two_accepting_window_size_share_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 200.0,
                        "resolved_size_usd": 200.0,
                        "accepted_window_count": 3,
                        "inactive_window_count": 0,
                        "top_two_accepting_window_accepted_size_share": 0.8,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 150.0,
                        "resolved_size_usd": 150.0,
                        "accepted_window_count": 3,
                        "inactive_window_count": 0,
                        "top_two_accepting_window_accepted_size_share": 0.5,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            mode_top_two_accepting_window_accepted_size_share_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_top_two_accepting_window_accepted_size_share_penalty_usd"], 480.0)
        self.assertEqual(breakdown["score_usd"], -460.0)

    def test_score_breakdown_penalizes_mode_accepting_window_trade_concentration_index(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 3,
                        "inactive_window_count": 0,
                        "accepting_window_accepted_concentration_index": 0.375,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_window_count": 3,
                        "inactive_window_count": 0,
                        "accepting_window_accepted_concentration_index": 0.30,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
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
            mode_inactivity_penalty=0.0,
            mode_accepting_window_accepted_concentration_index_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_accepting_window_accepted_concentration_index_penalty_usd"], 225.0)
        self.assertEqual(breakdown["score_usd"], -205.0)

    def test_score_breakdown_penalizes_mode_accepting_window_size_concentration_index(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "accepted_size_usd": 200.0,
                        "resolved_count": 6,
                        "resolved_size_usd": 200.0,
                        "accepted_window_count": 3,
                        "inactive_window_count": 0,
                        "accepting_window_accepted_size_concentration_index": 0.4,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 150.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 150.0,
                        "accepted_window_count": 3,
                        "inactive_window_count": 0,
                        "accepting_window_accepted_size_concentration_index": 0.28,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            mode_accepting_window_accepted_size_concentration_index_penalty=0.2,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_accepting_window_accepted_size_concentration_index_penalty_usd"], 240.0)
        self.assertEqual(breakdown["score_usd"], -220.0)

    def test_score_breakdown_penalizes_open_exposure_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "max_open_exposure_share": 0.4,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            open_exposure_penalty=0.2,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["open_exposure_penalty_usd"], 240.0)
        self.assertEqual(breakdown["score_usd"], -220.0)

    def test_score_breakdown_penalizes_window_end_open_exposure_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "max_window_end_open_exposure_share": 0.3,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            open_exposure_penalty=0.0,
            window_end_open_exposure_penalty=0.2,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["window_end_open_exposure_penalty_usd"], 180.0)
        self.assertEqual(breakdown["score_usd"], -160.0)

    def test_score_breakdown_penalizes_avg_window_end_open_exposure_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "avg_window_end_open_exposure_share": 0.25,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            open_exposure_penalty=0.0,
            window_end_open_exposure_penalty=0.0,
            avg_window_end_open_exposure_penalty=0.2,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["avg_window_end_open_exposure_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_penalizes_carry_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "carry_window_count": 2,
                "carry_window_share": 0.5,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            open_exposure_penalty=0.0,
            window_end_open_exposure_penalty=0.0,
            carry_window_penalty=0.1,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["carry_window_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_penalizes_carry_restart_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "carry_restart_window_count": 1,
                "carry_restart_window_opportunity_count": 2,
                "carry_restart_window_share": 0.5,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            carry_restart_window_penalty=0.1,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["carry_restart_window_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_uses_active_window_carry_share_fallback(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 2,
                "carry_window_count": 1,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            open_exposure_penalty=0.0,
            window_end_open_exposure_penalty=0.0,
            carry_window_penalty=0.1,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["carry_window_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_penalizes_live_guard_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 2,
                "live_guard_window_count": 1,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            live_guard_window_penalty=0.1,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["live_guard_window_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_penalizes_daily_guard_restart_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "daily_guard_restart_window_count": 1,
                "daily_guard_restart_window_opportunity_count": 2,
                "daily_guard_restart_window_share": 0.5,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            daily_guard_restart_window_penalty=0.1,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["daily_guard_restart_window_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_penalizes_live_guard_restart_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "live_guard_restart_window_count": 1,
                "live_guard_restart_window_opportunity_count": 2,
                "live_guard_restart_window_share": 0.5,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            live_guard_restart_window_penalty=0.1,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["live_guard_restart_window_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_penalizes_daily_guard_window_share(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "active_window_count": 2,
                "daily_guard_window_count": 1,
                "accepted_count": 6,
                "resolved_count": 6,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            daily_guard_window_penalty=0.1,
            resolved_share_penalty=0.0,
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

        self.assertEqual(breakdown["daily_guard_window_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_constraint_failures_reject_low_accepted_window_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 3,
                "accepted_window_count": 1,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_share=0.5,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
        )

        self.assertEqual(failures, ["accepted_window_share"])

    def test_constraint_failures_use_exact_single_window_worst_active_depth_fallback(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 5,
                "resolved_count": 5,
                "accepted_size_usd": 125.0,
                "resolved_size_usd": 125.0,
                "trade_count": 5,
                "rejected_count": 0,
                "window_count": 1,
                "total_pnl_usd": 10.0,
            },
            **self._constraint_defaults(
                min_active_window_count=1,
                min_worst_active_window_accepted_count=5,
                min_worst_active_window_accepted_size_usd=125.0,
            ),
        )

        self.assertEqual(failures, [])

    def test_constraint_failures_use_single_window_final_equity_fallback_for_carry(self) -> None:
        failures = replay_search._constraint_failures(
            replay_search._with_window_activity_fields(
                {
                    "window_count": 1,
                    "initial_bankroll_usd": 100.0,
                    "total_pnl_usd": 5.0,
                    "final_bankroll_usd": 90.0,
                    "accepted_count": 2,
                    "resolved_count": 1,
                }
            ),
            **self._constraint_defaults(
                max_window_end_open_exposure_share=0.1,
            ),
        )

        self.assertEqual(failures, ["max_window_end_open_exposure_share"])

    def test_constraint_failures_use_null_single_window_final_equity_fallback_for_carry(self) -> None:
        failures = replay_search._constraint_failures(
            replay_search._with_window_activity_fields(
                {
                    "window_count": 1,
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": None,
                    "total_pnl_usd": 5.0,
                    "final_bankroll_usd": 90.0,
                    "accepted_count": 2,
                    "resolved_count": 1,
                }
            ),
            **self._constraint_defaults(
                max_window_end_open_exposure_share=0.1,
            ),
        )

        self.assertEqual(failures, ["max_window_end_open_exposure_share"])

    def test_constraint_failures_use_null_single_window_carry_fields_fallback(self) -> None:
        failures = replay_search._constraint_failures(
            replay_search._with_window_activity_fields(
                {
                    "window_count": 1,
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 105.0,
                    "final_bankroll_usd": 90.0,
                    "accepted_count": 2,
                    "resolved_count": 1,
                    "window_end_open_exposure_usd": None,
                    "window_end_open_exposure_share": None,
                    "max_window_end_open_exposure_usd": None,
                    "max_window_end_open_exposure_share": None,
                    "avg_window_end_open_exposure_share": None,
                    "carry_window_count": None,
                    "carry_window_share": None,
                }
            ),
            **self._constraint_defaults(
                max_window_end_open_exposure_share=0.1,
                max_carry_window_share=0.5,
            ),
        )

        self.assertEqual(
            failures,
            ["max_window_end_open_exposure_share", "carry_window_share"],
        )

    def test_constraint_failures_reject_low_accepted_window_count(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 3,
                "inactive_window_count": 1,
                "accepted_window_count": 1,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_count=2,
            min_accepted_window_share=0.0,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
        )

        self.assertEqual(failures, ["accepted_window_count"])

    def test_constraint_failures_reject_high_non_accepting_active_window_streak(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 2,
                "max_non_accepting_active_window_streak": 2,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_count=0,
            min_accepted_window_share=0.0,
            max_non_accepting_active_window_streak=1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
        )

        self.assertEqual(failures, ["max_non_accepting_active_window_streak"])

    def test_constraint_failures_reject_high_non_accepting_active_window_episode_count(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 2,
                "max_non_accepting_active_window_streak": 1,
                "non_accepting_active_window_episode_count": 2,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_count=0,
            min_accepted_window_share=0.0,
            max_non_accepting_active_window_streak=-1,
            max_non_accepting_active_window_episodes=1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
        )

        self.assertEqual(failures, ["non_accepting_active_window_episode_count"])

    def test_constraint_failures_reject_high_accepting_window_trade_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 3,
                "accepted_window_count": 2,
                "max_accepting_window_accepted_share": 0.8,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_share=0.0,
            max_accepting_window_accepted_share=0.7,
            max_accepting_window_accepted_size_share=0.0,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
        )

        self.assertEqual(failures, ["max_accepting_window_accepted_share"])

    def test_constraint_failures_fail_closed_on_legacy_multi_window_worst_coverage(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 200.0,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
            },
            **self._constraint_defaults(
                min_worst_window_resolved_share=0.5,
            ),
        )

        self.assertEqual(failures, ["worst_window_resolved_share"])

    def test_constraint_failures_fail_closed_on_legacy_multi_window_worst_window_pnl(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "total_pnl_usd": 20.0,
            },
            **self._constraint_defaults(
                min_worst_window_pnl_usd=1.0,
            ),
        )

        self.assertEqual(failures, ["worst_window_pnl_usd"])

    def test_constraint_failures_fail_closed_on_legacy_multi_window_negative_worst_window_pnl(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "total_pnl_usd": -12.0,
            },
            **self._constraint_defaults(
                min_worst_window_pnl_usd=-1.0,
            ),
        )

        self.assertEqual(failures, ["worst_window_pnl_usd"])

    def test_constraint_failures_fail_closed_on_legacy_multi_window_near_flat_negative_worst_window_pnl(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "total_pnl_usd": -1.0,
            },
            **self._constraint_defaults(
                min_worst_window_pnl_usd=-10.0,
            ),
        )

        self.assertEqual(failures, ["worst_window_pnl_usd"])

    def test_constraint_failures_fail_closed_on_explicitly_unproven_global_worst_window_pnl(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "worst_window_pnl_usd": 0.0,
                "has_proven_worst_window_pnl": False,
            },
            **self._constraint_defaults(
                min_worst_window_pnl_usd=-10.0,
            ),
        )

        self.assertEqual(failures, ["worst_window_pnl_usd"])

    def test_constraint_failures_fail_closed_on_legacy_multi_window_mode_near_flat_negative_worst_window_pnl(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": -1.0,
                    },
                },
            },
            **self._constraint_defaults(
                min_xgboost_pnl_usd=-10.0,
                min_xgboost_worst_window_pnl_usd=-10.0,
            ),
        )

        self.assertEqual(failures, ["xgboost_worst_window_pnl_usd"])

    def test_constraint_failures_fail_closed_on_explicitly_unproven_mode_worst_window_pnl(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                        "worst_window_pnl_usd": 0.0,
                        "has_proven_worst_window_pnl": False,
                    },
                },
            },
            **self._constraint_defaults(
                min_xgboost_worst_window_pnl_usd=-10.0,
            ),
        )

        self.assertEqual(failures, ["xgboost_worst_window_pnl_usd"])

    def test_constraint_failures_fail_closed_on_legacy_multi_window_mode_worst_active_depth(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_count": 8,
                "resolved_size_usd": 200.0,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 96.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 96.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                },
            },
            **self._constraint_defaults(
                min_xgboost_worst_active_window_accepted_count=1,
                min_xgboost_worst_active_window_accepted_size_usd=1.0,
            ),
        )

        self.assertEqual(
            failures,
            [
                "xgboost_worst_active_window_accepted_count",
                "xgboost_worst_active_window_accepted_size_usd",
            ],
        )

    def test_constraint_failures_fail_closed_on_legacy_multi_window_worst_window_drawdown(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "max_drawdown_pct": 0.18,
                "total_pnl_usd": 20.0,
            },
            **self._constraint_defaults(
                max_worst_window_drawdown_pct=0.10,
            ),
        )

        self.assertEqual(failures, ["worst_window_drawdown_pct"])

    def test_constraint_failures_fail_closed_on_legacy_multi_window_mode_positive_windows(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 8,
                        "resolved_count": 8,
                        "trade_count": 8,
                        "total_pnl_usd": 20.0,
                    }
                },
            },
            **self._constraint_defaults(
                min_xgboost_positive_window_count=1,
            ),
        )

        self.assertEqual(failures, ["xgboost_positive_window_count"])

    def test_constraint_failures_reject_high_accepting_window_size_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "accepted_size_usd": 120.0,
                "resolved_count": 6,
                "resolved_size_usd": 120.0,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 3,
                "accepted_window_count": 2,
                "max_accepting_window_accepted_size_share": 0.9,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_share=0.0,
            max_accepting_window_accepted_share=0.0,
            max_accepting_window_accepted_size_share=0.8,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
        )

        self.assertEqual(failures, ["max_accepting_window_accepted_size_share"])

    def test_constraint_failures_reject_high_top_two_accepting_window_trade_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 12,
                "resolved_count": 12,
                "trade_count": 12,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 4,
                "top_two_accepting_window_accepted_share": 0.8,
            },
            **self._constraint_defaults(
                max_top_two_accepting_window_accepted_share=0.7,
            ),
        )

        self.assertEqual(failures, ["top_two_accepting_window_accepted_share"])

    def test_constraint_failures_reject_high_top_two_accepting_window_size_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 12,
                "accepted_size_usd": 240.0,
                "resolved_count": 12,
                "resolved_size_usd": 240.0,
                "trade_count": 12,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 4,
                "top_two_accepting_window_accepted_size_share": 0.85,
            },
            **self._constraint_defaults(
                max_top_two_accepting_window_accepted_size_share=0.8,
            ),
        )

        self.assertEqual(failures, ["top_two_accepting_window_accepted_size_share"])

    def test_constraint_failures_reject_high_accepting_window_trade_concentration_index(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 12,
                "resolved_count": 12,
                "trade_count": 12,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 4,
                "accepting_window_accepted_concentration_index": 0.32,
            },
            **self._constraint_defaults(
                max_accepting_window_accepted_concentration_index=0.3,
            ),
        )

        self.assertEqual(failures, ["accepting_window_accepted_concentration_index"])

    def test_constraint_failures_fail_closed_on_legacy_accepting_window_trade_concentration_index(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 12,
                "resolved_count": 12,
                "trade_count": 12,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 3,
            },
            **self._constraint_defaults(
                max_accepting_window_accepted_concentration_index=0.9,
            ),
        )

        self.assertEqual(failures, ["accepting_window_accepted_concentration_index"])

    def test_constraint_failures_reject_high_accepting_window_size_concentration_index(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 12,
                "accepted_size_usd": 240.0,
                "resolved_count": 12,
                "resolved_size_usd": 240.0,
                "trade_count": 12,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 4,
                "accepted_window_count": 4,
                "accepting_window_accepted_size_concentration_index": 0.34,
            },
            **self._constraint_defaults(
                max_accepting_window_accepted_size_concentration_index=0.3,
            ),
        )

        self.assertEqual(failures, ["accepting_window_accepted_size_concentration_index"])

    def test_constraint_failures_reject_high_mode_accepting_window_trade_concentration_index(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_window_count": 3,
                        "accepting_window_accepted_concentration_index": 0.4,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_window_count": 3,
                        "accepting_window_accepted_concentration_index": 0.3,
                    },
                },
            },
            **self._constraint_defaults(
                max_heuristic_accepting_window_accepted_concentration_index=0.35,
            ),
        )

        self.assertEqual(failures, ["heuristic_accepting_window_accepted_concentration_index"])

    def test_constraint_failures_reject_high_mode_accepting_window_size_concentration_index(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "accepted_size_usd": 160.0,
                "resolved_count": 8,
                "resolved_size_usd": 160.0,
                "trade_count": 8,
                "rejected_count": 0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "accepted_size_usd": 80.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 80.0,
                        "accepted_window_count": 3,
                        "accepting_window_accepted_size_concentration_index": 0.42,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 80.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 80.0,
                        "accepted_window_count": 3,
                        "accepting_window_accepted_size_concentration_index": 0.3,
                    },
                },
            },
            **self._constraint_defaults(
                max_heuristic_accepting_window_accepted_size_concentration_index=0.35,
            ),
        )

        self.assertEqual(failures, ["heuristic_accepting_window_accepted_size_concentration_index"])

    def test_constraint_failures_reject_low_mode_accepted_window_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 10,
                "resolved_count": 10,
                "trade_count": 10,
                "rejected_count": 0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                    },
                },
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_share=0.0,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
            min_heuristic_accepted_window_share=0.75,
            min_xgboost_accepted_window_share=0.0,
        )

        self.assertEqual(failures, ["heuristic_accepted_window_share"])

    def test_constraint_failures_reject_low_mode_accepted_window_count(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 10,
                "resolved_count": 10,
                "trade_count": 10,
                "rejected_count": 0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                    },
                },
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_share=0.0,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
            min_heuristic_accepted_windows=2,
            min_xgboost_accepted_windows=0,
            min_heuristic_accepted_window_share=0.0,
            min_xgboost_accepted_window_share=0.0,
        )

        self.assertEqual(failures, ["heuristic_accepted_window_count"])

    def test_constraint_failures_reject_high_mode_non_accepting_active_window_streak(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 10,
                "resolved_count": 10,
                "trade_count": 10,
                "rejected_count": 0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                    },
                },
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_share=0.0,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
            min_heuristic_accepted_windows=0,
            min_xgboost_accepted_windows=0,
            min_heuristic_accepted_window_share=0.0,
            min_xgboost_accepted_window_share=0.0,
            max_heuristic_non_accepting_active_window_streak=-1,
            max_xgboost_non_accepting_active_window_streak=0,
        )

        self.assertEqual(failures, ["xgboost_max_non_accepting_active_window_streak"])

    def test_constraint_failures_reject_high_mode_non_accepting_active_window_episode_count(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 10,
                "resolved_count": 10,
                "trade_count": 10,
                "rejected_count": 0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "non_accepting_active_window_episode_count": 1,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "non_accepting_active_window_episode_count": 2,
                    },
                },
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_share=0.0,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
            min_heuristic_accepted_windows=0,
            min_xgboost_accepted_windows=0,
            min_heuristic_accepted_window_share=0.0,
            min_xgboost_accepted_window_share=0.0,
            max_heuristic_non_accepting_active_window_streak=-1,
            max_xgboost_non_accepting_active_window_streak=-1,
            max_heuristic_non_accepting_active_window_episodes=-1,
            max_xgboost_non_accepting_active_window_episodes=1,
        )

        self.assertEqual(failures, ["xgboost_non_accepting_active_window_episode_count"])

    def test_constraint_failures_reject_high_mode_accepting_window_trade_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 10,
                "resolved_count": 10,
                "trade_count": 10,
                "rejected_count": 0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "max_accepting_window_accepted_share": 0.8,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "max_accepting_window_accepted_share": 0.5,
                    },
                },
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_count=0,
            min_accepted_window_share=0.0,
            max_accepting_window_accepted_share=0.0,
            max_accepting_window_accepted_size_share=0.0,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
            min_heuristic_accepted_windows=0,
            min_xgboost_accepted_windows=0,
            min_heuristic_accepted_window_share=0.0,
            min_xgboost_accepted_window_share=0.0,
            max_heuristic_accepting_window_accepted_share=0.7,
            max_heuristic_accepting_window_accepted_size_share=0.0,
            max_xgboost_accepting_window_accepted_share=0.0,
            max_xgboost_accepting_window_accepted_size_share=0.0,
        )

        self.assertEqual(failures, ["heuristic_max_accepting_window_accepted_share"])

    def test_constraint_failures_reject_high_mode_accepting_window_size_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 300.0,
                "resolved_size_usd": 300.0,
                "trade_count": 10,
                "rejected_count": 0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 180.0,
                        "resolved_size_usd": 180.0,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "max_accepting_window_accepted_size_share": 0.85,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 120.0,
                        "resolved_size_usd": 120.0,
                        "accepted_window_count": 2,
                        "inactive_window_count": 0,
                        "max_accepting_window_accepted_size_share": 0.5,
                    },
                },
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_accepted_window_count=0,
            min_accepted_window_share=0.0,
            max_accepting_window_accepted_share=0.0,
            max_accepting_window_accepted_size_share=0.0,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            min_worst_active_window_accepted_count=0,
            min_worst_active_window_accepted_size_usd=0.0,
            max_window_end_open_exposure_share=0.0,
            max_avg_window_end_open_exposure_share=0.0,
            max_carry_window_share=0.0,
            max_carry_restart_window_share=0.0,
            min_heuristic_accepted_windows=0,
            min_xgboost_accepted_windows=0,
            min_heuristic_accepted_window_share=0.0,
            min_xgboost_accepted_window_share=0.0,
            max_heuristic_accepting_window_accepted_share=0.0,
            max_heuristic_accepting_window_accepted_size_share=0.8,
            max_xgboost_accepting_window_accepted_share=0.0,
            max_xgboost_accepting_window_accepted_size_share=0.0,
        )

        self.assertEqual(failures, ["heuristic_max_accepting_window_accepted_size_share"])

    def test_constraint_failures_reject_high_mode_top_two_accepting_window_trade_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 10,
                "resolved_count": 10,
                "trade_count": 10,
                "rejected_count": 0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_window_count": 4,
                        "inactive_window_count": 0,
                        "top_two_accepting_window_accepted_share": 0.8,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_window_count": 4,
                        "inactive_window_count": 0,
                        "top_two_accepting_window_accepted_share": 0.5,
                    },
                },
            },
            **self._constraint_defaults(
                max_heuristic_top_two_accepting_window_accepted_share=0.7,
            ),
        )

        self.assertEqual(failures, ["heuristic_top_two_accepting_window_accepted_share"])

    def test_constraint_failures_reject_high_mode_top_two_accepting_window_size_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 10,
                "accepted_size_usd": 300.0,
                "resolved_count": 10,
                "resolved_size_usd": 300.0,
                "trade_count": 10,
                "rejected_count": 0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "accepted_size_usd": 180.0,
                        "resolved_count": 6,
                        "resolved_size_usd": 180.0,
                        "accepted_window_count": 4,
                        "inactive_window_count": 0,
                        "top_two_accepting_window_accepted_size_share": 0.85,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "accepted_size_usd": 120.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 120.0,
                        "accepted_window_count": 4,
                        "inactive_window_count": 0,
                        "top_two_accepting_window_accepted_size_share": 0.5,
                    },
                },
            },
            **self._constraint_defaults(
                max_heuristic_top_two_accepting_window_accepted_size_share=0.8,
            ),
        )

        self.assertEqual(failures, ["heuristic_top_two_accepting_window_accepted_size_share"])

    def test_constraint_failures_reject_open_exposure_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "max_open_exposure_share": 0.41,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.4,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
        )

        self.assertEqual(failures, ["max_open_exposure_share"])

    def test_constraint_failures_reject_window_end_open_exposure_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "max_window_end_open_exposure_share": 0.31,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            max_window_end_open_exposure_share=0.3,
        )

        self.assertEqual(failures, ["max_window_end_open_exposure_share"])

    def test_constraint_failures_reject_avg_window_end_open_exposure_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "avg_window_end_open_exposure_share": 0.26,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            max_avg_window_end_open_exposure_share=0.25,
        )

        self.assertEqual(failures, ["avg_window_end_open_exposure_share"])

    def test_constraint_failures_use_single_window_end_state_share_fallbacks(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "window_count": 1,
                "initial_bankroll_usd": 100.0,
                "total_pnl_usd": 5.0,
                "final_bankroll_usd": 90.0,
                "accepted_count": 2,
                "resolved_count": 2,
                "trade_count": 2,
                "rejected_count": 0,
                "window_end_live_guard_triggered": 1,
                "window_end_daily_guard_triggered": 1,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.5,
            max_live_guard_window_share=0.5,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            max_window_end_open_exposure_share=0.1,
            max_avg_window_end_open_exposure_share=0.1,
            max_carry_window_share=0.5,
        )

        self.assertEqual(
            failures,
            [
                "max_window_end_open_exposure_share",
                "avg_window_end_open_exposure_share",
                "carry_window_share",
                "daily_guard_window_share",
                "live_guard_window_share",
            ],
        )

    def test_constraint_failures_reject_carry_window_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "carry_window_count": 2,
                "carry_window_share": 0.5,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            max_carry_window_share=0.4,
        )

        self.assertEqual(failures, ["carry_window_share"])

    def test_constraint_failures_use_active_window_carry_share_fallback(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 2,
                "carry_window_count": 1,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            max_carry_window_share=0.4,
        )

        self.assertEqual(failures, ["carry_window_share"])

    def test_constraint_failures_reject_live_guard_window_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 2,
                "live_guard_window_count": 1,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_live_guard_window_share=0.4,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
        )

        self.assertEqual(failures, ["live_guard_window_share"])

    def test_constraint_failures_use_null_single_window_live_guard_fallback(self) -> None:
        failures = replay_search._constraint_failures(
            replay_search._with_window_activity_fields(
                {
                    "initial_bankroll_usd": 100.0,
                    "final_equity_usd": 105.0,
                    "final_bankroll_usd": 105.0,
                    "total_pnl_usd": 5.0,
                    "accepted_count": 2,
                    "accepted_size_usd": 50.0,
                    "resolved_count": 2,
                    "resolved_size_usd": 50.0,
                    "trade_count": 2,
                    "rejected_count": 0,
                    "active_window_count": None,
                    "inactive_window_count": None,
                    "live_guard_window_count": None,
                    "live_guard_window_share": None,
                    "accepted_window_count": None,
                    "max_accepting_window_accepted_share": None,
                    "window_end_live_guard_triggered": 1,
                }
            ),
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_live_guard_window_share=0.5,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
        )

        self.assertEqual(failures, ["live_guard_window_share"])

    def test_constraint_failures_reject_carry_restart_window_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 3,
                "carry_restart_window_count": 1,
                "carry_restart_window_opportunity_count": 2,
                "carry_restart_window_share": 0.5,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
            max_carry_restart_window_share=0.4,
        )

        self.assertEqual(failures, ["carry_restart_window_share"])

    def test_constraint_failures_reject_daily_guard_window_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 4,
                "active_window_count": 2,
                "daily_guard_window_count": 1,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.4,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
        )

        self.assertEqual(failures, ["daily_guard_window_share"])

    def test_constraint_failures_reject_daily_guard_restart_window_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 3,
                "daily_guard_restart_window_count": 1,
                "daily_guard_restart_window_opportunity_count": 2,
                "daily_guard_restart_window_share": 0.5,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.4,
            max_live_guard_restart_window_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
        )

        self.assertEqual(failures, ["daily_guard_restart_window_share"])

    def test_constraint_failures_reject_live_guard_restart_window_share(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 6,
                "resolved_count": 6,
                "trade_count": 6,
                "rejected_count": 0,
                "window_count": 3,
                "live_guard_restart_window_count": 1,
                "live_guard_restart_window_opportunity_count": 2,
                "live_guard_restart_window_share": 0.5,
            },
            allow_heuristic=True,
            allow_xgboost=True,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            max_open_exposure_share=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=0,
            min_xgboost_accepted_count=0,
            min_heuristic_resolved_count=0,
            min_xgboost_resolved_count=0,
            min_heuristic_win_rate=0.0,
            min_xgboost_win_rate=0.0,
            min_heuristic_resolved_share=0.0,
            min_xgboost_resolved_share=0.0,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=0.0,
            min_xgboost_pnl_usd=0.0,
            min_heuristic_worst_window_pnl_usd=-1_000_000_000.0,
            min_xgboost_worst_window_pnl_usd=-1_000_000_000.0,
            min_heuristic_worst_window_resolved_share=0.0,
            min_xgboost_worst_window_resolved_share=0.0,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=0,
            min_xgboost_positive_window_count=0,
            min_heuristic_worst_active_window_accepted_count=0,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=0,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.0,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.0,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            max_daily_guard_window_share=0.0,
            max_live_guard_window_share=0.0,
            max_daily_guard_restart_window_share=0.0,
            max_live_guard_restart_window_share=0.4,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
        )

        self.assertEqual(failures, ["live_guard_restart_window_share"])

    def test_score_breakdown_does_not_treat_losing_active_windows_as_inactive(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": -15.0,
                "max_drawdown_pct": 0.0,
                "window_count": 1,
                "active_window_count": 1,
                "inactive_window_count": 0,
                "accepted_count": 4,
                "resolved_count": 4,
                "positive_window_count": 0,
                "negative_window_count": 1,
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
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            window_inactivity_penalty=0.2,
        )

        self.assertEqual(breakdown["window_inactivity_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], -15.0)

    def test_score_breakdown_penalizes_global_worst_active_window_depth(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "active_window_count": 2,
                "inactive_window_count": 0,
                "accepted_count": 5,
                "resolved_count": 5,
                "worst_active_window_accepted_count": 2,
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
            worst_active_window_accepted_penalty=0.1,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["worst_active_window_accepted_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_uses_exact_single_window_worst_active_window_depth_fallback(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 1,
                "accepted_count": 5,
                "resolved_count": 5,
                "accepted_size_usd": 125.0,
                "resolved_size_usd": 125.0,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            worst_active_window_accepted_penalty=0.1,
            worst_active_window_accepted_size_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_size_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["worst_active_window_accepted_penalty_usd"], 60.0)
        self.assertEqual(breakdown["score_usd"], -40.0)

    def test_score_breakdown_penalizes_global_worst_active_window_deployed_dollars(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "active_window_count": 2,
                "inactive_window_count": 0,
                "accepted_window_count": 2,
                "accepted_count": 5,
                "resolved_count": 5,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 200.0,
                "worst_active_window_accepted_count": 2,
                "worst_active_window_accepted_size_usd": 50.0,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            worst_active_window_accepted_penalty=0.0,
            worst_active_window_accepted_size_penalty=0.1,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_size_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["worst_active_window_accepted_size_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_uses_accepting_windows_for_global_sparse_size_penalty(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "active_window_count": 3,
                "accepted_window_count": 2,
                "inactive_window_count": 0,
                "accepted_count": 5,
                "resolved_count": 5,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 200.0,
                "worst_active_window_accepted_count": 2,
                "worst_active_window_accepted_size_usd": 50.0,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            worst_active_window_accepted_penalty=0.0,
            worst_active_window_accepted_size_penalty=0.1,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_size_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["worst_active_window_accepted_size_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_penalizes_low_breadth_counts(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 8,
                "resolved_count": 8,
                "trader_concentration": {
                    "trader_count": 2,
                },
                "market_concentration": {
                    "market_count": 4,
                },
                "entry_price_band_concentration": {
                    "entry_price_band_count": 5,
                },
                "time_to_close_band_concentration": {
                    "time_to_close_band_count": 10,
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
            worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            wallet_count_penalty=0.1,
            market_count_penalty=0.1,
            entry_price_band_count_penalty=0.1,
            time_to_close_band_count_penalty=0.1,
        )

        self.assertEqual(breakdown["wallet_count_penalty_usd"], 150.0)
        self.assertEqual(breakdown["market_count_penalty_usd"], 75.0)
        self.assertEqual(breakdown["entry_price_band_count_penalty_usd"], 60.0)
        self.assertEqual(breakdown["time_to_close_band_count_penalty_usd"], 30.0)
        self.assertEqual(breakdown["score_usd"], -295.0)

    def test_score_breakdown_penalizes_deployed_dollar_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 8,
                "resolved_count": 8,
                "trader_concentration": {
                    "trader_count": 4,
                    "top_size_share": 0.50,
                },
                "market_concentration": {
                    "market_count": 4,
                    "top_size_share": 0.40,
                },
                "entry_price_band_concentration": {
                    "entry_price_band_count": 5,
                    "top_size_share": 0.30,
                },
                "time_to_close_band_concentration": {
                    "time_to_close_band_count": 5,
                    "top_size_share": 0.20,
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
            worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            wallet_size_concentration_penalty=0.1,
            market_size_concentration_penalty=0.1,
            entry_price_band_size_concentration_penalty=0.1,
            time_to_close_band_size_concentration_penalty=0.1,
        )

        self.assertEqual(breakdown["wallet_size_concentration_penalty_usd"], 150.0)
        self.assertEqual(breakdown["market_size_concentration_penalty_usd"], 120.0)
        self.assertEqual(breakdown["entry_price_band_size_concentration_penalty_usd"], 90.0)
        self.assertEqual(breakdown["time_to_close_band_size_concentration_penalty_usd"], 60.0)
        self.assertEqual(breakdown["score_usd"], -400.0)

    def test_score_breakdown_penalizes_mode_worst_active_window_depth(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": 4,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": 2,
                    },
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
            worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.1,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_worst_active_window_accepted_penalty_usd"], 150.0)
        self.assertEqual(breakdown["score_usd"], -130.0)

    def test_score_breakdown_penalizes_mode_worst_active_window_deployed_dollars(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "accepted_size_usd": 120.0,
                        "accepted_window_count": 2,
                        "resolved_count": 4,
                        "resolved_size_usd": 120.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": 4,
                        "worst_active_window_accepted_size_usd": 60.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "accepted_size_usd": 200.0,
                        "accepted_window_count": 2,
                        "resolved_count": 6,
                        "resolved_size_usd": 200.0,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": 2,
                        "worst_active_window_accepted_size_usd": 40.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            worst_active_window_accepted_penalty=0.0,
            worst_active_window_accepted_size_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_size_penalty=0.1,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_worst_active_window_accepted_size_penalty_usd"], 180.0)
        self.assertEqual(breakdown["score_usd"], -160.0)

    def test_score_breakdown_uses_accepting_windows_for_mode_sparse_size_penalty(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "accepted_size_usd": 120.0,
                        "accepted_window_count": 2,
                        "resolved_count": 4,
                        "resolved_size_usd": 120.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": 4,
                        "worst_active_window_accepted_size_usd": 60.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "accepted_size_usd": 200.0,
                        "accepted_window_count": 2,
                        "resolved_count": 6,
                        "resolved_size_usd": 200.0,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": 2,
                        "worst_active_window_accepted_size_usd": 40.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            worst_active_window_accepted_penalty=0.0,
            worst_active_window_accepted_size_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_size_penalty=0.1,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_worst_active_window_accepted_size_penalty_usd"], 180.0)
        self.assertEqual(breakdown["score_usd"], -160.0)

    def test_score_breakdown_fails_closed_on_legacy_mode_sparse_count_penalty(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "accepted_size_usd": 120.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 120.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": 4,
                        "worst_active_window_accepted_size_usd": 60.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "accepted_size_usd": 200.0,
                        "resolved_count": 6,
                        "resolved_size_usd": 200.0,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": None,
                        "worst_active_window_accepted_size_usd": None,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            worst_active_window_accepted_penalty=0.0,
            worst_active_window_accepted_size_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.1,
            mode_worst_active_window_accepted_size_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_worst_active_window_accepted_penalty_usd"], 300.0)
        self.assertEqual(breakdown["score_usd"], -280.0)

    def test_score_breakdown_fails_closed_on_legacy_mode_sparse_size_penalty(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 3,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "accepted_size_usd": 120.0,
                        "resolved_count": 4,
                        "resolved_size_usd": 120.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": 4,
                        "worst_active_window_accepted_size_usd": 60.0,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "accepted_size_usd": 200.0,
                        "resolved_count": 6,
                        "resolved_size_usd": 200.0,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": None,
                        "worst_active_window_accepted_size_usd": None,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            worst_active_window_accepted_penalty=0.0,
            worst_active_window_accepted_size_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_size_penalty=0.1,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_worst_active_window_accepted_size_penalty_usd"], 300.0)
        self.assertEqual(breakdown["score_usd"], -280.0)

    def test_score_breakdown_ignores_disabled_mode_worst_active_window_depth(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                        "inactive_window_count": 0,
                        "worst_active_window_accepted_count": 1,
                    },
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
            worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.1,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=False,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_worst_active_window_accepted_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_penalizes_mode_active_window_mix(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 100.0,
                "resolved_size_usd": 100.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_size_usd": 50.0,
                        "resolved_size_usd": 50.0,
                        "trade_count": 5,
                        "total_pnl_usd": 8.0,
                        "max_active_window_accepted_share": 0.9,
                        "max_active_window_accepted_size_share": 0.85,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_size_usd": 50.0,
                        "resolved_size_usd": 50.0,
                        "trade_count": 5,
                        "total_pnl_usd": 12.0,
                        "min_active_window_accepted_share": 0.1,
                        "min_active_window_accepted_size_share": 0.15,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_active_window_accepted_share_penalty=0.1,
            mode_active_window_accepted_size_share_penalty=0.2,
            worst_active_window_accepted_penalty=0.0,
            worst_active_window_accepted_size_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_size_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_active_window_accepted_share_penalty_usd"], 270.0)
        self.assertEqual(breakdown["mode_active_window_accepted_size_share_penalty_usd"], 510.0)
        self.assertEqual(breakdown["score_usd"], -760.0)

    def test_score_breakdown_ignores_mode_active_window_mix_when_peer_scorer_disabled(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 5,
                "resolved_count": 5,
                "accepted_size_usd": 50.0,
                "resolved_size_usd": 50.0,
                "window_count": 2,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_size_usd": 50.0,
                        "resolved_size_usd": 50.0,
                        "trade_count": 5,
                        "total_pnl_usd": 20.0,
                        "max_active_window_accepted_share": 1.0,
                        "max_active_window_accepted_size_share": 1.0,
                    },
                    "xgboost": {
                        "accepted_count": 0,
                        "resolved_count": 0,
                        "accepted_size_usd": 0.0,
                        "resolved_size_usd": 0.0,
                        "trade_count": 0,
                        "total_pnl_usd": 0.0,
                        "min_active_window_accepted_share": 0.0,
                        "min_active_window_accepted_size_share": 0.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_active_window_accepted_share_penalty=0.1,
            mode_active_window_accepted_size_share_penalty=0.2,
            worst_active_window_accepted_penalty=0.0,
            worst_active_window_accepted_size_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_size_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=False,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_active_window_accepted_share_penalty_usd"], 0.0)
        self.assertEqual(breakdown["mode_active_window_accepted_size_share_penalty_usd"], 0.0)
        self.assertEqual(breakdown["score_usd"], 20.0)

    def test_score_breakdown_fails_closed_on_legacy_multi_window_mode_active_window_mix(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 72.0,
                        "resolved_size_usd": 72.0,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 48.0,
                        "resolved_size_usd": 48.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_active_window_accepted_share_penalty=0.1,
            mode_active_window_accepted_size_share_penalty=0.2,
            worst_active_window_accepted_penalty=0.0,
            worst_active_window_accepted_size_penalty=0.0,
            mode_worst_active_window_accepted_penalty=0.0,
            mode_worst_active_window_accepted_size_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_active_window_accepted_share_penalty_usd"], 300.0)
        self.assertEqual(breakdown["mode_active_window_accepted_size_share_penalty_usd"], 600.0)
        self.assertEqual(breakdown["score_usd"], -880.0)

    def test_score_breakdown_fails_closed_on_legacy_mode_accepting_window_concentration(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 72.0,
                        "resolved_size_usd": 72.0,
                        "accepted_window_count": 3,
                        "trade_count": 6,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 48.0,
                        "resolved_size_usd": 48.0,
                        "accepted_window_count": 3,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
            mode_accepting_window_accepted_share_penalty=0.1,
            mode_accepting_window_accepted_size_share_penalty=0.2,
            mode_top_two_accepting_window_accepted_share_penalty=0.05,
            mode_top_two_accepting_window_accepted_size_share_penalty=0.15,
            mode_accepting_window_accepted_concentration_index_penalty=0.03,
            mode_accepting_window_accepted_size_concentration_index_penalty=0.04,
        )

        self.assertEqual(breakdown["mode_accepting_window_accepted_share_penalty_usd"], 300.0)
        self.assertEqual(breakdown["mode_accepting_window_accepted_size_share_penalty_usd"], 600.0)
        self.assertEqual(breakdown["mode_top_two_accepting_window_accepted_share_penalty_usd"], 150.0)
        self.assertEqual(breakdown["mode_top_two_accepting_window_accepted_size_share_penalty_usd"], 450.0)
        self.assertEqual(breakdown["mode_accepting_window_accepted_concentration_index_penalty_usd"], 90.0)
        self.assertEqual(breakdown["mode_accepting_window_accepted_size_concentration_index_penalty_usd"], 120.0)
        self.assertEqual(breakdown["score_usd"], -1690.0)

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

    def test_score_breakdown_penalizes_size_weighted_coverage(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 120.0,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 80.0,
                        "resolved_size_usd": 80.0,
                        "trade_count": 4,
                        "total_pnl_usd": 8.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 120.0,
                        "resolved_size_usd": 40.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.1,
            worst_window_resolved_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.1,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["resolved_size_share_penalty_usd"], 120.0)
        self.assertEqual(breakdown["mode_resolved_size_share_penalty_usd"], 200.0)
        self.assertEqual(breakdown["score_usd"], -300.0)

    def test_score_breakdown_penalizes_global_worst_window_size_weighted_coverage(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 160.0,
                "worst_window_resolved_size_share": 0.4,
                "worst_active_window_resolved_size_share": 0.4,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.1,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["worst_window_resolved_size_share_penalty_usd"], 180.0)
        self.assertEqual(breakdown["score_usd"], -160.0)

    def test_score_breakdown_penalizes_mode_worst_window_size_weighted_coverage(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 160.0,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 80.0,
                        "resolved_size_usd": 80.0,
                        "trade_count": 4,
                        "total_pnl_usd": 8.0,
                        "worst_window_resolved_size_share": 1.0,
                        "worst_active_window_resolved_size_share": 1.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 120.0,
                        "resolved_size_usd": 80.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                        "worst_window_resolved_size_share": 1.0 / 3.0,
                        "worst_active_window_resolved_size_share": 1.0 / 3.0,
                    },
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.1,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertAlmostEqual(breakdown["mode_worst_window_resolved_size_share_penalty_usd"], 200.0, places=3)
        self.assertAlmostEqual(breakdown["score_usd"], -180.0, places=3)

    def test_score_breakdown_fails_closed_on_legacy_multi_window_worst_coverage(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 200.0,
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.1,
            worst_window_resolved_size_share_penalty=0.2,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.0,
            mode_worst_window_resolved_size_share_penalty=0.0,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=True,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["worst_window_resolved_share_penalty_usd"], 300.0)
        self.assertEqual(breakdown["worst_window_resolved_size_share_penalty_usd"], 600.0)
        self.assertEqual(breakdown["score_usd"], -880.0)

    def test_score_breakdown_fails_closed_on_legacy_multi_window_mode_worst_coverage(self) -> None:
        breakdown = replay_search._score_breakdown(
            {
                "total_pnl_usd": 20.0,
                "max_drawdown_pct": 0.0,
                "window_count": 4,
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 200.0,
                "signal_mode_summary": {
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 120.0,
                        "resolved_size_usd": 120.0,
                        "trade_count": 4,
                        "total_pnl_usd": 12.0,
                    }
                },
            },
            initial_bankroll_usd=3000.0,
            drawdown_penalty=0.0,
            window_stddev_penalty=0.0,
            worst_window_penalty=0.0,
            pause_guard_penalty=0.0,
            resolved_share_penalty=0.0,
            resolved_size_share_penalty=0.0,
            worst_window_resolved_share_penalty=0.0,
            worst_window_resolved_size_share_penalty=0.0,
            mode_resolved_share_penalty=0.0,
            mode_resolved_size_share_penalty=0.0,
            mode_worst_window_resolved_share_penalty=0.1,
            mode_worst_window_resolved_size_share_penalty=0.2,
            mode_loss_penalty=0.0,
            mode_inactivity_penalty=0.0,
            allow_heuristic=False,
            allow_xgboost=True,
            wallet_concentration_penalty=0.0,
            market_concentration_penalty=0.0,
        )

        self.assertEqual(breakdown["mode_worst_window_resolved_share_penalty_usd"], 300.0)
        self.assertEqual(breakdown["mode_worst_window_resolved_size_share_penalty_usd"], 600.0)
        self.assertEqual(breakdown["score_usd"], -880.0)

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
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=1,
            min_xgboost_accepted_count=1,
            min_heuristic_resolved_count=1,
            min_xgboost_resolved_count=1,
            min_heuristic_win_rate=0.5,
            min_xgboost_win_rate=0.5,
            min_heuristic_resolved_share=0.5,
            min_xgboost_resolved_share=0.5,
            min_heuristic_resolved_size_share=0.0,
            min_xgboost_resolved_size_share=0.0,
            min_heuristic_pnl_usd=1.0,
            min_xgboost_pnl_usd=1.0,
            min_heuristic_worst_window_pnl_usd=1.0,
            min_xgboost_worst_window_pnl_usd=1.0,
            min_heuristic_worst_window_resolved_share=0.5,
            min_xgboost_worst_window_resolved_share=0.5,
            min_heuristic_worst_window_resolved_size_share=0.0,
            min_xgboost_worst_window_resolved_size_share=0.0,
            min_heuristic_positive_window_count=1,
            min_xgboost_positive_window_count=1,
            min_heuristic_worst_active_window_accepted_count=1,
            min_heuristic_worst_active_window_accepted_size_usd=0.0,
            min_xgboost_worst_active_window_accepted_count=1,
            min_xgboost_worst_active_window_accepted_size_usd=0.0,
            max_heuristic_inactive_window_count=0,
            max_xgboost_inactive_window_count=-1,
            max_heuristic_accepted_share=0.5,
            max_heuristic_accepted_size_share=0.0,
            max_heuristic_active_window_accepted_share=0.0,
            max_heuristic_active_window_accepted_size_share=0.0,
            min_xgboost_accepted_share=0.5,
            min_xgboost_accepted_size_share=0.0,
            min_xgboost_active_window_accepted_share=0.0,
            min_xgboost_active_window_accepted_size_share=0.0,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
        )

        self.assertEqual(failures, [])

    def test_constraint_failures_ignores_mix_guards_when_peer_scorer_disabled(self) -> None:
        failures = replay_search._constraint_failures(
            {
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 160.0,
                "resolved_size_usd": 160.0,
                "win_rate": 0.625,
                "max_drawdown_pct": 0.04,
                "worst_window_pnl_usd": 40.0,
                "worst_window_drawdown_pct": 0.04,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 8,
                        "resolved_count": 8,
                        "accepted_size_usd": 160.0,
                        "resolved_size_usd": 160.0,
                        "trade_count": 8,
                        "total_pnl_usd": 40.0,
                        "win_count": 5,
                        "positive_window_count": 1,
                    }
                },
            },
            allow_heuristic=True,
            allow_xgboost=False,
            min_accepted_count=0,
            min_resolved_count=0,
            min_resolved_share=0.0,
            min_resolved_size_share=0.0,
            min_win_rate=0.0,
            min_total_pnl_usd=-1_000_000_000.0,
            max_drawdown_pct=0.0,
            min_worst_window_pnl_usd=-1_000_000_000.0,
            min_worst_window_resolved_share=0.0,
            min_worst_window_resolved_size_share=0.0,
            max_worst_window_drawdown_pct=0.0,
            min_heuristic_accepted_count=1,
            min_xgboost_accepted_count=1,
            min_heuristic_resolved_count=1,
            min_xgboost_resolved_count=1,
            min_heuristic_win_rate=0.5,
            min_xgboost_win_rate=0.5,
            min_heuristic_resolved_share=0.5,
            min_xgboost_resolved_share=0.5,
            min_heuristic_resolved_size_share=0.5,
            min_xgboost_resolved_size_share=0.5,
            min_heuristic_pnl_usd=1.0,
            min_xgboost_pnl_usd=1.0,
            min_heuristic_worst_window_pnl_usd=1.0,
            min_xgboost_worst_window_pnl_usd=1.0,
            min_heuristic_worst_window_resolved_share=0.5,
            min_xgboost_worst_window_resolved_share=0.5,
            min_heuristic_worst_window_resolved_size_share=0.5,
            min_xgboost_worst_window_resolved_size_share=0.5,
            min_heuristic_positive_window_count=1,
            min_xgboost_positive_window_count=1,
            min_heuristic_worst_active_window_accepted_count=1,
            min_heuristic_worst_active_window_accepted_size_usd=1.0,
            min_xgboost_worst_active_window_accepted_count=1,
            min_xgboost_worst_active_window_accepted_size_usd=1.0,
            max_heuristic_inactive_window_count=-1,
            max_xgboost_inactive_window_count=0,
            max_heuristic_accepted_share=0.5,
            max_heuristic_accepted_size_share=0.5,
            max_heuristic_active_window_accepted_share=0.5,
            max_heuristic_active_window_accepted_size_share=0.5,
            min_xgboost_accepted_share=0.5,
            min_xgboost_accepted_size_share=0.5,
            min_xgboost_active_window_accepted_share=0.5,
            min_xgboost_active_window_accepted_size_share=0.5,
            max_pause_guard_reject_share=0.0,
            min_active_window_count=0,
            max_inactive_window_count=-1,
            min_trader_count=0,
            min_market_count=0,
            min_entry_price_band_count=0,
            min_time_to_close_band_count=0,
            max_top_trader_accepted_share=0.0,
            max_top_trader_abs_pnl_share=0.0,
            max_top_trader_size_share=0.0,
            max_top_market_accepted_share=0.0,
            max_top_market_abs_pnl_share=0.0,
            max_top_market_size_share=0.0,
            max_top_entry_price_band_accepted_share=0.0,
            max_top_entry_price_band_abs_pnl_share=0.0,
            max_top_entry_price_band_size_share=0.0,
            max_top_time_to_close_band_accepted_share=0.0,
            max_top_time_to_close_band_abs_pnl_share=0.0,
            max_top_time_to_close_band_size_share=0.0,
        )

        self.assertEqual(failures, [])

    def test_main_filters_infeasible_candidates_from_best_feasible_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
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
            "--base-policy-json",
            json.dumps({"allow_heuristic": False}),
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

    def test_load_base_policy_merges_file_and_inline_json_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_policy_path = Path(tmpdir) / "base_policy.json"
            base_policy_path.write_text(
                json.dumps({"allow_heuristic": False, "min_confidence": 0.61}),
                encoding="utf-8",
            )

            class Args:
                base_policy_file = str(base_policy_path)
                base_policy_json = json.dumps({"min_confidence": 0.67, "allow_xgboost": False})

            policy = replay_search._load_base_policy(Args())

        self.assertFalse(policy.allow_heuristic)
        self.assertFalse(policy.allow_xgboost)
        self.assertAlmostEqual(policy.min_confidence, 0.67, places=6)

    def test_load_grid_merges_file_and_inline_json_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            grid_path = Path(tmpdir) / "grid.json"
            grid_path.write_text(
                json.dumps({"min_confidence": [0.60], "max_bet_fraction": [0.02]}),
                encoding="utf-8",
            )

            class Args:
                grid_file = str(grid_path)
                grid_json = json.dumps({"max_bet_fraction": [0.05], "max_total_open_exposure_fraction": [0.1]})

            grid = replay_search._load_grid(Args())

        self.assertEqual(grid["min_confidence"], [0.60])
        self.assertEqual(grid["max_bet_fraction"], [0.05])
        self.assertEqual(grid["max_total_open_exposure_fraction"], [0.1])

    def test_main_supports_constraints_json_payload(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            accepted_count = 3 if min_conf >= 0.65 else 1
            return {
                "run_id": 2 if min_conf >= 0.65 else 1,
                "total_pnl_usd": 12.0 if min_conf >= 0.65 else 10.0,
                "max_drawdown_pct": 0.02,
                "accepted_count": accepted_count,
                "resolved_count": accepted_count,
                "win_rate": 0.6,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--constraints-json",
            json.dumps({"min_accepted_count": 2}),
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(payload["constraints"]["min_accepted_count"], 2)
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        self.assertEqual(rejected["constraint_failures"], ["accepted_count"])
        self.assertIn("reject accepted_count", stderr.getvalue())

    def test_main_supports_constraints_file_payload(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            total_pnl_usd = 12.0 if min_conf >= 0.65 else 8.0
            return {
                "run_id": 2 if min_conf >= 0.65 else 1,
                "total_pnl_usd": total_pnl_usd,
                "max_drawdown_pct": 0.02,
                "accepted_count": 4,
                "resolved_count": 4,
                "win_rate": 0.6,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            constraints_path = Path(tmpdir) / "constraints.json"
            constraints_path.write_text(json.dumps({"min_total_pnl_usd": 10.0}), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--grid-json",
                json.dumps({"min_confidence": [0.60, 0.65]}),
                "--constraints-file",
                str(constraints_path),
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

        payload = json.loads(stdout.getvalue())
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(payload["constraints"]["min_total_pnl_usd"], 10.0)
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        self.assertEqual(rejected["constraint_failures"], ["total_pnl_usd"])
        self.assertIn("reject total_pnl_usd", stderr.getvalue())

    def test_main_accepts_checked_in_replay_search_specs_from_env_example_paths(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        env_example_values: dict[str, str] = {}
        for raw_line in (repo_root / ".env.example").read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_example_values[key.strip()] = value.strip()

        base_policy_path = repo_root / env_example_values["REPLAY_SEARCH_BASE_POLICY_FILE"]
        grid_path = repo_root / env_example_values["REPLAY_SEARCH_GRID_FILE"]
        constraints_path = repo_root / env_example_values["REPLAY_SEARCH_CONSTRAINTS_FILE"]
        expected_grid = json.loads(grid_path.read_text(encoding="utf-8"))
        expected_constraints = json.loads(constraints_path.read_text(encoding="utf-8"))
        expected_candidate_count = 1
        for values in expected_grid.values():
            expected_candidate_count *= len(values)

        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            return {
                "run_id": int(min_conf * 100),
                "total_pnl_usd": 40.0 + min_conf * 10.0,
                "max_drawdown_pct": 0.05,
                "accepted_count": 12,
                "resolved_count": 12,
                "accepted_size_usd": 120.0,
                "resolved_size_usd": 120.0,
                "win_rate": 0.6,
                "active_window_count": 1,
                "accepted_window_count": 1,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_specs.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--base-policy-file",
                str(base_policy_path),
                "--grid-file",
                str(grid_path),
                "--constraints-file",
                str(constraints_path),
                "--max-combos",
                "64",
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
        self.assertEqual(payload["base_policy"]["mode"], "shadow")
        self.assertEqual(payload["grid"], expected_grid)
        self.assertEqual(
            {key: payload["constraints"][key] for key in expected_constraints},
            expected_constraints,
        )
        self.assertEqual(payload["candidate_count"], expected_candidate_count)
        self.assertEqual(payload["feasible_count"], expected_candidate_count)
        self.assertGreater(len(payload["ranked"]), 1)
        self.assertIn("Replay sweep top candidates:", stderr.getvalue())

    def test_main_merges_constraints_file_and_json_payloads(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            accepted_count = 4 if min_conf >= 0.65 else 1
            total_pnl_usd = 12.0 if min_conf >= 0.65 else 8.0
            return {
                "run_id": 2 if min_conf >= 0.65 else 1,
                "total_pnl_usd": total_pnl_usd,
                "max_drawdown_pct": 0.02,
                "accepted_count": accepted_count,
                "resolved_count": accepted_count,
                "win_rate": 0.6,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            constraints_path = Path(tmpdir) / "constraints.json"
            constraints_path.write_text(json.dumps({"min_total_pnl_usd": 10.0}), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--grid-json",
                json.dumps({"min_confidence": [0.60, 0.65]}),
                "--constraints-file",
                str(constraints_path),
                "--constraints-json",
                json.dumps({"min_accepted_count": 2}),
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                replay_search.main()

        payload = json.loads(stdout.getvalue())
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        self.assertEqual(payload["constraints"]["min_total_pnl_usd"], 10.0)
        self.assertEqual(payload["constraints"]["min_accepted_count"], 2)
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        self.assertEqual(rejected["constraint_failures"], ["accepted_count", "total_pnl_usd"])
        self.assertIn("reject accepted_count,total_pnl_usd", stderr.getvalue())

    def test_main_supports_score_weights_json_payload(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 20.0,
                    "max_drawdown_pct": 0.02,
                    "accepted_count": 4,
                    "resolved_count": 4,
                    "win_rate": 0.6,
                    "worst_window_pnl_usd": -10.0,
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 15.0,
                "max_drawdown_pct": 0.02,
                "accepted_count": 4,
                "resolved_count": 4,
                "win_rate": 0.6,
                "worst_window_pnl_usd": 0.0,
            }

        stdout = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--score-weights-json",
            json.dumps({"worst_window_penalty": 1.0}),
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        self.assertEqual(payload["best_feasible"]["result"]["score_breakdown"]["worst_window_penalty_usd"], 0.0)

    def test_main_persists_request_token_on_replay_search_run(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
            return {
                "run_id": 1,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.02,
                "accepted_count": 4,
                "resolved_count": 4,
                "win_rate": 0.6,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_request_token.db"
            stdout = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--request-token",
                "req-123",
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
            ):
                replay_search.main()

            conn = sqlite3.connect(str(db_path))
            try:
                request_token = conn.execute(
                    "SELECT request_token FROM replay_search_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(request_token, "req-123")

    def test_main_merges_score_weights_file_and_json_payloads(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 20.0,
                    "max_drawdown_pct": 0.02,
                    "accepted_count": 4,
                    "resolved_count": 4,
                    "win_rate": 0.6,
                    "worst_window_pnl_usd": -10.0,
                    "window_pnl_stddev_usd": 1.0,
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 15.0,
                "max_drawdown_pct": 0.02,
                "accepted_count": 4,
                "resolved_count": 4,
                "win_rate": 0.6,
                "worst_window_pnl_usd": 0.0,
                "window_pnl_stddev_usd": 20.0,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            weights_path = Path(tmpdir) / "score_weights.json"
            weights_path.write_text(json.dumps({"worst_window_penalty": 1.0}), encoding="utf-8")
            stdout = io.StringIO()
            argv = [
                "replay_search.py",
                "--grid-json",
                json.dumps({"min_confidence": [0.60, 0.65]}),
                "--score-weights-file",
                str(weights_path),
                "--score-weights-json",
                json.dumps({"window_stddev_penalty": 1.0}),
            ]
            with (
                patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
                patch("sys.argv", argv),
                redirect_stdout(stdout),
            ):
                replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        self.assertEqual(payload["worst_window_penalty"], 1.0)
        self.assertEqual(payload["window_stddev_penalty"], 1.0)

    def test_main_cli_score_weights_override_score_weight_payload(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 20.0,
                    "max_drawdown_pct": 0.02,
                    "accepted_count": 4,
                    "resolved_count": 4,
                    "win_rate": 0.6,
                    "worst_window_pnl_usd": -10.0,
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 15.0,
                "max_drawdown_pct": 0.02,
                "accepted_count": 4,
                "resolved_count": 4,
                "win_rate": 0.6,
                "worst_window_pnl_usd": 0.0,
            }

        stdout = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--score-weights-json",
            json.dumps({"worst_window_penalty": 1.0}),
            "--worst-window-penalty",
            "0",
        ]
        with (
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["worst_window_penalty"], 0.0)
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)

    def test_main_rejects_unknown_score_weight_payload_keys(self) -> None:
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60]}),
            "--score-weights-json",
            json.dumps({"not_a_real_penalty": 1}),
        ]
        with (
            patch("sys.argv", argv),
            self.assertRaisesRegex(ValueError, "Unknown replay-search score-weight key"),
        ):
            replay_search.main()

    def test_main_rejects_invalid_score_weight_payload_values(self) -> None:
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60]}),
            "--score-weights-json",
            json.dumps({"drawdown_penalty": "abc"}),
        ]
        with (
            patch("sys.argv", argv),
            self.assertRaisesRegex(ValueError, "must be a finite non-negative number"),
        ):
            replay_search.main()

        negative_argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60]}),
            "--score-weights-json",
            json.dumps({"drawdown_penalty": -1}),
        ]
        with (
            patch("sys.argv", negative_argv),
            self.assertRaisesRegex(ValueError, "must be a finite non-negative number"),
        ):
            replay_search.main()

    def test_main_cli_constraints_override_constraints_json_payload(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            accepted_count = 3 if min_conf >= 0.65 else 1
            return {
                "run_id": 2 if min_conf >= 0.65 else 1,
                "total_pnl_usd": 12.0 if min_conf >= 0.65 else 10.0,
                "max_drawdown_pct": 0.02,
                "accepted_count": accepted_count,
                "resolved_count": accepted_count,
                "win_rate": 0.6,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--constraints-json",
            json.dumps({"min_accepted_count": 4}),
            "--min-accepted-count",
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
        self.assertEqual(payload["constraints"]["min_accepted_count"], 2)
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.65)
        self.assertIn("reject accepted_count", stderr.getvalue())

    def test_main_rejects_unknown_constraints_payload_keys(self) -> None:
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60]}),
            "--constraints-json",
            json.dumps({"not_a_real_constraint": 1}),
        ]
        with (
            patch("sys.argv", argv),
            self.assertRaisesRegex(ValueError, "Unknown replay-search constraint key"),
        ):
            replay_search.main()

    def test_main_supports_list_valued_segment_filter_overrides(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", initial_state=None):
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

        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 70.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 12,
                    "accepted_size_usd": 120.0,
                    "resolved_count": 12,
                    "resolved_size_usd": 120.0,
                    "win_rate": 0.7,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 5, "accepted_size_usd": 50.0, "resolved_count": 5, "resolved_size_usd": 50.0, "trade_count": 5, "total_pnl_usd": 18.0, "win_count": 3},
                        "xgboost": {"accepted_count": 7, "accepted_size_usd": 70.0, "resolved_count": 7, "resolved_size_usd": 70.0, "trade_count": 7, "total_pnl_usd": 52.0, "win_count": 5},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 80.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 12,
                "accepted_size_usd": 120.0,
                "resolved_count": 12,
                "resolved_size_usd": 120.0,
                "win_rate": 0.68,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 12, "accepted_size_usd": 120.0, "resolved_count": 12, "resolved_size_usd": 120.0, "trade_count": 12, "total_pnl_usd": 80.0, "win_count": 8},
                    "xgboost": {"accepted_count": 0, "accepted_size_usd": 0.0, "resolved_count": 0, "resolved_size_usd": 0.0, "trade_count": 0, "total_pnl_usd": 0.0, "win_count": 0},
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
        self.assertIn("modes heur 5 (42%) sz 42% / xgb 7 (58%) sz 58%", stderr.getvalue())

    def test_main_can_require_mode_specific_accepted_shares(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 68.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "accepted_size_usd": 100.0,
                    "resolved_count": 10,
                    "resolved_size_usd": 100.0,
                    "win_rate": 0.7,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "accepted_size_usd": 40.0, "resolved_count": 4, "resolved_size_usd": 40.0, "trade_count": 4, "total_pnl_usd": 16.0, "win_count": 3},
                        "xgboost": {"accepted_count": 6, "accepted_size_usd": 60.0, "resolved_count": 6, "resolved_size_usd": 60.0, "trade_count": 6, "total_pnl_usd": 52.0, "win_count": 4},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 82.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "accepted_size_usd": 100.0,
                "resolved_count": 10,
                "resolved_size_usd": 100.0,
                "win_rate": 0.68,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 8, "accepted_size_usd": 80.0, "resolved_count": 8, "resolved_size_usd": 80.0, "trade_count": 8, "total_pnl_usd": 64.0, "win_count": 6},
                    "xgboost": {"accepted_count": 2, "accepted_size_usd": 20.0, "resolved_count": 2, "resolved_size_usd": 20.0, "trade_count": 2, "total_pnl_usd": 18.0, "win_count": 1},
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
        self.assertIn("modes heur 4 (40%) sz 40% / xgb 6 (60%) sz 60%", stderr.getvalue())

    def test_main_can_require_mode_specific_accepted_size_shares(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 68.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "accepted_size_usd": 180.0,
                    "resolved_count": 10,
                    "resolved_size_usd": 180.0,
                    "win_rate": 0.7,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "accepted_size_usd": 60.0, "resolved_count": 4, "resolved_size_usd": 60.0, "trade_count": 4, "total_pnl_usd": 16.0, "win_count": 3},
                        "xgboost": {"accepted_count": 6, "accepted_size_usd": 120.0, "resolved_count": 6, "resolved_size_usd": 120.0, "trade_count": 6, "total_pnl_usd": 52.0, "win_count": 4},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 82.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "accepted_size_usd": 200.0,
                "resolved_count": 10,
                "resolved_size_usd": 200.0,
                "win_rate": 0.68,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 6, "accepted_size_usd": 170.0, "resolved_count": 6, "resolved_size_usd": 170.0, "trade_count": 6, "total_pnl_usd": 64.0, "win_count": 5},
                    "xgboost": {"accepted_count": 4, "accepted_size_usd": 30.0, "resolved_count": 4, "resolved_size_usd": 30.0, "trade_count": 4, "total_pnl_usd": 18.0, "win_count": 2},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--max-heuristic-accepted-size-share",
            "0.60",
            "--min-xgboost-accepted-size-share",
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
        self.assertEqual(rejected["constraint_failures"], ["heuristic_accepted_size_share", "xgboost_accepted_size_share"])
        self.assertEqual(payload["constraints"]["max_heuristic_accepted_size_share"], 0.6)
        self.assertEqual(payload["constraints"]["min_xgboost_accepted_size_share"], 0.4)
        self.assertIn("modes heur 4 (40%) sz 33% / xgb 6 (60%) sz 67%", stderr.getvalue())

    def test_main_can_require_mode_specific_active_window_accepted_shares(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    heuristic_accepted = 5
                    xgboost_accepted = 5
                else:
                    heuristic_accepted = 9
                    xgboost_accepted = 1
            else:
                heuristic_accepted = 5
                xgboost_accepted = 5
            total_pnl = 40.0 if min_conf >= 0.65 else 34.0
            return {
                "run_id": (1 if min_conf >= 0.65 else 3) + (0 if start_ts == 1 else 1),
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": heuristic_accepted + xgboost_accepted,
                "resolved_count": heuristic_accepted + xgboost_accepted,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": heuristic_accepted + xgboost_accepted,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": heuristic_accepted, "resolved_count": heuristic_accepted, "trade_count": heuristic_accepted, "total_pnl_usd": 12.0, "win_count": max(heuristic_accepted - 1, 1)},
                    "xgboost": {"accepted_count": xgboost_accepted, "resolved_count": xgboost_accepted, "trade_count": xgboost_accepted, "total_pnl_usd": total_pnl - 12.0, "win_count": max(xgboost_accepted - 1, 1)},
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
            "--max-heuristic-active-window-accepted-share",
            "0.60",
            "--min-xgboost-active-window-accepted-share",
            "0.40",
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
            ["heuristic_active_window_accepted_share", "xgboost_active_window_accepted_share"],
        )
        self.assertEqual(payload["constraints"]["max_heuristic_active_window_accepted_share"], 0.6)
        self.assertEqual(payload["constraints"]["min_xgboost_active_window_accepted_share"], 0.4)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["heuristic"]["max_active_window_accepted_share"], 0.9)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["min_active_window_accepted_share"], 0.1)
        self.assertIn("reject heuristic_active_window_accepted_share,xgboost_active_window_accepted_share", stderr.getvalue())

    def test_main_can_require_mode_specific_active_window_accepted_size_shares(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    heuristic_size = 50.0
                    xgboost_size = 50.0
                else:
                    heuristic_size = 90.0
                    xgboost_size = 10.0
            else:
                heuristic_size = 50.0
                xgboost_size = 50.0
            total_pnl = 44.0 if min_conf >= 0.65 else 38.0
            return {
                "run_id": (1 if min_conf >= 0.65 else 3) + (0 if start_ts == 1 else 1),
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "accepted_size_usd": heuristic_size + xgboost_size,
                "resolved_count": 10,
                "resolved_size_usd": heuristic_size + xgboost_size,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 5, "accepted_size_usd": heuristic_size, "resolved_count": 5, "resolved_size_usd": heuristic_size, "trade_count": 5, "total_pnl_usd": 12.0, "win_count": 3},
                    "xgboost": {"accepted_count": 5, "accepted_size_usd": xgboost_size, "resolved_count": 5, "resolved_size_usd": xgboost_size, "trade_count": 5, "total_pnl_usd": total_pnl - 12.0, "win_count": 3},
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
            "--max-heuristic-active-window-accepted-size-share",
            "0.60",
            "--min-xgboost-active-window-accepted-size-share",
            "0.40",
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
            ["heuristic_active_window_accepted_size_share", "xgboost_active_window_accepted_size_share"],
        )
        self.assertEqual(payload["constraints"]["max_heuristic_active_window_accepted_size_share"], 0.6)
        self.assertEqual(payload["constraints"]["min_xgboost_active_window_accepted_size_share"], 0.4)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["heuristic"]["max_active_window_accepted_size_share"], 0.9)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["min_active_window_accepted_size_share"], 0.1)
        self.assertIn("reject heuristic_active_window_accepted_size_share,xgboost_active_window_accepted_size_share", stderr.getvalue())

    def test_main_can_limit_pause_guard_reject_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_can_limit_top_deployed_dollar_concentration(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 60.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "win_rate": 0.6,
                    "trader_concentration": {
                        "trader_count": 4,
                        "top_size_trader_address": "0xbbb",
                        "top_size_usd": 160.0,
                        "top_size_share": 0.40,
                    },
                    "market_concentration": {
                        "market_count": 4,
                        "top_size_market_id": "market-b",
                        "top_size_usd": 156.0,
                        "top_size_share": 0.39,
                    },
                    "entry_price_band_concentration": {
                        "entry_price_band_count": 3,
                        "top_size_entry_price_band": "0.60-0.69",
                        "top_size_usd": 140.0,
                        "top_size_share": 0.35,
                    },
                    "time_to_close_band_concentration": {
                        "time_to_close_band_count": 3,
                        "top_size_time_to_close_band": "2h-12h",
                        "top_size_usd": 144.0,
                        "top_size_share": 0.36,
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 76.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "win_rate": 0.6,
                "trader_concentration": {
                    "trader_count": 2,
                    "top_size_trader_address": "0xaaa",
                    "top_size_usd": 290.0,
                    "top_size_share": 0.725,
                },
                "market_concentration": {
                    "market_count": 2,
                    "top_size_market_id": "market-a",
                    "top_size_usd": 276.0,
                    "top_size_share": 0.69,
                },
                "entry_price_band_concentration": {
                    "entry_price_band_count": 2,
                    "top_size_entry_price_band": "0.60-0.69",
                    "top_size_usd": 288.0,
                    "top_size_share": 0.72,
                },
                "time_to_close_band_concentration": {
                    "time_to_close_band_count": 2,
                    "top_size_time_to_close_band": "2h-12h",
                    "top_size_usd": 280.0,
                    "top_size_share": 0.70,
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--max-top-trader-size-share",
            "0.60",
            "--max-top-market-size-share",
            "0.60",
            "--max-top-entry-price-band-size-share",
            "0.60",
            "--max-top-time-to-close-band-size-share",
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
        self.assertEqual(
            rejected["constraint_failures"],
            [
                "top_trader_size_share",
                "top_market_size_share",
                "top_entry_price_band_size_share",
                "top_time_to_close_band_size_share",
            ],
        )
        self.assertEqual(payload["constraints"]["max_top_trader_size_share"], 0.6)
        self.assertEqual(payload["constraints"]["max_top_market_size_share"], 0.6)
        self.assertEqual(payload["constraints"]["max_top_entry_price_band_size_share"], 0.6)
        self.assertEqual(payload["constraints"]["max_top_time_to_close_band_size_share"], 0.6)
        self.assertIn("wallet sz 40%", stderr.getvalue())
        self.assertIn("market sz 39%", stderr.getvalue())
        self.assertIn("band sz 35%", stderr.getvalue())
        self.assertIn("hzn sz 36%", stderr.getvalue())

    def test_main_can_require_minimum_distinct_concentration_counts(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
            "--base-policy-json",
            json.dumps({"allow_heuristic": False}),
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

    def test_main_can_require_minimum_worst_active_window_accepted_count(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 1 if start_ts == 1 else 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 18.0 if start_ts == 1 else 7.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 6 if start_ts == 1 else 1,
                    "resolved_count": 6 if start_ts == 1 else 1,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 6 if start_ts == 1 else 1,
                    "win_rate": 4 / 6 if start_ts == 1 else 1.0,
                }
            return {
                "run_id": 3 if start_ts == 1 else 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 11.0 if start_ts == 1 else 9.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 4,
                "resolved_count": 4,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 4,
                "win_rate": 0.75,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--base-policy-json",
            json.dumps({"allow_heuristic": False}),
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--window-days",
            "30",
            "--window-count",
            "2",
            "--min-worst-active-window-accepted-count",
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
        self.assertEqual(rejected["constraint_failures"], ["worst_active_window_accepted_count"])
        self.assertEqual(rejected["result"]["active_window_count"], 2)
        self.assertEqual(rejected["result"]["worst_active_window_accepted_count"], 1)
        self.assertEqual(payload["constraints"]["min_worst_active_window_accepted_count"], 2)
        self.assertIn("reject worst_active_window_accepted_count", stderr.getvalue())

    def test_main_can_penalize_worst_active_window_depth_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                accepted_count = 9 if start_ts == 1 else 2
                total_pnl = 28.0 if start_ts == 1 else 24.0
            else:
                accepted_count = 6
                total_pnl = 23.0 if start_ts == 1 else 22.0
            return {
                "run_id": 1 if start_ts == 1 else 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.03,
                "accepted_count": accepted_count,
                "resolved_count": accepted_count,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": accepted_count,
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
            "--worst-active-window-accepted-penalty",
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
        self.assertEqual(payload["worst_active_window_accepted_penalty"], 0.02)
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(rejected_breakdown["worst_active_window_accepted_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["worst_active_window_accepted_penalty_usd"], best_breakdown["worst_active_window_accepted_penalty_usd"])
        self.assertGreater(best_breakdown["score_usd"], rejected_breakdown["score_usd"])
        self.assertIn("accept 2/2", stderr.getvalue())
        self.assertIn("worst-acc 2", stderr.getvalue())

    def test_main_uses_worst_active_window_counts_for_distinct_concentration_floors(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_can_penalize_low_breadth_counts_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
                    "trader_concentration": {"trader_count": 4},
                    "market_concentration": {"market_count": 4},
                    "entry_price_band_concentration": {"entry_price_band_count": 4},
                    "time_to_close_band_concentration": {"time_to_close_band_count": 4},
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
                "trader_concentration": {"trader_count": 2},
                "market_concentration": {"market_count": 2},
                "entry_price_band_concentration": {"entry_price_band_count": 2},
                "time_to_close_band_concentration": {"time_to_close_band_count": 2},
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--wallet-count-penalty",
            "0.1",
            "--market-count-penalty",
            "0.1",
            "--entry-price-band-count-penalty",
            "0.1",
            "--time-to-close-band-count-penalty",
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
        self.assertEqual(payload["wallet_count_penalty"], 0.1)
        self.assertEqual(payload["market_count_penalty"], 0.1)
        self.assertEqual(payload["entry_price_band_count_penalty"], 0.1)
        self.assertEqual(payload["time_to_close_band_count_penalty"], 0.1)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(best_breakdown["wallet_count_penalty_usd"], 0.0)
        self.assertGreater(best_breakdown["market_count_penalty_usd"], 0.0)
        self.assertGreater(best_breakdown["entry_price_band_count_penalty_usd"], 0.0)
        self.assertGreater(best_breakdown["time_to_close_band_count_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["wallet_count_penalty_usd"], best_breakdown["wallet_count_penalty_usd"])
        self.assertGreater(rejected_breakdown["market_count_penalty_usd"], best_breakdown["market_count_penalty_usd"])
        self.assertGreater(rejected_breakdown["entry_price_band_count_penalty_usd"], best_breakdown["entry_price_band_count_penalty_usd"])
        self.assertGreater(rejected_breakdown["time_to_close_band_count_penalty_usd"], best_breakdown["time_to_close_band_count_penalty_usd"])

    def test_main_can_penalize_losing_scorer_paths_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_can_require_mode_specific_resolved_size_shares(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                return {
                    "run_id": 2,
                    "total_pnl_usd": 64.0,
                    "max_drawdown_pct": 0.05,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "accepted_size_usd": 240.0,
                    "resolved_size_usd": 200.0,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 4, "accepted_size_usd": 80.0, "resolved_size_usd": 80.0, "trade_count": 4, "total_pnl_usd": 16.0, "win_count": 2},
                        "xgboost": {"accepted_count": 6, "resolved_count": 6, "accepted_size_usd": 160.0, "resolved_size_usd": 120.0, "trade_count": 6, "total_pnl_usd": 48.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 1,
                "total_pnl_usd": 70.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 240.0,
                "resolved_size_usd": 180.0,
                "win_rate": 0.7,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 5, "resolved_count": 5, "accepted_size_usd": 100.0, "resolved_size_usd": 100.0, "trade_count": 5, "total_pnl_usd": 30.0, "win_count": 3},
                    "xgboost": {"accepted_count": 5, "resolved_count": 5, "accepted_size_usd": 140.0, "resolved_size_usd": 80.0, "trade_count": 5, "total_pnl_usd": 40.0, "win_count": 4},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--min-heuristic-resolved-size-share",
            "0.75",
            "--min-xgboost-resolved-size-share",
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
        self.assertEqual(rejected["constraint_failures"], ["xgboost_resolved_size_share"])
        self.assertEqual(payload["constraints"]["min_heuristic_resolved_size_share"], 0.75)
        self.assertEqual(payload["constraints"]["min_xgboost_resolved_size_share"], 0.75)

    def test_main_can_require_mode_specific_pnl_floors(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_can_require_global_resolved_size_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            resolved_size_usd = 180.0 if min_conf >= 0.65 else 140.0
            return {
                "run_id": 1,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 240.0,
                "resolved_size_usd": resolved_size_usd,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 80.0,
                        "resolved_size_usd": 80.0,
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 160.0,
                        "resolved_size_usd": resolved_size_usd - 80.0,
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
            "--min-resolved-size-share",
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
        self.assertEqual(rejected["constraint_failures"], ["resolved_size_share"])
        self.assertEqual(payload["constraints"]["min_resolved_size_share"], 0.75)

    def test_main_can_require_global_worst_window_resolved_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_can_require_global_worst_window_resolved_size_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                resolved_size_usd = 200.0
            elif min_conf >= 0.65:
                resolved_size_usd = 180.0
            else:
                resolved_size_usd = 80.0
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": resolved_size_usd,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 80.0,
                        "resolved_size_usd": 80.0,
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 120.0,
                        "resolved_size_usd": max(resolved_size_usd - 80.0, 0.0),
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
            "--min-worst-window-resolved-size-share",
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
        self.assertEqual(rejected["constraint_failures"], ["worst_window_resolved_size_share"])
        self.assertEqual(rejected["result"]["worst_window_resolved_size_share"], 0.4)
        self.assertEqual(payload["constraints"]["min_worst_window_resolved_size_share"], 0.75)
        self.assertIn("reject worst_window_resolved_size_share", stderr.getvalue())

    def test_main_can_penalize_low_worst_window_resolved_size_share_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                resolved_size_usd = 200.0
            elif min_conf >= 0.65:
                resolved_size_usd = 180.0
            else:
                resolved_size_usd = 80.0
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0 if min_conf >= 0.65 else 14.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": resolved_size_usd,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 80.0,
                        "resolved_size_usd": 80.0,
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 120.0,
                        "resolved_size_usd": max(resolved_size_usd - 80.0, 0.0),
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
            "--worst-window-resolved-size-share-penalty",
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
        self.assertEqual(payload["worst_window_resolved_size_share_penalty"], 0.1)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(rejected_breakdown["worst_window_resolved_size_share_penalty_usd"], 0.0)
        self.assertGreater(
            rejected_breakdown["worst_window_resolved_size_share_penalty_usd"],
            best_breakdown["worst_window_resolved_size_share_penalty_usd"],
        )
        self.assertGreater(payload["ranked"][0]["score"], rejected["score"])

    def test_main_can_require_mode_specific_worst_window_pnl_floors(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_can_require_mode_specific_worst_window_resolved_size_shares(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                xgboost_resolved_size_usd = 120.0
            elif min_conf >= 0.65:
                xgboost_resolved_size_usd = 100.0
            else:
                xgboost_resolved_size_usd = 40.0
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.05,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 80.0 + xgboost_resolved_size_usd,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 80.0,
                        "resolved_size_usd": 80.0,
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 120.0,
                        "resolved_size_usd": xgboost_resolved_size_usd,
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
            "--min-xgboost-worst-window-resolved-size-share",
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
        self.assertEqual(rejected["constraint_failures"], ["xgboost_worst_window_resolved_size_share"])
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["worst_window_resolved_size_share"], 0.333333)
        self.assertEqual(payload["constraints"]["min_xgboost_worst_window_resolved_size_share"], 0.75)
        self.assertIn("reject xgboost_worst_window_resolved_size_share", stderr.getvalue())

    def test_main_can_penalize_low_mode_worst_window_resolved_size_share_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if start_ts == 1:
                xgboost_resolved_size_usd = 120.0
            elif min_conf >= 0.65:
                xgboost_resolved_size_usd = 100.0
            else:
                xgboost_resolved_size_usd = 40.0
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0 if min_conf >= 0.65 else 14.0,
                "max_drawdown_pct": 0.05,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 80.0 + xgboost_resolved_size_usd,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_size_usd": 80.0,
                        "resolved_size_usd": 80.0,
                        "trade_count": 4,
                        "total_pnl_usd": 4.0,
                        "win_count": 2,
                    },
                    "xgboost": {
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "accepted_size_usd": 120.0,
                        "resolved_size_usd": xgboost_resolved_size_usd,
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
            "--mode-worst-window-resolved-size-share-penalty",
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
        self.assertEqual(payload["mode_worst_window_resolved_size_share_penalty"], 0.1)
        self.assertEqual(payload["ranked"][0]["overrides"]["min_confidence"], 0.65)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.6)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(best_breakdown["mode_worst_window_resolved_size_share_penalty_usd"], 0.0)
        self.assertGreater(
            rejected_breakdown["mode_worst_window_resolved_size_share_penalty_usd"],
            best_breakdown["mode_worst_window_resolved_size_share_penalty_usd"],
        )
        self.assertGreater(payload["ranked"][0]["score"], rejected["score"])

    def test_main_counts_missing_mode_windows_as_zero_activity_for_worst_window(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_can_require_mode_specific_worst_active_window_accepted_count(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                xgboost_accepted = 4 if start_ts == 1 else 1
                return {
                    "run_id": 1 if start_ts == 1 else 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 16.0 if start_ts == 1 else 7.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 7 if start_ts == 1 else 4,
                    "resolved_count": 7 if start_ts == 1 else 4,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 7 if start_ts == 1 else 4,
                    "win_rate": 4 / 7 if start_ts == 1 else 0.75,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 6.0, "win_count": 2},
                        "xgboost": {"accepted_count": xgboost_accepted, "resolved_count": xgboost_accepted, "trade_count": xgboost_accepted, "total_pnl_usd": 10.0 if start_ts == 1 else 1.0, "win_count": 2 if start_ts == 1 else 1},
                    },
                }
            return {
                "run_id": 3 if start_ts == 1 else 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0 if start_ts == 1 else 11.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 6,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 4 / 6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 5.0 if start_ts == 1 else 4.0, "win_count": 2},
                    "xgboost": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 7.0, "win_count": 2},
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
            "--min-xgboost-worst-active-window-accepted-count",
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
        self.assertEqual(rejected["constraint_failures"], ["xgboost_worst_active_window_accepted_count"])
        self.assertEqual(payload["constraints"]["min_xgboost_worst_active_window_accepted_count"], 2)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["worst_active_window_accepted_count"], 1)
        self.assertIn("reject xgboost_worst_active_window_accepted_count", stderr.getvalue())

    def test_main_can_require_mode_specific_accepted_windows(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                xgboost_accepted = 4 if start_ts == 1 else 0
                return {
                    "run_id": 1 if start_ts == 1 else 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 16.0 if start_ts == 1 else 7.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 7 if start_ts == 1 else 3,
                    "resolved_count": 7 if start_ts == 1 else 3,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 7 if start_ts == 1 else 3,
                    "win_rate": 4 / 7 if start_ts == 1 else 2 / 3,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 6.0 if start_ts == 1 else 7.0, "win_count": 2},
                        "xgboost": {"accepted_count": xgboost_accepted, "resolved_count": xgboost_accepted, "trade_count": xgboost_accepted, "total_pnl_usd": 10.0 if start_ts == 1 else 0.0, "win_count": 2 if start_ts == 1 else 0},
                    },
                }
            return {
                "run_id": 3 if start_ts == 1 else 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0 if start_ts == 1 else 11.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 6,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 4 / 6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 5.0 if start_ts == 1 else 4.0, "win_count": 2},
                    "xgboost": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 7.0, "win_count": 2},
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
            "--min-xgboost-accepted-windows",
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
        self.assertEqual(rejected["constraint_failures"], ["xgboost_accepted_window_count"])
        self.assertEqual(payload["constraints"]["min_xgboost_accepted_windows"], 2)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["accepted_window_count"], 1)
        self.assertIn("reject xgboost_accepted_window_count", stderr.getvalue())

    def test_main_can_require_global_accepted_windows(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 18.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 7,
                        "resolved_count": 7,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 7,
                        "win_rate": 5 / 7,
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
                "total_pnl_usd": 11.0 if start_ts == 1 else 10.0,
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
            "--min-accepted-windows",
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
        self.assertEqual(rejected["constraint_failures"], ["accepted_window_count"])
        self.assertEqual(payload["constraints"]["min_accepted_windows"], 2)
        self.assertEqual(rejected["result"]["accepted_window_count"], 1)
        self.assertIn("reject accepted_window_count", stderr.getvalue())

    def test_main_rejects_high_top_two_accepting_window_trade_share(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                accepted_count = 5 if start_ts == 1 else 3 if start_ts == 2_592_001 else 1
                return {
                    "run_id": 1 if start_ts == 1 else 2 if start_ts == 2_592_001 else 3,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 8.0 if start_ts == 1 else 5.0 if start_ts == 2_592_001 else 2.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": accepted_count,
                    "resolved_count": accepted_count,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": accepted_count,
                    "win_rate": 0.6,
                }
            return {
                "run_id": 10 if start_ts == 1 else 11 if start_ts == 2_592_001 else 12,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 6.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 3,
                "resolved_count": 3,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 3,
                "win_rate": 2 / 3,
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
            "3",
            "--max-top-two-accepting-window-accepted-share",
            "0.8",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=7_776_000),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        self.assertEqual(rejected["constraint_failures"], ["top_two_accepting_window_accepted_share"])
        self.assertEqual(payload["constraints"]["max_top_two_accepting_window_accepted_share"], 0.8)
        self.assertAlmostEqual(rejected["result"]["top_two_accepting_window_accepted_share"], 8.0 / 9.0, places=6)
        self.assertIn("top2-acc 89%", stderr.getvalue())
        self.assertIn("reject top_two_accepting_window_accepted_share", stderr.getvalue())

    def test_main_rejects_high_accepting_window_trade_concentration_index(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                accepted_count = 4 if start_ts == 1 else 2 if start_ts == 2_592_001 else 2
                return {
                    "run_id": 1 if start_ts == 1 else 2 if start_ts == 2_592_001 else 3,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 7.0 if start_ts == 1 else 4.0 if start_ts == 2_592_001 else 3.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": accepted_count,
                    "resolved_count": accepted_count,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": accepted_count,
                    "win_rate": 0.625,
                }
            accepted_count = 3 if start_ts == 1 else 3 if start_ts == 2_592_001 else 2
            return {
                "run_id": 10 if start_ts == 1 else 11 if start_ts == 2_592_001 else 12,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 6.0 if start_ts == 1 else 5.0 if start_ts == 2_592_001 else 3.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": accepted_count,
                "resolved_count": accepted_count,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": accepted_count,
                "win_rate": 0.625,
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
            "3",
            "--max-accepting-window-accepted-concentration-index",
            "0.36",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=7_776_000),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        self.assertEqual(rejected["constraint_failures"], ["accepting_window_accepted_concentration_index"])
        self.assertEqual(payload["constraints"]["max_accepting_window_accepted_concentration_index"], 0.36)
        self.assertAlmostEqual(rejected["result"]["accepting_window_accepted_concentration_index"], 0.375, places=6)
        self.assertIn("acc-ci 38%", stderr.getvalue())
        self.assertIn("reject accepting_window_accepted_concentration_index", stderr.getvalue())

    def test_main_rejects_high_non_accepting_active_window_streak(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 6.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 3,
                        "resolved_count": 3,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 3,
                        "win_rate": 2 / 3,
                    }
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 2.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 0,
                    "resolved_count": 1,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "win_rate": 1.0,
                }
            return {
                "run_id": 1 if start_ts == 1 else 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 5.0 if start_ts == 1 else 4.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 2,
                "resolved_count": 2,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 2,
                "win_rate": 0.5,
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
            "--max-non-accepting-active-window-streak",
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
        self.assertEqual(rejected["constraint_failures"], ["max_non_accepting_active_window_streak"])
        self.assertEqual(payload["constraints"]["max_non_accepting_active_window_streak"], 0)
        self.assertEqual(rejected["result"]["max_non_accepting_active_window_streak"], 1)
        self.assertIn("acc-gap 1", stderr.getvalue())
        self.assertIn("reject max_non_accepting_active_window_streak", stderr.getvalue())

    def test_main_does_not_count_leading_non_accepting_active_warmup_as_drought(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 1.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 0,
                        "resolved_count": 1,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 0,
                        "win_rate": 1.0,
                    }
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 6.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 3,
                    "resolved_count": 3,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 3,
                    "win_rate": 2 / 3,
                }
            return {
                "run_id": 10 if start_ts == 1 else 11,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 4.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 2,
                "resolved_count": 2,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 2,
                "win_rate": 0.5,
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
            "--max-non-accepting-active-window-streak",
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
        candidate = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        self.assertEqual(candidate["constraint_failures"], [])
        self.assertEqual(candidate["result"]["max_non_accepting_active_window_streak"], 0)
        self.assertEqual(candidate["result"]["non_accepting_active_window_episode_count"], 0)
        self.assertNotIn("reject max_non_accepting_active_window_streak", stderr.getvalue())

    def test_main_rejects_high_non_accepting_active_window_episode_count(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 6.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 3,
                        "resolved_count": 3,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 3,
                        "win_rate": 2 / 3,
                    }
                if start_ts == 2_592_001:
                    return {
                        "run_id": 2,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 2.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 0,
                        "resolved_count": 1,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 0,
                        "win_rate": 1.0,
                    }
                if start_ts == 5_184_001:
                    return {
                        "run_id": 3,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 5.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 2,
                        "resolved_count": 2,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 2,
                        "win_rate": 0.5,
                    }
                return {
                    "run_id": 4,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 2.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 0,
                    "resolved_count": 1,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 0,
                    "win_rate": 1.0,
                }
            return {
                "run_id": 10 if start_ts == 1 else 11 if start_ts == 2_592_001 else 12 if start_ts == 5_184_001 else 13,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 5.0 if start_ts in (1, 5_184_001) else 4.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 2 if start_ts in (1, 5_184_001) else 1,
                "resolved_count": 2 if start_ts in (1, 5_184_001) else 1,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 2 if start_ts in (1, 5_184_001) else 1,
                "win_rate": 0.5,
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
            "4",
            "--max-non-accepting-active-window-episodes",
            "1",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=10_368_000),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        self.assertEqual(rejected["constraint_failures"], ["non_accepting_active_window_episode_count"])
        self.assertEqual(payload["constraints"]["max_non_accepting_active_window_episodes"], 1)
        self.assertEqual(rejected["result"]["non_accepting_active_window_episode_count"], 2)
        self.assertIn("acc-runs 2", stderr.getvalue())
        self.assertIn("reject non_accepting_active_window_episode_count", stderr.getvalue())

    def test_main_rejects_high_xgboost_non_accepting_active_window_streak(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 7.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 6,
                        "win_rate": 4 / 6,
                        "signal_mode_summary": {
                            "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 3.0, "win_count": 2},
                            "xgboost": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 4.0, "win_count": 2},
                        },
                    }
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 4.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 4,
                    "resolved_count": 4,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 4,
                    "win_rate": 0.5,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 3, "trade_count": 4, "total_pnl_usd": 2.0, "win_count": 2},
                        "xgboost": {"accepted_count": 0, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 2.0, "win_count": 1},
                    },
                }
            return {
                "run_id": 1 if start_ts == 1 else 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 5.0 if start_ts == 1 else 4.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 6,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 0.5,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 2.0 if start_ts == 1 else 1.0, "win_count": 2},
                    "xgboost": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 3.0, "win_count": 2},
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
            "--max-xgboost-non-accepting-active-window-streak",
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
        self.assertEqual(rejected["constraint_failures"], ["xgboost_max_non_accepting_active_window_streak"])
        self.assertEqual(payload["constraints"]["max_xgboost_non_accepting_active_window_streak"], 0)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["max_non_accepting_active_window_streak"], 1)
        self.assertIn("reject xgboost_max_non_accepting_active_window_streak", stderr.getvalue())

    def test_main_rejects_high_xgboost_non_accepting_active_window_episode_count(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts in (1, 5_184_001):
                    return {
                        "run_id": 1 if start_ts == 1 else 3,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 7.0,
                        "max_drawdown_pct": 0.03,
                        "accepted_count": 6,
                        "resolved_count": 6,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 6,
                        "win_rate": 4 / 6,
                        "signal_mode_summary": {
                            "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 3.0, "win_count": 2},
                            "xgboost": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 4.0, "win_count": 2},
                        },
                    }
                return {
                    "run_id": 2 if start_ts == 2_592_001 else 4,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 4.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 4,
                    "resolved_count": 4,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 4,
                    "win_rate": 0.5,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 4, "trade_count": 4, "total_pnl_usd": 2.0, "win_count": 2},
                        "xgboost": {"accepted_count": 0, "resolved_count": 1, "trade_count": 1, "total_pnl_usd": 2.0, "win_count": 1},
                    },
                }
            return {
                "run_id": 10 if start_ts == 1 else 11 if start_ts == 2_592_001 else 12 if start_ts == 5_184_001 else 13,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 5.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 6,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 0.5,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 2.0, "win_count": 2},
                    "xgboost": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 3.0, "win_count": 2},
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
            "4",
            "--max-xgboost-non-accepting-active-window-episodes",
            "1",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=10_368_000),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        self.assertEqual(rejected["constraint_failures"], ["xgboost_non_accepting_active_window_episode_count"])
        self.assertEqual(payload["constraints"]["max_xgboost_non_accepting_active_window_episodes"], 1)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["non_accepting_active_window_episode_count"], 2)
        self.assertIn("reject xgboost_non_accepting_active_window_episode_count", stderr.getvalue())

    def test_main_treats_zero_accepted_mode_windows_as_inactive_not_shallow(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                if start_ts == 1:
                    xgboost = {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 8.0, "win_count": 2}
                    total_pnl = 12.0
                else:
                    xgboost = {"accepted_count": 0, "resolved_count": 0, "trade_count": 0, "total_pnl_usd": 0.0, "win_count": 0}
                    total_pnl = 5.0
                return {
                    "run_id": 1 if start_ts == 1 else 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": total_pnl,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 5 if start_ts == 1 else 2,
                    "resolved_count": 5 if start_ts == 1 else 2,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 5 if start_ts == 1 else 2,
                    "win_rate": 0.6,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 2, "resolved_count": 2, "trade_count": 2, "total_pnl_usd": total_pnl - float(xgboost["total_pnl_usd"]), "win_count": 1},
                        "xgboost": xgboost,
                    },
                }
            return {
                "run_id": 3 if start_ts == 1 else 4,
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
                    "heuristic": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 4.0 if start_ts == 1 else 3.0, "win_count": 2},
                    "xgboost": {"accepted_count": 3, "resolved_count": 3, "trade_count": 3, "total_pnl_usd": 7.0, "win_count": 2},
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
            "--min-xgboost-worst-active-window-accepted-count",
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
        self.assertEqual(rejected["constraint_failures"], ["xgboost_inactive_window_count"])
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["inactive_window_count"], 1)
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["worst_active_window_accepted_count"], 3)
        self.assertIn("reject xgboost_inactive_window_count", stderr.getvalue())

    def test_main_can_penalize_mode_worst_active_window_depth_in_ranking(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.65:
                xgboost_accepted = 6 if start_ts == 1 else 2
                xgboost_pnl = 14.0 if start_ts == 1 else 10.0
                heuristic_pnl = 4.0
            else:
                xgboost_accepted = 4
                xgboost_pnl = 10.0 if start_ts == 1 else 9.0
                heuristic_pnl = 4.0
            total_pnl = heuristic_pnl + xgboost_pnl
            return {
                "run_id": 1 if start_ts == 1 else 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.03,
                "accepted_count": xgboost_accepted + 2,
                "resolved_count": xgboost_accepted + 2,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": xgboost_accepted + 2,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 2, "resolved_count": 2, "trade_count": 2, "total_pnl_usd": heuristic_pnl, "win_count": 1},
                    "xgboost": {"accepted_count": xgboost_accepted, "resolved_count": xgboost_accepted, "trade_count": xgboost_accepted, "total_pnl_usd": xgboost_pnl, "win_count": max(xgboost_accepted - 1, 1)},
                },
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
            "--base-policy-json",
            json.dumps({"allow_heuristic": False}),
            "--grid-json",
            json.dumps({"min_confidence": [0.60, 0.65]}),
            "--window-days",
            "30",
            "--window-count",
            "2",
            "--mode-worst-active-window-accepted-penalty",
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
        self.assertEqual(payload["mode_worst_active_window_accepted_penalty"], 0.02)
        self.assertEqual(payload["best_feasible"]["overrides"]["min_confidence"], 0.6)
        best_breakdown = payload["ranked"][0]["result"]["score_breakdown"]
        rejected = next(row for row in payload["ranked"] if row["overrides"]["min_confidence"] == 0.65)
        rejected_breakdown = rejected["result"]["score_breakdown"]
        self.assertGreater(rejected_breakdown["mode_worst_active_window_accepted_penalty_usd"], 0.0)
        self.assertGreater(rejected_breakdown["mode_worst_active_window_accepted_penalty_usd"], best_breakdown["mode_worst_active_window_accepted_penalty_usd"])
        self.assertGreater(best_breakdown["score_usd"], rejected_breakdown["score_usd"])
        self.assertEqual(rejected["result"]["signal_mode_summary"]["xgboost"]["worst_active_window_accepted_count"], 2)

    def test_main_counts_fully_absent_mode_windows_as_inactive(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_reports_carry_against_active_windows_in_stderr(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            if start_ts == 0:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 12.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 10,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 2,
                    "trade_count": 10,
                    "win_rate": 0.6,
                    "window_end_open_exposure_usd": 10.0,
                    "window_end_open_exposure_share": 0.10,
                }
            if start_ts == 2_592_000:
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 5.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 10,
                    "win_rate": 0.6,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                }
            return {
                "run_id": 3,
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
                "window_end_open_exposure_usd": 0.0,
                "window_end_open_exposure_share": 0.0,
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
            "3",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=7_775_999),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        rendered = stderr.getvalue()
        self.assertIn("carry 1/2", rendered)
        self.assertIn("carry-avg 5%", rendered)

    def test_main_reports_carry_restart_against_restart_opportunities_in_stderr(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            if start_ts == 0:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 12.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 10,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 2,
                    "trade_count": 10,
                    "win_rate": 0.6,
                    "window_end_open_exposure_usd": 10.0,
                    "window_end_open_exposure_share": 0.10,
                }
            if start_ts == 2_592_000:
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 5.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 10,
                    "win_rate": 0.6,
                    "window_end_open_exposure_usd": 0.0,
                    "window_end_open_exposure_share": 0.0,
                }
            return {
                "run_id": 3,
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
                "window_end_open_exposure_usd": 0.0,
                "window_end_open_exposure_share": 0.0,
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
            "3",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=7_775_999),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        rendered = stderr.getvalue()
        self.assertIn("carry-rst 1/1", rendered)

    def test_main_reports_guard_restarts_against_restart_opportunities_in_stderr(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            if start_ts == 0:
                return {
                    "run_id": 1,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 12.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 10,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 2,
                    "trade_count": 10,
                    "win_rate": 0.6,
                    "window_end_daily_guard_triggered": 1,
                    "window_end_live_guard_triggered": 1,
                }
            if start_ts == 2_592_000:
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 5.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 10,
                    "resolved_count": 10,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 10,
                    "win_rate": 0.6,
                    "window_end_daily_guard_triggered": 0,
                    "window_end_live_guard_triggered": 0,
                }
            return {
                "run_id": 3,
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
                "window_end_daily_guard_triggered": 0,
                "window_end_live_guard_triggered": 0,
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
            "3",
        ]
        with (
            patch.object(replay_search, "_latest_trade_ts", return_value=7_775_999),
            patch.object(replay_search, "run_replay", side_effect=fake_run_replay),
            patch("sys.argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            replay_search.main()

        rendered = stderr.getvalue()
        self.assertIn("d-rst 1/1", rendered)
        self.assertIn("p-rst 1/1", rendered)

    def test_main_reports_single_window_carry_in_stderr(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            return {
                "run_id": 1,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 12.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 2,
                "trade_count": 8,
                "win_rate": 0.625,
                "window_end_open_exposure_usd": 10.0,
                "window_end_open_exposure_share": 0.10,
            }

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "replay_search.py",
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

        rendered = stderr.getvalue()
        self.assertIn("carry yes", rendered)
        self.assertIn("carry-avg 10%", rendered)
        self.assertNotIn(" | windows ", rendered)

    def test_main_can_reject_bad_worst_window(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
            constraints = json.loads(run_row[18])
            expected_constraints = {
                "max_drawdown_pct": 0.1,
                "max_heuristic_accepted_share": 0.0,
                "max_heuristic_accepted_size_share": 0.0,
                "max_heuristic_active_window_accepted_share": 0.0,
                "max_heuristic_active_window_accepted_size_share": 0.0,
                "max_heuristic_inactive_windows": -1,
                "max_inactive_windows": -1,
                "max_pause_guard_reject_share": 0.0,
                "max_top_market_accepted_share": 0.0,
                "max_top_market_abs_pnl_share": 0.0,
                "max_top_market_size_share": 0.0,
                "max_top_entry_price_band_accepted_share": 0.0,
                "max_top_entry_price_band_abs_pnl_share": 0.0,
                "max_top_entry_price_band_size_share": 0.0,
                "max_top_time_to_close_band_accepted_share": 0.0,
                "max_top_time_to_close_band_abs_pnl_share": 0.0,
                "max_top_time_to_close_band_size_share": 0.0,
                "max_top_trader_accepted_share": 0.0,
                "max_top_trader_abs_pnl_share": 0.0,
                "max_top_trader_size_share": 0.0,
                "max_worst_window_drawdown_pct": 0.0,
                "max_xgboost_inactive_windows": -1,
                "min_accepted_count": 5,
                "min_active_windows": 0,
                "min_worst_active_window_accepted_count": 0,
                "min_worst_active_window_accepted_size_usd": 0.0,
                "min_entry_price_band_count": 0,
                "min_heuristic_accepted_count": 0,
                "min_heuristic_resolved_count": 0,
                "min_heuristic_resolved_share": 0.0,
                "min_heuristic_resolved_size_share": 0.0,
                "min_heuristic_win_rate": 0.0,
                "min_heuristic_pnl_usd": 0.0,
                "min_heuristic_positive_windows": 0,
                "min_heuristic_worst_active_window_accepted_count": 0,
                "min_heuristic_worst_active_window_accepted_size_usd": 0.0,
                "min_heuristic_worst_window_pnl_usd": -1000000000.0,
                "min_heuristic_worst_window_resolved_share": 0.0,
                "min_heuristic_worst_window_resolved_size_share": 0.0,
                "min_market_count": 0,
                "min_positive_windows": 0,
                "min_resolved_count": 0,
                "min_resolved_share": 0.0,
                "min_resolved_size_share": 0.0,
                "min_total_pnl_usd": -1000000000.0,
                "min_time_to_close_band_count": 0,
                "min_trader_count": 0,
                "min_win_rate": 0.0,
                "min_worst_window_pnl_usd": -1000000000.0,
                "min_worst_window_resolved_share": 0.0,
                "min_worst_window_resolved_size_share": 0.0,
                "min_xgboost_accepted_share": 0.0,
                "min_xgboost_accepted_size_share": 0.0,
                "min_xgboost_active_window_accepted_share": 0.0,
                "min_xgboost_active_window_accepted_size_share": 0.0,
                "min_xgboost_accepted_count": 0,
                "min_xgboost_resolved_count": 0,
                "min_xgboost_resolved_share": 0.0,
                "min_xgboost_resolved_size_share": 0.0,
                "min_xgboost_win_rate": 0.0,
                "min_xgboost_pnl_usd": 0.0,
                "min_xgboost_positive_windows": 0,
                "min_xgboost_worst_active_window_accepted_count": 0,
                "min_xgboost_worst_active_window_accepted_size_usd": 0.0,
                "min_xgboost_worst_window_pnl_usd": -1000000000.0,
                "min_xgboost_worst_window_resolved_share": 0.0,
                "min_xgboost_worst_window_resolved_size_share": 0.0,
            }
            self.assertEqual(
                {key: constraints[key] for key in expected_constraints},
                expected_constraints,
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

    def test_main_persists_mode_active_window_mix_penalties(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.6:
                heuristic_share = 0.9
                heuristic_size_share = 0.85
                xgboost_share = 0.1
                xgboost_size_share = 0.15
                total_pnl = 48.0
            else:
                heuristic_share = 0.6
                heuristic_size_share = 0.55
                xgboost_share = 0.4
                xgboost_size_share = 0.45
                total_pnl = 42.0
            return {
                "run_id": 1,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "accepted_size_usd": 100.0,
                "resolved_size_usd": 100.0,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_size_usd": 50.0,
                        "resolved_size_usd": 50.0,
                        "trade_count": 5,
                        "total_pnl_usd": 14.0,
                        "win_count": 3,
                        "max_active_window_accepted_share": heuristic_share,
                        "max_active_window_accepted_size_share": heuristic_size_share,
                    },
                    "xgboost": {
                        "accepted_count": 5,
                        "resolved_count": 5,
                        "accepted_size_usd": 50.0,
                        "resolved_size_usd": 50.0,
                        "trade_count": 5,
                        "total_pnl_usd": total_pnl - 14.0,
                        "win_count": 3,
                        "min_active_window_accepted_share": xgboost_share,
                        "min_active_window_accepted_size_share": xgboost_size_share,
                    },
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mix_penalties.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--label-prefix",
                "mix-persist",
                "--grid-json",
                json.dumps({"min_confidence": [0.55, 0.60]}),
                "--mode-active-window-accepted-share-penalty",
                "0.1",
                "--mode-active-window-accepted-size-share-penalty",
                "0.2",
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
                    SELECT mode_active_window_accepted_share_penalty, mode_active_window_accepted_size_share_penalty
                    FROM replay_search_runs
                    """
                ).fetchone()
                candidate_row = conn.execute(
                    """
                    SELECT result_json
                    FROM replay_search_candidates
                    WHERE json_extract(overrides_json, '$.min_confidence') = 0.6
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(payload["mode_active_window_accepted_share_penalty"], 0.1)
            self.assertEqual(payload["mode_active_window_accepted_size_share_penalty"], 0.2)
            self.assertEqual(run_row, (0.1, 0.2))
            result_json = json.loads(candidate_row[0])
            self.assertEqual(result_json["score_breakdown"]["mode_active_window_accepted_share_penalty_usd"], 270.0)
            self.assertEqual(result_json["score_breakdown"]["mode_active_window_accepted_size_share_penalty_usd"], 510.0)

    def test_main_backfills_existing_search_tables_before_insert(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
            self.assertIn("daily_guard_window_penalty", run_columns)
            self.assertIn("live_guard_window_penalty", run_columns)
            self.assertIn("daily_guard_restart_window_penalty", run_columns)
            self.assertIn("live_guard_restart_window_penalty", run_columns)
            self.assertIn("resolved_share_penalty", run_columns)
            self.assertIn("resolved_size_share_penalty", run_columns)
            self.assertIn("worst_window_resolved_share_penalty", run_columns)
            self.assertIn("worst_window_resolved_size_share_penalty", run_columns)
            self.assertIn("mode_resolved_share_penalty", run_columns)
            self.assertIn("mode_resolved_size_share_penalty", run_columns)
            self.assertIn("mode_worst_window_resolved_share_penalty", run_columns)
            self.assertIn("mode_worst_window_resolved_size_share_penalty", run_columns)
            self.assertIn("worst_active_window_accepted_penalty", run_columns)
            self.assertIn("mode_worst_active_window_accepted_penalty", run_columns)
            self.assertIn("mode_loss_penalty", run_columns)
            self.assertIn("mode_inactivity_penalty", run_columns)
            self.assertIn("mode_accepted_window_count_penalty", run_columns)
            self.assertIn("mode_accepted_window_share_penalty", run_columns)
            self.assertIn("mode_non_accepting_active_window_streak_penalty", run_columns)
            self.assertIn("mode_non_accepting_active_window_episode_penalty", run_columns)
            self.assertIn("mode_accepting_window_accepted_share_penalty", run_columns)
            self.assertIn("mode_accepting_window_accepted_size_share_penalty", run_columns)
            self.assertIn("mode_top_two_accepting_window_accepted_share_penalty", run_columns)
            self.assertIn("mode_top_two_accepting_window_accepted_size_share_penalty", run_columns)
            self.assertIn("mode_accepting_window_accepted_concentration_index_penalty", run_columns)
            self.assertIn("mode_accepting_window_accepted_size_concentration_index_penalty", run_columns)
            self.assertIn("window_inactivity_penalty", run_columns)
            self.assertIn("accepted_window_count_penalty", run_columns)
            self.assertIn("accepted_window_share_penalty", run_columns)
            self.assertIn("non_accepting_active_window_episode_penalty", run_columns)
            self.assertIn("accepting_window_accepted_share_penalty", run_columns)
            self.assertIn("accepting_window_accepted_size_share_penalty", run_columns)
            self.assertIn("top_two_accepting_window_accepted_share_penalty", run_columns)
            self.assertIn("top_two_accepting_window_accepted_size_share_penalty", run_columns)
            self.assertIn("accepting_window_accepted_concentration_index_penalty", run_columns)
            self.assertIn("accepting_window_accepted_size_concentration_index_penalty", run_columns)
            self.assertIn("wallet_count_penalty", run_columns)
            self.assertIn("market_count_penalty", run_columns)
            self.assertIn("entry_price_band_count_penalty", run_columns)
            self.assertIn("time_to_close_band_count_penalty", run_columns)
            self.assertIn("wallet_concentration_penalty", run_columns)
            self.assertIn("market_concentration_penalty", run_columns)
            self.assertIn("wallet_size_concentration_penalty", run_columns)
            self.assertIn("market_size_concentration_penalty", run_columns)
            self.assertIn("entry_price_band_size_concentration_penalty", run_columns)
            self.assertIn("time_to_close_band_size_concentration_penalty", run_columns)
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
                    "trader_concentration": {"trader_count": 4, "top_accepted_share": 0.4, "top_abs_pnl_share": 0.45, "top_size_share": 0.42},
                    "market_concentration": {"market_count": 5, "top_accepted_share": 0.35, "top_abs_pnl_share": 0.3, "top_size_share": 0.33},
                    "entry_price_band_concentration": {"entry_price_band_count": 3, "top_accepted_share": 0.25, "top_abs_pnl_share": 0.2, "top_size_share": 0.22},
                    "time_to_close_band_concentration": {"time_to_close_band_count": 3, "top_accepted_share": 0.30, "top_abs_pnl_share": 0.28, "top_size_share": 0.26},
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
                "trader_concentration": {"trader_count": 2, "top_accepted_share": 0.75, "top_abs_pnl_share": 0.7, "top_size_share": 0.78},
                "market_concentration": {"market_count": 3, "top_accepted_share": 0.5, "top_abs_pnl_share": 0.55, "top_size_share": 0.58},
                "entry_price_band_concentration": {"entry_price_band_count": 2, "top_accepted_share": 0.7, "top_abs_pnl_share": 0.68, "top_size_share": 0.71},
                "time_to_close_band_concentration": {"time_to_close_band_count": 2, "top_accepted_share": 0.72, "top_abs_pnl_share": 0.74, "top_size_share": 0.76},
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
                "--wallet-size-concentration-penalty",
                "0.11",
                "--market-size-concentration-penalty",
                "0.12",
                "--entry-price-band-size-concentration-penalty",
                "0.13",
                "--time-to-close-band-size-concentration-penalty",
                "0.14",
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
                           wallet_size_concentration_penalty, market_size_concentration_penalty,
                           entry_price_band_size_concentration_penalty, time_to_close_band_size_concentration_penalty,
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
            self.assertEqual(run_row[4], 0.11)
            self.assertEqual(run_row[5], 0.12)
            self.assertEqual(run_row[6], 0.13)
            self.assertEqual(run_row[7], 0.14)
            current_result_json = json.loads(run_row[8])
            self.assertGreater(current_result_json["score_breakdown"]["wallet_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["market_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["entry_price_band_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["time_to_close_band_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["wallet_size_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["market_size_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["entry_price_band_size_concentration_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["time_to_close_band_size_concentration_penalty_usd"], 0.0)
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

    def test_main_persists_nonzero_count_penalties(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
                    "trader_concentration": {"trader_count": 4},
                    "market_concentration": {"market_count": 5},
                    "entry_price_band_concentration": {"entry_price_band_count": 4},
                    "time_to_close_band_concentration": {"time_to_close_band_count": 5},
                }
            return {
                "run_id": 0,
                "total_pnl_usd": 40.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 10,
                "resolved_count": 10,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 10,
                "win_rate": 0.6,
                "trader_concentration": {"trader_count": 2},
                "market_concentration": {"market_count": 2},
                "entry_price_band_concentration": {"entry_price_band_count": 2},
                "time_to_close_band_concentration": {"time_to_close_band_count": 2},
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_count_penalties.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--wallet-count-penalty",
                "0.25",
                "--market-count-penalty",
                "0.10",
                "--entry-price-band-count-penalty",
                "0.15",
                "--time-to-close-band-count-penalty",
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
                    SELECT wallet_count_penalty, market_count_penalty,
                           entry_price_band_count_penalty, time_to_close_band_count_penalty,
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
            self.assertGreater(current_result_json["score_breakdown"]["wallet_count_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["market_count_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["entry_price_band_count_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["time_to_close_band_count_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["wallet_count_penalty_usd"],
                best_candidate_json["score_breakdown"]["wallet_count_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["market_count_penalty_usd"],
                best_candidate_json["score_breakdown"]["market_count_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["entry_price_band_count_penalty_usd"],
                best_candidate_json["score_breakdown"]["entry_price_band_count_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["time_to_close_band_count_penalty_usd"],
                best_candidate_json["score_breakdown"]["time_to_close_band_count_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_loss_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_persists_nonzero_window_inactivity_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
                }
            return {
                "run_id": 3,
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

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_window_inactivity.db"
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
                "--window-inactivity-penalty",
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
                    SELECT window_inactivity_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["window_inactivity_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["window_inactivity_penalty_usd"],
                best_candidate_json["score_breakdown"]["window_inactivity_penalty_usd"],
            )

    def test_main_persists_nonzero_accepted_window_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                return {
                    "run_id": 1 if start_ts == 1 else 2,
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
                }
            if start_ts == 1:
                return {
                    "run_id": 3,
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
                }
            return {
                "run_id": 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 5.0,
                "max_drawdown_pct": 0.01,
                "accepted_count": 0,
                "resolved_count": 2,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 2,
                "win_rate": 1.0,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_accept_freq.db"
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
                "--accepted-window-share-penalty",
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
                    SELECT accepted_window_share_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["accepted_window_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["accepted_window_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["accepted_window_share_penalty_usd"],
            )

    def test_main_persists_nonzero_non_accepting_active_window_streak_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                if start_ts == 1:
                    accepted_count = 8
                    resolved_count = 8
                    trade_count = 8
                    total_pnl = 60.0
                elif start_ts == 2_592_001:
                    accepted_count = 0
                    resolved_count = 2
                    trade_count = 2
                    total_pnl = 6.0
                else:
                    accepted_count = 7
                    resolved_count = 7
                    trade_count = 7
                    total_pnl = 55.0
            else:
                if start_ts == 1:
                    accepted_count = 7
                    resolved_count = 7
                    trade_count = 7
                    total_pnl = 52.0
                elif start_ts == 2_592_001:
                    accepted_count = 0
                    resolved_count = 2
                    trade_count = 2
                    total_pnl = 6.0
                else:
                    accepted_count = 0
                    resolved_count = 2
                    trade_count = 2
                    total_pnl = 5.0
            return {
                "run_id": 1 if start_ts == 1 else 2 if start_ts == 2_592_001 else 3,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": accepted_count,
                "resolved_count": resolved_count,
                "rejected_count": 0,
                "unresolved_count": max(accepted_count - resolved_count, 0),
                "trade_count": trade_count,
                "win_rate": 0.625 if resolved_count > 0 else None,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_acc_gap_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--base-policy-json",
                json.dumps({"min_confidence": 0.55}),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--window-days",
                "30",
                "--window-count",
                "3",
                "--non-accepting-active-window-streak-penalty",
                "0.25",
            ]
            with (
                patch.object(replay_search, "_latest_trade_ts", return_value=7_776_000),
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
                    SELECT non_accepting_active_window_streak_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["non_accepting_active_window_streak_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["non_accepting_active_window_streak_penalty_usd"],
                best_candidate_json["score_breakdown"]["non_accepting_active_window_streak_penalty_usd"],
            )

    def test_main_persists_nonzero_non_accepting_active_window_episode_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                accepted_count = 7
                resolved_count = 7
                trade_count = 7
                total_pnl = 60.0 if start_ts == 1 else 58.0 if start_ts == 2_592_001 else 57.0 if start_ts == 5_184_001 else 56.0
            else:
                if start_ts == 1:
                    accepted_count = 7
                    resolved_count = 7
                    trade_count = 7
                    total_pnl = 52.0
                elif start_ts == 2_592_001:
                    accepted_count = 0
                    resolved_count = 2
                    trade_count = 2
                    total_pnl = 6.0
                elif start_ts == 5_184_001:
                    accepted_count = 6
                    resolved_count = 6
                    trade_count = 6
                    total_pnl = 50.0
                else:
                    accepted_count = 0
                    resolved_count = 2
                    trade_count = 2
                    total_pnl = 5.0
            return {
                "run_id": 1 if start_ts == 1 else 2 if start_ts == 2_592_001 else 3 if start_ts == 5_184_001 else 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": accepted_count,
                "resolved_count": resolved_count,
                "rejected_count": 0,
                "unresolved_count": max(accepted_count - resolved_count, 0),
                "trade_count": trade_count,
                "win_rate": 0.625 if resolved_count > 0 else None,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_acc_runs_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--base-policy-json",
                json.dumps({"min_confidence": 0.55}),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--window-days",
                "30",
                "--window-count",
                "4",
                "--non-accepting-active-window-episode-penalty",
                "0.25",
            ]
            with (
                patch.object(replay_search, "_latest_trade_ts", return_value=10_368_000),
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
                    SELECT non_accepting_active_window_episode_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["non_accepting_active_window_episode_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["non_accepting_active_window_episode_penalty_usd"],
                best_candidate_json["score_breakdown"]["non_accepting_active_window_episode_penalty_usd"],
            )

    def test_main_persists_nonzero_accepted_window_count_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                return {
                    "run_id": 1 if start_ts == 1 else 2,
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
                }
            if start_ts == 1:
                return {
                    "run_id": 3,
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
                }
            return {
                "run_id": 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 5.0,
                "max_drawdown_pct": 0.01,
                "accepted_count": 0,
                "resolved_count": 2,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 2,
                "win_rate": 1.0,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_accept_win.db"
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
                "--accepted-window-count-penalty",
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
                    SELECT accepted_window_count_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["accepted_window_count_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["accepted_window_count_penalty_usd"],
                best_candidate_json["score_breakdown"]["accepted_window_count_penalty_usd"],
            )

    def test_main_persists_nonzero_accepting_window_concentration_penalties(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                accepted_count = 4
                accepted_size_usd = 40.0 if start_ts == 1 else 60.0
                total_pnl_usd = 55.0 if start_ts == 1 else 53.0
            else:
                accepted_count = 7 if start_ts == 1 else 1
                accepted_size_usd = 140.0 if start_ts == 1 else 10.0
                total_pnl_usd = 52.0 if start_ts == 1 else 8.0
            return {
                "run_id": 1 if start_ts == 1 else 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl_usd,
                "max_drawdown_pct": 0.04,
                "accepted_count": accepted_count,
                "accepted_size_usd": accepted_size_usd,
                "resolved_count": accepted_count,
                "resolved_size_usd": accepted_size_usd,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": accepted_count,
                "win_rate": 0.625,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_accept_concentration.db"
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
                "--accepting-window-accepted-share-penalty",
                "0.25",
                "--accepting-window-accepted-size-share-penalty",
                "0.30",
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
                    SELECT accepting_window_accepted_share_penalty,
                           accepting_window_accepted_size_share_penalty,
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
            self.assertEqual(run_row[1], 0.30)
            current_result_json = json.loads(run_row[2])
            self.assertGreater(current_result_json["score_breakdown"]["accepting_window_accepted_share_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["accepting_window_accepted_size_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["accepting_window_accepted_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["accepting_window_accepted_share_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["accepting_window_accepted_size_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["accepting_window_accepted_size_share_penalty_usd"],
            )

    def test_main_persists_nonzero_top_two_accepting_window_concentration_penalties(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                if start_ts == 1:
                    accepted_count, accepted_size_usd, total_pnl_usd = 4, 120.0, 55.0
                elif start_ts == 2_592_001:
                    accepted_count, accepted_size_usd, total_pnl_usd = 3, 90.0, 53.0
                else:
                    accepted_count, accepted_size_usd, total_pnl_usd = 3, 90.0, 52.0
            else:
                if start_ts == 1:
                    accepted_count, accepted_size_usd, total_pnl_usd = 5, 150.0, 52.0
                elif start_ts == 2_592_001:
                    accepted_count, accepted_size_usd, total_pnl_usd = 4, 120.0, 38.0
                else:
                    accepted_count, accepted_size_usd, total_pnl_usd = 1, 30.0, 8.0
            return {
                "run_id": 1 if start_ts == 1 else 2 if start_ts == 2_592_001 else 3,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl_usd,
                "max_drawdown_pct": 0.04,
                "accepted_count": accepted_count,
                "accepted_size_usd": accepted_size_usd,
                "resolved_count": accepted_count,
                "resolved_size_usd": accepted_size_usd,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": accepted_count,
                "win_rate": 0.625,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_top_two_accept_concentration.db"
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
                "3",
                "--top-two-accepting-window-accepted-share-penalty",
                "0.25",
                "--top-two-accepting-window-accepted-size-share-penalty",
                "0.30",
            ]
            with (
                patch.object(replay_search, "_latest_trade_ts", return_value=7_776_000),
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
                    SELECT top_two_accepting_window_accepted_share_penalty,
                           top_two_accepting_window_accepted_size_share_penalty,
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
            self.assertEqual(run_row[1], 0.30)
            current_result_json = json.loads(run_row[2])
            self.assertGreater(current_result_json["score_breakdown"]["top_two_accepting_window_accepted_share_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["top_two_accepting_window_accepted_size_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["top_two_accepting_window_accepted_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["top_two_accepting_window_accepted_share_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["top_two_accepting_window_accepted_size_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["top_two_accepting_window_accepted_size_share_penalty_usd"],
            )

    def test_main_persists_nonzero_accepting_window_concentration_index_penalties(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                if start_ts == 1:
                    accepted_count, accepted_size_usd, total_pnl_usd = 4, 80.0, 55.0
                else:
                    accepted_count, accepted_size_usd, total_pnl_usd = 4, 120.0, 53.0
            else:
                if start_ts == 1:
                    accepted_count, accepted_size_usd, total_pnl_usd = 7, 140.0, 52.0
                else:
                    accepted_count, accepted_size_usd, total_pnl_usd = 1, 10.0, 8.0
            return {
                "run_id": 1 if start_ts == 1 else 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl_usd,
                "max_drawdown_pct": 0.04,
                "accepted_count": accepted_count,
                "accepted_size_usd": accepted_size_usd,
                "resolved_count": accepted_count,
                "resolved_size_usd": accepted_size_usd,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": accepted_count,
                "win_rate": 0.625,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_accept_ci_penalty.db"
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
                "--accepting-window-accepted-concentration-index-penalty",
                "0.25",
                "--accepting-window-accepted-size-concentration-index-penalty",
                "0.30",
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
                    SELECT accepting_window_accepted_concentration_index_penalty,
                           accepting_window_accepted_size_concentration_index_penalty,
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
            self.assertEqual(run_row[1], 0.30)
            current_result_json = json.loads(run_row[2])
            self.assertGreater(current_result_json["score_breakdown"]["accepting_window_accepted_concentration_index_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["accepting_window_accepted_size_concentration_index_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["accepting_window_accepted_concentration_index_penalty_usd"],
                best_candidate_json["score_breakdown"]["accepting_window_accepted_concentration_index_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["accepting_window_accepted_size_concentration_index_penalty_usd"],
                best_candidate_json["score_breakdown"]["accepting_window_accepted_size_concentration_index_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_accepted_window_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 60.0,
                        "max_drawdown_pct": 0.04,
                        "accepted_count": 8,
                        "resolved_count": 8,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 8,
                        "win_rate": 0.625,
                        "signal_mode_summary": {
                            "heuristic": {"accepted_count": 3, "resolved_count": 3, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 3, "total_pnl_usd": 18.0},
                            "xgboost": {"accepted_count": 5, "resolved_count": 5, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 5, "total_pnl_usd": 42.0},
                        },
                    }
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 55.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 2, "resolved_count": 2, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 2, "total_pnl_usd": 16.0},
                        "xgboost": {"accepted_count": 6, "resolved_count": 6, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 6, "total_pnl_usd": 39.0},
                    },
                }
            if start_ts == 1:
                return {
                    "run_id": 3,
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
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 3, "total_pnl_usd": 17.0},
                        "xgboost": {"accepted_count": 4, "resolved_count": 4, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 4, "total_pnl_usd": 35.0},
                    },
                }
            return {
                "run_id": 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 30.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 4,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 5 / 6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 0, "resolved_count": 2, "accepted_window_count": 0, "inactive_window_count": 0, "trade_count": 2, "total_pnl_usd": 6.0},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 4, "total_pnl_usd": 24.0},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_accept_freq.db"
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
                "--mode-accepted-window-share-penalty",
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
                    SELECT mode_accepted_window_share_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["mode_accepted_window_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_accepted_window_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_accepted_window_share_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_non_accepting_active_window_streak_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                total_pnl = 60.0 if start_ts == 1 else 58.0 if start_ts == 2_592_001 else 57.0
                return {
                    "run_id": 1 if start_ts == 1 else 2 if start_ts == 2_592_001 else 3,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": total_pnl,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 3,
                            "resolved_count": 3,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 0,
                            "trade_count": 3,
                            "total_pnl_usd": 18.0,
                        },
                        "xgboost": {
                            "accepted_count": 5,
                            "resolved_count": 5,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 0,
                            "trade_count": 5,
                            "total_pnl_usd": total_pnl - 18.0,
                        },
                    },
                }
            if start_ts == 1:
                return {
                    "run_id": 4,
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
                        "heuristic": {
                            "accepted_count": 3,
                            "resolved_count": 3,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 0,
                            "trade_count": 3,
                            "total_pnl_usd": 17.0,
                        },
                        "xgboost": {
                            "accepted_count": 4,
                            "resolved_count": 4,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 0,
                            "trade_count": 4,
                            "total_pnl_usd": 35.0,
                        },
                    },
                }
            if start_ts == 2_592_001:
                return {
                    "run_id": 5,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 30.0,
                    "max_drawdown_pct": 0.03,
                    "accepted_count": 4,
                    "resolved_count": 6,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 6,
                    "win_rate": 5 / 6,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 0,
                            "resolved_count": 2,
                            "accepted_window_count": 0,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 1,
                            "trade_count": 2,
                            "total_pnl_usd": 6.0,
                        },
                        "xgboost": {
                            "accepted_count": 4,
                            "resolved_count": 4,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 0,
                            "trade_count": 4,
                            "total_pnl_usd": 24.0,
                        },
                    },
                }
            return {
                "run_id": 6,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 28.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 4,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 5 / 6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 0,
                        "resolved_count": 2,
                        "accepted_window_count": 0,
                        "inactive_window_count": 0,
                        "max_non_accepting_active_window_streak": 2,
                        "trade_count": 2,
                        "total_pnl_usd": 5.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                        "max_non_accepting_active_window_streak": 0,
                        "trade_count": 4,
                        "total_pnl_usd": 23.0,
                    },
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_acc_gap_penalty.db"
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
                "3",
                "--mode-non-accepting-active-window-streak-penalty",
                "0.25",
            ]
            with (
                patch.object(replay_search, "_latest_trade_ts", return_value=7_776_000),
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
                    SELECT mode_non_accepting_active_window_streak_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["mode_non_accepting_active_window_streak_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_non_accepting_active_window_streak_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_non_accepting_active_window_streak_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_non_accepting_active_window_episode_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                total_pnl = 60.0 if start_ts == 1 else 58.0 if start_ts == 2_592_001 else 57.0 if start_ts == 5_184_001 else 56.0
                return {
                    "run_id": 1 if start_ts == 1 else 2 if start_ts == 2_592_001 else 3 if start_ts == 5_184_001 else 4,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": total_pnl,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 3,
                            "resolved_count": 3,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 0,
                            "non_accepting_active_window_episode_count": 0,
                            "trade_count": 3,
                            "total_pnl_usd": 18.0,
                        },
                        "xgboost": {
                            "accepted_count": 5,
                            "resolved_count": 5,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 0,
                            "non_accepting_active_window_episode_count": 0,
                            "trade_count": 5,
                            "total_pnl_usd": total_pnl - 18.0,
                        },
                    },
                }
            if start_ts in (1, 5_184_001):
                total_pnl = 52.0 if start_ts == 1 else 50.0
                return {
                    "run_id": 5 if start_ts == 1 else 7,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": total_pnl,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 7,
                    "resolved_count": 7,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 7,
                    "win_rate": 4 / 7,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 3,
                            "resolved_count": 3,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 0,
                            "non_accepting_active_window_episode_count": 0,
                            "trade_count": 3,
                            "total_pnl_usd": 17.0,
                        },
                        "xgboost": {
                            "accepted_count": 4,
                            "resolved_count": 4,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "max_non_accepting_active_window_streak": 0,
                            "non_accepting_active_window_episode_count": 0,
                            "trade_count": 4,
                            "total_pnl_usd": total_pnl - 17.0,
                        },
                    },
                }
            total_pnl = 30.0 if start_ts == 2_592_001 else 28.0
            return {
                "run_id": 6 if start_ts == 2_592_001 else 8,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.03,
                "accepted_count": 4,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 5 / 6,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 0,
                        "resolved_count": 2,
                        "accepted_window_count": 0,
                        "inactive_window_count": 0,
                        "max_non_accepting_active_window_streak": 1,
                        "non_accepting_active_window_episode_count": 1,
                        "trade_count": 2,
                        "total_pnl_usd": 6.0 if start_ts == 2_592_001 else 5.0,
                    },
                    "xgboost": {
                        "accepted_count": 4,
                        "resolved_count": 4,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                        "max_non_accepting_active_window_streak": 0,
                        "non_accepting_active_window_episode_count": 0,
                        "trade_count": 4,
                        "total_pnl_usd": 24.0 if start_ts == 2_592_001 else 23.0,
                    },
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_acc_runs_penalty.db"
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
                "4",
                "--mode-non-accepting-active-window-episode-penalty",
                "0.25",
            ]
            with (
                patch.object(replay_search, "_latest_trade_ts", return_value=10_368_000),
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
                    SELECT mode_non_accepting_active_window_episode_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["mode_non_accepting_active_window_episode_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_non_accepting_active_window_episode_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_non_accepting_active_window_episode_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_accepting_window_concentration_penalties(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 58.0,
                        "max_drawdown_pct": 0.04,
                        "accepted_count": 7,
                        "accepted_size_usd": 210.0,
                        "resolved_count": 7,
                        "resolved_size_usd": 210.0,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 7,
                        "win_rate": 5 / 7,
                        "signal_mode_summary": {
                            "heuristic": {
                                "accepted_count": 3,
                                "accepted_size_usd": 90.0,
                                "resolved_count": 3,
                                "resolved_size_usd": 90.0,
                                "accepted_window_count": 1,
                                "inactive_window_count": 0,
                                "trade_count": 3,
                                "total_pnl_usd": 20.0,
                            },
                            "xgboost": {
                                "accepted_count": 4,
                                "accepted_size_usd": 120.0,
                                "resolved_count": 4,
                                "resolved_size_usd": 120.0,
                                "accepted_window_count": 1,
                                "inactive_window_count": 0,
                                "trade_count": 4,
                                "total_pnl_usd": 38.0,
                            },
                        },
                    }
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 56.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 7,
                    "accepted_size_usd": 210.0,
                    "resolved_count": 7,
                    "resolved_size_usd": 210.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 7,
                    "win_rate": 5 / 7,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 3,
                            "accepted_size_usd": 90.0,
                            "resolved_count": 3,
                            "resolved_size_usd": 90.0,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "trade_count": 3,
                            "total_pnl_usd": 18.0,
                        },
                        "xgboost": {
                            "accepted_count": 4,
                            "accepted_size_usd": 120.0,
                            "resolved_count": 4,
                            "resolved_size_usd": 120.0,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "trade_count": 4,
                            "total_pnl_usd": 38.0,
                        },
                    },
                }
            if start_ts == 1:
                return {
                    "run_id": 3,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 50.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "accepted_size_usd": 240.0,
                    "resolved_count": 8,
                    "resolved_size_usd": 240.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {
                            "accepted_count": 6,
                            "accepted_size_usd": 180.0,
                            "resolved_count": 6,
                            "resolved_size_usd": 180.0,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "trade_count": 6,
                            "total_pnl_usd": 30.0,
                        },
                        "xgboost": {
                            "accepted_count": 2,
                            "accepted_size_usd": 60.0,
                            "resolved_count": 2,
                            "resolved_size_usd": 60.0,
                            "accepted_window_count": 1,
                            "inactive_window_count": 0,
                            "trade_count": 2,
                            "total_pnl_usd": 20.0,
                        },
                    },
                }
            return {
                "run_id": 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 28.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 2,
                "accepted_size_usd": 60.0,
                "resolved_count": 2,
                "resolved_size_usd": 60.0,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 2,
                "win_rate": 1.0,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": 1,
                        "accepted_size_usd": 30.0,
                        "resolved_count": 1,
                        "resolved_size_usd": 30.0,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                        "trade_count": 1,
                        "total_pnl_usd": 14.0,
                    },
                    "xgboost": {
                        "accepted_count": 1,
                        "accepted_size_usd": 30.0,
                        "resolved_count": 1,
                        "resolved_size_usd": 30.0,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                        "trade_count": 1,
                        "total_pnl_usd": 14.0,
                    },
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_accepting_window_penalty.db"
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
                "--mode-accepting-window-accepted-share-penalty",
                "0.25",
                "--mode-accepting-window-accepted-size-share-penalty",
                "0.30",
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
                    SELECT mode_accepting_window_accepted_share_penalty,
                           mode_accepting_window_accepted_size_share_penalty,
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
            self.assertEqual(run_row[1], 0.30)
            current_result_json = json.loads(run_row[2])
            self.assertGreater(current_result_json["score_breakdown"]["mode_accepting_window_accepted_share_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["mode_accepting_window_accepted_size_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_accepting_window_accepted_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_accepting_window_accepted_share_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_accepting_window_accepted_size_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_accepting_window_accepted_size_share_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_top_two_accepting_window_concentration_penalties(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                if start_ts == 1:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 2, 60.0, 4, 120.0, 58.0
                elif start_ts == 2_592_001:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 2, 60.0, 4, 120.0, 56.0
                else:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 2, 60.0, 4, 120.0, 54.0
            else:
                if start_ts == 1:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 4, 120.0, 3, 90.0, 50.0
                elif start_ts == 2_592_001:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 2, 60.0, 3, 90.0, 34.0
                else:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 0, 0.0, 3, 90.0, 18.0
            accepted_count = heuristic_count + xgboost_count
            accepted_size_usd = heuristic_size + xgboost_size
            heuristic_pnl_usd = float(heuristic_count * 6)
            return {
                "run_id": 1 if start_ts == 1 else 2 if start_ts == 2_592_001 else 3,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl_usd,
                "max_drawdown_pct": 0.04,
                "accepted_count": accepted_count,
                "accepted_size_usd": accepted_size_usd,
                "resolved_count": accepted_count,
                "resolved_size_usd": accepted_size_usd,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": accepted_count,
                "win_rate": 5 / 7,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": heuristic_count,
                        "accepted_size_usd": heuristic_size,
                        "resolved_count": heuristic_count,
                        "resolved_size_usd": heuristic_size,
                        "accepted_window_count": 1 if heuristic_count > 0 else 0,
                        "inactive_window_count": 0,
                        "trade_count": heuristic_count,
                        "total_pnl_usd": heuristic_pnl_usd,
                    },
                    "xgboost": {
                        "accepted_count": xgboost_count,
                        "accepted_size_usd": xgboost_size,
                        "resolved_count": xgboost_count,
                        "resolved_size_usd": xgboost_size,
                        "accepted_window_count": 1 if xgboost_count > 0 else 0,
                        "inactive_window_count": 0,
                        "trade_count": xgboost_count,
                        "total_pnl_usd": total_pnl_usd - heuristic_pnl_usd,
                    },
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_top_two_accept_penalty.db"
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
                "3",
                "--mode-top-two-accepting-window-accepted-share-penalty",
                "0.25",
                "--mode-top-two-accepting-window-accepted-size-share-penalty",
                "0.30",
            ]
            with (
                patch.object(replay_search, "_latest_trade_ts", return_value=7_776_000),
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
                    SELECT mode_top_two_accepting_window_accepted_share_penalty,
                           mode_top_two_accepting_window_accepted_size_share_penalty,
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
            self.assertEqual(run_row[1], 0.30)
            current_result_json = json.loads(run_row[2])
            self.assertGreater(current_result_json["score_breakdown"]["mode_top_two_accepting_window_accepted_share_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["mode_top_two_accepting_window_accepted_size_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_top_two_accepting_window_accepted_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_top_two_accepting_window_accepted_share_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_top_two_accepting_window_accepted_size_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_top_two_accepting_window_accepted_size_share_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_accepting_window_concentration_index_penalties(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                if start_ts == 1:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 3, 90.0, 4, 120.0, 58.0
                else:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 3, 90.0, 4, 120.0, 56.0
            else:
                if start_ts == 1:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 6, 180.0, 2, 60.0, 50.0
                else:
                    heuristic_count, heuristic_size, xgboost_count, xgboost_size, total_pnl_usd = 1, 30.0, 1, 30.0, 28.0
            accepted_count = heuristic_count + xgboost_count
            accepted_size_usd = heuristic_size + xgboost_size
            heuristic_pnl_usd = float(heuristic_count * 5)
            return {
                "run_id": 1 if start_ts == 1 else 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl_usd,
                "max_drawdown_pct": 0.04,
                "accepted_count": accepted_count,
                "accepted_size_usd": accepted_size_usd,
                "resolved_count": accepted_count,
                "resolved_size_usd": accepted_size_usd,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": accepted_count,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {
                        "accepted_count": heuristic_count,
                        "accepted_size_usd": heuristic_size,
                        "resolved_count": heuristic_count,
                        "resolved_size_usd": heuristic_size,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                        "trade_count": heuristic_count,
                        "total_pnl_usd": heuristic_pnl_usd,
                    },
                    "xgboost": {
                        "accepted_count": xgboost_count,
                        "accepted_size_usd": xgboost_size,
                        "resolved_count": xgboost_count,
                        "resolved_size_usd": xgboost_size,
                        "accepted_window_count": 1,
                        "inactive_window_count": 0,
                        "trade_count": xgboost_count,
                        "total_pnl_usd": total_pnl_usd - heuristic_pnl_usd,
                    },
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_accept_ci_penalty.db"
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
                "--mode-accepting-window-accepted-concentration-index-penalty",
                "0.25",
                "--mode-accepting-window-accepted-size-concentration-index-penalty",
                "0.30",
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
                    SELECT mode_accepting_window_accepted_concentration_index_penalty,
                           mode_accepting_window_accepted_size_concentration_index_penalty,
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
            self.assertEqual(run_row[1], 0.30)
            current_result_json = json.loads(run_row[2])
            self.assertGreater(current_result_json["score_breakdown"]["mode_accepting_window_accepted_concentration_index_penalty_usd"], 0.0)
            self.assertGreater(current_result_json["score_breakdown"]["mode_accepting_window_accepted_size_concentration_index_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_accepting_window_accepted_concentration_index_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_accepting_window_accepted_concentration_index_penalty_usd"],
            )
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_accepting_window_accepted_size_concentration_index_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_accepting_window_accepted_size_concentration_index_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_accepted_window_count_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                if start_ts == 1:
                    return {
                        "run_id": 1,
                        "window_start_ts": start_ts,
                        "window_end_ts": end_ts,
                        "total_pnl_usd": 60.0,
                        "max_drawdown_pct": 0.04,
                        "accepted_count": 8,
                        "resolved_count": 8,
                        "rejected_count": 0,
                        "unresolved_count": 0,
                        "trade_count": 8,
                        "win_rate": 0.625,
                        "signal_mode_summary": {
                            "heuristic": {"accepted_count": 3, "resolved_count": 3, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 3, "total_pnl_usd": 18.0},
                            "xgboost": {"accepted_count": 5, "resolved_count": 5, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 5, "total_pnl_usd": 42.0},
                        },
                    }
                return {
                    "run_id": 2,
                    "window_start_ts": start_ts,
                    "window_end_ts": end_ts,
                    "total_pnl_usd": 55.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 2, "resolved_count": 2, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 2, "total_pnl_usd": 16.0},
                        "xgboost": {"accepted_count": 6, "resolved_count": 6, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 6, "total_pnl_usd": 39.0},
                    },
                }
            if start_ts == 1:
                return {
                    "run_id": 3,
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
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 3, "total_pnl_usd": 17.0},
                        "xgboost": {"accepted_count": 4, "resolved_count": 4, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 4, "total_pnl_usd": 35.0},
                    },
                }
            return {
                "run_id": 4,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 30.0,
                "max_drawdown_pct": 0.03,
                "accepted_count": 4,
                "resolved_count": 6,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 6,
                "win_rate": 5 / 6,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 0, "resolved_count": 2, "accepted_window_count": 0, "inactive_window_count": 0, "trade_count": 2, "total_pnl_usd": 6.0},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "accepted_window_count": 1, "inactive_window_count": 0, "trade_count": 4, "total_pnl_usd": 24.0},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_accept_win.db"
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
                "--mode-accepted-window-count-penalty",
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
                    SELECT mode_accepted_window_count_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["mode_accepted_window_count_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_accepted_window_count_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_accepted_window_count_penalty_usd"],
            )

    def test_main_persists_nonzero_worst_active_window_accepted_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                accepted_count = 8 if start_ts == 1 else 8
                total_pnl = 60.0 if start_ts == 1 else 55.0
            else:
                accepted_count = 7 if start_ts == 1 else 2
                total_pnl = 52.0 if start_ts == 1 else 48.0
            return {
                "run_id": 1 if start_ts == 1 else 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": total_pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": accepted_count,
                "resolved_count": accepted_count,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": accepted_count,
                "win_rate": 0.625,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_worst_active_window_accepted_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--base-policy-json",
                json.dumps({"min_confidence": 0.55, "allow_heuristic": False}),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--window-days",
                "30",
                "--window-count",
                "2",
                "--worst-active-window-accepted-penalty",
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
                    SELECT worst_active_window_accepted_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["worst_active_window_accepted_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["worst_active_window_accepted_penalty_usd"],
                best_candidate_json["score_breakdown"]["worst_active_window_accepted_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_worst_active_window_accepted_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                xgboost_accepted = 5
                xgboost_pnl = 18.0 if start_ts == 1 else 16.0
            else:
                xgboost_accepted = 6 if start_ts == 1 else 2
                xgboost_pnl = 20.0 if start_ts == 1 else 12.0
            return {
                "run_id": 1 if start_ts == 1 else 2,
                "window_start_ts": start_ts,
                "window_end_ts": end_ts,
                "total_pnl_usd": 4.0 + xgboost_pnl,
                "max_drawdown_pct": 0.04,
                "accepted_count": 2 + xgboost_accepted,
                "resolved_count": 2 + xgboost_accepted,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 2 + xgboost_accepted,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 2, "resolved_count": 2, "trade_count": 2, "total_pnl_usd": 4.0, "win_count": 1},
                    "xgboost": {"accepted_count": xgboost_accepted, "resolved_count": xgboost_accepted, "trade_count": xgboost_accepted, "total_pnl_usd": xgboost_pnl, "win_count": max(xgboost_accepted - 1, 1)},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_worst_active_window_accepted_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--base-policy-json",
                json.dumps({"min_confidence": 0.55, "allow_heuristic": False}),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--window-days",
                "30",
                "--window-count",
                "2",
                "--mode-worst-active-window-accepted-penalty",
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
                    SELECT mode_worst_active_window_accepted_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["mode_worst_active_window_accepted_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_worst_active_window_accepted_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_worst_active_window_accepted_penalty_usd"],
            )

    def test_main_persists_nonzero_resolved_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_persists_nonzero_resolved_size_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                return {
                    "run_id": 1,
                    "total_pnl_usd": 60.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "accepted_size_usd": 200.0,
                    "resolved_size_usd": 200.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 3, "resolved_count": 3, "accepted_size_usd": 80.0, "resolved_size_usd": 80.0, "trade_count": 3, "total_pnl_usd": 18.0, "win_count": 2},
                        "xgboost": {"accepted_count": 5, "resolved_count": 5, "accepted_size_usd": 120.0, "resolved_size_usd": 120.0, "trade_count": 5, "total_pnl_usd": 42.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 2,
                "total_pnl_usd": 62.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 120.0,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "accepted_size_usd": 80.0, "resolved_size_usd": 80.0, "trade_count": 4, "total_pnl_usd": 24.0, "win_count": 3},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "accepted_size_usd": 120.0, "resolved_size_usd": 40.0, "trade_count": 4, "total_pnl_usd": 38.0, "win_count": 2},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_resolved_size_share_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--resolved-size-share-penalty",
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
                    SELECT resolved_size_share_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["resolved_size_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["resolved_size_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["resolved_size_share_penalty_usd"],
            )

    def test_main_persists_nonzero_worst_window_resolved_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

    def test_main_persists_nonzero_mode_resolved_size_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
            min_conf = float(policy.as_dict()["min_confidence"])
            if min_conf >= 0.60:
                return {
                    "run_id": 1,
                    "total_pnl_usd": 60.0,
                    "max_drawdown_pct": 0.04,
                    "accepted_count": 8,
                    "resolved_count": 8,
                    "accepted_size_usd": 200.0,
                    "resolved_size_usd": 160.0,
                    "rejected_count": 0,
                    "unresolved_count": 0,
                    "trade_count": 8,
                    "win_rate": 0.625,
                    "signal_mode_summary": {
                        "heuristic": {"accepted_count": 4, "resolved_count": 4, "accepted_size_usd": 80.0, "resolved_size_usd": 80.0, "trade_count": 4, "total_pnl_usd": 18.0, "win_count": 2},
                        "xgboost": {"accepted_count": 4, "resolved_count": 4, "accepted_size_usd": 120.0, "resolved_size_usd": 80.0, "trade_count": 4, "total_pnl_usd": 42.0, "win_count": 3},
                    },
                }
            return {
                "run_id": 2,
                "total_pnl_usd": 62.0,
                "max_drawdown_pct": 0.04,
                "accepted_count": 8,
                "resolved_count": 8,
                "accepted_size_usd": 200.0,
                "resolved_size_usd": 160.0,
                "rejected_count": 0,
                "unresolved_count": 0,
                "trade_count": 8,
                "win_rate": 0.625,
                "signal_mode_summary": {
                    "heuristic": {"accepted_count": 4, "resolved_count": 4, "accepted_size_usd": 80.0, "resolved_size_usd": 80.0, "trade_count": 4, "total_pnl_usd": 24.0, "win_count": 3},
                    "xgboost": {"accepted_count": 4, "resolved_count": 4, "accepted_size_usd": 120.0, "resolved_size_usd": 40.0, "trade_count": 4, "total_pnl_usd": 38.0, "win_count": 2},
                },
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "replay_search_mode_resolved_size_share_penalty.db"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "replay_search.py",
                "--db",
                str(db_path),
                "--grid-json",
                json.dumps({"min_confidence": [0.60]}),
                "--mode-resolved-size-share-penalty",
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
                    SELECT mode_resolved_size_share_penalty, current_candidate_result_json
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
            self.assertGreater(current_result_json["score_breakdown"]["mode_resolved_size_share_penalty_usd"], 0.0)
            current_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 1))
            best_candidate_json = json.loads(next(row[1] for row in candidate_rows if row[0] == 0))
            self.assertGreater(
                current_candidate_json["score_breakdown"]["mode_resolved_size_share_penalty_usd"],
                best_candidate_json["score_breakdown"]["mode_resolved_size_share_penalty_usd"],
            )

    def test_main_persists_nonzero_mode_worst_window_resolved_share_penalty(self) -> None:
        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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

        def fake_run_replay(*, policy, db_path=None, label="", notes="", start_ts=None, end_ts=None, initial_state=None):
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
            self.assertEqual(candidate_rows, [(0, 1, 1), (1, 1, 0)])


if __name__ == "__main__":
    unittest.main()
