from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
from kelly_watcher import shadow_reset


class ShadowResetTest(unittest.TestCase):
    def test_preferred_python_executable_prefers_repo_venv(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            python_path = repo_root / ".venv" / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("", encoding="utf-8")

            with patch.object(shadow_reset, "REPO_ROOT", repo_root), patch.object(
                shadow_reset.sys, "executable", "/usr/bin/python3"
            ):
                selected = shadow_reset.preferred_python_executable()

        self.assertEqual(selected, str(python_path))

    def test_runtime_env_sets_cross_platform_temp_defaults(self) -> None:
        with patch("tempfile.gettempdir", return_value="/tmp/cross-platform"):
            env = shadow_reset.runtime_env({})

        self.assertEqual(env["UV_CACHE_DIR"], "/tmp/cross-platform/uv-cache")
        self.assertEqual(
            env["PYTHONPYCACHEPREFIX"],
            "/tmp/cross-platform/kelly-watcher-pycache",
        )

    def test_runtime_env_preserves_existing_overrides(self) -> None:
        env = shadow_reset.runtime_env(
            {
                "UV_CACHE_DIR": "/custom/cache",
                "PYTHONPYCACHEPREFIX": "/custom/pycache",
            }
        )

        self.assertEqual(env["UV_CACHE_DIR"], "/custom/cache")
        self.assertEqual(env["PYTHONPYCACHEPREFIX"], "/custom/pycache")

    def test_find_bot_pids_uses_matching_processes_and_pid_file(self) -> None:
        with patch.object(
            shadow_reset,
            "_scan_process_table",
            return_value={
                111: "python main.py",
                222: "python something_else.py",
            },
        ), patch.object(shadow_reset, "_read_pid_file", return_value=333), patch.object(
            shadow_reset, "_process_exists", side_effect=lambda pid: pid in {111, 333}
        ), patch("os.getpid", return_value=999):
            pids = shadow_reset.find_bot_pids()

        self.assertEqual(pids, [111, 333])

    def test_run_refuses_live_mode(self) -> None:
        stdout = io.StringIO()
        with patch.object(shadow_reset, "use_real_money", return_value=True), redirect_stdout(stdout):
            exit_code = shadow_reset.run(foreground=False, start_bot=True, clear_wallets=False)

        self.assertEqual(exit_code, 1)
        self.assertIn("USE_REAL_MONEY=true", stdout.getvalue())

    def test_run_background_resets_and_restarts(self) -> None:
        stdout = io.StringIO()
        with patch.object(shadow_reset, "use_real_money", return_value=False), patch.object(
            shadow_reset, "shadow_bankroll_usd", return_value=3000.0
        ), patch.object(shadow_reset, "stop_existing_bot") as stop_bot, patch.object(
            shadow_reset, "reset_shadow_runtime"
        ) as reset_runtime, patch.object(
            shadow_reset, "launch_background_bot", return_value=4321
        ), redirect_stdout(stdout):
            exit_code = shadow_reset.run(foreground=False, start_bot=True, wallet_mode="keep_all")

        self.assertEqual(exit_code, 0)
        stop_bot.assert_called_once_with()
        reset_runtime.assert_called_once_with()
        output = stdout.getvalue()
        self.assertIn("Resetting shadow runtime state back to the configured bankroll of $3000.00", output)
        self.assertIn("PID: 4321", output)
        self.assertIn("Initial bankroll: $3000.00", output)

    def test_run_reset_only_resets_without_starting(self) -> None:
        stdout = io.StringIO()
        with patch.object(shadow_reset, "use_real_money", return_value=False), patch.object(
            shadow_reset, "shadow_bankroll_usd", return_value=3000.0
        ), patch.object(shadow_reset, "stop_existing_bot") as stop_bot, patch.object(
            shadow_reset, "reset_shadow_runtime"
        ) as reset_runtime, patch.object(
            shadow_reset, "launch_background_bot"
        ) as launch_background_bot, redirect_stdout(stdout):
            exit_code = shadow_reset.run(foreground=False, start_bot=False, wallet_mode="keep_all")

        self.assertEqual(exit_code, 0)
        stop_bot.assert_called_once_with()
        reset_runtime.assert_called_once_with()
        launch_background_bot.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("Shadow runtime reset.", output)
        self.assertIn("Initial bankroll: $3000.00", output)
        self.assertIn("WATCHED_WALLETS preserved.", output)

    def test_run_keep_active_wallets_rewrites_watchlist_before_restart(self) -> None:
        stdout = io.StringIO()
        with patch.object(shadow_reset, "use_real_money", return_value=False), patch.object(
            shadow_reset, "shadow_bankroll_usd", return_value=3000.0
        ), patch.object(shadow_reset, "_read_env_value", return_value="0xactive,0xdropped"), patch.object(
            shadow_reset, "_active_watched_wallets", return_value=["0xactive"]
        ) as active_wallets, patch.object(
            shadow_reset, "_write_env_value"
        ) as write_env_value, patch.object(
            shadow_reset, "stop_existing_bot"
        ) as stop_bot, patch.object(
            shadow_reset, "reset_shadow_runtime"
        ) as reset_runtime, patch.object(
            shadow_reset, "launch_background_bot", return_value=4321
        ), redirect_stdout(stdout):
            exit_code = shadow_reset.run(foreground=False, start_bot=True, wallet_mode="keep_active")

        self.assertEqual(exit_code, 0)
        stop_bot.assert_called_once_with()
        reset_runtime.assert_called_once_with()
        active_wallets.assert_called_once_with(["0xactive", "0xdropped"])
        write_env_value.assert_called_once_with("WATCHED_WALLETS", "0xactive")
        output = stdout.getvalue()
        self.assertIn("Reducing WATCHED_WALLETS to currently active wallets", output)
        self.assertIn("WATCHED_WALLETS reduced to active wallets.", output)

    def test_active_watched_wallets_excludes_only_dropped_wallets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address, status, tracking_started_at, updated_at
                    ) VALUES (?, ?, ?, ?), (?, ?, ?, ?)
                    """,
                    (
                        "0xdropped",
                        "dropped",
                        1_700_000_000,
                        1_700_000_000,
                        "0xactive",
                        "active",
                        1_700_000_000,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                active_wallets = shadow_reset._active_watched_wallets(["0xactive", "0xdropped", "0xunknown"])
            finally:
                db.DB_PATH = original_db_path

        self.assertEqual(active_wallets, ["0xactive", "0xunknown"])

    def test_reset_shadow_runtime_clears_training_history_and_runtime_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            log_dir = Path(tmpdir) / "logs"
            data_dir.mkdir(parents=True, exist_ok=True)
            log_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "trading.db"
            db_wal_path = Path(f"{db_path}-wal")
            db_shm_path = Path(f"{db_path}-shm")
            event_file = data_dir / "events.jsonl"
            bot_state_file = data_dir / "bot_state.json"
            pid_file = data_dir / "shadow_bot.pid"
            identity_file = data_dir / "identity_cache.json"
            manual_retrain_file = data_dir / "manual_retrain_request.json"
            manual_trade_file = data_dir / "manual_trade_request.json"
            telegram_state_file = data_dir / "telegram_state.json"
            background_log = log_dir / "shadow_runtime.out"
            model_artifact = Path(tmpdir) / "save" / "model.joblib"
            model_artifact.parent.mkdir(parents=True, exist_ok=True)
            event_file.write_text("event\n", encoding="utf-8")
            bot_state_file.write_text("{}", encoding="utf-8")
            pid_file.write_text("123\n", encoding="utf-8")
            identity_file.write_text("{}", encoding="utf-8")
            manual_retrain_file.write_text("{}", encoding="utf-8")
            manual_trade_file.write_text("{}", encoding="utf-8")
            telegram_state_file.write_text("{}", encoding="utf-8")
            background_log.write_text("runtime log\n", encoding="utf-8")
            model_artifact.write_text("model\n", encoding="utf-8")

            with patch("db.DB_PATH", db_path), patch.object(shadow_reset, "DATA_DIR", data_dir), patch.object(
                shadow_reset, "LOG_DIR", log_dir
            ), patch.object(
                shadow_reset,
                "_reset_file_paths",
                return_value=(
                    db_path,
                    db_shm_path,
                    db_wal_path,
                    event_file,
                    bot_state_file,
                    pid_file,
                    identity_file,
                    manual_retrain_file,
                    manual_trade_file,
                    telegram_state_file,
                    background_log,
                    model_artifact,
                ),
            ):
                db.init_db()
                conn = db.get_conn()
                try:
                    conn.execute(
                        """
                        INSERT INTO model_history (
                            trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (1774252800, 3211, 0.1799, 0.5219, "confidence", "save/model.joblib", 1),
                    )
                    conn.execute(
                        """
                        INSERT INTO retrain_runs (
                            started_at, finished_at, trigger, status, ok, deployed, sample_count, min_samples,
                            brier_score, log_loss, message
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            1774252500,
                            1774252848,
                            "manual",
                            "completed_not_deployed",
                            1,
                            0,
                            3211,
                            100,
                            0.1799,
                            0.5219,
                            "shared holdout ll/brier: 0.5219 / 0.1799 | incumbent ll/brier: 0.5233 / 0.1785",
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO trade_log (
                            trade_id, market_id, trader_address, side, source_action,
                            price_at_signal, signal_size_usd, confidence, kelly_fraction, placed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("trade-1", "market-1", "0xabc", "yes", "buy", 0.61, 10.0, 0.66, 0.05, 1774252900),
                    )
                    conn.execute(
                        """
                        INSERT INTO perf_snapshots (
                            snapshot_at, mode, n_signals, n_acted, n_resolved, win_rate,
                            total_pnl_usd, avg_confidence, sharpe
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (1774252900, "shadow", 10, 3, 2, 0.5, 12.0, 0.67, 1.2),
                    )
                    conn.commit()
                finally:
                    conn.close()

                shadow_reset.reset_shadow_runtime()

                conn = db.get_conn()
                try:
                    model_history_count = conn.execute("SELECT COUNT(*) FROM model_history").fetchone()[0]
                    retrain_runs_count = conn.execute("SELECT COUNT(*) FROM retrain_runs").fetchone()[0]
                    trade_log_count = conn.execute("SELECT COUNT(*) FROM trade_log").fetchone()[0]
                    perf_snapshot_count = conn.execute("SELECT COUNT(*) FROM perf_snapshots").fetchone()[0]
                finally:
                    conn.close()

        self.assertEqual(model_history_count, 0)
        self.assertEqual(retrain_runs_count, 0)
        self.assertEqual(trade_log_count, 0)
        self.assertEqual(perf_snapshot_count, 0)
        self.assertFalse(event_file.exists())
        self.assertFalse(bot_state_file.exists())
        self.assertFalse(pid_file.exists())
        self.assertFalse(identity_file.exists())
        self.assertFalse(manual_retrain_file.exists())
        self.assertFalse(manual_trade_file.exists())
        self.assertFalse(telegram_state_file.exists())
        self.assertFalse(background_log.exists())
        self.assertFalse(model_artifact.exists())


if __name__ == "__main__":
    unittest.main()
