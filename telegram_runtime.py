from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from alerter import send_telegram_message
from config import telegram_bot_token, telegram_chat_id
from performance_preview import render_tracker_preview_message
from rank_copytrade_wallets import fetch_leaderboard
from runtime_paths import BOT_STATE_FILE, MANUAL_RETRAIN_REQUEST_FILE, TELEGRAM_STATE_FILE

logger = logging.getLogger(__name__)
_COMMAND_POLL_INTERVAL_S = 2.0
_LEADERBOARD_PERIODS = (
    ("DAY", "24h"),
    ("WEEK", "7d"),
    ("MONTH", "30d"),
)
_LEADERBOARD_ROWS_PER_PERIOD = 5
_next_command_poll_at = 0.0


def _load_telegram_state() -> dict[str, Any]:
    try:
        payload = json.loads(TELEGRAM_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _persist_telegram_state(state: dict[str, Any]) -> None:
    TELEGRAM_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TELEGRAM_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _load_bot_state() -> dict[str, Any]:
    try:
        payload = json.loads(BOT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_message_command(text: str) -> str:
    head = str(text or "").strip().split(maxsplit=1)[0].lower()
    if not head.startswith("/"):
        return ""
    head = head.rstrip("?")
    if "@" in head:
        head = head.split("@", 1)[0]
    return head


def _request_file_is_recent(path: Path, max_age_seconds: float) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) <= max(max_age_seconds, 0.0)
    except Exception:
        return False


def _request_manual_retrain(*, source: str = "telegram") -> str:
    bot_state = _load_bot_state()
    now_ts = int(time.time())
    started_at = int(bot_state.get("started_at") or 0)
    last_activity_at = int(bot_state.get("last_activity_at") or 0)
    heartbeat_window = max(float(bot_state.get("poll_interval") or 1.0) * 3.0, 30.0)

    if started_at <= 0 or last_activity_at <= 0:
        return "manual retrain is unavailable because bot state is missing. start the bot first."
    if (now_ts - last_activity_at) > heartbeat_window:
        return "manual retrain is unavailable because the bot state looks stale. restart or refresh the bot first."
    if bool(bot_state.get("retrain_in_progress")):
        return "manual retrain is already running."
    if _request_file_is_recent(MANUAL_RETRAIN_REQUEST_FILE, 30.0):
        return "manual retrain already requested. waiting for the bot to pick it up."

    payload = {
        "action": "manual_retrain",
        "source": str(source or "telegram").strip().lower() or "telegram",
        "request_id": f"telegram-{now_ts}-{os.getpid()}",
        "requested_at": now_ts,
    }
    try:
        MANUAL_RETRAIN_REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = MANUAL_RETRAIN_REQUEST_FILE.with_name(f"{MANUAL_RETRAIN_REQUEST_FILE.name}.{os.getpid()}.tmp")
        temp_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
        temp_path.replace(MANUAL_RETRAIN_REQUEST_FILE)
        return "Manual retrain requested. The bot should pick it up within about a second."
    except Exception as exc:
        logger.warning("Failed to persist manual retrain request: %s", exc)
        return f"Failed to request manual retrain: {exc}"


def _short_wallet(wallet: str) -> str:
    text = str(wallet or "").strip()
    if text.lower().startswith("0x") and len(text) > 16:
        return f"{text[:8]}...{text[-6:]}"
    return text


def _format_signed_usd(value: float) -> str:
    amount = abs(float(value))
    if value > 0:
        return f"+${amount:.2f}"
    if value < 0:
        return f"-${amount:.2f}"
    return f"${amount:.2f}"


def _format_usd(value: float) -> str:
    return f"${float(value):.2f}"


def _leaderboard_entry_line(entry: Any, *, fallback_rank: int) -> str:
    rank = int(getattr(entry, "rank", 0) or fallback_rank)
    username = str(getattr(entry, "username", "") or "").strip()
    wallet = str(getattr(entry, "address", "") or "").strip().lower()
    wallet_label = _short_wallet(wallet)
    if username and username != "-":
        display = f"{username} ({wallet_label})" if wallet_label else username
    else:
        display = wallet_label or "unknown wallet"
    return (
        f"{rank}. {display} | pnl {_format_signed_usd(float(getattr(entry, 'pnl_usd', 0.0) or 0.0))}"
        f" | vol {_format_usd(float(getattr(entry, 'volume_usd', 0.0) or 0.0))}"
    )


def render_leaderboards_message() -> str:
    lines = ["polymarket leaderboards"]
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            for time_period, label in _LEADERBOARD_PERIODS:
                entries = fetch_leaderboard(
                    client,
                    category="OVERALL",
                    time_period=time_period,
                    order_by="PNL",
                    per_page=_LEADERBOARD_ROWS_PER_PERIOD,
                    pages=1,
                )[:_LEADERBOARD_ROWS_PER_PERIOD]
                lines.append(f"{label}:")
                if not entries:
                    lines.append("- unavailable")
                    continue
                lines.extend(
                    _leaderboard_entry_line(entry, fallback_rank=index)
                    for index, entry in enumerate(entries, start=1)
                )
    except Exception as exc:
        logger.warning("Failed to render leaderboards message: %s", exc)
        return "leaderboards are unavailable right now."

    return "\n".join(lines)


def _build_command_reply(command: str) -> str | None:
    if command == "/balance":
        return render_tracker_preview_message()
    if command == "/train":
        return _request_manual_retrain(source="telegram")
    if command in {"/leaderboard", "/leaderboards"}:
        return render_leaderboards_message()
    return None


def service_telegram_commands() -> int:
    global _next_command_poll_at

    now_ts = time.time()
    if now_ts < _next_command_poll_at:
        return 0
    _next_command_poll_at = now_ts + _COMMAND_POLL_INTERVAL_S

    token = telegram_bot_token()
    allowed_chat_id = str(telegram_chat_id() or "").strip()
    if not token or not allowed_chat_id:
        return 0

    state = _load_telegram_state()
    last_update_id = int(state.get("last_update_id") or 0)
    params: dict[str, Any] = {
        "timeout": 0,
        "offset": last_update_id + 1,
        "allowed_updates": json.dumps(["message"]),
    }

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("Telegram command poll failed: %s", exc)
        return 0

    updates = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(updates, list) or not updates:
        return 0

    handled = 0
    max_update_id = last_update_id
    for update in updates:
        if not isinstance(update, dict):
            continue

        update_id = int(update.get("update_id") or 0)
        max_update_id = max(max_update_id, update_id)
        message = update.get("message")
        if not isinstance(message, dict):
            continue

        command = _normalize_message_command(str(message.get("text") or ""))
        if command not in {"/balance", "/train", "/leaderboard", "/leaderboards"}:
            continue

        chat = message.get("chat")
        message_chat_id = str(chat.get("id") if isinstance(chat, dict) else "").strip()
        if message_chat_id != allowed_chat_id:
            continue

        reply_to_message_id = int(message.get("message_id") or 0) or None
        reply = _build_command_reply(command)
        if not reply:
            continue
        send_telegram_message(reply, chat_id=message_chat_id, reply_to_message_id=reply_to_message_id)
        handled += 1

    if max_update_id > last_update_id:
        state["last_update_id"] = max_update_id
        _persist_telegram_state(state)

    return handled
