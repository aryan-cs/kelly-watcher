from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import patch

if "apscheduler.schedulers.background" not in sys.modules:
    apscheduler_module = types.ModuleType("apscheduler")
    schedulers_module = types.ModuleType("apscheduler.schedulers")
    background_module = types.ModuleType("apscheduler.schedulers.background")

    class _BackgroundScheduler:
        def add_job(self, *args, **kwargs) -> None:
            return None

        def start(self) -> None:
            return None

    background_module.BackgroundScheduler = _BackgroundScheduler
    schedulers_module.background = background_module
    apscheduler_module.schedulers = schedulers_module
    sys.modules["apscheduler"] = apscheduler_module
    sys.modules["apscheduler.schedulers"] = schedulers_module
    sys.modules["apscheduler.schedulers.background"] = background_module

if "py_clob_client.clob_types" not in sys.modules:
    py_clob_client_module = types.ModuleType("py_clob_client")
    clob_types_module = types.ModuleType("py_clob_client.clob_types")
    order_builder_module = types.ModuleType("py_clob_client.order_builder")
    constants_module = types.ModuleType("py_clob_client.order_builder.constants")
    client_module = types.ModuleType("py_clob_client.client")

    class _MarketOrderArgs:
        def __init__(self, *, token_id: str, amount: float, side: str) -> None:
            self.token_id = token_id
            self.amount = amount
            self.side = side

    class _OrderType:
        FOK = "FOK"

    class _ClobClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def set_api_creds(self, *args, **kwargs) -> None:
            return None

        def create_or_derive_api_creds(self):
            return {}

    clob_types_module.MarketOrderArgs = _MarketOrderArgs
    clob_types_module.OrderType = _OrderType
    constants_module.BUY = "BUY"
    constants_module.SELL = "SELL"
    client_module.ClobClient = _ClobClient
    order_builder_module.constants = constants_module
    py_clob_client_module.clob_types = clob_types_module
    py_clob_client_module.order_builder = order_builder_module
    py_clob_client_module.client = client_module
    sys.modules["py_clob_client"] = py_clob_client_module
    sys.modules["py_clob_client.clob_types"] = clob_types_module
    sys.modules["py_clob_client.order_builder"] = order_builder_module
    sys.modules["py_clob_client.order_builder.constants"] = constants_module
    sys.modules["py_clob_client.client"] = client_module

import kelly_watcher.data.db as db
import kelly_watcher.main as main


class ManagedWalletRegistryStateTests(unittest.TestCase):
    def test_registry_state_reports_missing_when_table_absent(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute("DROP TABLE managed_wallets")
                conn.commit()
                conn.close()

                state = db.managed_wallet_registry_state()

                self.assertEqual(state["managed_wallet_registry_status"], "missing")
                self.assertFalse(state["managed_wallet_registry_available"])
                self.assertEqual(state["managed_wallet_count"], 0)
                self.assertEqual(state["managed_wallet_total_count"], 0)
                self.assertEqual(state["managed_wallets"], [])
                self.assertEqual(state["managed_wallet_registry_error"], "")
            finally:
                db.DB_PATH = original_db_path

    def test_registry_state_reports_empty_and_ready(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                empty_state = db.managed_wallet_registry_state()
                self.assertEqual(empty_state["managed_wallet_registry_status"], "empty")
                self.assertTrue(empty_state["managed_wallet_registry_available"])
                self.assertEqual(empty_state["managed_wallet_total_count"], 0)

                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO managed_wallets (
                        wallet_address, tracking_enabled, source, added_at, updated_at, metadata_json
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    ("0xwallet", 1, "auto_promoted", 1_700_000_000, 1_700_000_000, "{}"),
                )
                conn.commit()
                conn.close()

                ready_state = db.managed_wallet_registry_state()
                self.assertEqual(ready_state["managed_wallet_registry_status"], "ready")
                self.assertTrue(ready_state["managed_wallet_registry_available"])
                self.assertEqual(ready_state["managed_wallet_count"], 1)
                self.assertEqual(ready_state["managed_wallet_total_count"], 1)
                self.assertEqual(ready_state["managed_wallets"], ["0xwallet"])
                self.assertEqual(ready_state["managed_wallet_registry_error"], "")
            finally:
                db.DB_PATH = original_db_path

    def test_registry_state_reports_unreadable_when_table_probe_raises(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                with patch("kelly_watcher.data.db._table_exists", side_effect=sqlite3.DatabaseError("database disk image is malformed")):
                    state = db.managed_wallet_registry_state()

                self.assertEqual(state["managed_wallet_registry_status"], "unreadable")
                self.assertFalse(state["managed_wallet_registry_available"])
                self.assertEqual(state["managed_wallet_count"], 0)
                self.assertEqual(state["managed_wallet_total_count"], 0)
                self.assertEqual(state["managed_wallets"], [])
                self.assertIn("database disk image is malformed", str(state["managed_wallet_registry_error"]))
            finally:
                db.DB_PATH = original_db_path

    def test_bootstrap_import_requires_explicit_empty_status(self) -> None:
        with patch.object(main, "WATCHED_WALLETS", ["0xenv"]), patch(
            "kelly_watcher.main.managed_wallet_registry_state",
            return_value={"managed_wallet_registry_status": "empty", "managed_wallet_registry_available": True},
        ):
            self.assertTrue(main._should_import_bootstrap_watched_wallets(False))

        for status in ("ready", "missing", "unreadable"):
            with self.subTest(status=status):
                with patch.object(main, "WATCHED_WALLETS", ["0xenv"]), patch(
                    "kelly_watcher.main.managed_wallet_registry_state",
                    return_value={"managed_wallet_registry_status": status, "managed_wallet_registry_available": status == "ready"},
                ):
                    self.assertFalse(main._should_import_bootstrap_watched_wallets(False))

        with patch.object(main, "WATCHED_WALLETS", ["0xenv"]), patch(
            "kelly_watcher.main.managed_wallet_registry_state",
            return_value={"managed_wallet_registry_status": "empty", "managed_wallet_registry_available": True},
        ):
            self.assertFalse(main._should_import_bootstrap_watched_wallets(False, snapshot_restore_failed=True))

    def test_registry_runtime_wallets_fail_closed_for_missing_or_unreadable(self) -> None:
        self.assertIsNone(
            main._managed_wallet_registry_runtime_wallets(
                {"managed_wallet_registry_status": "missing", "managed_wallet_registry_available": False}
            )
        )
        self.assertIsNone(
            main._managed_wallet_registry_runtime_wallets(
                {"managed_wallet_registry_status": "unreadable", "managed_wallet_registry_available": False}
            )
        )
        self.assertEqual(
            main._managed_wallet_registry_runtime_wallets(
                {
                    "managed_wallet_registry_status": "empty",
                    "managed_wallet_registry_available": True,
                    "managed_wallets": [],
                }
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
