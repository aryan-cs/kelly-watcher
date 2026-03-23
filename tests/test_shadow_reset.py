from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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
            exit_code = shadow_reset.run(foreground=False, start_bot=True, clear_wallets=False)

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
            exit_code = shadow_reset.run(foreground=False, start_bot=False, clear_wallets=False)

        self.assertEqual(exit_code, 0)
        stop_bot.assert_called_once_with()
        reset_runtime.assert_called_once_with()
        launch_background_bot.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("Shadow runtime reset.", output)
        self.assertIn("Initial bankroll: $3000.00", output)
        self.assertIn("WATCHED_WALLETS preserved.", output)


if __name__ == "__main__":
    unittest.main()
