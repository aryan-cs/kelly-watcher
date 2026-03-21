from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import performance_preview
import telegram_runtime


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


class TelegramCommandTest(unittest.TestCase):
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

                self.assertEqual(summary.title, "Tracker")
                self.assertEqual(summary.mode, "shadow")
                self.assertEqual(summary.acted, 2)
                self.assertEqual(summary.resolved, 1)
                self.assertEqual(summary.wins, 1)
                self.assertAlmostEqual(summary.total_pnl, 3.0)
                self.assertAlmostEqual(summary.current_balance or 0.0, 123.456)
                self.assertAlmostEqual(summary.win_rate, 1.0)
                self.assertAlmostEqual(summary.avg_confidence or 0.0, 0.7)
                self.assertAlmostEqual(summary.avg_total or 0.0, 9.5)
                self.assertIn("Tracker performance", message)
                self.assertIn("Total P&L: +$3.000", message)
                self.assertIn("Current balance: $123.456", message)
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

                with patch("telegram_runtime.telegram_bot_token", return_value="token"), patch(
                    "telegram_runtime.telegram_chat_id", return_value="123"
                ), patch("telegram_runtime.httpx.Client", return_value=client), patch(
                    "telegram_runtime.render_tracker_preview_message", return_value="reply"
                ), patch("telegram_runtime.send_telegram_message", return_value=True) as send_message:
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

                with patch("telegram_runtime.telegram_bot_token", return_value="token"), patch(
                    "telegram_runtime.telegram_chat_id", return_value="123"
                ), patch("telegram_runtime.httpx.Client", return_value=client), patch(
                    "telegram_runtime.send_telegram_message", return_value=True
                ) as send_message, patch("telegram_runtime.time.time", return_value=1_700_000_400):
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


if __name__ == "__main__":
    unittest.main()
