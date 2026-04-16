from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.dashboard_api as dashboard_api
import kelly_watcher.data.db as db


class WalletBackendApiTest(unittest.TestCase):
    def test_discovery_candidates_response_blocks_when_registry_is_unavailable(self) -> None:
        with patch(
            "kelly_watcher.dashboard_api.database_integrity_state",
            return_value={"db_integrity_known": True, "db_integrity_ok": True, "db_integrity_message": ""},
        ), patch(
            "kelly_watcher.dashboard_api.managed_wallet_registry_state",
            return_value={
                "managed_wallet_registry_available": False,
                "managed_wallet_registry_status": "missing",
                "managed_wallet_registry_error": "",
                "managed_wallets": [],
                "managed_wallet_count": 0,
                "managed_wallet_total_count": 0,
                "managed_wallet_registry_updated_at": 0,
            },
        ):
            payload = dashboard_api._discovery_candidates_response()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["managed_wallet_registry_status"], "missing")
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["candidates"], [])
        self.assertIn("managed wallet registry is missing", str(payload["message"]).lower())

    def test_discovery_candidates_response_enriches_gate_and_promotion_fields(self) -> None:
        with patch(
            "kelly_watcher.dashboard_api.database_integrity_state",
            return_value={"db_integrity_known": True, "db_integrity_ok": True, "db_integrity_message": ""},
        ), patch(
            "kelly_watcher.dashboard_api.managed_wallet_registry_state",
            return_value={
                "managed_wallet_registry_available": True,
                "managed_wallet_registry_status": "ready",
                "managed_wallet_registry_error": "",
                "managed_wallets": ["0xabc"],
                "managed_wallet_count": 1,
                "managed_wallet_total_count": 1,
                "managed_wallet_registry_updated_at": 1_700_000_000,
            },
        ), patch(
            "kelly_watcher.dashboard_api.load_wallet_discovery_candidates",
            return_value=[
                {
                    "wallet_address": "0xabc",
                    "username": "alpha",
                    "source_labels": ["leaderboard:week-pnl", "adjacent:managed-wallet"],
                    "follow_score": 0.81,
                    "accepted": False,
                    "reject_reason": "conviction_ratio<30%",
                }
            ],
        ), patch(
            "kelly_watcher.dashboard_api._wallet_policy_metrics_rows",
            return_value={
                "0xabc": {
                    "post_promotion_baseline_at": 1_700_000_100,
                    "post_promotion_evidence_ready": False,
                    "post_promotion_evidence_note": "1/6 buy signals",
                    "post_promotion_total_buy_signals": 1,
                    "post_promotion_uncopyable_skip_rate": 0.25,
                    "post_promotion_resolved_copied_count": 0,
                    "post_promotion_resolved_copied_avg_return": None,
                    "post_promotion_resolved_copied_total_pnl_usd": -5.0,
                }
            },
        ), patch(
            "kelly_watcher.dashboard_api.load_wallet_promotion_state",
            return_value={
                "0xabc": {
                    "is_auto_promoted": True,
                    "promoted_at": 1_700_000_050,
                    "baseline_at": 1_700_000_100,
                }
            },
        ):
            payload = dashboard_api._discovery_candidates_response()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["ready_count"], 0)
        self.assertEqual(payload["review_count"], 1)
        row = payload["candidates"][0]
        self.assertEqual(row["copyability_gate_status"], "review_conviction")
        self.assertTrue(row["promoted"])
        self.assertEqual(row["promoted_at"], 1_700_000_050)
        self.assertEqual(row["post_promotion_baseline_at"], 1_700_000_100)
        self.assertFalse(row["post_promotion_evidence_ready"])
        self.assertEqual(row["post_promotion_total_buy_signals"], 1)
        self.assertAlmostEqual(row["post_promotion_uncopyable_skip_rate"], 0.25)
        self.assertEqual(row["post_promotion_resolved_copied_total_pnl_usd"], -5.0)

    def test_wallet_registry_summary_surfaces_explicit_registry_health(self) -> None:
        with patch(
            "kelly_watcher.dashboard_api.managed_wallet_registry_state",
            return_value={
                "managed_wallet_registry_available": False,
                "managed_wallet_registry_status": "unreadable",
                "managed_wallet_registry_error": "database disk image is malformed",
                "managed_wallets": [],
                "managed_wallet_count": 0,
                "managed_wallet_total_count": 0,
                "managed_wallet_registry_updated_at": 0,
            },
        ), patch(
            "kelly_watcher.dashboard_api._managed_wallet_rows",
            return_value=[],
        ), patch(
            "kelly_watcher.dashboard_api._wallet_membership_events",
            return_value=([], "wallet_membership_events"),
        ):
            payload = dashboard_api._wallet_registry_summary()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["managed_wallet_registry_status"], "unreadable")
        self.assertIn("database disk image is malformed", payload["managed_wallet_registry_error"])
        self.assertEqual(payload["wallets"], [])

    def test_discovery_scan_response_blocks_when_registry_is_unreadable(self) -> None:
        with patch(
            "kelly_watcher.dashboard_api._blocked_shadow_mutation_response",
            return_value=None,
        ), patch(
            "kelly_watcher.dashboard_api.database_integrity_state",
            return_value={"db_integrity_known": True, "db_integrity_ok": True, "db_integrity_message": ""},
        ), patch(
            "kelly_watcher.dashboard_api.managed_wallet_registry_state",
            return_value={
                "managed_wallet_registry_available": False,
                "managed_wallet_registry_status": "unreadable",
                "managed_wallet_registry_error": "database disk image is malformed",
                "managed_wallets": [],
                "managed_wallet_count": 0,
                "managed_wallet_total_count": 0,
                "managed_wallet_registry_updated_at": 0,
            },
        ):
            payload = dashboard_api._discovery_scan_response()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["managed_wallet_registry_status"], "unreadable")
        self.assertIn("managed wallet registry is unreadable", str(payload["message"]).lower())

    def test_enable_disable_wallet_responses_mutate_managed_registry(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_dashboard_db_path = dashboard_api.DB_PATH
            temp_db_path = Path(tmpdir) / "data" / "trading.db"
            try:
                db.DB_PATH = temp_db_path
                dashboard_api.DB_PATH = temp_db_path
                db.init_db()
                db.import_managed_wallets_from_env(["0xabc"])

                with patch(
                    "kelly_watcher.dashboard_api._blocked_shadow_mutation_response",
                    return_value=None,
                ):
                    disabled = dashboard_api._disable_wallet_response("0xabc")
                    disabled_rows = db.load_managed_wallet_registry_rows(include_disabled=True)
                    enabled = dashboard_api._enable_wallet_response("0xabc")
                    enabled_rows = db.load_managed_wallet_registry_rows(include_disabled=True)

                self.assertTrue(disabled["ok"])
                self.assertEqual(disabled["message"], "Wallet disabled.")
                self.assertFalse(disabled_rows[0]["tracking_enabled"])
                self.assertEqual(disabled_rows[0]["disabled_reason"], "wallet disabled from web dashboard")
                self.assertTrue(enabled["ok"])
                self.assertEqual(enabled["message"], "Wallet enabled.")
                self.assertTrue(enabled_rows[0]["tracking_enabled"])
                self.assertIsNone(enabled_rows[0]["disabled_at"])
                self.assertEqual(enabled_rows[0]["disabled_reason"], "")
            finally:
                dashboard_api.DB_PATH = original_dashboard_db_path
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
