from __future__ import annotations

import gzip
import os
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
        self.assertEqual(state["db_recovery_inventory"], [])
        self.assertEqual(state["db_recovery_inventory_count"], 0)

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
        self.assertEqual(state["db_recovery_inventory_count"], 1)
        self.assertEqual(state["db_recovery_inventory"][0]["path"], str(backup_path))
        self.assertEqual(state["db_recovery_inventory"][0]["kind"], "primary_backup")
        self.assertTrue(state["db_recovery_inventory"][0]["ready"])
        self.assertTrue(state["db_recovery_inventory"][0]["selected"])
        self.assertEqual(before_stat.st_size, after_stat.st_size)
        self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    def test_create_verified_backup_compresses_rotated_backup_history(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with patch.object(db, "DB_PATH", db_path), patch.object(main, "DB_PATH", db_path):
                db.init_db()
                first = db.create_verified_backup()
                second = db.create_verified_backup()
                history_paths = db._verified_backup_history_paths(db_path)
                self.assertTrue(Path(str(second["backup_path"])).exists())

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(len(history_paths), 1)
        self.assertTrue(str(history_paths[0]).endswith(".db.gz"))

    def test_recovery_state_falls_back_to_compressed_history_when_primary_backup_is_invalid(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            primary_backup = db_path.with_suffix(".db.bak")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with patch.object(db, "DB_PATH", db_path), patch.object(main, "DB_PATH", db_path):
                db.init_db()
                db.create_verified_backup()
                db.create_verified_backup()
                history_paths = db._verified_backup_history_paths(db_path)
                primary_backup.write_text("not a sqlite database", encoding="utf-8")
                state = _recovery_state_fn()()

        self.assertTrue(state["db_recovery_state_known"])
        self.assertTrue(state["db_recovery_candidate_ready"])
        self.assertEqual(state["db_recovery_candidate_path"], str(history_paths[0]))
        self.assertTrue(str(history_paths[0]).endswith(".db.gz"))
        self.assertEqual(state["db_recovery_inventory_count"], 2)
        self.assertEqual(state["db_recovery_inventory"][0]["path"], str(primary_backup))
        self.assertFalse(state["db_recovery_inventory"][0]["ready"])
        self.assertEqual(state["db_recovery_inventory"][1]["path"], str(history_paths[0]))
        self.assertTrue(state["db_recovery_inventory"][1]["ready"])
        self.assertTrue(state["db_recovery_inventory"][1]["selected"])

    def test_verified_backup_history_paths_prefer_filename_timestamp_over_mtime(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            backup_dir = db_path.parent / "db_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            older = backup_dir / "trading.20260416_120000.db.gz"
            newer = backup_dir / "trading.20260416_120001.db.gz"
            older.write_bytes(b"older")
            newer.write_bytes(b"newer")
            newer_mtime = newer.stat().st_mtime
            os.utime(older, (newer_mtime + 60, newer_mtime + 60))

            history_paths = db._verified_backup_history_paths(db_path)

        self.assertEqual(history_paths[0], newer)
        self.assertEqual(history_paths[1], older)

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
        self.assertEqual(state["db_recovery_inventory_count"], 1)
        self.assertEqual(state["db_recovery_inventory"][0]["path"], str(backup_path))
        self.assertFalse(state["db_recovery_inventory"][0]["ready"])
        self.assertTrue(str(state["db_recovery_inventory"][0]["message"]).strip())

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

    def test_recover_db_from_compressed_verified_backup_history(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with patch.object(db, "DB_PATH", db_path), patch.object(main, "DB_PATH", db_path):
                db.init_db()
                db.create_verified_backup()
                db.create_verified_backup()
                history_paths = db._verified_backup_history_paths(db_path)
                compressed_history = history_paths[0]
                self.assertTrue(str(compressed_history).endswith(".db.gz"))
                with gzip.open(compressed_history, "rb") as handle:
                    self.assertGreater(len(handle.read()), 0)
                db_path.write_text("corrupted live image", encoding="utf-8")

                result = db.recover_db_from_verified_backup(backup_path=compressed_history)
                self.assertTrue(result["ok"])
                self.assertEqual(result["backup_path"], str(compressed_history))
                integrity = db.database_integrity_state(db_path)
                self.assertTrue(integrity["db_integrity_known"])
                self.assertTrue(integrity["db_integrity_ok"])

    def test_recover_db_from_verified_backup_compresses_older_quarantine_images(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            backup_path = db_path.with_suffix(".db.bak")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with patch.object(db, "DB_PATH", db_path), patch.object(main, "DB_PATH", db_path), patch.object(
                db.time,
                "strftime",
                side_effect=["20260416_120000", "20260416_120001"],
            ):
                db.init_db()
                shutil.copy2(db_path, backup_path)

                db_path.write_text("corrupted live image 1", encoding="utf-8")
                first = db.recover_db_from_verified_backup()
                self.assertTrue(first["ok"])
                first_quarantine = Path(str(first["quarantined_path"]))
                self.assertTrue(first_quarantine.exists())

                db_path.write_text("corrupted live image 2", encoding="utf-8")
                second = db.recover_db_from_verified_backup()
                self.assertTrue(second["ok"])
                second_quarantine = Path(str(second["quarantined_path"]))
                self.assertTrue(second_quarantine.exists())
                self.assertFalse(first_quarantine.exists())
                self.assertEqual(second_quarantine.read_text(encoding="utf-8"), "corrupted live image 2")
                compressed_first = Path(f"{first_quarantine}.gz")
                self.assertTrue(compressed_first.exists())
                with gzip.open(compressed_first, "rt", encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "corrupted live image 1")

    def test_recovery_quarantine_prunes_old_compressed_images(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            backup_path = db_path.with_suffix(".db.bak")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with patch.object(db, "DB_PATH", db_path), patch.object(main, "DB_PATH", db_path), patch.object(
                db, "RECOVERY_QUARANTINE_RETENTION", 2
            ), patch.object(
                db,
                "RECOVERY_QUARANTINE_UNCOMPRESSED_RETENTION",
                1,
            ), patch.object(
                db.time,
                "strftime",
                side_effect=["20260416_120000", "20260416_120001", "20260416_120002"],
            ):
                db.init_db()
                shutil.copy2(db_path, backup_path)
                quarantine_paths: list[Path] = []
                for index in range(3):
                    db_path.write_text(f"corrupted live image {index}", encoding="utf-8")
                    result = db.recover_db_from_verified_backup()
                    self.assertTrue(result["ok"])
                    quarantine_paths.append(Path(str(result["quarantined_path"])))
                self.assertFalse(quarantine_paths[0].exists())
                self.assertFalse(Path(f"{quarantine_paths[0]}.gz").exists())
                self.assertFalse(quarantine_paths[1].exists())
                self.assertTrue(Path(f"{quarantine_paths[1]}.gz").exists())
                self.assertTrue(quarantine_paths[2].exists())


if __name__ == "__main__":
    unittest.main()
