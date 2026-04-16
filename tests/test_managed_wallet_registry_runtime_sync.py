from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

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

import kelly_watcher.main as main


class ManagedWalletRegistryRuntimeSyncTests(unittest.TestCase):
    def _run_until_registry_sync(
        self,
        *,
        degraded_state: dict[str, object],
        initial_wallets: list[str],
    ) -> tuple[SimpleNamespace, SimpleNamespace, Mock, SimpleNamespace]:
        class _StopAfterRegistrySync(RuntimeError):
            pass

        current_registry_state: dict[str, object] = {
            "managed_wallet_registry_available": True,
            "managed_wallet_registry_status": "ready" if initial_wallets else "empty",
            "managed_wallet_registry_error": "",
            "managed_wallets": list(initial_wallets),
            "managed_wallet_count": len(initial_wallets),
            "managed_wallet_total_count": len(initial_wallets),
            "managed_wallet_registry_updated_at": 0,
        }

        class _FakeScheduler:
            def __init__(self) -> None:
                self.jobs: dict[str, object] = {}
                self.shutdown = Mock()

            def add_job(self, func, *args, **kwargs) -> None:
                job_id = str(kwargs.get("id") or "")
                if job_id:
                    self.jobs[job_id] = func

            def start(self) -> None:
                callback = self.jobs.get("managed_wallet_registry_sync")
                if callback is None:
                    raise AssertionError("managed_wallet_registry_sync job was not registered")
                current_registry_state.clear()
                current_registry_state.update(degraded_state)
                callback()
                raise _StopAfterRegistrySync("stop after managed wallet registry sync")

        class _FakeThread:
            def __init__(self, *args, **kwargs) -> None:
                self.start = Mock()
                self.join = Mock()

        with TemporaryDirectory() as tmpdir:
            original_state_file = main.BOT_STATE_FILE
            original_event_file = main.EVENT_FILE
            dashboard_server = SimpleNamespace(stop=Mock())
            watch_wallets = list(initial_wallets)
            tracker_wallets = list(initial_wallets)

            def _replace_watch_wallets(wallets: list[str]) -> None:
                watch_wallets[:] = list(wallets)

            def _replace_tracker_wallets(wallets: list[str]) -> None:
                tracker_wallets[:] = list(wallets)

            watchlist_stub = SimpleNamespace(
                state_fields=lambda: {
                    "tracked_wallet_count": len(watch_wallets),
                    "dropped_wallet_count": 0,
                    "hot_wallet_count": 0,
                    "warm_wallet_count": 0,
                    "discovery_wallet_count": len(watch_wallets),
                },
                startup_wallets=lambda: list(watch_wallets),
                active_wallets=lambda: list(watch_wallets),
                replace_wallets=Mock(side_effect=_replace_watch_wallets),
                refresh=Mock(),
            )
            tracker_stub = SimpleNamespace(
                wallets=list(tracker_wallets),
                seen_ids=set(),
                replace_wallets=Mock(side_effect=_replace_tracker_wallets),
                close=Mock(),
            )
            executor_stub = SimpleNamespace(
                validate_live_wallet_ready=Mock(),
                get_usdc_balance=Mock(return_value=100.0),
                close=Mock(),
            )
            dedup_stub = SimpleNamespace(
                seen_ids=set(),
                open_positions=set(),
                load_from_db=Mock(),
                sync_positions_from_api=Mock(return_value=True),
            )
            engine_stub = SimpleNamespace(runtime_info=Mock(return_value={}))
            scheduler_stub = _FakeScheduler()

            def _managed_wallet_registry_state(*args, **kwargs):
                return dict(current_registry_state)

            try:
                main.BOT_STATE_FILE = Path(tmpdir) / "bot_state.json"
                main.EVENT_FILE = Path(tmpdir) / "events.jsonl"
                with ExitStack() as stack:
                    stack.enter_context(patch.object(main, "WATCHED_WALLETS", ["0xbootstrap"]))
                    stack.enter_context(patch("kelly_watcher.main.init_db"))
                    stack.enter_context(
                        patch(
                            "kelly_watcher.main.restore_managed_wallet_registry_snapshot",
                            return_value={"restored": False, "wallets": [], "clear_all": False},
                        )
                    )
                    stack.enter_context(patch("kelly_watcher.main.import_managed_wallets_from_env", return_value=0))
                    stack.enter_context(
                        patch(
                            "kelly_watcher.main.managed_wallet_registry_state",
                            side_effect=_managed_wallet_registry_state,
                        )
                    )
                    stack.enter_context(patch("kelly_watcher.main._validate_startup"))
                    stack.enter_context(patch("kelly_watcher.main._write_bot_pid_file"))
                    stack.enter_context(patch("kelly_watcher.main._clear_bot_pid_file"))
                    stack.enter_context(patch("kelly_watcher.main._repair_event_file_market_urls"))
                    stack.enter_context(patch("kelly_watcher.main._install_shutdown_signal_handlers", return_value=[]))
                    stack.enter_context(patch("kelly_watcher.main._restore_shutdown_signal_handlers"))
                    stack.enter_context(patch("kelly_watcher.main._latest_retrain_run", return_value=None))
                    stack.enter_context(patch("kelly_watcher.main._latest_replay_search_run", return_value=None))
                    stack.enter_context(patch("kelly_watcher.main._latest_replay_promotion", return_value=None))
                    stack.enter_context(patch("kelly_watcher.main._latest_applied_replay_promotion", return_value=None))
                    stack.enter_context(patch("kelly_watcher.main.start_dashboard_api_server", return_value=dashboard_server))
                    stack.enter_context(patch("kelly_watcher.main.sync_belief_priors"))
                    stack.enter_context(patch("kelly_watcher.main.WatchlistManager", return_value=watchlist_stub))
                    stack.enter_context(patch("kelly_watcher.main.PolymarketTracker", return_value=tracker_stub))
                    stack.enter_context(patch("kelly_watcher.main.PolymarketExecutor", return_value=executor_stub))
                    stack.enter_context(patch("kelly_watcher.main.SignalEngine", return_value=engine_stub))
                    stack.enter_context(patch("kelly_watcher.main.DedupeCache", return_value=dedup_stub))
                    stack.enter_context(patch("kelly_watcher.main._init_live_entry_guard", return_value=None))
                    stack.enter_context(patch("kelly_watcher.main._init_daily_loss_guard", return_value=None))
                    stack.enter_context(
                        patch(
                            "kelly_watcher.main._shadow_history_gate_metrics",
                            return_value=(
                                {
                                    "current_window_resolved": 0,
                                    "current_baseline_resolved": 0,
                                    "all_time_resolved": 0,
                                    "routed_current_window_resolved": 0,
                                },
                                None,
                            ),
                        )
                    )
                    stack.enter_context(patch("kelly_watcher.main.live_require_shadow_history", return_value=False))
                    stack.enter_context(patch("kelly_watcher.main.live_min_shadow_resolved", return_value=0))
                    stack.enter_context(
                        patch("kelly_watcher.main.live_min_shadow_resolved_since_promotion", return_value=0)
                    )
                    stack.enter_context(
                        patch("kelly_watcher.main.compute_segment_shadow_report", return_value={})
                    )
                    stack.enter_context(
                        patch(
                            "kelly_watcher.main.compute_tracker_preview_summary",
                            return_value=SimpleNamespace(),
                        )
                    )
                    stack.enter_context(patch("kelly_watcher.main._resolved_shadow_trade_count", return_value=0))
                    stack.enter_context(
                        patch(
                            "kelly_watcher.main.database_integrity_state",
                            return_value={
                                "db_integrity_known": True,
                                "db_integrity_ok": True,
                                "db_integrity_message": "",
                            },
                        )
                    )
                    stack.enter_context(
                        patch("kelly_watcher.main.db_recovery_state", return_value={"db_recovery_state_known": False})
                    )
                    stack.enter_context(patch("kelly_watcher.main._compute_db_recovery_shadow_state", return_value={}))
                    refresh_cache = stack.enter_context(patch("kelly_watcher.main.refresh_trader_cache"))
                    stack.enter_context(patch("kelly_watcher.main.BackgroundScheduler", return_value=scheduler_stub))
                    stack.enter_context(
                        patch("kelly_watcher.main.threading.Thread", side_effect=lambda *a, **k: _FakeThread())
                    )
                    stack.enter_context(patch("kelly_watcher.main.use_real_money", return_value=False))
                    stack.enter_context(patch("kelly_watcher.main.poll_interval", return_value=5.0))
                    stack.enter_context(patch("kelly_watcher.main.wallet_discovery_enabled", return_value=False))
                    stack.enter_context(patch("kelly_watcher.main.send_alert"))
                    with self.assertRaisesRegex(_StopAfterRegistrySync, "stop after managed wallet registry sync"):
                        main.main()
            finally:
                main.BOT_STATE_FILE = original_state_file
                main.EVENT_FILE = original_event_file

        return watchlist_stub, tracker_stub, refresh_cache, dashboard_server

    def test_registry_sync_clears_runtime_wallets_when_registry_degrades(self) -> None:
        for degraded_state in (
            {
                "managed_wallet_registry_available": False,
                "managed_wallet_registry_status": "missing",
                "managed_wallet_registry_error": "",
                "managed_wallets": [],
                "managed_wallet_count": 0,
                "managed_wallet_total_count": 0,
                "managed_wallet_registry_updated_at": 0,
            },
            {
                "managed_wallet_registry_available": False,
                "managed_wallet_registry_status": "unreadable",
                "managed_wallet_registry_error": "database disk image is malformed",
                "managed_wallets": [],
                "managed_wallet_count": 0,
                "managed_wallet_total_count": 0,
                "managed_wallet_registry_updated_at": 0,
            },
        ):
            with self.subTest(status=str(degraded_state["managed_wallet_registry_status"])):
                watchlist_stub, tracker_stub, refresh_cache, dashboard_server = self._run_until_registry_sync(
                    degraded_state=degraded_state,
                    initial_wallets=["0xold"],
                )

                watchlist_stub.replace_wallets.assert_called_once_with([])
                tracker_stub.replace_wallets.assert_called_once_with([])
                self.assertIn(call([]), refresh_cache.call_args_list)
                dashboard_server.stop.assert_called_once()
                tracker_stub.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
