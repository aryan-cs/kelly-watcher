from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "dashboard-web" / "src" / "App.tsx"
API_PATH = ROOT / "dashboard-web" / "src" / "api.ts"


class DashboardWebSourceTests(unittest.TestCase):
    def test_api_contract_includes_recovery_integrity_archive_and_storage_fields(self) -> None:
        source = API_PATH.read_text(encoding="utf-8")
        self.assertIn("configured_mode?: 'shadow' | 'live'", source)
        self.assertIn("mode_block_reason?: string", source)
        self.assertIn("startup_recovery_only?: boolean", source)
        self.assertIn("startup_block_reason?: string", source)
        self.assertIn("db_integrity_message?: string", source)
        self.assertIn("shadow_snapshot_block_reason?: string", source)
        self.assertIn("trade_log_archive_status?: string", source)
        self.assertIn("trade_log_archive_active_db_allocated_bytes?: number", source)
        self.assertIn("storage_trading_db_allocated_bytes?: number", source)
        self.assertIn("db_recovery_candidate_mode?: string", source)

    def test_app_surfaces_fail_closed_operational_panel_and_no_cli_copy(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn("Recovery, integrity, and storage truth", source)
        self.assertIn("configured LIVE", source)
        self.assertIn("/api/shadow/restart", source)
        self.assertIn("/api/shadow/recover-db", source)
        self.assertIn("/api/shadow/archive-trade-log", source)
        self.assertIn("Restart Shadow", source)
        self.assertIn("Recover DB", source)
        self.assertIn("Archive Trade Log", source)
        self.assertIn("Event feed is paused:", source)
        self.assertIn("Trade log DB:", source)
        self.assertIn("Archive rows: active", source)
        self.assertNotIn("One backend, two frontends", source)


if __name__ == "__main__":
    unittest.main()
