from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from kelly_watcher.integrations.alerter import send_telegram_message
from kelly_watcher.config import (
    dashboard_api_port,
    dashboard_url,
    telegram_balance_cache_max_age_seconds,
    telegram_bot_token,
    telegram_chat_id,
)
from kelly_watcher.runtime.performance_preview import render_tracker_preview_message
from kelly_watcher.tools.rank_copytrade_wallets import fetch_leaderboard
from kelly_watcher.runtime_paths import BOT_STATE_FILE, MANUAL_RETRAIN_REQUEST_FILE, TELEGRAM_STATE_FILE

logger = logging.getLogger(__name__)
_COMMAND_POLL_INTERVAL_S = 2.0
_COMMAND_POLL_MAX_BACKOFF_S = 300.0
_COMMAND_POLL_WARNING_INTERVAL_S = 60.0
_COMMAND_UPDATE_LIMIT = 5
_LEADERBOARD_PERIODS = (
    ("DAY", "24h"),
    ("WEEK", "7d"),
    ("MONTH", "30d"),
)
_LEADERBOARD_ROWS_PER_PERIOD = 5
_next_command_poll_at = 0.0
_command_poll_failure_count = 0
_last_command_poll_warning_at = 0.0
_COMMAND_CLIENT_LOCK = threading.Lock()
_COMMAND_CLIENT: httpx.Client | None = None


def _telegram_command_client() -> httpx.Client:
    global _COMMAND_CLIENT
    with _COMMAND_CLIENT_LOCK:
        if _COMMAND_CLIENT is None or bool(getattr(_COMMAND_CLIENT, "is_closed", False)):
            _COMMAND_CLIENT = httpx.Client(
                timeout=5.0,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            )
        return _COMMAND_CLIENT


def close_telegram_command_client() -> None:
    _drop_telegram_command_client()
    _reset_command_poll_backoff()


def _drop_telegram_command_client() -> None:
    global _COMMAND_CLIENT
    with _COMMAND_CLIENT_LOCK:
        client = _COMMAND_CLIENT
        _COMMAND_CLIENT = None
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _reset_command_poll_backoff() -> None:
    global _command_poll_failure_count, _last_command_poll_warning_at
    _command_poll_failure_count = 0
    _last_command_poll_warning_at = 0.0


def _record_command_poll_success() -> None:
    _reset_command_poll_backoff()


def _record_command_poll_failure(exc: Exception, *, now_ts: float) -> None:
    global _command_poll_failure_count, _last_command_poll_warning_at, _next_command_poll_at
    _command_poll_failure_count += 1
    delay_s = min(
        _COMMAND_POLL_MAX_BACKOFF_S,
        _COMMAND_POLL_INTERVAL_S * (2 ** min(_command_poll_failure_count - 1, 8)),
    )
    _next_command_poll_at = now_ts + delay_s

    should_log = (
        _command_poll_failure_count == 1
        or (now_ts - _last_command_poll_warning_at) >= _COMMAND_POLL_WARNING_INTERVAL_S
    )
    if should_log:
        _last_command_poll_warning_at = now_ts
        logger.warning(
            "Telegram command poll failed; backing off for %.0fs after %s consecutive failure(s): %s",
            delay_s,
            _command_poll_failure_count,
            exc,
        )
    _drop_telegram_command_client()


def _load_telegram_state() -> dict[str, Any]:
    try:
        payload = json.loads(TELEGRAM_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _persist_telegram_state(state: dict[str, Any]) -> None:
    TELEGRAM_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = TELEGRAM_STATE_FILE.with_name(f"{TELEGRAM_STATE_FILE.name}.{os.getpid()}.tmp")
    temp_path.write_text(f"{json.dumps(state, indent=2)}\n", encoding="utf-8")
    temp_path.replace(TELEGRAM_STATE_FILE)


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


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _optional_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _format_optional_usd(value: Any, *, signed: bool = False) -> str:
    numeric = _optional_float(value)
    if numeric is None:
        return "-"
    return _format_signed_usd(numeric) if signed else _format_usd(numeric)


def _format_optional_pct(value: Any) -> str:
    numeric = _optional_float(value)
    if numeric is None:
        return "-"
    return f"{numeric * 100:.2f}%"


def _format_optional_ratio(value: Any) -> str:
    numeric = _optional_float(value)
    if numeric is None:
        return "-"
    return "inf" if math.isinf(numeric) else f"{numeric:.2f}"


def _seconds_ago(value: Any, *, now_ts: int | None = None) -> str:
    timestamp = _optional_int(value)
    if timestamp <= 0:
        return "-"
    now = int(now_ts if now_ts is not None else time.time())
    elapsed = max(now - timestamp, 0)
    if elapsed < 60:
        return f"{elapsed}s ago"
    if elapsed < 3600:
        return f"{elapsed // 60}m ago"
    return f"{elapsed // 3600}h {(elapsed % 3600) // 60}m ago"


def render_cached_tracker_preview_message() -> str | None:
    bot_state = _load_bot_state()
    if not bot_state:
        return None

    mode = "live" if str(bot_state.get("mode") or "").strip().lower() == "live" else "shadow"
    snapshot_known = bool(bot_state.get("shadow_snapshot_state_known")) or bool(
        bot_state.get("routed_shadow_state_known")
    )
    bankroll = _optional_float(bot_state.get("bankroll_usd"))
    if not snapshot_known and bankroll is None:
        return None

    now_ts = int(time.time())
    freshness_anchor = max(_optional_int(bot_state.get("last_poll_at")), _optional_int(bot_state.get("last_activity_at")))
    max_age = telegram_balance_cache_max_age_seconds()
    if max_age > 0 and freshness_anchor > 0 and (now_ts - freshness_anchor) > max_age:
        freshness = f"cached state is stale ({_seconds_ago(freshness_anchor, now_ts=now_ts)})"
    else:
        freshness = f"cached state updated {_seconds_ago(freshness_anchor, now_ts=now_ts)}"

    balance_label = "Current balance" if mode == "live" else "Estimated shadow bankroll"
    title = "Live tracker performance" if mode == "live" else "Shadow tracker performance"
    total_pnl = bot_state.get("shadow_snapshot_total_pnl_usd")
    return_pct = bot_state.get("shadow_snapshot_return_pct")
    resolved = _optional_int(
        bot_state.get("shadow_snapshot_resolved")
        or bot_state.get("resolved_shadow_trade_count")
        or bot_state.get("shadow_history_all_time_resolved")
    )
    routed_resolved = _optional_int(bot_state.get("routed_shadow_routed_resolved"))
    routed_legacy_resolved = _optional_int(bot_state.get("routed_shadow_legacy_resolved"))
    routed_total_resolved = _optional_int(bot_state.get("routed_shadow_total_resolved"))
    loop_started_at = _optional_int(bot_state.get("last_loop_started_at"))
    loop_in_progress = bool(bot_state.get("loop_in_progress"))
    poll_duration = _optional_float(bot_state.get("last_poll_duration_s"))
    routed_gate = str(bot_state.get("routed_shadow_block_reason") or "").strip()
    data_warning = str(
        bot_state.get("shadow_snapshot_block_reason")
        or bot_state.get("routed_shadow_data_warning")
        or ""
    ).strip()

    lines = [
        title,
        "Shadow/paper estimates only; /balance does not read a live wallet balance."
        if mode == "shadow"
        else "Live balance and equity are shown where available.",
        freshness,
        f"Total P&L: {_format_optional_usd(total_pnl, signed=True)}",
        f"Return %: {_format_optional_pct(return_pct)}",
        f"{balance_label}: {_format_optional_usd(bankroll)}",
        f"Profit factor: {_format_optional_ratio(bot_state.get('shadow_snapshot_profit_factor'))}",
        f"Expectancy: {_format_optional_usd(bot_state.get('shadow_snapshot_expectancy_usd'), signed=True)}",
        f"Resolved: {resolved}",
        (
            "Poll: "
            f"last {_seconds_ago(bot_state.get('last_poll_at'), now_ts=now_ts)}"
            + (f", duration {poll_duration:.1f}s" if poll_duration is not None else "")
        ),
        (
            "Source queue: "
            f"{_optional_int(bot_state.get('source_events_pending'))} pending, "
            f"{_optional_int(bot_state.get('source_events_processing'))} processing, "
            f"{_optional_int(bot_state.get('source_events_failed'))} retry"
        ),
    ]
    if loop_in_progress and loop_started_at > 0:
        lines.append(f"Loop: in progress for {_seconds_ago(loop_started_at, now_ts=now_ts).replace(' ago', '')}")
    if mode == "shadow":
        lines.extend(
            [
                "Routed fixed-segment shadow only",
                (
                    f"Routed coverage: {_format_optional_pct(bot_state.get('routed_shadow_coverage_pct'))} "
                    f"({routed_resolved} routed resolved, {routed_legacy_resolved} legacy/unassigned resolved excluded)"
                ),
                f"Routed total resolved: {routed_total_resolved}",
                f"Routed P&L: {_format_optional_usd(bot_state.get('routed_shadow_total_pnl_usd'), signed=True)}",
                f"Routed P&L / bankroll: {_format_optional_pct(bot_state.get('routed_shadow_return_pct'))}",
                f"Routed profit factor: {_format_optional_ratio(bot_state.get('routed_shadow_profit_factor'))}",
                f"Routed expectancy: {_format_optional_usd(bot_state.get('routed_shadow_expectancy_usd'), signed=True)}",
            ]
        )
    if routed_gate:
        lines.append(f"Routed gate: {routed_gate}")
    elif data_warning:
        lines.append(f"State note: {data_warning}")
    return "\n".join(line for line in lines if line)


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


def _tailscale_magicdns_name() -> str:
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    self_payload = payload.get("Self") if isinstance(payload, dict) else None
    if not isinstance(self_payload, dict):
        return ""
    dns_name = str(self_payload.get("DNSName") or "").strip().rstrip(".")
    if dns_name:
        return dns_name
    host_name = str(self_payload.get("HostName") or "").strip()
    magicdns_suffix = str(payload.get("MagicDNSSuffix") or "").strip().strip(".")
    if host_name and magicdns_suffix:
        return f"{host_name}.{magicdns_suffix}"
    return host_name


def _tailscale_dashboard_url() -> str:
    dns_name = _tailscale_magicdns_name()
    if not dns_name:
        return ""
    return f"http://{dns_name}:{dashboard_api_port()}"


def _dashboard_link_message() -> str:
    url = dashboard_url() or _tailscale_dashboard_url()
    if not url:
        return (
            "dashboard link unavailable. set DASHBOARD_WEB_URL or make sure tailscale is running "
            "so the bot can detect its MagicDNS name."
        )
    return f"dashboard: {url}"


def _build_command_reply(command: str) -> str | None:
    if command == "/balance":
        # Keep /balance responsive under DB contention by replying from the
        # cached bot-state snapshot. Fall back to the full DB preview only when
        # no cached runtime state exists yet.
        return render_cached_tracker_preview_message() or render_tracker_preview_message()
    if command == "/link":
        return _dashboard_link_message()
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
        "limit": _COMMAND_UPDATE_LIMIT,
        "allowed_updates": json.dumps(["message"]),
    }

    try:
        response = _telegram_command_client().get(f"https://api.telegram.org/bot{token}/getUpdates", params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        _record_command_poll_failure(exc, now_ts=now_ts)
        return 0
    _record_command_poll_success()

    updates = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(updates, list) or not updates:
        return 0

    handled = 0
    for update in updates:
        if not isinstance(update, dict):
            continue

        update_id = int(update.get("update_id") or 0)
        try:
            message = update.get("message")
            if not isinstance(message, dict):
                continue

            command = _normalize_message_command(str(message.get("text") or ""))
            if command not in {"/balance", "/link", "/train", "/leaderboard", "/leaderboards"}:
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
        finally:
            if update_id > last_update_id:
                last_update_id = update_id
                state["last_update_id"] = update_id
                _persist_telegram_state(state)

    return handled
