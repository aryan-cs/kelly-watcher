from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.data.db as db
import kelly_watcher.main as main


def _recovery_state_fn():
    for module in (db, main):
        for name in ("db_recovery_state", "recovery_state", "_db_recovery_state"):
            fn = getattr(module, name, None)
            if callable(fn):
                return fn
    raise AssertionError(
        "Expected a non-destructive DB recovery helper in db.py or main.py named "
        "`db_recovery_state`, `recovery_state`, or `_db_recovery_state`."
    )


class DbRecoveryToolingTest(unittest.TestCase):
    def test_recovery_state_is_empty_without_any_backup_candidate(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.touch()

            with patch.object(db, "DB_PATH", db_path), patch.object(main, "DB_PATH", db_path):
                state = _recovery_state_fn()()

        self.assertFalse(state["db_recovery_state_known"])
        self.assertFalse(state["db_recovery_candidate_ready"])
        self.assertEqual(state["db_recovery_candidate_path"], "")
        self.assertEqual(state["db_recovery_candidate_source_path"], "")
        self.assertEqual(state["db_recovery_candidate_message"], "")
        self.assertEqual(state["db_recovery_latest_verified_backup_path"], "")
        self.assertEqual(state["db_recovery_latest_verified_backup_at"], 0)

    def test_recovery_state_prefers_verified_backup_without_mutating_source(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            backup_path = db_path.with_suffix(".db.bak")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            live_source = db_path
            with patch.object(db, "DB_PATH", live_source), patch.object(main, "DB_PATH", live_source):
                db.init_db()
                shutil.copy2(live_source, backup_path)
                before_stat = live_source.stat()
                state = _recovery_state_fn()()
                after_stat = live_source.stat()

        self.assertTrue(state["db_recovery_state_known"])
        self.assertTrue(state["db_recovery_candidate_ready"])
        self.assertEqual(state["db_recovery_candidate_path"], str(backup_path))
        self.assertEqual(state["db_recovery_candidate_source_path"], str(live_source))
        self.assertEqual(state["db_recovery_latest_verified_backup_path"], str(backup_path))
        self.assertGreater(state["db_recovery_latest_verified_backup_at"], 0)
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    def test_recovery_state_rejects_corrupt_backup(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            backup_path = db_path.with_suffix(".db.bak")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            live_source = db_path
            with patch.object(db, "DB_PATH", live_source), patch.object(main, "DB_PATH", live_source):
                db.init_db()
                backup_path.write_text("not a sqlite database", encoding="utf-8")
                state = _recovery_state_fn()()

        self.assertTrue(state["db_recovery_state_known"])
        self.assertFalse(state["db_recovery_candidate_ready"])
        self.assertEqual(state["db_recovery_candidate_path"], "")
        self.assertEqual(state["db_recovery_latest_verified_backup_path"], "")
        self.assertIn("integrity", str(state["db_recovery_candidate_message"]).lower())

    def test_recover_db_from_verified_backup_restores_live_db_and_quarantines_previous_image(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            backup_path = db_path.with_suffix(".db.bak")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with patch.object(db, "DB_PATH", db_path), patch.object(main, "DB_PATH", db_path):
                db.init_db()
                shutil.copy2(db_path, backup_path)
                db_path.write_text("corrupted live image", encoding="utf-8")

                result = db.recover_db_from_verified_backup()
                self.assertTrue(result["ok"])
                self.assertEqual(result["backup_path"], str(backup_path))
                self.assertEqual(result["restored_path"], str(db_path))
                self.assertTrue(result["quarantined_path"])
                quarantine_path = Path(str(result["quarantined_path"]))
                self.assertTrue(quarantine_path.exists())
                self.assertEqual(quarantine_path.read_text(encoding="utf-8"), "corrupted live image")
                integrity = db.database_integrity_state(db_path)
                self.assertTrue(integrity["db_integrity_known"])
                self.assertTrue(integrity["db_integrity_ok"])

    def test_create_verified_backup_removes_temp_file_when_finalize_fails(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            primary_backup = Path(f"{db_path}.bak")
            legacy_tmp_backup = primary_backup.with_suffix(primary_backup.suffix + ".tmp")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db.init_db(path=db_path)
            legacy_tmp_backup.write_text("stale temp backup", encoding="utf-8")

            with patch.object(db, "_fsync_file", side_effect=OSError("fsync failed")):
                with self.assertRaisesRegex(OSError, "fsync failed"):
                    db.create_verified_backup(db_path)

            self.assertFalse(primary_backup.exists())
            self.assertFalse(legacy_tmp_backup.exists())
            self.assertEqual(list(db_path.parent.glob(f"{primary_backup.name}*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
