from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import shadow_reset


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
            shadow_reset, "_launch_background_bot_verified", return_value=4321
        ), redirect_stdout(stdout):
            exit_code = shadow_reset.run(foreground=False, start_bot=True, wallet_mode="keep_all")

        self.assertEqual(exit_code, 0)
        stop_bot.assert_called_once_with(target_pids=None)
        reset_runtime.assert_called_once_with()
        output = stdout.getvalue()
        self.assertIn("Resetting shadow account by deleting the entire save directory", output)
        self.assertIn("PID: 4321", output)
        self.assertIn("Initial bankroll: $3000.00", output)

    def test_run_honors_delay_seconds_before_stopping_existing_bot(self) -> None:
        stdout = io.StringIO()
        with patch.object(shadow_reset, "use_real_money", return_value=False), patch.object(
            shadow_reset, "shadow_bankroll_usd", return_value=3000.0
        ), patch.object(shadow_reset.time, "sleep") as sleep_mock, patch.object(
            shadow_reset, "stop_existing_bot"
        ) as stop_bot, patch.object(
            shadow_reset, "reset_shadow_runtime"
        ) as reset_runtime, patch.object(
            shadow_reset, "_launch_background_bot_verified", return_value=4321
        ), redirect_stdout(stdout):
            exit_code = shadow_reset.run(
                foreground=False,
                start_bot=True,
                wallet_mode="keep_all",
                delay_seconds=0.75,
            )

        self.assertEqual(exit_code, 0)
        sleep_mock.assert_called_once_with(0.75)
        stop_bot.assert_called_once_with(target_pids=None)
        reset_runtime.assert_called_once_with()
        self.assertIn("Waiting 0.75s before stopping the current bot...", stdout.getvalue())

    def test_run_reset_only_resets_without_starting(self) -> None:
        stdout = io.StringIO()
        with patch.object(shadow_reset, "use_real_money", return_value=False), patch.object(
            shadow_reset, "shadow_bankroll_usd", return_value=3000.0
        ), patch.object(shadow_reset, "stop_existing_bot") as stop_bot, patch.object(
            shadow_reset, "reset_shadow_runtime"
        ) as reset_runtime, patch.object(
            shadow_reset, "_launch_background_bot_verified"
        ) as launch_background_bot, redirect_stdout(stdout):
            exit_code = shadow_reset.run(foreground=False, start_bot=False, wallet_mode="keep_all")

        self.assertEqual(exit_code, 0)
        stop_bot.assert_called_once_with(target_pids=None)
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
            shadow_reset, "_launch_background_bot_verified", return_value=4321
        ), redirect_stdout(stdout):
            exit_code = shadow_reset.run(foreground=False, start_bot=True, wallet_mode="keep_active")

        self.assertEqual(exit_code, 0)
        stop_bot.assert_called_once_with(target_pids=None)
        reset_runtime.assert_called_once_with()
        active_wallets.assert_called_once_with(["0xactive", "0xdropped"])
        write_env_value.assert_called_once_with("WATCHED_WALLETS", "0xactive")
        output = stdout.getvalue()
        self.assertIn("Reducing WATCHED_WALLETS to currently active wallets", output)
        self.assertIn("WATCHED_WALLETS reduced to active wallets.", output)

    def test_run_forwards_target_pids_to_stop_existing_bot(self) -> None:
        stdout = io.StringIO()
        with patch.object(shadow_reset, "use_real_money", return_value=False), patch.object(
            shadow_reset, "shadow_bankroll_usd", return_value=3000.0
        ), patch.object(shadow_reset, "stop_existing_bot") as stop_bot, patch.object(
            shadow_reset, "reset_shadow_runtime"
        ) as reset_runtime, patch.object(
            shadow_reset, "_launch_background_bot_verified", return_value=4321
        ), redirect_stdout(stdout):
            exit_code = shadow_reset.run(
                foreground=False,
                start_bot=True,
                wallet_mode="keep_all",
                target_pids=[111, 222],
            )

        self.assertEqual(exit_code, 0)
        stop_bot.assert_called_once_with(target_pids=[111, 222])
        reset_runtime.assert_called_once_with()

    def test_run_preserve_model_forwards_reset_preserve_flags(self) -> None:
        stdout = io.StringIO()
        with patch.object(shadow_reset, "use_real_money", return_value=False), patch.object(
            shadow_reset, "shadow_bankroll_usd", return_value=3000.0
        ), patch.object(shadow_reset, "stop_existing_bot") as stop_bot, patch.object(
            shadow_reset, "reset_shadow_runtime"
        ) as reset_runtime, patch.object(
            shadow_reset, "_launch_background_bot_verified", return_value=4321
        ), redirect_stdout(stdout):
            exit_code = shadow_reset.run(
                foreground=False,
                start_bot=True,
                wallet_mode="keep_all",
                preserve_model_artifact=True,
                preserve_identity_cache=True,
                preserve_telegram_state=True,
            )

        self.assertEqual(exit_code, 0)
        stop_bot.assert_called_once_with(target_pids=None)
        reset_runtime.assert_called_once_with(
            preserve_model_artifact=True,
            preserve_identity_cache=True,
            preserve_telegram_state=True,
        )
        output = stdout.getvalue()
        self.assertIn("Resetting shadow account runtime while preserving selected local artifacts", output)
        self.assertIn("preserving model artifact, identity cache, Telegram state", output)

    def test_launch_background_bot_verified_raises_when_child_exits_immediately(self) -> None:
        with patch.object(shadow_reset, "launch_background_bot", return_value=4321), patch.object(
            shadow_reset.time, "sleep"
        ) as sleep_mock, patch.object(
            shadow_reset, "_process_exists", return_value=False
        ), patch.object(
            shadow_reset, "PID_FILE", Path("/tmp/test-shadow.pid")
        ):
            with self.assertRaisesRegex(RuntimeError, "exited immediately"):
                shadow_reset._launch_background_bot_verified()

        sleep_mock.assert_called_once_with(1.5)

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

    def test_reset_shadow_runtime_rebuilds_fresh_runtime_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            save_dir = Path(tmpdir) / "save"
            data_dir = save_dir / "data"
            log_dir = save_dir / "logs"
            backups_dir = data_dir / "backups"
            data_dir.mkdir(parents=True, exist_ok=True)
            log_dir.mkdir(parents=True, exist_ok=True)
            backups_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "trading.db"
            extra_db_path = data_dir / "polymarket.db"
            event_file = data_dir / "events.jsonl"
            bot_state_file = data_dir / "bot_state.json"
            pid_file = data_dir / "shadow_bot.pid"
            identity_file = data_dir / "identity_cache.json"
            manual_retrain_file = data_dir / "manual_retrain_request.json"
            manual_trade_file = data_dir / "manual_trade_request.json"
            telegram_state_file = data_dir / "telegram_state.json"
            shadow_evidence_epoch_file = data_dir / "shadow_evidence_epoch.json"
            background_log = log_dir / "shadow_runtime.out"
            backup_db = backups_dir / "trading_before_shadow_reset.db"
            model_artifact = save_dir / "model.joblib"
            event_file.write_text("event\n", encoding="utf-8")
            bot_state_file.write_text("{}", encoding="utf-8")
            pid_file.write_text("123\n", encoding="utf-8")
            identity_file.write_text("{}", encoding="utf-8")
            manual_retrain_file.write_text("{}", encoding="utf-8")
            manual_trade_file.write_text("{}", encoding="utf-8")
            telegram_state_file.write_text("{}", encoding="utf-8")
            background_log.write_text("runtime log\n", encoding="utf-8")
            model_artifact.write_text("model\n", encoding="utf-8")
            extra_db_path.write_text("extra db\n", encoding="utf-8")
            backup_db.write_text("backup db\n", encoding="utf-8")

            with patch("db.DB_PATH", db_path), patch.object(shadow_reset, "SAVE_DIR", save_dir), patch.object(
                shadow_reset, "DATA_DIR", data_dir
            ), patch.object(
                shadow_reset, "LOG_DIR", log_dir
            ), patch.object(
                shadow_reset, "BACKGROUND_LOG", background_log
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
                            price_at_signal, signal_size_usd, confidence, kelly_fraction, placed_at, real_money
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("trade-1", "market-1", "0xabc", "yes", "buy", 0.61, 10.0, 0.66, 0.05, 1774252900, 0),
                    )
                    conn.execute(
                        """
                        INSERT INTO trade_log_manual_edits (
                            trade_log_id, entry_price, shares, size_usd, status, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (1, 0.62, 16.0, 10.0, "shadow", 1774252901),
                    )
                    conn.execute(
                        """
                        INSERT INTO positions (
                            market_id, side, size_usd, avg_price, token_id, entered_at, real_money
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("market-1", "yes", 10.0, 0.61, "token-1", 1774252900, 0),
                    )
                    conn.execute(
                        """
                        INSERT INTO position_manual_edits (
                            market_id, token_id, side, real_money, entry_price, shares, size_usd, status, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("market-1", "token-1", "yes", 0, 0.61, 16.0, 10.0, "shadow", 1774252901),
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
                    conn.execute(
                        """
                        INSERT INTO belief_priors (
                            feature_name, bucket, wins, losses, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        ("days_to_res", "15m_1h", 4.0, 2.0, 1774252900),
                    )
                    conn.execute(
                        """
                        INSERT INTO belief_updates (
                            trade_log_id, applied_at
                        ) VALUES (?, ?)
                        """,
                        (1, 1774252900),
                    )
                    conn.execute(
                        """
                        INSERT INTO trader_cache (
                            trader_address, win_rate, n_trades, consistency, volume_usd, avg_size_usd,
                            diversity, account_age_d, wins, ties, realized_pnl_usd, avg_return,
                            open_positions, open_value_usd, open_pnl_usd, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "0xabc",
                            0.58,
                            12,
                            0.21,
                            420.0,
                            35.0,
                            6,
                            90,
                            7,
                            1,
                            18.5,
                            0.06,
                            2,
                            44.0,
                            3.0,
                            1774252900,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO wallet_cursors (
                            wallet_address, last_source_ts, last_trade_ids_json, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        ("0xabc", 1774252800, '["trade-1"]', 1774252900),
                    )
                    conn.execute(
                        """
                        INSERT INTO wallet_watch_state (
                            wallet_address, status, tracking_started_at, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        ("0xabc", "dropped", 1774252000, 1774252900),
                    )
                    conn.commit()
                finally:
                    conn.close()

                shadow_reset.reset_shadow_runtime()

                epoch_payload = json.loads(shadow_evidence_epoch_file.read_text(encoding="utf-8"))

                conn = db.get_conn()
                try:
                    model_history_count = conn.execute("SELECT COUNT(*) FROM model_history").fetchone()[0]
                    retrain_runs_count = conn.execute("SELECT COUNT(*) FROM retrain_runs").fetchone()[0]
                    trade_log_count = conn.execute("SELECT COUNT(*) FROM trade_log").fetchone()[0]
                    positions_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
                    perf_snapshot_count = conn.execute("SELECT COUNT(*) FROM perf_snapshots").fetchone()[0]
                    belief_prior_count = conn.execute("SELECT COUNT(*) FROM belief_priors").fetchone()[0]
                    belief_update_count = conn.execute("SELECT COUNT(*) FROM belief_updates").fetchone()[0]
                    trader_cache_count = conn.execute("SELECT COUNT(*) FROM trader_cache").fetchone()[0]
                    wallet_cursor_count = conn.execute("SELECT COUNT(*) FROM wallet_cursors").fetchone()[0]
                    wallet_watch_state_count = conn.execute("SELECT COUNT(*) FROM wallet_watch_state").fetchone()[0]
                    manual_trade_edit_count = conn.execute("SELECT COUNT(*) FROM trade_log_manual_edits").fetchone()[0]
                    manual_position_edit_count = conn.execute("SELECT COUNT(*) FROM position_manual_edits").fetchone()[0]
                finally:
                    conn.close()

            self.assertEqual(model_history_count, 0)
            self.assertEqual(retrain_runs_count, 0)
            self.assertEqual(trade_log_count, 0)
            self.assertEqual(positions_count, 0)
            self.assertEqual(perf_snapshot_count, 0)
            self.assertEqual(belief_prior_count, 0)
            self.assertEqual(belief_update_count, 0)
            self.assertEqual(trader_cache_count, 0)
            self.assertEqual(wallet_cursor_count, 0)
            self.assertEqual(wallet_watch_state_count, 0)
            self.assertEqual(manual_trade_edit_count, 0)
            self.assertEqual(manual_position_edit_count, 0)
            self.assertTrue(save_dir.exists())
            self.assertTrue(data_dir.exists())
            self.assertTrue(log_dir.exists())
            self.assertTrue(db_path.exists())
            self.assertTrue(shadow_evidence_epoch_file.exists())
            self.assertGreater(int(epoch_payload.get("started_at") or 0), 0)
            self.assertEqual(str(epoch_payload.get("source") or ""), "shadow_reset")
            self.assertFalse(event_file.exists())
            self.assertFalse(bot_state_file.exists())
            self.assertFalse(pid_file.exists())
            self.assertFalse(identity_file.exists())
            self.assertFalse(manual_retrain_file.exists())
            self.assertFalse(manual_trade_file.exists())
            self.assertFalse(telegram_state_file.exists())
            self.assertFalse(background_log.exists())
            self.assertFalse(model_artifact.exists())
            self.assertFalse(extra_db_path.exists())
            self.assertFalse(backup_db.exists())

    def test_reset_shadow_runtime_can_preserve_model_and_operator_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            save_dir = Path(tmpdir) / "save"
            data_dir = save_dir / "data"
            log_dir = save_dir / "logs"
            data_dir.mkdir(parents=True, exist_ok=True)
            log_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "trading.db"
            event_file = data_dir / "events.jsonl"
            bot_state_file = data_dir / "bot_state.json"
            model_artifact = save_dir / "model.joblib"
            identity_file = data_dir / "identity_cache.json"
            telegram_state_file = data_dir / "telegram_state.json"
            background_log = log_dir / "shadow_runtime.out"
            shadow_evidence_epoch_file = data_dir / "shadow_evidence_epoch.json"

            event_file.write_text("event\n", encoding="utf-8")
            bot_state_file.write_text('{"status": "old"}\n', encoding="utf-8")
            model_artifact.write_text("trained model\n", encoding="utf-8")
            identity_file.write_text('{"wallet": "name"}\n', encoding="utf-8")
            telegram_state_file.write_text('{"last_update_id": 123}\n', encoding="utf-8")
            background_log.write_text("runtime log\n", encoding="utf-8")

            with patch("db.DB_PATH", db_path), patch.object(shadow_reset, "SAVE_DIR", save_dir), patch.object(
                shadow_reset, "DATA_DIR", data_dir
            ), patch.object(
                shadow_reset, "LOG_DIR", log_dir
            ), patch.object(
                shadow_reset, "MODEL_ARTIFACT_PATH", model_artifact
            ), patch.object(
                shadow_reset, "IDENTITY_CACHE_PATH", identity_file
            ), patch.object(
                shadow_reset, "TELEGRAM_STATE_FILE", telegram_state_file
            ), patch.object(
                shadow_reset, "BACKGROUND_LOG", background_log
            ):
                db.init_db()
                conn = db.get_conn()
                try:
                    conn.execute(
                        """
                        INSERT INTO trade_log (
                            trade_id, market_id, trader_address, side, source_action,
                            price_at_signal, signal_size_usd, confidence, kelly_fraction, placed_at, real_money
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        ("trade-1", "market-1", "0xabc", "yes", "buy", 0.61, 10.0, 0.66, 0.05, 1774252900, 0),
                    )
                    conn.commit()
                finally:
                    conn.close()

                shadow_reset.reset_shadow_runtime(
                    preserve_model_artifact=True,
                    preserve_identity_cache=True,
                    preserve_telegram_state=True,
                )

                conn = db.get_conn()
                try:
                    trade_log_count = conn.execute("SELECT COUNT(*) FROM trade_log").fetchone()[0]
                finally:
                    conn.close()

            self.assertEqual(trade_log_count, 0)
            self.assertEqual(model_artifact.read_text(encoding="utf-8"), "trained model\n")
            self.assertEqual(identity_file.read_text(encoding="utf-8"), '{"wallet": "name"}\n')
            self.assertEqual(
                telegram_state_file.read_text(encoding="utf-8"),
                '{"last_update_id": 123}\n',
            )
            self.assertTrue(shadow_evidence_epoch_file.exists())
            self.assertFalse(event_file.exists())
            self.assertFalse(bot_state_file.exists())
            self.assertFalse(background_log.exists())

    def test_reset_shadow_runtime_restores_preserved_model_when_db_init_fails(self) -> None:
        with TemporaryDirectory() as tmpdir:
            save_dir = Path(tmpdir) / "save"
            data_dir = save_dir / "data"
            log_dir = save_dir / "logs"
            save_dir.mkdir(parents=True, exist_ok=True)
            model_artifact = save_dir / "model.joblib"
            model_artifact.write_text("trained model\n", encoding="utf-8")

            with patch.object(shadow_reset, "SAVE_DIR", save_dir), patch.object(
                shadow_reset, "DATA_DIR", data_dir
            ), patch.object(
                shadow_reset, "LOG_DIR", log_dir
            ), patch.object(
                shadow_reset, "MODEL_ARTIFACT_PATH", model_artifact
            ), patch.object(
                shadow_reset.db, "init_db", side_effect=RuntimeError("init failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "init failed"):
                    shadow_reset.reset_shadow_runtime(preserve_model_artifact=True)

            self.assertEqual(model_artifact.read_text(encoding="utf-8"), "trained model\n")


if __name__ == "__main__":
    unittest.main()
