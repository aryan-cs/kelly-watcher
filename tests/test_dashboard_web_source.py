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
        self.assertIn("db_recovery_candidate_path?: string", source)
        self.assertIn("db_recovery_candidate_source_path?: string", source)
        self.assertIn("db_recovery_candidate_class_reason?: string", source)
        self.assertIn("db_recovery_latest_verified_backup_path?: string", source)
        self.assertIn("db_recovery_latest_verified_backup_at?: number", source)
        self.assertIn("startup_recovery_only?: boolean", source)
        self.assertIn("startup_block_reason?: string", source)
        self.assertIn("db_integrity_message?: string", source)
        self.assertIn("trade_log_archive_cutoff_ts?: number", source)
        self.assertIn("trade_log_archive_preserve_since_ts?: number", source)
        self.assertIn("trade_log_archive_last_candidate_count?: number", source)
        self.assertIn("trade_log_archive_last_vacuumed?: boolean", source)
        self.assertIn("shadow_snapshot_block_reason?: string", source)
        self.assertIn("trade_log_archive_status?: string", source)
        self.assertIn("trade_log_archive_active_db_allocated_bytes?: number", source)
        self.assertIn("storage_trading_db_allocated_bytes?: number", source)
        self.assertIn("db_recovery_candidate_mode?: string", source)

    def test_app_surfaces_fail_closed_operational_panel_and_no_cli_copy(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertIn("Recovery, integrity, and storage truth", source)
        self.assertIn("configured LIVE", source)
        self.assertIn("/api/live-mode", source)
        self.assertIn("/api/shadow/restart", source)
        self.assertIn("/api/shadow/recover-db", source)
        self.assertIn("/api/shadow/archive-trade-log", source)
        self.assertIn("Restart Shadow", source)
        self.assertIn("Restart Shadow ({humanizeStatus(shadowRestartWalletMode)}):", source)
        self.assertIn("Reset wallets", source)
        self.assertIn("Keep active", source)
        self.assertIn("Keep all", source)
        self.assertIn("Clear all", source)
        self.assertIn("Recover DB", source)
        self.assertIn("Recover DB:", source)
        self.assertIn("Archive Trade Log", source)
        self.assertIn("Archive Trade Log:", source)
        self.assertIn("Set Shadow-Only", source)
        self.assertIn("Set Shadow-Only:", source)
        self.assertIn("Mode policy:", source)
        self.assertIn("Mode override:", source)
        self.assertIn("Latest Backup", source)
        self.assertIn("Archive Window", source)
        self.assertIn("Archive Result", source)
        self.assertIn("Another dashboard action is already in progress.", source)
        self.assertIn("Event feed is paused:", source)
        self.assertIn("Trade log DB:", source)
        self.assertIn("Archive rows: active", source)
        self.assertNotIn("One backend, two frontends", source)


if __name__ == "__main__":
    unittest.main()
