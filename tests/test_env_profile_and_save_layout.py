from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import env_profile
import runtime_paths


class EnvProfileTests(unittest.TestCase):
    def test_prod_flag_selects_dot_env_prod(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            env_prod = repo_root / ".env.prod"
            env_prod.write_text("USE_REAL_MONEY=true\n", encoding="utf-8")

            self.assertEqual(
                env_profile.active_env_profile(argv=["--prod"], environ={}),
                "prod",
            )
            self.assertEqual(
                env_profile.active_env_path(argv=["--prod"], environ={}, repo_root=repo_root),
                env_prod,
            )

    def test_dev_profile_falls_back_to_legacy_dot_env(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            legacy_env = repo_root / ".env"
            legacy_env.write_text("USE_REAL_MONEY=false\n", encoding="utf-8")

            self.assertEqual(
                env_profile.active_env_path(argv=["--dev"], environ={}, repo_root=repo_root),
                legacy_env,
            )


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
