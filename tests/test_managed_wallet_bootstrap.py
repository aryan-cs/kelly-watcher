from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import kelly_watcher.data.db as db


class ManagedWalletBootstrapTest(unittest.TestCase):
    def test_import_managed_wallets_from_env_populates_watch_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                imported = db.import_managed_wallets_from_env(
                    [
                        "0x1111111111111111111111111111111111111111",
                        "0x2222222222222222222222222222222222222222",
                    ]
                )

                self.assertEqual(imported, 2)
                conn = db.get_conn()
                try:
                    rows = conn.execute(
                        """
                        SELECT wallet_address, status, reactivated_at, tracking_started_at,
                               last_source_ts_at_status, updated_at
                        FROM wallet_watch_state
                        ORDER BY wallet_address ASC
                        """
                    ).fetchall()
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["wallet_address"], "0x1111111111111111111111111111111111111111")
        self.assertEqual(rows[1]["wallet_address"], "0x2222222222222222222222222222222222222222")
        for row in rows:
            self.assertEqual(row["status"], "active")
            self.assertGreater(int(row["reactivated_at"] or 0), 0)
            self.assertGreater(int(row["tracking_started_at"] or 0), 0)
            self.assertGreater(int(row["last_source_ts_at_status"] or 0), 0)
            self.assertGreater(int(row["updated_at"] or 0), 0)


if __name__ == "__main__":
    unittest.main()
