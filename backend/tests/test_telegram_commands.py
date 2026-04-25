from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import kelly_watcher.data.db as db
import kelly_watcher.runtime.performance_preview as performance_preview
import kelly_watcher.integrations.telegram_runtime as telegram_runtime


class _HttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _HttpClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.get_calls: list[tuple[str, dict | None]] = []

    def __enter__(self) -> _HttpClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def get(self, url: str, *, params=None) -> _HttpResponse:
        self.get_calls.append((url, params))
        return _HttpResponse(self.payload)


class _FailingHttpClient:
    def __init__(self, calls: list[tuple[str, dict | None]]) -> None:
        self.calls = calls
        self.is_closed = False

    def get(self, url: str, *, params=None) -> _HttpResponse:
        self.calls.append((url, params))
        raise OSError("[Errno 8] nodename nor servname provided, or not known")

    def close(self) -> None:
        self.is_closed = True


class _ContextClient:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class TelegramCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        telegram_runtime.close_telegram_command_client()

    def tearDown(self) -> None:
        telegram_runtime.close_telegram_command_client()

    def test_tracker_preview_summary_matches_performance_box_logic(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_bot_state_file = performance_preview.BOT_STATE_FILE
            try:
                tmp_path = Path(tmpdir)
                db.DB_PATH = tmp_path / "data" / "trading.db"
                performance_preview.BOT_STATE_FILE = tmp_path / "data" / "bot_state.json"
                db.init_db()
                performance_preview.BOT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                performance_preview.BOT_STATE_FILE.write_text(
                    json.dumps({"mode": "shadow", "bankroll_usd": 123.456}),
                    encoding="utf-8",
                )

                conn = db.get_conn()
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS trade_log_manual_edits (
                      trade_log_id INTEGER PRIMARY KEY,
                      entry_price  REAL,
                      shares       REAL,
                      size_usd     REAL,
                      status       TEXT,
                      updated_at   INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS position_manual_edits (
                      market_id   TEXT NOT NULL,
                      token_id    TEXT NOT NULL DEFAULT '',
                      side        TEXT NOT NULL,
                      real_money  INTEGER NOT NULL DEFAULT 0,
                      entry_price REAL,
                      shares      REAL,
                      size_usd    REAL,
                      status      TEXT,
                      updated_at  INTEGER NOT NULL,
                      PRIMARY KEY (market_id, token_id, side, real_money)
                    );
                    """
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at, market_close_ts,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        remaining_entry_shares, remaining_entry_size_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "open-shadow",
                        "market-open",
                        "Open market",
                        "0xopen",
                        "yes",
                        "",
                        "buy",
                        0.6,
                        10.0,
                        0.6,
                        0.1,
                        0,
                        0,
                        1_700_000_000,
                        4_102_444_800,
                        0.5,
                        20.0,
                        10.0,
                        20.0,
                        10.0,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, token_id,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at, resolved_at, outcome,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        shadow_pnl_usd
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "resolved-shadow",
                        "market-win",
                        "Resolved market",
                        "0xwin",
                        "yes",
                        "",
                        "buy",
                        0.7,
                        6.0,
                        0.8,
                        0.1,
                        0,
                        0,
                        1_700_000_100,
                        1_700_000_300,
                        1,
                        0.6,
                        10.0,
                        6.0,
                        4.0,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log_manual_edits (
                        trade_log_id, entry_price, shares, size_usd, status, updated_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (1, 0.6, 20.0, 12.0, "open", 1_700_000_050),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log_manual_edits (
                        trade_log_id, entry_price, shares, size_usd, status, updated_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (2, 0.7, 10.0, 7.0, "win", 1_700_000_350),
                )
                conn.commit()
                conn.close()

                summary = performance_preview.compute_tracker_preview_summary(now_ts=1_700_000_400)
                message = performance_preview.render_tracker_preview_message(summary)

                self.assertEqual(summary.title, "Shadow tracker")
                self.assertEqual(summary.mode, "shadow")
                self.assertEqual(summary.acted, 2)
                self.assertEqual(summary.resolved, 1)
                self.assertEqual(summary.wins, 1)
                self.assertAlmostEqual(summary.total_pnl, 3.0)
                self.assertAlmostEqual(summary.current_balance or 0.0, 123.456)
                self.assertAlmostEqual(summary.current_equity or 0.0, 135.456)
                self.assertAlmostEqual(summary.return_pct or 0.0, 0.0226, places=4)
                self.assertAlmostEqual(summary.win_rate, 1.0)
                self.assertTrue(summary.profit_factor and summary.profit_factor > 1000)
                self.assertAlmostEqual(summary.expectancy_usd or 0.0, 3.0)
                self.assertAlmostEqual(summary.expectancy_pct or 0.0, 0.4286, places=4)
                self.assertAlmostEqual(summary.exposure_pct or 0.0, 0.0886, places=4)
                self.assertAlmostEqual(summary.max_drawdown_pct or 0.0, 0.0)
                self.assertAlmostEqual(summary.avg_confidence or 0.0, 0.7)
                self.assertAlmostEqual(summary.avg_total or 0.0, 9.5)
                self.assertIn("Shadow tracker performance", message)
                self.assertIn("Shadow/paper estimates only; /balance does not read a live wallet balance.", message)
                self.assertIn("Total P&L: +$3.000", message)
                self.assertIn("Return %: 2.260%", message)
                self.assertIn("Estimated shadow bankroll: $123.456", message)
                self.assertIn("Estimated paper equity: $135.456", message)
                self.assertIn("Profit factor: inf", message)
                self.assertIn("Expectancy: +$3.000 / 42.860%", message)
                self.assertIn("Exposure: 8.860%", message)
                self.assertIn("Max drawdown: 0.000%", message)
                self.assertIn("Avg total: +$9.500", message)
            finally:
                db.DB_PATH = original_db_path
                performance_preview.BOT_STATE_FILE = original_bot_state_file

    def test_service_telegram_commands_replies_to_balance_variants(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = telegram_runtime.TELEGRAM_STATE_FILE
            original_bot_state_file = telegram_runtime.BOT_STATE_FILE
            original_retrain_request_file = telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE
            original_next_poll_at = telegram_runtime._next_command_poll_at
            try:
                tmp_path = Path(tmpdir)
                telegram_runtime.TELEGRAM_STATE_FILE = tmp_path / "telegram_state.json"
                telegram_runtime.BOT_STATE_FILE = tmp_path / "bot_state.json"
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = tmp_path / "manual_retrain_request.json"
                telegram_runtime._next_command_poll_at = 0.0
                client = _HttpClient(
                    {
                        "ok": True,
                        "result": [
                            {
                                "update_id": 1001,
                                "message": {
                                    "message_id": 41,
                                    "chat": {"id": 123},
                                    "text": "/balance?",
                                },
                            },
                            {
                                "update_id": 1002,
                                "message": {
                                    "message_id": 42,
                                    "chat": {"id": 999},
                                    "text": "/balance@kellywatcherbot?",
                                },
                            },
                            {
                                "update_id": 1003,
                                "message": {
                                    "message_id": 43,
                                    "chat": {"id": 123},
                                    "text": "/balance@kellywatcherbot",
                                },
                            },
                        ],
                    }
                )

                with patch("kelly_watcher.integrations.telegram_runtime.telegram_bot_token", return_value="token"), patch(
                    "kelly_watcher.integrations.telegram_runtime.telegram_chat_id", return_value="123"
                ), patch("kelly_watcher.integrations.telegram_runtime.httpx.Client", return_value=client), patch(
                    "kelly_watcher.integrations.telegram_runtime.render_tracker_preview_message", return_value="reply"
                ), patch("kelly_watcher.integrations.telegram_runtime.send_telegram_message", return_value=True) as send_message:
                    handled = telegram_runtime.service_telegram_commands()

                self.assertEqual(handled, 2)
                self.assertEqual(send_message.call_count, 2)
                send_message.assert_any_call("reply", chat_id="123", reply_to_message_id=41)
                send_message.assert_any_call("reply", chat_id="123", reply_to_message_id=43)
                saved_state = json.loads(telegram_runtime.TELEGRAM_STATE_FILE.read_text(encoding="utf-8"))
                self.assertEqual(saved_state["last_update_id"], 1003)
                self.assertEqual(client.get_calls[0][1]["offset"], 1)
            finally:
                telegram_runtime.TELEGRAM_STATE_FILE = original_state_file
                telegram_runtime.BOT_STATE_FILE = original_bot_state_file
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = original_retrain_request_file
                telegram_runtime._next_command_poll_at = original_next_poll_at

    def test_service_telegram_commands_backs_off_after_transport_failure(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = telegram_runtime.TELEGRAM_STATE_FILE
            original_next_poll_at = telegram_runtime._next_command_poll_at
            original_failure_count = telegram_runtime._command_poll_failure_count
            original_last_warning_at = telegram_runtime._last_command_poll_warning_at
            try:
                telegram_runtime.TELEGRAM_STATE_FILE = Path(tmpdir) / "telegram_state.json"
                telegram_runtime._next_command_poll_at = 0.0
                telegram_runtime._command_poll_failure_count = 0
                telegram_runtime._last_command_poll_warning_at = 0.0
                calls: list[tuple[str, dict | None]] = []

                with patch("kelly_watcher.integrations.telegram_runtime.telegram_bot_token", return_value="token"), patch(
                    "kelly_watcher.integrations.telegram_runtime.telegram_chat_id", return_value="123"
                ), patch(
                    "kelly_watcher.integrations.telegram_runtime.httpx.Client",
                    side_effect=lambda *args, **kwargs: _FailingHttpClient(calls),
                ), patch(
                    "kelly_watcher.integrations.telegram_runtime.time.time", return_value=1_000.0
                ), self.assertLogs("kelly_watcher.integrations.telegram_runtime", level="WARNING") as logs:
                    handled = telegram_runtime.service_telegram_commands()

                self.assertEqual(handled, 0)
                self.assertEqual(len(calls), 1)
                self.assertEqual(telegram_runtime._command_poll_failure_count, 1)
                self.assertEqual(telegram_runtime._next_command_poll_at, 1_002.0)
                self.assertIn("backing off for 2s", "\n".join(logs.output))

                with patch("kelly_watcher.integrations.telegram_runtime.telegram_bot_token", return_value="token"), patch(
                    "kelly_watcher.integrations.telegram_runtime.telegram_chat_id", return_value="123"
                ), patch("kelly_watcher.integrations.telegram_runtime.time.time", return_value=1_001.0):
                    handled = telegram_runtime.service_telegram_commands()

                self.assertEqual(handled, 0)
                self.assertEqual(len(calls), 1)
            finally:
                telegram_runtime.TELEGRAM_STATE_FILE = original_state_file
                telegram_runtime._next_command_poll_at = original_next_poll_at
                telegram_runtime._command_poll_failure_count = original_failure_count
                telegram_runtime._last_command_poll_warning_at = original_last_warning_at

    def test_balance_command_uses_cached_bot_state_without_db_preview(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = telegram_runtime.TELEGRAM_STATE_FILE
            original_bot_state_file = telegram_runtime.BOT_STATE_FILE
            original_retrain_request_file = telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE
            original_next_poll_at = telegram_runtime._next_command_poll_at
            try:
                tmp_path = Path(tmpdir)
                telegram_runtime.TELEGRAM_STATE_FILE = tmp_path / "telegram_state.json"
                telegram_runtime.BOT_STATE_FILE = tmp_path / "bot_state.json"
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = tmp_path / "manual_retrain_request.json"
                telegram_runtime._next_command_poll_at = 0.0
                telegram_runtime.BOT_STATE_FILE.write_text(
                    json.dumps(
                        {
                            "mode": "shadow",
                            "bankroll_usd": 2546.27,
                            "last_poll_at": 1_700_000_390,
                            "last_activity_at": 1_700_000_395,
                            "last_poll_duration_s": 3.112,
                            "loop_in_progress": True,
                            "last_loop_started_at": 1_700_000_380,
                            "shadow_snapshot_state_known": True,
                            "shadow_snapshot_total_pnl_usd": -459.027,
                            "shadow_snapshot_return_pct": -0.153,
                            "shadow_snapshot_profit_factor": 0.916,
                            "shadow_snapshot_expectancy_usd": -0.681,
                            "shadow_snapshot_resolved": 674,
                            "routed_shadow_state_known": True,
                            "routed_shadow_coverage_pct": 0.0549,
                            "routed_shadow_routed_resolved": 37,
                            "routed_shadow_legacy_resolved": 637,
                            "routed_shadow_total_resolved": 674,
                            "routed_shadow_total_pnl_usd": -116.602,
                            "routed_shadow_return_pct": -0.0389,
                            "routed_shadow_profit_factor": 0.488,
                            "routed_shadow_expectancy_usd": -3.151,
                        }
                    ),
                    encoding="utf-8",
                )
                client = _HttpClient(
                    {
                        "ok": True,
                        "result": [
                            {
                                "update_id": 1501,
                                "message": {
                                    "message_id": 45,
                                    "chat": {"id": 123},
                                    "text": "/balance",
                                },
                            }
                        ],
                    }
                )

                with patch("kelly_watcher.integrations.telegram_runtime.telegram_bot_token", return_value="token"), patch(
                    "kelly_watcher.integrations.telegram_runtime.telegram_chat_id", return_value="123"
                ), patch("kelly_watcher.integrations.telegram_runtime.httpx.Client", return_value=client), patch(
                    "kelly_watcher.integrations.telegram_runtime.render_tracker_preview_message",
                    side_effect=AssertionError("full DB preview should not run for cached balance"),
                ), patch(
                    "kelly_watcher.integrations.telegram_runtime.telegram_balance_cache_max_age_seconds",
                    return_value=900,
                ), patch("kelly_watcher.integrations.telegram_runtime.time.time", return_value=1_700_000_400), patch(
                    "kelly_watcher.integrations.telegram_runtime.send_telegram_message", return_value=True
                ) as send_message:
                    handled = telegram_runtime.service_telegram_commands()

                self.assertEqual(handled, 1)
                reply = send_message.call_args.args[0]
                self.assertIn("Estimated shadow bankroll: $2546.27", reply)
                self.assertIn("Total P&L: -$459.03", reply)
                self.assertIn("Poll: last 10s ago, duration 3.1s", reply)
                self.assertIn("Loop: in progress for 20s", reply)
            finally:
                telegram_runtime.TELEGRAM_STATE_FILE = original_state_file
                telegram_runtime.BOT_STATE_FILE = original_bot_state_file
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = original_retrain_request_file
                telegram_runtime._next_command_poll_at = original_next_poll_at

    def test_service_telegram_commands_replies_to_train_and_writes_request(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = telegram_runtime.TELEGRAM_STATE_FILE
            original_bot_state_file = telegram_runtime.BOT_STATE_FILE
            original_retrain_request_file = telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE
            original_next_poll_at = telegram_runtime._next_command_poll_at
            try:
                tmp_path = Path(tmpdir)
                telegram_runtime.TELEGRAM_STATE_FILE = tmp_path / "telegram_state.json"
                telegram_runtime.BOT_STATE_FILE = tmp_path / "bot_state.json"
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = tmp_path / "manual_retrain_request.json"
                telegram_runtime._next_command_poll_at = 0.0
                telegram_runtime.BOT_STATE_FILE.write_text(
                    json.dumps(
                        {
                            "started_at": 1_700_000_000,
                            "last_activity_at": 1_700_000_390,
                            "poll_interval": 2.0,
                            "retrain_in_progress": False,
                        }
                    ),
                    encoding="utf-8",
                )
                client = _HttpClient(
                    {
                        "ok": True,
                        "result": [
                            {
                                "update_id": 2001,
                                "message": {
                                    "message_id": 55,
                                    "chat": {"id": 123},
                                    "text": "/train@kellywatcherbot?",
                                },
                            }
                        ],
                    }
                )

                with patch("kelly_watcher.integrations.telegram_runtime.telegram_bot_token", return_value="token"), patch(
                    "kelly_watcher.integrations.telegram_runtime.telegram_chat_id", return_value="123"
                ), patch("kelly_watcher.integrations.telegram_runtime.httpx.Client", return_value=client), patch(
                    "kelly_watcher.integrations.telegram_runtime.send_telegram_message", return_value=True
                ) as send_message, patch("kelly_watcher.integrations.telegram_runtime.time.time", return_value=1_700_000_400):
                    handled = telegram_runtime.service_telegram_commands()

                self.assertEqual(handled, 1)
                send_message.assert_called_once_with(
                    "Manual retrain requested. The bot should pick it up within about a second.",
                    chat_id="123",
                    reply_to_message_id=55,
                )
                request_payload = json.loads(
                    telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE.read_text(encoding="utf-8")
                )
                self.assertEqual(request_payload["action"], "manual_retrain")
                self.assertEqual(request_payload["source"], "telegram")
                self.assertEqual(request_payload["requested_at"], 1_700_000_400)
                self.assertTrue(request_payload["request_id"].startswith("telegram-1700000400-"))
            finally:
                telegram_runtime.TELEGRAM_STATE_FILE = original_state_file
                telegram_runtime.BOT_STATE_FILE = original_bot_state_file
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = original_retrain_request_file
                telegram_runtime._next_command_poll_at = original_next_poll_at

    def test_service_telegram_commands_replies_to_link(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = telegram_runtime.TELEGRAM_STATE_FILE
            original_bot_state_file = telegram_runtime.BOT_STATE_FILE
            original_retrain_request_file = telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE
            original_next_poll_at = telegram_runtime._next_command_poll_at
            try:
                tmp_path = Path(tmpdir)
                telegram_runtime.TELEGRAM_STATE_FILE = tmp_path / "telegram_state.json"
                telegram_runtime.BOT_STATE_FILE = tmp_path / "bot_state.json"
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = tmp_path / "manual_retrain_request.json"
                telegram_runtime._next_command_poll_at = 0.0
                client = _HttpClient(
                    {
                        "ok": True,
                        "result": [
                            {
                                "update_id": 2501,
                                "message": {
                                    "message_id": 63,
                                    "chat": {"id": 123},
                                    "text": "/link@kellywatcherbot?",
                                },
                            }
                        ],
                    }
                )

                with patch("kelly_watcher.integrations.telegram_runtime.telegram_bot_token", return_value="token"), patch(
                    "kelly_watcher.integrations.telegram_runtime.telegram_chat_id", return_value="123"
                ), patch(
                    "kelly_watcher.integrations.telegram_runtime.dashboard_url",
                    return_value="https://windows-box.tailnet-name.ts.net:8765",
                ), patch("kelly_watcher.integrations.telegram_runtime.httpx.Client", return_value=client), patch(
                    "kelly_watcher.integrations.telegram_runtime.send_telegram_message", return_value=True
                ) as send_message:
                    handled = telegram_runtime.service_telegram_commands()

                self.assertEqual(handled, 1)
                send_message.assert_called_once_with(
                    "dashboard: https://windows-box.tailnet-name.ts.net:8765",
                    chat_id="123",
                    reply_to_message_id=63,
                )
            finally:
                telegram_runtime.TELEGRAM_STATE_FILE = original_state_file
                telegram_runtime.BOT_STATE_FILE = original_bot_state_file
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = original_retrain_request_file
                telegram_runtime._next_command_poll_at = original_next_poll_at

    def test_dashboard_link_message_falls_back_to_tailscale_magicdns_name(self) -> None:
        tailscale_payload = {
            "Self": {
                "DNSName": "windows-box.tailnet-name.ts.net.",
            }
        }

        with patch("kelly_watcher.integrations.telegram_runtime.dashboard_url", return_value=""), patch(
            "kelly_watcher.integrations.telegram_runtime.dashboard_api_port", return_value=8765
        ), patch(
            "kelly_watcher.integrations.telegram_runtime.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(tailscale_payload)),
        ):
            message = telegram_runtime._dashboard_link_message()

        self.assertEqual(message, "dashboard: http://windows-box.tailnet-name.ts.net:8765")

    def test_service_telegram_commands_replies_to_leaderboards(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_state_file = telegram_runtime.TELEGRAM_STATE_FILE
            original_bot_state_file = telegram_runtime.BOT_STATE_FILE
            original_retrain_request_file = telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE
            original_next_poll_at = telegram_runtime._next_command_poll_at
            try:
                tmp_path = Path(tmpdir)
                telegram_runtime.TELEGRAM_STATE_FILE = tmp_path / "telegram_state.json"
                telegram_runtime.BOT_STATE_FILE = tmp_path / "bot_state.json"
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = tmp_path / "manual_retrain_request.json"
                telegram_runtime._next_command_poll_at = 0.0
                client = _HttpClient(
                    {
                        "ok": True,
                        "result": [
                            {
                                "update_id": 3001,
                                "message": {
                                    "message_id": 77,
                                    "chat": {"id": 123},
                                    "text": "/leaderboards@kellywatcherbot?",
                                },
                            }
                        ],
                    }
                )

                with patch("kelly_watcher.integrations.telegram_runtime.telegram_bot_token", return_value="token"), patch(
                    "kelly_watcher.integrations.telegram_runtime.telegram_chat_id", return_value="123"
                ), patch("kelly_watcher.integrations.telegram_runtime.httpx.Client", return_value=client), patch(
                    "kelly_watcher.integrations.telegram_runtime.render_leaderboards_message", return_value="leaders"
                ), patch("kelly_watcher.integrations.telegram_runtime.send_telegram_message", return_value=True) as send_message:
                    handled = telegram_runtime.service_telegram_commands()

                self.assertEqual(handled, 1)
                send_message.assert_called_once_with(
                    "leaders",
                    chat_id="123",
                    reply_to_message_id=77,
                )
            finally:
                telegram_runtime.TELEGRAM_STATE_FILE = original_state_file
                telegram_runtime.BOT_STATE_FILE = original_bot_state_file
                telegram_runtime.MANUAL_RETRAIN_REQUEST_FILE = original_retrain_request_file
                telegram_runtime._next_command_poll_at = original_next_poll_at

    def test_render_leaderboards_message_formats_periods(self) -> None:
        wallet = "0x1234567890abcdef1234567890abcdef12345678"

        def _fake_fetch(_client, *, time_period, **_kwargs):
            if time_period == "DAY":
                return [
                    SimpleNamespace(
                        rank=1,
                        username="alpha",
                        address=wallet,
                        pnl_usd=12.34,
                        volume_usd=56.78,
                    )
                ]
            return []

        with patch("kelly_watcher.integrations.telegram_runtime.httpx.Client", return_value=_ContextClient()), patch(
            "kelly_watcher.integrations.telegram_runtime.fetch_leaderboard",
            side_effect=_fake_fetch,
        ):
            message = telegram_runtime.render_leaderboards_message()

        self.assertEqual(
            message,
            "polymarket leaderboards\n"
            "24h:\n"
            "1. alpha (0x123456...345678) | pnl +$12.34 | vol $56.78\n"
            "7d:\n"
            "- unavailable\n"
            "30d:\n"
            "- unavailable",
        )

    def test_render_tracker_preview_message_includes_integrity_warning(self) -> None:
        summary = performance_preview.PerformancePreviewSummary(
            title="Shadow tracker",
            mode="shadow",
            total_pnl=1.0,
            current_balance=101.0,
            current_equity=102.0,
            return_pct=0.01,
            win_rate=1.0,
            profit_factor=float("inf"),
            expectancy_usd=1.0,
            expectancy_pct=0.1,
            exposure_pct=0.02,
            max_drawdown_pct=0.0,
            resolved=1,
            avg_confidence=0.7,
            avg_total=10.0,
            acted=1,
            wins=1,
            data_warning="WARNING: SQLite integrity check failed; performance numbers may be unreliable",
            routed_history_status="mixed",
            routed_resolved=1,
            routed_legacy_resolved=2,
            routed_total_pnl=0.5,
            routed_return_pct=0.005,
            routed_profit_factor=1.2,
            routed_expectancy_usd=0.5,
            routed_expectancy_pct=0.05,
            routed_coverage_pct=1 / 3,
        )

        message = performance_preview.render_tracker_preview_message(summary)

        self.assertIn("WARNING: SQLite integrity check failed; performance numbers may be unreliable", message)
        self.assertIn(
            "Preview blocked: shadow performance numbers are not trustworthy until Recover DB or Restart Shadow restores a clean ledger.",
            message,
        )
        self.assertNotIn("Estimated shadow bankroll: $101.000", message)
        self.assertNotIn("Total P&L: +$1.000", message)
        self.assertIn("Routed fixed-segment shadow only", message)
        self.assertIn("Routed coverage: 33.333% (1 routed resolved, 2 legacy/unassigned resolved excluded)", message)
        self.assertIn("Routed history: mixed", message)


if __name__ == "__main__":
    unittest.main()
