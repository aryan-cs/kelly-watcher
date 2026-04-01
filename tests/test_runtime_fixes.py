from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import auto_retrain
import alerter
import beliefs
import config
import dedup
import db
import dashboard_api
import evaluator
import httpx
import identity_cache
import main
import tracker
import trader_scorer
from executor import PolymarketExecutor, TotalExposureDecision, log_trade
from market_scorer import MarketScorer, build_market_features
from trader_scorer import TraderScorer


class RuntimeFixesTest(unittest.TestCase):
    def test_send_alert_suppresses_non_trade_notifications(self) -> None:
        client = Mock()
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            alerter.send_alert("Bot started")

        client.post.assert_not_called()

    def test_send_alert_allows_buy_notifications(self) -> None:
        client = Mock()
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            alerter.send_alert("Bought", kind="buy")

        client.post.assert_called_once()
        self.assertEqual(client.post.call_args.kwargs["json"]["text"], "bought")

    def test_send_alert_allows_retrain_notifications(self) -> None:
        client = Mock()
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            alerter.send_alert("Retrain accepted", kind="retrain")

        client.post.assert_called_once()

    def test_send_alert_allows_status_notifications(self) -> None:
        client = Mock()
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            alerter.send_alert("bot started", kind="status")

        client.post.assert_called_once()

    def test_build_trade_entry_alert_formats_market_line(self) -> None:
        message = alerter.build_trade_entry_alert(
            mode="shadow",
            side="yes",
            shares=12.5,
            price=0.437,
            total_usd=5.46,
            confidence=0.713,
            question="Will BTC finish March above $90k?",
            market_url="https://polymarket.com/event/btc-above-90k",
        )

        self.assertEqual(
            message,
            "shadow bought 12.5 YES shares @ 43.7 cents for a total of $5.46, 71.3% confident\n\n"
            "Will BTC finish March above $90k?: https://polymarket.com/event/btc-above-90k",
        )

    def test_build_trade_resolution_alert_formats_loss(self) -> None:
        message = alerter.build_trade_resolution_alert(
            mode="live",
            won=False,
            side="no",
            pnl_usd=-3.25,
            question="Will Team A win?",
            market_url="https://polymarket.com/event/team-a-win",
        )

        self.assertEqual(
            message,
            "❌ live lost NO, lost $3.25\n\nWill Team A win?: https://polymarket.com/event/team-a-win",
        )

    def test_build_trade_resolution_alert_includes_tracked_trader(self) -> None:
        message = alerter.build_trade_resolution_alert(
            mode="shadow",
            won=True,
            side="yes",
            pnl_usd=4.0,
            question="Will BTC finish March above $90k?",
            market_url="https://polymarket.com/event/btc-above-90k",
            tracked_trader_name="TraderOne",
            tracked_trader_address="0x1234567890abcdef1234567890abcdef12345678",
        )

        self.assertEqual(
            message,
            "✅ shadow won YES, made $4.00 | tracking TraderOne (0x123456...345678)\n\n"
            "Will BTC finish March above $90k?: https://polymarket.com/event/btc-above-90k",
        )

    def test_send_telegram_message_lowercases_non_url_text(self) -> None:
        client = Mock()
        response = Mock()
        response.raise_for_status = Mock(return_value=None)
        client.post.return_value = response
        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client)
        client_context.__exit__ = Mock(return_value=False)

        with patch("alerter.telegram_bot_token", return_value="token"), patch(
            "alerter.telegram_chat_id", return_value="chat-id"
        ), patch("alerter.httpx.Client", return_value=client_context):
            ok = alerter.send_telegram_message("Hello Trader\nWill BTC Win? https://polymarket.com/Event/ABC")

        self.assertTrue(ok)
        self.assertEqual(
            client.post.call_args.kwargs["json"]["text"],
            "hello trader\nwill btc win? https://polymarket.com/Event/ABC",
        )

    def test_resolve_wallet_for_username_returns_wallet_and_caches_identity(self) -> None:
        class _Response:
            def __init__(self, text: str, status_code: int = 200) -> None:
                self.text = text
                self.status_code = status_code

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        "request failed",
                        request=httpx.Request("GET", "https://polymarket.com"),
                        response=httpx.Response(self.status_code),
                    )
                return None

        class _Client:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def get(self, url: str) -> _Response:
                self.calls.append(url)
                if url != "https://polymarket.com/@TraderName":
                    return _Response("", status_code=404)
                return _Response(
                    """
                    <html>
                      0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
                      <script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">
                        {"props":{"pageProps":{"proxyAddress":"0x1234567890abcdef1234567890abcdef12345678"}}}
                      </script>
                      0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
                    </html>
                    """
                )

        with TemporaryDirectory() as tmpdir:
            original_cache_path = identity_cache.CACHE_PATH
            try:
                identity_cache.CACHE_PATH = Path(tmpdir) / "identity_cache.json"
                client = _Client()
                resolved = identity_cache.resolve_wallet_for_username("TraderName", client)
                self.assertEqual(resolved, "0x1234567890abcdef1234567890abcdef12345678")
                self.assertEqual(
                    identity_cache.lookup_wallet("TraderName"),
                    "0x1234567890abcdef1234567890abcdef12345678",
                )
                self.assertEqual(client.calls, ["https://polymarket.com/@TraderName"])
                self.assertEqual(
                    identity_cache.lookup_username("0x1234567890abcdef1234567890abcdef12345678"),
                    "tradername",
                )
            finally:
                identity_cache.CACHE_PATH = original_cache_path

    def test_live_account_equity_includes_open_positions(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor.get_usdc_balance = lambda: 80.0
        executor._fetch_live_positions = lambda: [
            {"currentValue": "25.5"},
            {"totalBought": "10", "cashPnl": "2"},
        ]

        with patch("executor.use_real_money", return_value=True):
            self.assertEqual(executor.get_account_equity_usd(), 117.5)

    def test_max_daily_loss_pct_reloads_from_env_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("MAX_DAILY_LOSS_PCT=0.12\n", encoding="utf-8")
            with patch.object(config, "ENV_PATH", env_path), patch.dict(
                "os.environ",
                {"MAX_DAILY_LOSS_PCT": "0.05"},
                clear=False,
            ):
                self.assertAlmostEqual(config.max_daily_loss_pct(), 0.12)
                env_path.write_text("MAX_DAILY_LOSS_PCT=0.07\n", encoding="utf-8")
                self.assertAlmostEqual(config.max_daily_loss_pct(), 0.07)

    def test_dashboard_config_snapshot_includes_max_market_horizon_after_write(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "save" / ".env.dev"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            repo_env_path = Path(tmpdir) / ".env.dev"
            env_example_path = Path(tmpdir) / ".env.example"
            repo_env_path.write_text("MAX_MARKET_HORIZON=365d\n", encoding="utf-8")
            env_example_path.write_text("", encoding="utf-8")

            with patch.object(dashboard_api, "ENV_PATH", env_path), patch.object(
                dashboard_api, "REPO_ROOT", Path(tmpdir)
            ), patch.object(
                dashboard_api, "ENV_EXAMPLE_PATH", env_example_path
            ):
                dashboard_api._write_env_value("MAX_MARKET_HORIZON", "7d")
                snapshot = dashboard_api._config_snapshot()

            self.assertEqual(snapshot["safe_values"]["MAX_MARKET_HORIZON"], "7d")
            self.assertIn("MAX_MARKET_HORIZON=7d", env_path.read_text(encoding="utf-8"))

    def test_dashboard_config_snapshot_includes_open_exposure_cap_after_write(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "save" / ".env.dev"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            repo_env_path = Path(tmpdir) / ".env.dev"
            env_example_path = Path(tmpdir) / ".env.example"
            repo_env_path.write_text("MAX_TOTAL_OPEN_EXPOSURE_FRACTION=0.60\n", encoding="utf-8")
            env_example_path.write_text("", encoding="utf-8")

            with patch.object(dashboard_api, "ENV_PATH", env_path), patch.object(
                dashboard_api, "REPO_ROOT", Path(tmpdir)
            ), patch.object(
                dashboard_api, "ENV_EXAMPLE_PATH", env_example_path
            ):
                dashboard_api._write_env_value("MAX_TOTAL_OPEN_EXPOSURE_FRACTION", "0.42")
                snapshot = dashboard_api._config_snapshot()

            self.assertEqual(snapshot["safe_values"]["MAX_TOTAL_OPEN_EXPOSURE_FRACTION"], "0.42")
            self.assertIn("MAX_TOTAL_OPEN_EXPOSURE_FRACTION=0.42", env_path.read_text(encoding="utf-8"))

    def test_dashboard_config_snapshot_includes_heuristic_min_entry_price_after_write(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "save" / ".env.dev"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            repo_env_path = Path(tmpdir) / ".env.dev"
            env_example_path = Path(tmpdir) / ".env.example"
            repo_env_path.write_text("HEURISTIC_MIN_ENTRY_PRICE=0.35\n", encoding="utf-8")
            env_example_path.write_text("", encoding="utf-8")

            with patch.object(dashboard_api, "ENV_PATH", env_path), patch.object(
                dashboard_api, "REPO_ROOT", Path(tmpdir)
            ), patch.object(
                dashboard_api, "ENV_EXAMPLE_PATH", env_example_path
            ):
                dashboard_api._write_env_value("HEURISTIC_MIN_ENTRY_PRICE", "0.50")
                snapshot = dashboard_api._config_snapshot()

            self.assertEqual(snapshot["safe_values"]["HEURISTIC_MIN_ENTRY_PRICE"], "0.50")
            self.assertIn("HEURISTIC_MIN_ENTRY_PRICE=0.50", env_path.read_text(encoding="utf-8"))

    def test_dashboard_spawn_shadow_restart_process_writes_request_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            request_file = data_dir / "shadow_reset_request.json"
            with patch.object(dashboard_api, "DATA_DIR", data_dir), patch.object(
                dashboard_api, "SHADOW_RESET_REQUEST_FILE", request_file
            ):
                result = dashboard_api._spawn_shadow_restart_process(wallet_mode="clear_all")
                payload = json.loads(request_file.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertEqual(payload["wallet_mode"], "clear_all")
            self.assertTrue(str(payload["request_id"]).startswith("shadow-reset-"))

    def test_dashboard_spawn_shadow_restart_process_supports_keep_active_mode(self) -> None:
        with TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            request_file = data_dir / "shadow_reset_request.json"
            with patch.object(dashboard_api, "DATA_DIR", data_dir), patch.object(
                dashboard_api, "SHADOW_RESET_REQUEST_FILE", request_file
            ):
                result = dashboard_api._spawn_shadow_restart_process(wallet_mode="keep_active")
                payload = json.loads(request_file.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertEqual(payload["wallet_mode"], "keep_active")

    def test_dashboard_launch_shadow_restart_queues_request(self) -> None:
        with patch.object(dashboard_api, "_live_trading_enabled_in_config", return_value=False), patch.object(
            dashboard_api, "_current_bot_mode", return_value="shadow"
        ), patch.object(dashboard_api, "use_real_money", return_value=False), patch.object(
            dashboard_api, "_spawn_shadow_restart_process", return_value={"ok": True, "message": "queued"}
        ) as spawn_mock:
            result = dashboard_api._launch_shadow_restart(wallet_mode="keep_all")

        self.assertTrue(result["ok"])
        self.assertIn("wipe state and restart itself", result["message"])
        spawn_mock.assert_called_once_with("keep_all")

    def test_consume_shadow_reset_request_reads_valid_request(self) -> None:
        with TemporaryDirectory() as tmpdir:
            request_file = Path(tmpdir) / "shadow_reset_request.json"
            request_file.write_text(
                json.dumps(
                    {
                        "wallet_mode": "keep_all",
                        "request_id": "shadow-reset-1",
                        "requested_at": int(time.time()),
                        "source": "dashboard",
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(main, "SHADOW_RESET_REQUEST_FILE", request_file):
                request = main._consume_shadow_reset_request()

        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.wallet_mode, "keep_all")
        self.assertEqual(request.request_id, "shadow-reset-1")
        self.assertFalse(request_file.exists())

    def test_wait_for_next_poll_returns_when_shutdown_requested(self) -> None:
        stop_event = main.threading.Event()
        stop_event.set()
        started_at = time.time()

        main._wait_for_next_poll(started_at, {"started_at": 1}, stop_event=stop_event)

        self.assertLess(time.time() - started_at, 0.5)

    def test_entry_pause_reason_refreshes_daily_loss_guard_from_config(self) -> None:
        now_ts = int(time.time())
        guard = main.DailyLossGuard(
            start_equity=100.0,
            loss_limit_pct=0.10,
            day_key=time.strftime("%Y-%m-%d", time.localtime(now_ts)),
            _equity_locked=True,
        )
        tracker_stub = SimpleNamespace(trade_feed_health=lambda: (now_ts, 0))
        executor_stub = SimpleNamespace(live_entry_health_reason=lambda: None)

        with patch("main.max_daily_loss_pct", return_value=0.0), patch("main.use_real_money", return_value=False):
            reason = main._entry_pause_reason(
                tracker_stub,
                executor_stub,
                None,
                guard,
                89.0,
            )

        self.assertIsNone(reason)
        self.assertEqual(guard.loss_limit_pct, 0.0)

    def test_entry_pause_alert_tracker_debounces_state_flaps(self) -> None:
        tracker = main.EntryPauseAlertTracker(required_stable_loops=2)
        first_state = main.EntryPauseState(
            key="trade_feed_stale",
            reason="source trade feed is stale; the last successful trade poll was 181s ago",
        )
        second_state = main.EntryPauseState(
            key="trade_feed_stale",
            reason="source trade feed is stale; the last successful trade poll was 192s ago",
        )

        self.assertIsNone(tracker.update(first_state))

        paused_transition = tracker.update(second_state)
        self.assertEqual(paused_transition, ("paused", second_state))

        self.assertIsNone(tracker.update(second_state))
        self.assertIsNone(tracker.update(None))

        resumed_transition = tracker.update(None)
        self.assertEqual(resumed_transition[0], "resumed")
        self.assertEqual(resumed_transition[1], second_state)

    def test_entry_pause_state_uses_stable_live_health_key(self) -> None:
        now_ts = int(time.time())
        guard = main.DailyLossGuard(
            start_equity=100.0,
            loss_limit_pct=0.10,
            day_key=time.strftime("%Y-%m-%d", time.localtime(now_ts)),
            _equity_locked=True,
        )
        tracker_stub = SimpleNamespace(trade_feed_health=lambda: (now_ts, 0))
        executor_stub = SimpleNamespace(
            live_entry_health_status=lambda: (
                "wallet_balance_failures",
                "live balance health degraded after 4 consecutive wallet-balance failures",
            )
        )

        with patch("main.use_real_money", return_value=True):
            state = main._entry_pause_state(
                tracker_stub,
                executor_stub,
                None,
                guard,
                100.0,
            )

        self.assertIsNotNone(state)
        self.assertEqual(state.key, "live_health:wallet_balance_failures")
        self.assertIn("wallet-balance failures", state.reason)

    def test_live_entry_health_status_keeps_key_stable_as_failure_count_changes(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._consecutive_live_balance_failures = 3
        executor._consecutive_live_position_sync_failures = 0

        with patch("executor.max_live_health_failures", return_value=3):
            first_status = executor.live_entry_health_status()
            executor._consecutive_live_balance_failures = 5
            second_status = executor.live_entry_health_status()

        self.assertEqual(first_status[0], "wallet_balance_failures")
        self.assertEqual(second_status[0], "wallet_balance_failures")
        self.assertNotEqual(first_status[1], second_status[1])

    def test_resolve_shadow_trades_labels_exited_rows_without_overwriting_realized_pnl(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at, exited_at,
                        resolved_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-1",
                        "market-1",
                        "Will it happen?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.40,
                        10.0,
                        0.75,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        1_700_000_100,
                        1_700_000_100,
                        0.40,
                        25.0,
                        10.0,
                        3.21,
                    ),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": True,
                    "tokens": [
                        {"outcome": "yes", "winner": True},
                        {"outcome": "no", "winner": False},
                    ],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, counterfactual_return,
                           shadow_pnl_usd, exited_at, resolved_at, label_applied_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-1",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "yes")
                self.assertAlmostEqual(float(row["counterfactual_return"]), 1.5, places=6)
                self.assertAlmostEqual(float(row["shadow_pnl_usd"]), 3.21, places=2)
                self.assertEqual(int(row["exited_at"]), 1_700_000_100)
                self.assertEqual(int(row["resolved_at"]), 1_700_000_100)
                self.assertGreater(int(row["label_applied_at"]), 0)
            finally:
                db.DB_PATH = original_db_path

    def test_send_resolution_alerts_only_notifies_executed_positions(self) -> None:
        resolved_rows = [
            {
                "executed": True,
                "real_money": 0,
                "won": True,
                "side": "yes",
                "pnl": 3.5,
                "question": "Will it happen?",
                "market_resolved_outcome": "yes",
                "market_url": "https://polymarket.com/event/will-it-happen",
            },
            {
                "executed": False,
                "real_money": 0,
                "won": False,
                "side": "no",
                "pnl": 0.0,
                "question": "Skipped trade",
            },
        ]

        with patch("main.send_alert") as alert_mock:
            main._send_resolution_alerts(resolved_rows)

        alert_mock.assert_called_once()
        self.assertEqual(
            alert_mock.call_args.args[0],
            "✅ shadow won YES, made $3.50\nWill it happen?: https://polymarket.com/event/will-it-happen",
        )
        self.assertEqual(alert_mock.call_args.kwargs["kind"], "resolution")

    def test_resolve_shadow_trades_does_not_resolve_open_markets_from_prices(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-early",
                        "market-eth-1900",
                        "Will the price of Ethereum be above $1,900 on March 20?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.996,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": False,
                    "tokens": [
                        {"outcome": "Yes", "winner": False, "price": "0.994"},
                        {"outcome": "No", "winner": False, "price": "0.006"},
                    ],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(resolved, [])

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at, label_applied_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-early",),
                ).fetchone()
                conn.close()

                self.assertIsNone(row["outcome"])
                self.assertIsNone(row["market_resolved_outcome"])
                self.assertIsNone(row["resolved_at"])
                self.assertIsNone(row["label_applied_at"])
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_resolves_closed_markets_from_token_winner(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-after-end",
                        "market-eth-1900",
                        "Will the price of Ethereum be above $1,900 on March 20?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.996,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": True,
                    "tokens": [
                        {"outcome": "Yes", "winner": True},
                        {"outcome": "No", "winner": False},
                    ],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ), patch("evaluator.time.time", return_value=1_700_000_123):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at, label_applied_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-after-end",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "yes")
                self.assertEqual(int(row["resolved_at"]), 1_700_000_123)
                self.assertEqual(int(row["label_applied_at"]), 1_700_000_123)
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_deletes_positions_when_token_id_delete_misses(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-token-mismatch",
                        "market-token-mismatch",
                        "Will it happen?",
                        "0xabc",
                        "yes",
                        "TOKEN-1",
                        "buy",
                        0.4,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        0.4,
                        25.0,
                        10.0,
                    ),
                )
                conn.execute(
                    "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
                    ("market-token-mismatch", "yes", 10.0, 0.4, "token-1", 1_700_000_000, 0),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": True,
                    "tokens": [
                        {"outcome": "yes", "winner": True},
                        {"outcome": "no", "winner": False},
                    ],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)

                conn = db.get_conn()
                position = conn.execute(
                    "SELECT 1 FROM positions WHERE market_id=? AND real_money=0",
                    ("market-token-mismatch",),
                ).fetchone()
                conn.close()

                self.assertIsNone(position)
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_waits_for_polymarket_closed_even_if_event_ended(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-sports-ended",
                        "market-favbet-bo3",
                        "Counter-Strike: Favbet vs ESC Gaming (BO3) - CCT Europe Series #18 Playoffs",
                        "0xabc",
                        "favbet",
                        "buy",
                        0.5,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                market = {
                    "closed": False,
                    "tokens": [
                        {"outcome": "Favbet", "winner": True},
                        {"outcome": "ESC Gaming", "winner": False},
                    ],
                    "events": [{"ended": True, "live": False}],
                }
                with patch("evaluator._fetch_market", return_value=market), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(resolved, [])

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at, label_applied_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-sports-ended",),
                ).fetchone()
                conn.close()

                self.assertIsNone(row["outcome"])
                self.assertIsNone(row["market_resolved_outcome"])
                self.assertIsNone(row["resolved_at"])
                self.assertIsNone(row["label_applied_at"])
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_falls_back_to_sports_page_for_team_market(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-sports-page-team",
                        "0x00342368bcca5a5b6e21d837edaebc7efaab6e48ce9041c6a215ddc7d22420d6",
                        "High Point Panthers vs. Wisconsin Badgers",
                        "https://polymarket.com/event/cbb-hpnt-wisc-2026-03-19",
                        "0xabc",
                        "high point panthers",
                        "token-team",
                        "buy",
                        0.45,
                        10.0,
                        0.64,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                sports_snapshot = {
                    "ended": True,
                    "score": "83-82",
                    "canonicalUrl": "https://polymarket.com/sports/cbb/cbb-hpnt-wisc-2026-03-19",
                    "event": {
                        "slug": "cbb-hpnt-wisc-2026-03-19",
                        "markets": [
                            {
                                "conditionId": "0x00342368bcca5a5b6e21d837edaebc7efaab6e48ce9041c6a215ddc7d22420d6",
                                "question": "High Point Panthers vs. Wisconsin Badgers",
                                "sportsMarketType": "moneyline",
                                "outcomes": ["High Point Panthers", "Wisconsin Badgers"],
                                "teams": [
                                    {"name": "High Point Panthers", "score": 83},
                                    {"name": "Wisconsin Badgers", "score": 82},
                                ],
                            }
                        ],
                    },
                }
                with patch("evaluator._fetch_market", return_value=None) as fetch_market_mock, patch(
                    "evaluator._fetch_sports_page_snapshot", return_value=sports_snapshot
                ), patch("evaluator.sync_belief_priors", return_value=0):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)
                fetch_market_mock.assert_not_called()

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-sports-page-team",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "high point panthers")
                resolution_payload = json.loads(row["resolution_json"])
                self.assertEqual(resolution_payload["source"], "sports_page")
                self.assertEqual(resolution_payload["score"], "83-82")
                self.assertTrue(resolution_payload["closed"])
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_falls_back_to_sports_page_for_yes_no_team_win_market(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-sports-page-yes-no",
                        "market-yes-no",
                        "Will High Point Panthers win on 2026-03-19?",
                        "https://polymarket.com/event/cbb-hpnt-wisc-2026-03-19",
                        "0xabc",
                        "yes",
                        "token-yes-no",
                        "buy",
                        0.44,
                        10.0,
                        0.64,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                sports_snapshot = {
                    "ended": True,
                    "score": "83-82",
                    "canonicalUrl": "https://polymarket.com/sports/cbb/cbb-hpnt-wisc-2026-03-19",
                    "event": {
                        "slug": "cbb-hpnt-wisc-2026-03-19",
                        "markets": [
                            {
                                "conditionId": "different-condition-id",
                                "question": "High Point Panthers vs. Wisconsin Badgers",
                                "sportsMarketType": "moneyline",
                                "outcomes": ["High Point Panthers", "Wisconsin Badgers"],
                                "teams": [
                                    {"name": "High Point Panthers", "score": 83},
                                    {"name": "Wisconsin Badgers", "score": 82},
                                ],
                            }
                        ],
                    },
                }
                with patch(
                    "evaluator._fetch_market",
                    return_value={
                        "closed": False,
                        "tokens": [
                            {"outcome": "Yes", "winner": False},
                            {"outcome": "No", "winner": False},
                        ],
                    },
                ), patch("evaluator._fetch_sports_page_snapshot", return_value=sports_snapshot), patch(
                    "evaluator.sync_belief_priors", return_value=0
                ):
                    resolved = evaluator.resolve_shadow_trades()

                self.assertEqual(len(resolved), 1)

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-sports-page-yes-no",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "yes")
            finally:
                db.DB_PATH = original_db_path

    def test_sports_route_candidates_support_valorant_slugs(self) -> None:
        routes = evaluator._sports_route_candidates("val-uwgc-og-2026-03-25", None)
        self.assertIn("valorant", routes)
        self.assertIn("esports", routes)
        self.assertEqual(routes[0], "valorant")

    def test_resolve_shadow_trades_can_target_one_valorant_match_by_question(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-valorant-target",
                        "market-valorant-target",
                        "Valorant: University War GC vs Olimpo Gold (BO3) - VCT Game Changers Latin America South Group Stage",
                        "https://polymarket.com/event/val-uwgc-og-2026-03-25",
                        "0xabc",
                        "olimpo gold",
                        "token-valorant-target",
                        "buy",
                        0.43,
                        10.0,
                        0.68,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-valorant-other",
                        "market-valorant-other",
                        "Valorant: Evil Geniuses Academy vs Azure Dragon Gaming (BO3)",
                        "https://polymarket.com/event/val-ega-adg-2026-03-24",
                        "0xdef",
                        "evil geniuses academy",
                        "token-valorant-other",
                        "buy",
                        0.51,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        0,
                        1_700_000_001,
                    ),
                )
                conn.commit()
                conn.close()

                sports_snapshot = {
                    "ended": True,
                    "canonicalUrl": "https://polymarket.com/sports/valorant/val-uwgc-og-2026-03-25",
                    "event": {
                        "slug": "val-uwgc-og-2026-03-25",
                        "finishedTimestamp": "2026-03-25T03:00:00Z",
                        "markets": [
                            {
                                "conditionId": "market-valorant-target",
                                "question": "University War GC vs Olimpo Gold",
                                "sportsMarketType": "moneyline",
                                "outcomes": ["University War GC", "Olimpo Gold"],
                                "teams": [
                                    {"name": "University War GC", "score": 0},
                                    {"name": "Olimpo Gold", "score": 2},
                                ],
                            }
                        ],
                    },
                }
                with patch("evaluator._fetch_market", return_value=None), patch(
                    "evaluator._fetch_sports_page_snapshot", return_value=sports_snapshot
                ), patch("evaluator.sync_belief_priors", return_value=0), patch(
                    "evaluator.time.time", return_value=1_700_000_123
                ):
                    resolved = evaluator.resolve_shadow_trades(
                        question_contains="University War GC vs Olimpo Gold"
                    )

                self.assertEqual(len(resolved), 1)
                self.assertEqual(resolved[0]["trade_id"], "trade-valorant-target")
                self.assertEqual(resolved[0]["market_resolved_outcome"], "olimpo gold")

                conn = db.get_conn()
                target_row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-valorant-target",),
                ).fetchone()
                other_row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-valorant-other",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(target_row["outcome"]), 1)
                self.assertEqual(target_row["market_resolved_outcome"], "olimpo gold")
                self.assertEqual(int(target_row["resolved_at"]), 1_700_000_123)
                self.assertIsNone(other_row["outcome"])
                self.assertIsNone(other_row["market_resolved_outcome"])
                self.assertIsNone(other_row["resolved_at"])
            finally:
                db.DB_PATH = original_db_path

    def test_resolve_shadow_trades_can_force_manual_outcome_for_targeted_match(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-valorant-force",
                        "market-valorant-force",
                        "Valorant: University War GC vs Olimpo Gold (BO3) - VCT Game Changers Latin America South Group Stage",
                        "https://polymarket.com/event/val-uw-og2-2026-03-17",
                        "0xabc",
                        "olimpo gold",
                        "token-valorant-force",
                        "buy",
                        0.43,
                        10.0,
                        0.68,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                    ),
                )
                conn.commit()
                conn.close()

                with patch("evaluator.sync_belief_priors", return_value=0), patch(
                    "evaluator.time.time", return_value=1_700_000_456
                ):
                    resolved = evaluator.resolve_shadow_trades(
                        question_contains="University War GC vs Olimpo Gold",
                        forced_outcome="olimpo gold",
                    )

                self.assertEqual(len(resolved), 1)
                self.assertEqual(resolved[0]["trade_id"], "trade-valorant-force")
                self.assertTrue(resolved[0]["won"])
                self.assertEqual(resolved[0]["market_resolved_outcome"], "olimpo gold")

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, resolved_at, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("trade-valorant-force",),
                ).fetchone()
                conn.close()

                self.assertEqual(int(row["outcome"]), 1)
                self.assertEqual(row["market_resolved_outcome"], "olimpo gold")
                self.assertEqual(int(row["resolved_at"]), 1_700_000_456)
                resolution_json = json.loads(row["resolution_json"])
                self.assertEqual(resolution_json["source"], "manual_override")
                self.assertEqual(resolution_json["forcedOutcome"], "olimpo gold")
            finally:
                db.DB_PATH = original_db_path

    def test_cleanup_premature_resolutions_reopens_bad_rows_and_rebuilds_beliefs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                premature_resolution_json = json.dumps(
                    {
                        "closed": False,
                        "endDate": "2026-03-20T16:00:00Z",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.994", "0.006"]',
                        "events": [{"ended": False, "live": True}],
                    }
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, signal_mode, placed_at,
                        resolved_at, label_applied_at, outcome, market_resolved_outcome,
                        counterfactual_return, resolution_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "skip-bad",
                        "market-skip",
                        "Will it happen?",
                        "0xskip",
                        "yes",
                        "buy",
                        0.9,
                        10.0,
                        0.61,
                        0.10,
                        0,
                        1,
                        "confidence was 0.61 below the 0.62 minimum",
                        "heuristic",
                        1_700_000_000,
                        1_700_000_100,
                        1_700_000_100,
                        1,
                        "yes",
                        0.111111,
                        premature_resolution_json,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, actual_entry_price,
                        actual_entry_shares, actual_entry_size_usd, source_shares, confidence,
                        kelly_fraction, real_money, skipped, placed_at, resolved_at,
                        label_applied_at, outcome, market_resolved_outcome, counterfactual_return,
                        shadow_pnl_usd, remaining_entry_shares, remaining_entry_size_usd,
                        remaining_source_shares, resolution_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "open-bad",
                        "market-open",
                        "Counter-Strike: Favbet vs ESC Gaming - Map 2 Winner",
                        "0xopen",
                        "favbet",
                        "token-open",
                        "buy",
                        0.5,
                        1.0,
                        0.5,
                        2.0,
                        1.0,
                        19.0,
                        0.617,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        1_700_000_100,
                        1_700_000_100,
                        0,
                        "esc gaming",
                        -1.0,
                        -1.0,
                        0.0,
                        0.0,
                        0.0,
                        premature_resolution_json,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, actual_entry_price,
                        actual_entry_shares, actual_entry_size_usd, source_shares, confidence,
                        kelly_fraction, real_money, skipped, placed_at, exited_at,
                        resolved_at, shadow_pnl_usd, resolution_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "resolved-good",
                        "market-good",
                        "Already settled",
                        "0xgood",
                        "yes",
                        "token-good",
                        "buy",
                        0.4,
                        10.0,
                        0.4,
                        25.0,
                        10.0,
                        100.0,
                        0.7,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        1_700_000_200,
                        1_700_000_200,
                        3.21,
                        json.dumps({"closed": True, "winner": "yes", "endDate": "2026-03-19T16:00:00Z"}),
                    ),
                )
                conn.execute(
                    "INSERT INTO belief_updates (trade_log_id, applied_at) VALUES (?, ?)",
                    (999, 1_700_000_300),
                )
                conn.execute(
                    "INSERT INTO belief_priors (feature_name, bucket, wins, losses, updated_at) VALUES (?,?,?,?,?)",
                    ("__global__", "all", 99.0, 1.0, 1_700_000_300),
                )
                conn.commit()
                conn.close()

                backup_path = Path(tmpdir) / "premature_cleanup_test.bak"
                result = evaluator.cleanup_premature_resolutions(backup_path=backup_path)

                self.assertEqual(result["rows_cleaned"], 2)
                self.assertEqual(result["skipped_rows_cleaned"], 1)
                self.assertEqual(result["open_positions_reopened"], 1)
                self.assertEqual(result["exited_rows_preserved"], 0)
                self.assertEqual(result["belief_rows_reapplied"], 1)
                self.assertTrue(backup_path.exists())

                conn = db.get_conn()
                skipped_row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, counterfactual_return,
                           resolved_at, label_applied_at, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("skip-bad",),
                ).fetchone()
                open_row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, shadow_pnl_usd, actual_pnl_usd,
                           remaining_entry_shares, remaining_entry_size_usd, remaining_source_shares,
                           resolved_at, label_applied_at, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("open-bad",),
                ).fetchone()
                position_row = conn.execute(
                    """
                    SELECT size_usd, avg_price, token_id, side
                    FROM positions
                    WHERE market_id=? AND token_id=? AND real_money=0
                    """,
                    ("market-open", "token-open"),
                ).fetchone()
                belief_updates_count = conn.execute("SELECT COUNT(*) AS n FROM belief_updates").fetchone()["n"]
                belief_priors_count = conn.execute("SELECT COUNT(*) AS n FROM belief_priors").fetchone()["n"]
                conn.close()

                self.assertIsNone(skipped_row["outcome"])
                self.assertIsNone(skipped_row["market_resolved_outcome"])
                self.assertIsNone(skipped_row["counterfactual_return"])
                self.assertIsNone(skipped_row["resolved_at"])
                self.assertIsNone(skipped_row["label_applied_at"])
                self.assertIsNone(skipped_row["resolution_json"])

                self.assertIsNone(open_row["outcome"])
                self.assertIsNone(open_row["market_resolved_outcome"])
                self.assertIsNone(open_row["shadow_pnl_usd"])
                self.assertIsNone(open_row["actual_pnl_usd"])
                self.assertAlmostEqual(float(open_row["remaining_entry_shares"]), 2.0, places=6)
                self.assertAlmostEqual(float(open_row["remaining_entry_size_usd"]), 1.0, places=6)
                self.assertAlmostEqual(float(open_row["remaining_source_shares"]), 19.0, places=6)
                self.assertIsNone(open_row["resolved_at"])
                self.assertIsNone(open_row["label_applied_at"])
                self.assertIsNone(open_row["resolution_json"])

                self.assertIsNotNone(position_row)
                self.assertAlmostEqual(float(position_row["size_usd"]), 1.0, places=6)
                self.assertAlmostEqual(float(position_row["avg_price"]), 0.5, places=6)
                self.assertEqual(position_row["token_id"], "token-open")
                self.assertEqual(position_row["side"], "favbet")

                self.assertEqual(int(belief_updates_count), 1)
                self.assertGreater(int(belief_priors_count), 0)
            finally:
                db.DB_PATH = original_db_path

    def test_cleanup_premature_resolutions_clears_closed_false_rows_even_when_event_ended(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, actual_entry_price,
                        actual_entry_shares, actual_entry_size_usd, source_shares, confidence,
                        kelly_fraction, real_money, skipped, placed_at, resolved_at,
                        label_applied_at, outcome, market_resolved_outcome, counterfactual_return,
                        shadow_pnl_usd, remaining_entry_shares, remaining_entry_size_usd,
                        remaining_source_shares, resolution_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "ended-but-open",
                        "market-ended-open",
                        "Spread: Wisconsin Badgers (-10.5)",
                        "0xabc",
                        "wisconsin badgers",
                        "buy",
                        0.51,
                        1.0,
                        0.51,
                        1.9607843137,
                        1.0,
                        20.0,
                        0.64,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        1_700_000_200,
                        1_700_000_200,
                        0,
                        "high point panthers",
                        -1.0,
                        -1.0,
                        0.0,
                        0.0,
                        0.0,
                        json.dumps(
                            {
                                "closed": False,
                                "events": [{"ended": True, "score": "83-82"}],
                                "tokens": [
                                    {"outcome": "Wisconsin Badgers", "winner": False},
                                    {"outcome": "High Point Panthers", "winner": True},
                                ],
                            }
                        ),
                    ),
                )
                conn.commit()
                conn.close()

                result = evaluator.cleanup_premature_resolutions(backup_path=Path(tmpdir) / "cleanup.bak")
                self.assertEqual(result["rows_cleaned"], 1)
                self.assertEqual(result["open_positions_reopened"], 1)

                conn = db.get_conn()
                row = conn.execute(
                    """
                    SELECT outcome, market_resolved_outcome, shadow_pnl_usd,
                           remaining_entry_shares, remaining_entry_size_usd, resolution_json
                    FROM trade_log
                    WHERE trade_id=?
                    """,
                    ("ended-but-open",),
                ).fetchone()
                conn.close()

                self.assertIsNone(row["outcome"])
                self.assertIsNone(row["market_resolved_outcome"])
                self.assertIsNone(row["shadow_pnl_usd"])
                self.assertAlmostEqual(float(row["remaining_entry_shares"]), 1.960784, places=5)
                self.assertAlmostEqual(float(row["remaining_entry_size_usd"]), 1.0, places=6)
                self.assertIsNone(row["resolution_json"])
            finally:
                db.DB_PATH = original_db_path

    def test_early_retrain_counts_recent_labels_not_old_entry_times(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO model_history (
                        trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (2_000, 250, 0.2, 0.6, "[]", "model.joblib", 1),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd, resolved_at, label_applied_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-2",
                        "market-2",
                        "Old trade, new label",
                        "0xdef",
                        "yes",
                        "buy",
                        0.45,
                        12.0,
                        0.72,
                        0.08,
                        0,
                        0,
                        1_000,
                        0.45,
                        26.666667,
                        12.0,
                        2.5,
                        1_500,
                        3_000,
                    ),
                )
                conn.commit()
                conn.close()

                with patch("auto_retrain.retrain_min_new_labels", return_value=1):
                    self.assertTrue(auto_retrain.should_retrain_early(None))
            finally:
                db.DB_PATH = original_db_path

    def test_early_retrain_without_deployed_model_uses_configured_min_samples(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()

                with patch("auto_retrain.load_training_data", return_value=[object()] * 149), patch(
                    "auto_retrain.min_samples_required", return_value=150
                ):
                    self.assertFalse(auto_retrain.should_retrain_early(None))

                with patch("auto_retrain.load_training_data", return_value=[object()] * 150), patch(
                    "auto_retrain.min_samples_required", return_value=150
                ):
                    self.assertTrue(auto_retrain.should_retrain_early(None))
            finally:
                db.DB_PATH = original_db_path

    def test_live_order_response_fill_overrides_book_estimate_for_entries(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._clob = SimpleNamespace(
            create_market_order=lambda order: order,
            post_order=lambda signed, order_type: {
                "success": True,
                "status": "matched",
                "orderID": "order-1",
                "makingAmount": "10.0",
                "takingAmount": "20.0",
            },
        )
        executor._ensure_live_token_allowance = lambda token_id: None
        executor.get_usdc_balance = lambda: 100.0
        executor._measure_live_balance_change = lambda before, expect_increase=False: (before, 0.0)
        executor._sync_live_positions = lambda *args, **kwargs: None
        executor.estimate_entry_fill = lambda raw_book, amount: (SimpleNamespace(spent_usd=7.0, shares=7.0, avg_price=1.0), None)
        executor._reconcile_live_order_fill = PolymarketExecutor._reconcile_live_order_fill.__get__(executor, PolymarketExecutor)
        dedup_cache = SimpleNamespace(
            confirm=lambda *args, **kwargs: None,
            mark_seen=lambda *args, **kwargs: None,
            release=lambda *args, **kwargs: None,
        )
        event = SimpleNamespace(
            question="Will it happen?",
            trader_address="0xabc",
            price=0.5,
            raw_orderbook=None,
            token_id="token-1",
            trader_name="Trader",
            raw_trade=None,
            raw_market_metadata=None,
            snapshot=None,
            action="buy",
            timestamp=1_700_000_000,
            observed_at=1_700_000_001,
            poll_started_at=1_700_000_000,
            market_close_ts=1_700_010_000,
            metadata_fetched_at=1_700_000_000,
            orderbook_fetched_at=1_700_000_000,
            source_ts_raw="1700000000",
            shares=5.0,
            size_usd=2.5,
        )
        market_f = SimpleNamespace(
            execution_price=0.75,
            price_1h_ago=None,
            volume_7d_avg_usd=None,
            best_ask=0.5,
            best_bid=0.49,
            mid=0.495,
            volume_24h_usd=None,
            oi_usd=None,
            top_holder_pct=None,
            bid_depth_usd=None,
            ask_depth_usd=None,
            days_to_res=None,
        )
        captured: dict[str, float] = {}

        with patch("executor.log_trade", side_effect=lambda **kwargs: captured.update(kwargs) or 1), patch("executor.send_alert"):
            result = executor._execute_live(
                "trade-1",
                "market-1",
                "token-1",
                "yes",
                10.0,
                0.1,
                0.7,
                {"mode": "heuristic"},
                event,
                None,
                market_f,
                dedup_cache,
            )

        self.assertTrue(result.placed)
        self.assertAlmostEqual(result.dollar_size, 10.0, places=6)
        self.assertAlmostEqual(result.shares, 20.0, places=6)
        self.assertAlmostEqual(float(captured["actual_entry_price"]), 0.5, places=6)
        self.assertAlmostEqual(float(captured["actual_entry_shares"]), 20.0, places=6)
        self.assertAlmostEqual(float(captured["actual_entry_size_usd"]), 10.0, places=6)

    def test_validate_startup_fails_closed_on_invalid_live_risk_limit(self) -> None:
        valid_wallet = "0x1111111111111111111111111111111111111111"
        watched_wallet = "0x2222222222222222222222222222222222222222"
        with patch.dict(
            "os.environ",
            {
                "USE_REAL_MONEY": "true",
                "POLYGON_PRIVATE_KEY": "0xabc123",
                "POLYGON_WALLET_ADDRESS": valid_wallet,
                "MAX_MARKET_EXPOSURE_FRACTION": "abc",
            },
            clear=False,
        ), patch.object(main, "WATCHED_WALLETS", [watched_wallet]), patch("main._resolved_shadow_trade_count", return_value=999), patch("main.send_alert"):
            with self.assertRaises(RuntimeError) as ctx:
                main._validate_startup()
        self.assertIn("MAX_MARKET_EXPOSURE_FRACTION must be numeric", str(ctx.exception))

    def test_entry_risk_does_not_block_only_because_many_positions_are_open(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (20.0, {"other-market": 5.0}, {"0xother": 5.0})

        with patch("executor.use_real_money", return_value=True), patch("executor.max_total_open_exposure_fraction", return_value=0.60), patch("executor.max_market_exposure_fraction", return_value=0.20), patch("executor.max_trader_exposure_fraction", return_value=0.30):
            reason = executor.entry_risk_block_reason(
                market_id="market-1",
                trader_address="0xabc",
                proposed_size_usd=5.0,
                account_equity=100.0,
            )

        self.assertIsNone(reason)

    def test_entry_risk_does_not_hard_block_after_total_exposure_clip(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (680.0, {"other-market": 120.0}, {"0xother": 120.0})

        with patch("executor.use_real_money", return_value=True), patch(
            "executor.max_total_open_exposure_fraction", return_value=0.30
        ), patch("executor.max_market_exposure_fraction", return_value=0.20), patch(
            "executor.max_trader_exposure_fraction", return_value=0.20
        ):
            reason = executor.entry_risk_block_reason(
                market_id="market-1",
                trader_address="0xabc",
                proposed_size_usd=11.59,
                account_equity=2305.31,
            )

        self.assertIsNone(reason)

    def test_entry_risk_still_blocks_market_concentration(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (50.0, {"market-1": 15.0}, {"0xother": 5.0})

        with patch("executor.use_real_money", return_value=True), patch(
            "executor.max_market_exposure_fraction", return_value=0.20
        ), patch("executor.max_trader_exposure_fraction", return_value=0.30):
            reason = executor.entry_risk_block_reason(
                market_id="market-1",
                trader_address="0xabc",
                proposed_size_usd=10.5,
                account_equity=100.0,
            )

        self.assertIn("market exposure for market-1 would be $25.50", str(reason))

    def test_entry_risk_still_blocks_trader_concentration(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (50.0, {"other-market": 5.0}, {"0xabc": 25.0})

        with patch("executor.use_real_money", return_value=True), patch(
            "executor.max_market_exposure_fraction", return_value=0.40
        ), patch("executor.max_trader_exposure_fraction", return_value=0.30):
            reason = executor.entry_risk_block_reason(
                market_id="market-1",
                trader_address="0xabc",
                proposed_size_usd=6.0,
                account_equity=100.0,
            )

        self.assertIn("trader exposure for 0xabc would be $31.00", str(reason))

    def test_total_open_exposure_decision_clips_to_remaining_headroom(self) -> None:
        executor = object.__new__(PolymarketExecutor)
        executor._open_risk_snapshot = lambda **kwargs: (260.0, {}, {})

        with patch("executor.use_real_money", return_value=True), patch(
            "executor.max_total_open_exposure_fraction", return_value=0.30
        ):
            decision = executor.total_open_exposure_decision(
                proposed_size_usd=80.0,
                account_equity=1000.0,
            )

        self.assertTrue(decision.clipped)
        self.assertIsNone(decision.block_reason)
        self.assertEqual(decision.allowed_size_usd, 40.0)

    def test_apply_total_exposure_cap_to_sizing_rescales_effective_fraction(self) -> None:
        executor = Mock()
        executor.total_open_exposure_decision.return_value = TotalExposureDecision(
            allowed_size_usd=40.0,
            clipped=True,
        )

        sizing, clip_note = main._apply_total_exposure_cap_to_sizing(
            executor,
            {"dollar_size": 80.0, "kelly_f": 0.08, "reason": "ok"},
            bankroll=1000.0,
            account_equity=1000.0,
        )

        self.assertEqual(sizing["dollar_size"], 40.0)
        self.assertAlmostEqual(sizing["kelly_f"], 0.04, places=6)
        self.assertEqual(sizing["reason"], "ok")
        self.assertEqual(clip_note, "total exposure cap clipped size from $80.00 to $40.00")

    def test_apply_total_exposure_cap_to_sizing_blocks_when_headroom_falls_below_minimum_bet(self) -> None:
        executor = Mock()
        executor.total_open_exposure_decision.return_value = TotalExposureDecision(
            allowed_size_usd=0.75,
            clipped=True,
        )

        with patch("main.min_bet_usd", return_value=1.0):
            sizing, clip_note = main._apply_total_exposure_cap_to_sizing(
                executor,
                {"dollar_size": 10.0, "kelly_f": 0.01, "reason": "ok"},
                bankroll=1000.0,
                account_equity=1000.0,
            )

        self.assertEqual(sizing["dollar_size"], 0.0)
        self.assertEqual(sizing["kelly_f"], 0.0)
        self.assertIn("remaining total exposure headroom was $0.75", sizing["reason"])
        self.assertIsNone(clip_note)

    def test_trader_cache_refresh_rotates_batched_wallets(self) -> None:
        wallets = ["0x1", "0x2", "0x3"]
        calls: list[str] = []
        original_cursor = trader_scorer._refresh_cursor
        try:
            trader_scorer._refresh_cursor = 0
            with patch.object(trader_scorer, "TRADER_CACHE_REFRESH_BATCH_SIZE", 2), patch("trader_scorer._trader_cache_updated_at_map", return_value={}), patch("trader_scorer.get_trader_features", side_effect=lambda wallet, observed_size_usd, force_refresh=False: calls.append(wallet) or SimpleNamespace()):
                trader_scorer.refresh_trader_cache(wallets)
                trader_scorer.refresh_trader_cache(wallets)
        finally:
            trader_scorer._refresh_cursor = original_cursor

        self.assertEqual(calls[:2], ["0x1", "0x2"])
        self.assertEqual(calls[2:], ["0x3", "0x1"])

    def test_trader_remote_429_arms_backoff(self) -> None:
        response = httpx.Response(
            429,
            headers={"Retry-After": "30"},
            request=httpx.Request("GET", "https://data-api.polymarket.com/closed-positions"),
        )

        class FakeClient:
            def get(self, url, params=None):
                return response

        original_backoff = trader_scorer._remote_backoff_until
        try:
            trader_scorer._remote_backoff_until = 0.0
            with patch("time.time", return_value=1_000.0):
                payload, ok = trader_scorer._request_data_api_json(
                    FakeClient(),
                    "https://data-api.polymarket.com/closed-positions",
                    params={"user": "0xabc"},
                    failure_log="test failure",
                )
                active = trader_scorer._remote_backoff_active(1_000.0)
                remaining = trader_scorer._remote_backoff_remaining_seconds(1_000.0)
        finally:
            trader_scorer._remote_backoff_until = original_backoff

        self.assertIsNone(payload)
        self.assertFalse(ok)
        self.assertTrue(active)
        self.assertGreaterEqual(remaining, trader_scorer.REMOTE_BACKOFF_DEFAULT_S)

    def test_sync_belief_priors_expands_sql_contract_macros(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd, resolved_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "belief-1",
                        "market-1",
                        "Will it happen?",
                        "0xabc",
                        "yes",
                        "buy",
                        0.4,
                        10.0,
                        0.7,
                        0.1,
                        0,
                        0,
                        1_700_000_000,
                        0.4,
                        25.0,
                        10.0,
                        1.5,
                        1_700_000_100,
                    ),
                )
                conn.commit()
                conn.close()

                beliefs.invalidate_belief_cache()
                applied = beliefs.sync_belief_priors()

                self.assertEqual(applied, 1)

                conn = db.get_conn()
                update_count = conn.execute("SELECT COUNT(*) AS n FROM belief_updates").fetchone()["n"]
                prior_count = conn.execute("SELECT COUNT(*) AS n FROM belief_priors").fetchone()["n"]
                conn.close()

                self.assertEqual(update_count, 1)
                self.assertGreater(prior_count, 0)
            finally:
                db.DB_PATH = original_db_path

    def test_startup_validation_reports_bad_numeric_config_cleanly(self) -> None:
        with patch("main.WATCHED_WALLETS", ["0xabc"]), patch("main.min_confidence", side_effect=main.ConfigError("MIN_CONFIDENCE must be numeric, got 'abc'")):
            with self.assertRaisesRegex(RuntimeError, "MIN_CONFIDENCE must be numeric, got 'abc'"):
                main._validate_startup()

    def test_partial_bot_state_heartbeat_preserves_last_completed_poll(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = main.BOT_STATE_FILE
            try:
                main.BOT_STATE_FILE = Path(tmpdir) / "bot_state.json"
                with patch("main.use_real_money", return_value=False), patch("main.poll_interval", return_value=2.0), patch.object(main, "WATCHED_WALLETS", ["0xabc"]):
                    main._write_bot_state(
                        started_at=100,
                        last_poll_at=120,
                        last_poll_duration_s=8.5,
                        loop_in_progress=False,
                    )
                    main._write_bot_state(
                        last_activity_at=130,
                        last_loop_started_at=125,
                        loop_in_progress=True,
                    )

                payload = json.loads(main.BOT_STATE_FILE.read_text(encoding="utf-8"))
                self.assertEqual(payload["last_poll_at"], 120)
                self.assertEqual(payload["last_poll_duration_s"], 8.5)
                self.assertEqual(payload["last_activity_at"], 130)
                self.assertEqual(payload["last_loop_started_at"], 125)
                self.assertTrue(payload["loop_in_progress"])
            finally:
                main.BOT_STATE_FILE = original_state_file

    def test_log_runtime_ready_explains_quiet_console_and_runtime_files(self) -> None:
        tracker_stub = SimpleNamespace(wallets=["0xabc", "0xdef"])
        watchlist_stub = SimpleNamespace(
            state_fields=lambda: {
                "tracked_wallet_count": 2,
                "dropped_wallet_count": 1,
                "hot_wallet_count": 1,
                "warm_wallet_count": 1,
                "discovery_wallet_count": 0,
            }
        )

        with patch("main.poll_interval", return_value=5.0):
            with self.assertLogs("main", level="INFO") as captured:
                main._log_runtime_ready(tracker_stub, watchlist_stub)

        output = "\n".join(captured.output)
        self.assertIn("Startup complete. Polling 2 wallets every 5.0s", output)
        self.assertIn("Runtime files: db=", output)
        self.assertIn("Console output stays quiet between events.", output)

    def test_log_first_poll_summary_reports_initial_poll_completion(self) -> None:
        with self.assertLogs("main", level="INFO") as captured:
            main._log_first_poll_summary(
                elapsed=3.25,
                polled_wallet_count=4,
                event_count=0,
                bankroll=1000.0,
            )

        output = "\n".join(captured.output)
        self.assertIn("First poll completed in 3.25s: wallets=4 events=0 bankroll=$1000.00", output)

    def test_partial_exit_keeps_remaining_shadow_position_and_realized_pnl(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                buy_event = SimpleNamespace(
                    action="buy",
                    token_id="token-1",
                    timestamp=1_700_000_000,
                    trader_name="Trader",
                    observed_at=1_700_000_005,
                    poll_started_at=1_700_000_004,
                    market_close_ts=1_700_010_000,
                    metadata_fetched_at=1_700_000_004,
                    orderbook_fetched_at=1_700_000_004,
                    source_ts_raw="1700000000",
                    shares=100.0,
                    size_usd=50.0,
                    question="Will it happen?",
                    raw_trade=None,
                    raw_market_metadata=None,
                    raw_orderbook=None,
                    snapshot=None,
                    trader_address="0xabc",
                )
                row_id = log_trade(
                    trade_id="buy-1",
                    market_id="market-1",
                    question="Will it happen?",
                    trader_address="0xabc",
                    side="yes",
                    price=0.5,
                    signal_size_usd=50.0,
                    confidence=0.7,
                    kelly_f=0.1,
                    real_money=False,
                    order_id=None,
                    skipped=False,
                    skip_reason=None,
                    actual_entry_price=0.5,
                    actual_entry_shares=100.0,
                    actual_entry_size_usd=50.0,
                    event=buy_event,
                    signal={"mode": "heuristic"},
                )
                conn = db.get_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
                    ("market-1", "yes", 50.0, 0.5, "token-1", 1_700_000_000, 0),
                )
                conn.commit()
                entry_row = conn.execute("SELECT * FROM trade_log WHERE id=?", (row_id,)).fetchone()
                conn.close()

                cache = dedup.DedupeCache()
                cache.load_from_db(rebuild_shadow_positions=True)
                executor = object.__new__(PolymarketExecutor)
                shares, exit_notional, pnl = executor._finalize_exit(
                    entries=[dict(entry_row)],
                    position={"market_id": "market-1", "token_id": "token-1", "side": "yes"},
                    real_money=False,
                    exit_trade_id="sell-1",
                    exit_price=0.6,
                    exit_fraction=0.4,
                    exit_shares=40.0,
                    exit_notional=24.0,
                    exit_reason="partial exit",
                    exit_order_id=None,
                    market_id="market-1",
                    trader_address="0xabc",
                    dedup=cache,
                    refresh_position_from_trade_log=True,
                )

                self.assertAlmostEqual(shares, 40.0, places=6)
                self.assertAlmostEqual(exit_notional, 24.0, places=6)
                self.assertAlmostEqual(pnl, 4.0, places=2)

                conn = db.get_conn()
                updated = conn.execute(
                    """
                    SELECT remaining_entry_shares, remaining_entry_size_usd, realized_exit_shares,
                           realized_exit_size_usd, realized_exit_pnl_usd, partial_exit_count, shadow_pnl_usd
                    FROM trade_log
                    WHERE id=?
                    """,
                    (row_id,),
                ).fetchone()
                position = conn.execute(
                    "SELECT size_usd, avg_price FROM positions WHERE market_id=? AND token_id=? AND real_money=0",
                    ("market-1", "token-1"),
                ).fetchone()
                conn.close()

                self.assertAlmostEqual(float(updated["remaining_entry_shares"]), 60.0, places=6)
                self.assertAlmostEqual(float(updated["remaining_entry_size_usd"]), 30.0, places=6)
                self.assertAlmostEqual(float(updated["realized_exit_shares"]), 40.0, places=6)
                self.assertAlmostEqual(float(updated["realized_exit_size_usd"]), 24.0, places=6)
                self.assertAlmostEqual(float(updated["realized_exit_pnl_usd"]), 4.0, places=6)
                self.assertEqual(int(updated["partial_exit_count"]), 1)
                self.assertIsNone(updated["shadow_pnl_usd"])
                self.assertAlmostEqual(float(position["size_usd"]), 30.0, places=6)
                self.assertAlmostEqual(float(position["avg_price"]), 0.5, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_resolved_shadow_trade_count_uses_fill_aware_realized_rows_only(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("skip-1", "m0", "Skipped", "0x1", "yes", "buy", 0.5, 10.0, 0.6, 0.1, 0, 0, 10, 1.0),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("open-1", "m1", "Open", "0x2", "yes", "buy", 0.5, 10.0, 0.6, 0.1, 0, 0, 11, 0.5, 20.0, 10.0),
                )
                conn.execute(
                    f"""
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    ("resolved-1", "m2", "Resolved", "0x3", "yes", "buy", 0.5, 10.0, 0.6, 0.1, 0, 0, 12, 0.5, 20.0, 10.0, 3.5),
                )
                conn.commit()
                conn.close()

                self.assertEqual(main._resolved_shadow_trade_count(), 1)
            finally:
                db.DB_PATH = original_db_path

    def test_tracker_cursor_and_stale_checks(self) -> None:
        stale_event = tracker.TradeEvent(
            trade_id="t-old",
            market_id="m1",
            question="Question",
            side="yes",
            action="buy",
            price=0.5,
            shares=10.0,
            size_usd=5.0,
            token_id="token-1",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=100,
            close_time="",
        )
        duplicate_event = tracker.TradeEvent(
            trade_id="t-1",
            market_id="m1",
            question="Question",
            side="yes",
            action="buy",
            price=0.5,
            shares=10.0,
            size_usd=5.0,
            token_id="token-1",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=200,
            close_time="",
        )
        new_same_ts = tracker.TradeEvent(
            trade_id="t-2",
            market_id="m1",
            question="Question",
            side="yes",
            action="buy",
            price=0.5,
            shares=10.0,
            size_usd=5.0,
            token_id="token-1",
            trader_name="Trader",
            trader_address="0xabc",
            timestamp=200,
            close_time="",
        )
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        tracker_obj.wallet_cursors = {"0xabc": tracker.WalletCursor(last_source_ts=200, last_trade_ids={"t-1"})}

        self.assertFalse(tracker_obj._is_new_for_wallet("0xabc", duplicate_event))
        self.assertTrue(tracker_obj._is_new_for_wallet("0xabc", new_same_ts))
        with patch("tracker.max_source_trade_age_seconds", return_value=30):
            self.assertTrue(tracker_obj._is_stale_event(stale_event, poll_started_at=200))
            self.assertFalse(tracker_obj._is_stale_event(new_same_ts, poll_started_at=220))

    def test_tracker_rejects_missing_timestamp_and_missing_price(self) -> None:
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        tracker_obj.client = object()
        tracker_obj.get_market_metadata = lambda _condition_id: (
            {
                "question": "Will it happen?",
                "endDate": "2030-01-01T00:00:00Z",
                "outcomes": '["Yes","No"]',
                "clobTokenIds": '["token-yes","token-no"]',
            },
            123,
        )

        raw_missing_timestamp = {
            "conditionId": "market-1",
            "side": "BUY",
            "asset": "token-yes",
            "size": 10,
            "price": 0.55,
        }
        raw_missing_price = {
            "conditionId": "market-1",
            "side": "BUY",
            "asset": "token-yes",
            "size": 10,
            "timestamp": 1_700_000_000,
        }

        with patch("tracker.hydrate_observed_identity", return_value="Trader"):
            self.assertIsNone(tracker_obj._parse_raw_trade(raw_missing_timestamp, "0xabc", 1_700_000_010))
            self.assertIsNone(tracker_obj._parse_raw_trade(raw_missing_price, "0xabc", 1_700_000_010))

    def test_tracker_resolves_outcome_from_metadata_token_map(self) -> None:
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        tracker_obj.client = object()
        tracker_obj.get_market_metadata = lambda _condition_id: (
            {
                "question": "Will it happen?",
                "endDate": "2030-01-01T00:00:00Z",
                "outcomes": '["Yes","No"]',
                "clobTokenIds": '["token-yes","token-no"]',
            },
            456,
        )
        raw = {
            "conditionId": "market-1",
            "side": "BUY",
            "asset": "token-no",
            "size": 12,
            "price": 0.42,
            "timestamp": 1_700_000_000,
            "title": "Will it happen?",
        }

        with patch("tracker.hydrate_observed_identity", return_value="Trader"):
            event = tracker_obj._parse_raw_trade(raw, "0xabc", 1_700_000_010)

        self.assertIsNotNone(event)
        self.assertEqual(event.side, "no")
        self.assertEqual(event.price, 0.42)
        self.assertEqual(event.timestamp, 1_700_000_000)

    def test_tracker_batches_wallet_trade_fetches_in_parallel(self) -> None:
        tracker_obj = tracker.PolymarketTracker(["0x1", "0x2", "0x3", "0x4"])
        tracker_obj.get_wallet_trades = lambda address, limit=50: time.sleep(0.12) or []
        try:
            started = time.perf_counter()
            result = tracker_obj._fetch_wallet_trades_batch(["0x1", "0x2", "0x3", "0x4"])
            elapsed = time.perf_counter() - started
        finally:
            tracker_obj.close()

        self.assertEqual(result, {"0x1": [], "0x2": [], "0x3": [], "0x4": []})
        self.assertLess(elapsed, 0.35)

    def test_tracker_poll_filters_old_rows_before_metadata_and_skips_price_history_fetch(self) -> None:
        tracker_obj = tracker.PolymarketTracker(["0xabc"])
        now_ts = int(time.time())
        tracker_obj.wallet_cursors = {
            "0xabc": tracker.WalletCursor(last_source_ts=now_ts - 90, last_trade_ids={"t-seen"})
        }
        tracker_obj._flush_dirty_wallet_cursors = lambda: None

        old_raw = {
            "id": "t-old",
            "conditionId": "market-old",
            "side": "BUY",
            "asset": "token-old",
            "size": 10,
            "price": 0.41,
            "timestamp": now_ts - 120,
            "title": "Old market",
        }
        new_raw = {
            "id": "t-new",
            "conditionId": "market-new",
            "side": "BUY",
            "asset": "token-yes",
            "size": 10,
            "price": 0.55,
            "timestamp": now_ts - 30,
            "title": "New market",
        }

        metadata_calls: list[tuple[str, ...]] = []
        orderbook_calls: list[tuple[str, ...]] = []
        price_history_calls: list[str] = []

        tracker_obj._fetch_wallet_trades_batch = lambda wallets, limit=50: {"0xabc": [old_raw, new_raw]}
        tracker_obj._fetch_market_metadata_batch = (
            lambda condition_ids: metadata_calls.append(tuple(condition_ids)) or {
                "market-new": (
                    {
                        "conditionId": "market-new",
                        "question": "Will it happen?",
                        "endDate": "2030-01-01T00:00:00Z",
                        "outcomes": '["Yes","No"]',
                        "clobTokenIds": '["token-yes","token-no"]',
                    },
                    123,
                )
            }
        )
        tracker_obj._fetch_orderbook_snapshots_batch = (
            lambda token_ids: orderbook_calls.append(tuple(token_ids)) or {
                "token-yes": (
                    {
                        "best_bid": 0.54,
                        "best_ask": 0.56,
                        "mid": 0.55,
                        "bid_depth_usd": 500.0,
                        "ask_depth_usd": 500.0,
                    },
                    {"bids": [], "asks": []},
                    456,
                )
            }
        )
        tracker_obj.get_price_history = lambda token_id, interval="1h": price_history_calls.append(token_id) or []

        try:
            with patch("tracker.hydrate_observed_identity", return_value="Trader"):
                events = tracker_obj.poll(["0xabc"], trade_limit=50)
        finally:
            tracker_obj.close()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trade_id, "t-new")
        self.assertEqual(metadata_calls, [("market-new",)])
        self.assertEqual(orderbook_calls, [("token-yes",)])
        self.assertEqual(price_history_calls, [])

    def test_tracker_market_metadata_cache_reuses_recent_fetch(self) -> None:
        tracker_obj = object.__new__(tracker.PolymarketTracker)
        calls: list[str] = []

        def fake_request_json(url: str, **kwargs):
            calls.append(url)
            return ([{"conditionId": "market-1", "question": "Question"}], True)

        tracker_obj._market_metadata_cache = {}
        tracker_obj._request_json = fake_request_json

        first = tracker_obj.get_market_metadata("market-1")
        second = tracker_obj.get_market_metadata("market-1")

        self.assertEqual(first[0]["question"], "Question")
        self.assertEqual(second[0]["question"], "Question")
        self.assertEqual(len(calls), 1)

    def test_hydrate_observed_identity_skips_network_when_disabled(self) -> None:
        wallet = "0x1111111111111111111111111111111111111111"
        with patch("identity_cache.lookup_username", return_value=None), patch(
            "identity_cache.resolve_username_for_wallet"
        ) as resolve_mock:
            resolved = identity_cache.hydrate_observed_identity(
                wallet,
                "",
                allow_network=False,
            )

        self.assertEqual(resolved, "")
        resolve_mock.assert_not_called()

    def test_market_scorer_handles_missing_optional_features(self) -> None:
        snapshot = {
            "best_bid": 0.49,
            "best_ask": 0.51,
            "mid": 0.5,
            "volume_24h_usd": 1000.0,
            "oi_usd": 2500.0,
            "bid_depth_usd": 800.0,
            "ask_depth_usd": 700.0,
            "top_holder_pct": None,
            "price_history_1h": [],
        }
        features = build_market_features(snapshot, "2030-01-01T00:00:00Z", order_size_usd=25.0, execution_price=0.5)
        self.assertIsNotNone(features)
        self.assertIsNone(features.price_1h_ago)
        scorer = MarketScorer()
        score = scorer.score(features)
        self.assertGreaterEqual(score["score"], 0.0)
        self.assertLessEqual(score["score"], 1.0)

    def test_market_scorer_rejects_missing_or_bad_close_time(self) -> None:
        snapshot = {
            "best_bid": 0.49,
            "best_ask": 0.51,
            "mid": 0.5,
            "bid_depth_usd": 800.0,
            "ask_depth_usd": 700.0,
        }
        self.assertIsNone(build_market_features(snapshot, "", order_size_usd=25.0, execution_price=0.5))
        self.assertIsNone(
            build_market_features(snapshot, "not-a-timestamp", order_size_usd=25.0, execution_price=0.5)
        )

    def test_trader_score_win_rate_shrinks_small_samples(self) -> None:
        low_evidence = TraderScorer._score_win_rate(0.9, 2)
        high_evidence = TraderScorer._score_win_rate(0.9, 200)

        self.assertGreater(low_evidence, 0.5)
        self.assertLess(low_evidence, 0.9)
        self.assertGreater(high_evidence, low_evidence)


if __name__ == "__main__":
    unittest.main()
