from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import kelly_watcher.env_profile as env_profile
import kelly_watcher.runtime_paths as runtime_paths


class EnvProfileTests(unittest.TestCase):
    def test_env_path_points_to_repo_config_env(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            self.assertEqual(
                env_profile.env_path_for_profile("prod", repo_root=repo_root),
                repo_root / "kelly-config.env",
            )
            self.assertEqual(
                env_profile.secrets_env_path_for_profile("prod", repo_root=repo_root),
                repo_root / "kelly-secrets.env",
            )

    def test_flags_and_env_do_not_select_profile_specific_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)

            self.assertEqual(
                env_profile.active_env_profile(argv=["--prod"], environ={env_profile.ENV_PROFILE_ENV_VAR: "prod"}),
                "default",
            )
            self.assertEqual(
                env_profile.active_env_path(argv=["--prod"], environ={}, repo_root=repo_root),
                repo_root / "kelly-config.env",
            )
            self.assertEqual(
                env_profile.active_env_path(argv=["--dev"], environ={}, repo_root=repo_root),
                repo_root / "kelly-config.env",
            )

    def test_ensure_persistent_env_path_does_not_copy_to_save_folder(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            repo_env = repo_root / "kelly-config.env"
            repo_env.write_text("USE_REAL_MONEY=false\n", encoding="utf-8")

            env_path = env_profile.ensure_persistent_env_path("dev", repo_root=repo_root)

            self.assertEqual(env_path, repo_env)
            self.assertEqual(repo_env.read_text(encoding="utf-8"), "USE_REAL_MONEY=false\n")
            self.assertFalse((repo_root / "save" / ".env.dev").exists())

    def test_init_env_profile_loads_config_and_secrets_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_env = repo_root / "kelly-config.env"
            secrets_env = repo_root / "kelly-secrets.env"
            config_env.write_text("USE_REAL_MONEY=false\n", encoding="utf-8")
            secrets_env.write_text("TELEGRAM_CHAT_ID=123\n", encoding="utf-8")

            with mock.patch.object(env_profile, "REPO_ROOT", repo_root), mock.patch.dict(
                "os.environ", {}, clear=True
            ):
                _profile, env_path = env_profile.init_env_profile()

                self.assertEqual(env_path, config_env)
                self.assertEqual(env_profile.os.environ.get("USE_REAL_MONEY"), "false")
                self.assertEqual(env_profile.os.environ.get("TELEGRAM_CHAT_ID"), "123")

    def test_init_env_profile_ignores_save_env_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            save_env = repo_root / "save" / ".env"
            save_env.parent.mkdir()
            save_env.write_text("USE_REAL_MONEY=true\n", encoding="utf-8")

            with mock.patch.object(env_profile, "REPO_ROOT", repo_root), mock.patch.dict(
                "os.environ", {}, clear=True
            ):
                _profile, env_path = env_profile.init_env_profile()

                self.assertEqual(env_path, repo_root / "kelly-config.env")
                self.assertNotIn("USE_REAL_MONEY", env_profile.os.environ)

    def test_local_mode_overrides_network_endpoints_without_editing_env_files(self) -> None:
        env = {
            "DASHBOARD_API_HOST": "0.0.0.0",
            "DASHBOARD_API_PORT": "9999",
            "KELLY_API_BASE_URL": "http://100.91.53.63:9999",
            "DASHBOARD_WEB_URL": "http://100.91.53.63:9999",
            "DASHBOARD_API_TOKEN": "token",
        }

        env_profile.apply_local_runtime_overrides(env)

        self.assertEqual(env["DASHBOARD_API_HOST"], "127.0.0.1")
        self.assertEqual(env["KELLY_API_BASE_URL"], "http://127.0.0.1:9999")
        self.assertEqual(env["DASHBOARD_WEB_URL"], "http://127.0.0.1:9999")
        self.assertEqual(env["KELLY_API_TOKEN"], "token")


class RuntimeSaveLayoutTests(unittest.TestCase):
    def test_migrate_runtime_state_moves_legacy_files_into_save_folder(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            legacy_data = repo_root / "data"
            legacy_logs = repo_root / "logs"
            legacy_model = repo_root / "model.joblib"

            legacy_data.mkdir()
            legacy_logs.mkdir()
            (legacy_data / "trading.db").write_text("db", encoding="utf-8")
            (legacy_data / "bot_state.json").write_text("{}", encoding="utf-8")
            (legacy_logs / "bot.log").write_text("log", encoding="utf-8")
            legacy_model.write_text("model", encoding="utf-8")

            layout = runtime_paths.migrate_runtime_state(repo_root)

            self.assertEqual((layout.data_dir / "trading.db").read_text(encoding="utf-8"), "db")
            self.assertEqual((layout.data_dir / "bot_state.json").read_text(encoding="utf-8"), "{}")
            self.assertEqual((layout.log_dir / "bot.log").read_text(encoding="utf-8"), "log")
            self.assertEqual(layout.model_artifact_path.read_text(encoding="utf-8"), "model")
            self.assertFalse(legacy_data.exists())
            self.assertFalse(legacy_logs.exists())
            self.assertFalse(legacy_model.exists())


if __name__ == "__main__":
    unittest.main()
