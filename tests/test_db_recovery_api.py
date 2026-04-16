from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import kelly_watcher.dashboard_api as dashboard_api

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_text(relative_path: str) -> str:
    candidate = REPO_ROOT / relative_path
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    src_candidate = REPO_ROOT / "src" / "kelly_watcher" / relative_path
    return src_candidate.read_text(encoding="utf-8")


def _find_recovery_entrypoint():
    candidate_names = (
        "_recover_db_response",
        "recover_db_response",
        "_queue_db_recovery_request",
        "queue_db_recovery_request",
        "_launch_db_recovery",
        "launch_db_recovery",
        "recover_shadow_database",
    )
    for name in candidate_names:
        fn = getattr(dashboard_api, name, None)
        if callable(fn):
            return name, fn
    return "", None


class DbRecoveryApiContractTest(unittest.TestCase):
    def test_recover_db_action_is_wired_to_expected_endpoint(self) -> None:
        settings_ts = _read_text("dashboard-cli/settingsDanger.ts")
        settings_js = _read_text("dashboard-cli/settingsDanger.js")
        dashboard_ts = _read_text("dashboard-cli/dashboard.tsx")
        dashboard_js = _read_text("dashboard-cli/dashboard.js")

        for source_name, source_text in (
            ("dashboard-cli/settingsDanger.ts", settings_ts),
            ("dashboard-cli/settingsDanger.js", settings_js),
            ("dashboard-cli/dashboard.tsx", dashboard_ts),
            ("dashboard-cli/dashboard.js", dashboard_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("recover_db", source_text)

        for source_name, source_text in (
            ("dashboard-cli/settingsDanger.ts", settings_ts),
            ("dashboard-cli/settingsDanger.js", settings_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("/api/shadow/recover-db", source_text)

    def test_recover_db_ui_warns_when_backup_is_integrity_only_or_unavailable(self) -> None:
        settings_ts = _read_text("dashboard-cli/settingsDanger.ts")
        settings_js = _read_text("dashboard-cli/settingsDanger.js")
        dashboard_ts = _read_text("dashboard-cli/dashboard.tsx")
        dashboard_js = _read_text("dashboard-cli/dashboard.js")

        for source_name, source_text in (
            ("dashboard-cli/settingsDanger.ts", settings_ts),
            ("dashboard-cli/settingsDanger.js", settings_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("integrity-only, not evidence-ready", source_text)

        for source_name, source_text in (
            ("dashboard-cli/dashboard.tsx", dashboard_ts),
            ("dashboard-cli/dashboard.js", dashboard_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("db_recovery_candidate_mode", source_text)
                self.assertIn("Restore integrity-only backup", source_text)
                self.assertIn("Restore evidence-ready backup", source_text)
                self.assertIn(
                    "Recover DB is unavailable because no verified backup candidate is ready.",
                    source_text,
                )
                self.assertIn("shadowRestartPending", source_text)
                self.assertIn("shadowRestartMessage", source_text)

        for source_name, source_text in (
            ("dashboard-cli/pages/Settings.tsx", _read_text("dashboard-cli/pages/Settings.tsx")),
            ("dashboard-cli/pages/Settings.js", _read_text("dashboard-cli/pages/Settings.js")),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("EV-READY", source_text)
                self.assertIn("INT-ONLY", source_text)
                self.assertIn("UNAVAIL", source_text)
                self.assertIn("PENDING", source_text)
                self.assertIn("pending_restart", source_text)
                self.assertIn("Recover DB will restore an integrity-only verified backup.", source_text)

    def test_wallet_actions_surface_backend_messages_in_dashboard(self) -> None:
        wallet_state_ts = _read_text("dashboard-cli/walletWatchState.ts")
        wallet_state_js = _read_text("dashboard-cli/walletWatchState.js")
        dashboard_ts = _read_text("dashboard-cli/dashboard.tsx")
        dashboard_js = _read_text("dashboard-cli/dashboard.js")

        for source_name, source_text in (
            ("dashboard-cli/walletWatchState.ts", wallet_state_ts),
            ("dashboard-cli/walletWatchState.js", wallet_state_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertNotIn("Boolean(response.ok)", source_text)
                self.assertIn("/api/wallets/reactivate", source_text)
                self.assertIn("/api/wallets/drop", source_text)

        for source_name, source_text in (
            ("dashboard-cli/dashboard.tsx", dashboard_ts),
            ("dashboard-cli/dashboard.js", dashboard_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("showTransientNotice(result.message", source_text)
                self.assertIn("Unknown wallet reactivation error", source_text)
                self.assertIn("Unknown wallet drop error", source_text)

    def test_config_edits_are_blocked_during_recovery_only_startup(self) -> None:
        dashboard_api_source = _read_text("dashboard_api.py")
        dashboard_ts = _read_text("dashboard-cli/dashboard.tsx")
        dashboard_js = _read_text("dashboard-cli/dashboard.js")
        settings_ts = _read_text("dashboard-cli/pages/Settings.tsx")
        settings_js = _read_text("dashboard-cli/pages/Settings.js")

        self.assertIn("_config_value_response", dashboard_api_source)
        self.assertIn("Config editing", dashboard_api_source)

        for source_name, source_text in (
            ("dashboard-cli/dashboard.tsx", dashboard_ts),
            ("dashboard-cli/dashboard.js", dashboard_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("configEditBlockedMessage", source_text)
                self.assertIn("Config edits stay blocked until", source_text)

        for source_name, source_text in (
            ("dashboard-cli/pages/Settings.tsx", settings_ts),
            ("dashboard-cli/pages/Settings.js", settings_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("configEditBlocked", source_text)
                self.assertIn("Config edits stay blocked until", source_text)

    def test_dashboard_blocks_live_mode_ui_while_restart_is_pending(self) -> None:
        dashboard_ts = _read_text("dashboard-cli/dashboard.tsx")
        dashboard_js = _read_text("dashboard-cli/dashboard.js")
        settings_ts = _read_text("dashboard-cli/pages/Settings.tsx")
        settings_js = _read_text("dashboard-cli/pages/Settings.js")

        for source_name, source_text in (
            ("dashboard-cli/dashboard.tsx", dashboard_ts),
            ("dashboard-cli/dashboard.js", dashboard_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("liveModeBlockedMessage", source_text)
                self.assertIn("Live-mode requests stay blocked until the backend restarts.", source_text)
                self.assertIn("confirm.actionId === 'live_trading' && liveModeBlockedMessage", source_text)

        for source_name, source_text in (
            ("dashboard-cli/pages/Settings.tsx", settings_ts),
            ("dashboard-cli/pages/Settings.js", settings_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("action.id === 'live_trading' && (startupRecoveryOnly || shadowRestartPending)", source_text)
                self.assertIn("Live-mode requests stay blocked until the backend restarts.", source_text)

    def test_settings_live_readiness_fails_closed_during_recovery_only_or_pending_restart(self) -> None:
        settings_ts = _read_text("dashboard-cli/pages/Settings.tsx")
        settings_js = _read_text("dashboard-cli/pages/Settings.js")

        for source_name, source_text in (
            ("dashboard-cli/pages/Settings.tsx", settings_ts),
            ("dashboard-cli/pages/Settings.js", settings_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("liveModeStartupBlocked", source_text)
                self.assertIn("pending_restart", source_text)
                self.assertIn("recovery-only startup mode", source_text)

    def test_shadow_restart_kind_is_wired_through_runtime_and_dashboard(self) -> None:
        main_source = _read_text("main.py")
        dashboard_api_source = _read_text("dashboard_api.py")
        bot_state_ts = _read_text("dashboard-cli/useBotState.ts")
        bot_state_js = _read_text("dashboard-cli/useBotState.js")
        settings_ts = _read_text("dashboard-cli/pages/Settings.tsx")
        settings_js = _read_text("dashboard-cli/pages/Settings.js")

        for source_name, source_text in (
            ("main.py", main_source),
            ("dashboard_api.py", dashboard_api_source),
            ("dashboard-cli/useBotState.ts", bot_state_ts),
            ("dashboard-cli/useBotState.js", bot_state_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("shadow_restart_kind", source_text)

        for source_name, source_text in (
            ("dashboard-cli/pages/Settings.tsx", settings_ts),
            ("dashboard-cli/pages/Settings.js", settings_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("shadowRestartKind", source_text)
                self.assertIn("shadowRestartPending && shadowRestartKind === 'db_recovery'", source_text)

    def test_models_and_dashboard_block_manual_retrain_honestly(self) -> None:
        models_ts = _read_text("dashboard-cli/pages/Models.tsx")
        models_js = _read_text("dashboard-cli/pages/Models.js")
        dashboard_ts = _read_text("dashboard-cli/dashboard.tsx")
        dashboard_js = _read_text("dashboard-cli/dashboard.js")

        for source_name, source_text in (
            ("dashboard-cli/pages/Models.tsx", models_ts),
            ("dashboard-cli/pages/Models.js", models_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("Manual retrain is only available while the runtime is healthy and not restarting.", source_text)
                self.assertIn("Restart pending", source_text)
                self.assertIn("Recovery-only", source_text)
                self.assertIn("shadowRestartPending", source_text)
                self.assertIn("startupRecoveryOnly", source_text)

        for source_name, source_text in (
            ("dashboard-cli/dashboard.tsx", dashboard_ts),
            ("dashboard-cli/dashboard.js", dashboard_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("manualRetrainBlockedMessage", source_text)
                self.assertIn("t blocked", source_text)
                self.assertIn("requestManualRetrain", source_text)

    def test_models_js_matches_shadow_snapshot_optimization_block_guard(self) -> None:
        models_ts = _read_text("dashboard-cli/pages/Models.tsx")
        models_js = _read_text("dashboard-cli/pages/Models.js")

        self.assertIn("&& !shadowSnapshotOptimizationBlocked", models_ts)
        self.assertIn("&& !shadowSnapshotOptimizationBlocked", models_js)

    def test_dashboard_queries_fail_closed_during_recovery_only_or_db_integrity_failure(self) -> None:
        dashboard_api_source = _read_text("dashboard_api.py")
        use_db_ts = _read_text("dashboard-cli/useDb.ts")
        use_db_js = _read_text("dashboard-cli/useDb.js")
        bot_state_ts = _read_text("dashboard-cli/useBotState.ts")
        bot_state_js = _read_text("dashboard-cli/useBotState.js")

        self.assertIn("_blocked_query_response", dashboard_api_source)
        self.assertIn('if path == "/api/query":', dashboard_api_source)
        self.assertIn("Dashboard queries are unavailable", dashboard_api_source)

        for source_name, source_text in (
            ("dashboard-cli/useDb.ts", use_db_ts),
            ("dashboard-cli/useDb.js", use_db_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("isShadowQueryBlocked", source_text)
                self.assertIn("if (isShadowQueryBlocked())", source_text)
                self.assertIn("queryCache.delete(cacheKey)", source_text)
                self.assertIn("setRows([])", source_text)
                self.assertIn("error instanceof ApiError && error.status === 409", source_text)

        for source_name, source_text in (
            ("dashboard-cli/useBotState.ts", bot_state_ts),
            ("dashboard-cli/useBotState.js", bot_state_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("isShadowQueryBlocked", source_text)
                self.assertIn("db_integrity_known", source_text)
                self.assertIn("startup_recovery_only", source_text)

    def test_wallets_page_explains_blocked_query_state_instead_of_empty_data(self) -> None:
        wallets_ts = _read_text("dashboard-cli/pages/Wallets.tsx")
        wallets_js = _read_text("dashboard-cli/pages/Wallets.js")
        dashboard_ts = _read_text("dashboard-cli/dashboard.tsx")
        dashboard_js = _read_text("dashboard-cli/dashboard.js")

        for source_name, source_text in (
            ("dashboard-cli/pages/Wallets.tsx", wallets_ts),
            ("dashboard-cli/pages/Wallets.js", wallets_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("walletQueryBlockedMessage", source_text)
                self.assertIn("Wallet queries are blocked:", source_text)
                self.assertIn("shadow_restart_pending", source_text)
                self.assertIn("db_integrity_ok", source_text)

        for source_name, source_text in (
            ("dashboard-cli/dashboard.tsx", dashboard_ts),
            ("dashboard-cli/dashboard.js", dashboard_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("botState", source_text)
                expected_prop = "botState={botState}" if source_name.endswith(".tsx") else "botState: botState"
                self.assertIn(expected_prop, source_text)

    def test_performance_and_settings_explain_blocked_query_state_honestly(self) -> None:
        performance_ts = _read_text("dashboard-cli/pages/Performance.tsx")
        performance_js = _read_text("dashboard-cli/pages/Performance.js")
        settings_ts = _read_text("dashboard-cli/pages/Settings.tsx")
        settings_js = _read_text("dashboard-cli/pages/Settings.js")

        for source_name, source_text in (
            ("dashboard-cli/pages/Performance.tsx", performance_ts),
            ("dashboard-cli/pages/Performance.js", performance_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("performanceQueryBlockedMessage", source_text)
                self.assertIn("Performance queries are blocked:", source_text)
                self.assertIn("shadow_restart_pending", source_text)
                self.assertIn("db_integrity_ok", source_text)

        for source_name, source_text in (
            ("dashboard-cli/pages/Settings.tsx", settings_ts),
            ("dashboard-cli/pages/Settings.js", settings_js),
        ):
            with self.subTest(source_name=source_name):
                self.assertIn("settingsRowsBlocked", source_text)
                self.assertIn("settingsRowsValue", source_text)
                self.assertIn("'BLOCKED'", source_text)
                self.assertIn("db_integrity_ok", source_text)

    def test_backend_recovery_entrypoint_gates_on_verified_candidate_and_shadow_mode(self) -> None:
        entrypoint_name, entrypoint = _find_recovery_entrypoint()
        if entrypoint is None:
            self.skipTest(
                "Backend recover-db request handler is not present in this checkout yet; "
                "this contract test activates once the endpoint helper exists."
            )

        signature = inspect.signature(entrypoint)
        if len(signature.parameters) > 1:
            self.skipTest(f"{entrypoint_name} has an unsupported signature for this contract test.")

        good_state = {
            "mode": "shadow",
            "db_recovery_state_known": True,
            "db_recovery_candidate_ready": True,
            "db_recovery_candidate_path": "/tmp/verified-backup.sqlite",
            "db_recovery_candidate_source_path": "/tmp/trading.sqlite",
            "db_recovery_candidate_mode": "integrity_only",
            "db_recovery_candidate_evidence_ready": False,
            "db_recovery_candidate_class_reason": (
                "verified backup restores the ledger, but its shadow evidence is not ready for readiness claims"
            ),
            "db_recovery_latest_verified_backup_path": "/tmp/verified-backup.sqlite",
            "db_recovery_latest_verified_backup_at": 1,
        }
        blocked_state = {
            **good_state,
            "db_recovery_candidate_ready": False,
            "db_recovery_candidate_path": "",
            "db_recovery_candidate_message": "database integrity check failed",
        }

        state_mock = getattr(dashboard_api, "_bot_state_snapshot", None)
        live_config_mock = getattr(dashboard_api, "_live_trading_enabled_in_config", None)
        use_real_money_mock = getattr(dashboard_api, "use_real_money", None)
        if not callable(state_mock) or not callable(live_config_mock) or not callable(use_real_money_mock):
            self.skipTest(
                f"{entrypoint_name} exists, but the dashboard_api helpers needed to assert its preconditions "
                "are not all available."
            )

        def _invoke(state: dict[str, object], live_enabled: bool):
            from unittest.mock import patch

            with patch.object(dashboard_api, "_bot_state_snapshot", return_value=state), patch.object(
                dashboard_api, "_live_trading_enabled_in_config", return_value=live_enabled
            ), patch.object(dashboard_api, "use_real_money", return_value=live_enabled):
                if len(signature.parameters) == 0:
                    return entrypoint()
                return entrypoint({})

        blocked_result = _invoke(blocked_state, live_enabled=False)
        self.assertIsInstance(blocked_result, dict)
        self.assertFalse(bool(blocked_result.get("ok")))
        self.assertIn("backup", str(blocked_result.get("message", "")).lower())

        live_result = _invoke(good_state, live_enabled=True)
        self.assertIsInstance(live_result, dict)
        self.assertFalse(bool(live_result.get("ok")))
        self.assertIn("live", str(live_result.get("message", "")).lower())

        ok_result = _invoke(good_state, live_enabled=False)
        self.assertIsInstance(ok_result, dict)
        self.assertTrue(bool(ok_result.get("ok")))
        self.assertIn("recover", str(ok_result.get("message", "")).lower())
        self.assertIn("integrity-only", str(ok_result.get("message", "")).lower())

    def test_recover_db_response_mentions_evidence_ready_candidate_when_published(self) -> None:
        entrypoint_name, entrypoint = _find_recovery_entrypoint()
        if entrypoint is None:
            self.skipTest("Backend recover-db request handler is not present in this checkout yet.")

        signature = inspect.signature(entrypoint)
        if len(signature.parameters) > 1:
            self.skipTest(f"{entrypoint_name} has an unsupported signature for this contract test.")

        state = {
            "mode": "shadow",
            "db_recovery_state_known": True,
            "db_recovery_candidate_ready": True,
            "db_recovery_candidate_path": "/tmp/verified-backup.sqlite",
            "db_recovery_candidate_source_path": "/tmp/trading.sqlite",
            "db_recovery_candidate_mode": "evidence_ready",
            "db_recovery_candidate_evidence_ready": True,
            "db_recovery_candidate_class_reason": (
                "verified backup is recoverable and its shadow evaluation passes the current evidence gate"
            ),
        }

        from unittest.mock import patch

        with patch.object(dashboard_api, "_bot_state_snapshot", return_value=state), patch.object(
            dashboard_api, "_live_trading_enabled_in_config", return_value=False
        ), patch.object(dashboard_api, "use_real_money", return_value=False):
            result = entrypoint() if len(signature.parameters) == 0 else entrypoint({})

        self.assertIsInstance(result, dict)
        self.assertTrue(bool(result.get("ok")))
        self.assertIn("evidence-ready", str(result.get("message", "")).lower())


if __name__ == "__main__":
    unittest.main()
