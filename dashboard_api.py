from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from config import use_real_money
from db import DB_PATH, get_conn
from env_profile import LEGACY_ENV_PATH, active_env_flag, active_env_profile, env_path_for_profile
from kelly_watcher.shadow_reset import preferred_python_executable, runtime_env
from runtime_paths import (
    BOT_STATE_FILE,
    DATA_DIR,
    EVENT_FILE,
    IDENTITY_CACHE_PATH,
    LOG_DIR,
    MANUAL_RETRAIN_REQUEST_FILE,
    MANUAL_TRADE_REQUEST_FILE,
    REPO_ROOT,
)

logger = logging.getLogger(__name__)

ENV_PROFILE = active_env_profile()
ENV_PATH = env_path_for_profile(ENV_PROFILE)
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
IDENTITY_FILE = IDENTITY_CACHE_PATH
RESTART_SHADOW_SCRIPT = REPO_ROOT / "restart_shadow.py"
SHADOW_RESTART_LOG = LOG_DIR / "shadow_restart.out"
SHADOW_RESTART_DELAY_SECONDS = 0.25

SAFE_ENV_KEYS = {
    "WATCHED_WALLETS",
    "USE_REAL_MONEY",
    "POLL_INTERVAL_SECONDS",
    "MAX_MARKET_HORIZON",
    "HOT_WALLET_COUNT",
    "WARM_WALLET_COUNT",
    "WALLET_INACTIVITY_LIMIT",
    "WALLET_SLOW_DROP_MAX_TRACKING_AGE",
    "WALLET_PERFORMANCE_DROP_MIN_TRADES",
    "WALLET_PERFORMANCE_DROP_MAX_WIN_RATE",
    "WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN",
    "WALLET_QUALITY_SIZE_MIN_MULTIPLIER",
    "WALLET_QUALITY_SIZE_MAX_MULTIPLIER",
    "MIN_CONFIDENCE",
    "MIN_BET_USD",
    "MAX_BET_FRACTION",
    "SHADOW_BANKROLL_USD",
    "MAX_DAILY_LOSS_PCT",
    "RETRAIN_BASE_CADENCE",
    "RETRAIN_HOUR_LOCAL",
    "RETRAIN_EARLY_CHECK_INTERVAL",
    "RETRAIN_MIN_NEW_LABELS",
    "RETRAIN_MIN_SAMPLES",
    "WALLET_UNCOPYABLE_PENALTY_MIN_BUYS",
    "WALLET_UNCOPYABLE_PENALTY_WEIGHT",
}
SECRET_KEY_RE = re.compile(r"(KEY|TOKEN|PRIVATE|SECRET|PASSWORD)", re.IGNORECASE)
VALID_ENV_KEY_RE = re.compile(r"^[A-Z0-9_]+$")
READ_ONLY_SQL_RE = re.compile(r"^\s*(SELECT|WITH|PRAGMA)\b", re.IGNORECASE)
EVENT_TYPES = {"incoming", "signal"}
BEST_WALLET_DROP_PROTECTION_LIMIT = 5
EDITABLE_POSITION_STATUSES = {"open", "waiting", "win", "lose", "exit"}
RESOLVED_SHADOW_ENTRY_WHERE = """
real_money=0
AND skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
AND shadow_pnl_usd IS NOT NULL
"""

_env_lock = threading.Lock()
_request_lock = threading.Lock()


def _api_host() -> str:
    return str(os.getenv("DASHBOARD_API_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"


def _api_port() -> int:
    raw = str(os.getenv("DASHBOARD_API_PORT", "8765") or "8765").strip()
    try:
        return int(raw)
    except ValueError:
        return 8765


def _api_token() -> str | None:
    token = str(os.getenv("DASHBOARD_API_TOKEN", "") or "").strip()
    return token or None


def _source_env_path() -> Path:
    if ENV_PATH.exists():
        return ENV_PATH
    if ENV_PROFILE == "dev" and LEGACY_ENV_PATH.exists():
        return LEGACY_ENV_PATH
    return ENV_EXAMPLE_PATH


def _read_env_items() -> list[tuple[str, str]]:
    path = _source_env_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    items: list[tuple[str, str]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        items.append((key.strip(), value.strip()))
    return items


def _read_safe_env_values() -> dict[str, str]:
    safe_values: dict[str, str] = {}
    for key, value in _read_env_items():
        if key in SAFE_ENV_KEYS:
            safe_values[key] = value
    return safe_values


def _config_snapshot() -> dict[str, Any]:
    safe_values = _read_safe_env_values()
    watched_wallets = [
        wallet.strip().lower()
        for wallet in str(safe_values.get("WATCHED_WALLETS", "") or "").split(",")
        if wallet.strip()
    ]
    rows: list[dict[str, str]] = []
    for key, value in _read_env_items():
        if key == "WATCHED_WALLETS":
            continue
        redacted = "************" if SECRET_KEY_RE.search(key) else (value or "unset")
        rows.append({"key": key, "value": redacted})
    return {
        "safe_values": safe_values,
        "watched_wallets": watched_wallets,
        "rows": rows,
    }


def _write_env_value(key: str, value: str) -> None:
    if not VALID_ENV_KEY_RE.match(key):
        raise ValueError(f"Invalid config key: {key}")

    with _env_lock:
        source_path = _source_env_path()
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

        updated: list[str] = []
        found = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"{key}="):
                updated.append(f"{key}={value}")
                found = True
            else:
                updated.append(line)

        if not found:
            if updated and updated[-1] != "":
                updated.append("")
            updated.append(f"{key}={value}")

        ENV_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _request_is_recent(path: Path, max_age_seconds: int) -> bool:
    try:
        age_seconds = (time.time() - path.stat().st_mtime)
    except OSError:
        return False
    return age_seconds <= max_age_seconds


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
    temp_path.replace(path)


def _bot_state_snapshot() -> dict[str, Any]:
    return _read_json_dict(BOT_STATE_FILE)


def _heartbeat_window_seconds(bot_state: dict[str, Any]) -> int:
    try:
        poll_interval = float(bot_state.get("poll_interval") or 1)
    except (TypeError, ValueError):
        poll_interval = 1
    return int(max(poll_interval * 3, 30))


def _manual_retrain_response() -> dict[str, Any]:
    bot_state = _bot_state_snapshot()
    now = int(time.time())
    started_at = int(bot_state.get("started_at") or 0)
    last_activity_at = int(bot_state.get("last_activity_at") or 0)

    if started_at <= 0 or last_activity_at <= 0:
        return {
            "ok": False,
            "message": "Manual retrain is unavailable because bot state is missing. Start the bot first.",
        }

    if (now - last_activity_at) > _heartbeat_window_seconds(bot_state):
        return {
            "ok": False,
            "message": "Manual retrain is unavailable because the bot state looks stale. Restart or refresh the bot first.",
        }

    if bool(bot_state.get("retrain_in_progress")):
        return {"ok": False, "message": "A retrain is already running."}

    if _request_is_recent(MANUAL_RETRAIN_REQUEST_FILE, 30):
        return {
            "ok": True,
            "message": "Manual retrain already requested. Waiting for the bot to pick it up.",
        }

    payload = {
        "action": "manual_retrain",
        "source": "dashboard_api",
        "request_id": f"dashboard-api-{now}-{os.getpid()}",
        "requested_at": now,
    }

    try:
        with _request_lock:
            _write_atomic_json(MANUAL_RETRAIN_REQUEST_FILE, payload)
    except OSError as exc:
        return {"ok": False, "message": f"Failed to request manual retrain: {exc}"}

    return {
        "ok": True,
        "message": "Manual retrain requested. The running bot should pick it up within about a second.",
    }


def _normalize_manual_trade_action(raw_action: Any) -> str | None:
    action = str(raw_action or "").strip().lower()
    if action in {"buy_more", "cash_out"}:
        return action
    return None


def _manual_trade_response(raw_input: dict[str, Any]) -> dict[str, Any]:
    action = _normalize_manual_trade_action(raw_input.get("action"))
    market_id = str(raw_input.get("marketId") or raw_input.get("market_id") or "").strip()
    token_id = str(raw_input.get("tokenId") or raw_input.get("token_id") or "").strip()
    side = str(raw_input.get("side") or "").strip().lower()
    question = str(raw_input.get("question") or "").strip()
    trader_address = str(raw_input.get("traderAddress") or raw_input.get("trader_address") or "").strip().lower()
    amount_usd_raw = raw_input.get("amountUsd", raw_input.get("amount_usd"))
    amount_usd = float(amount_usd_raw) if amount_usd_raw is not None else None
    bot_state = _bot_state_snapshot()
    now = int(time.time())
    started_at = int(bot_state.get("started_at") or 0)
    last_activity_at = int(bot_state.get("last_activity_at") or 0)

    if not action:
        return {"ok": False, "message": "Manual trade request is missing a supported action."}
    if not market_id:
        return {"ok": False, "message": "Manual trade request is missing a market id."}
    if not token_id:
        return {"ok": False, "message": "Manual trade request is missing a token id."}
    if not side:
        return {"ok": False, "message": "Manual trade request is missing a side."}
    if action == "buy_more" and (amount_usd is None or not float(amount_usd) > 0):
        return {"ok": False, "message": "Buy more requires a positive USD amount."}
    if started_at <= 0 or last_activity_at <= 0:
        return {
            "ok": False,
            "message": "Manual trade actions are unavailable because bot state is missing. Start the bot first.",
        }
    if (now - last_activity_at) > _heartbeat_window_seconds(bot_state):
        return {
            "ok": False,
            "message": "Manual trade actions are unavailable because the bot state looks stale. Restart or refresh the bot first.",
        }
    if _request_is_recent(MANUAL_TRADE_REQUEST_FILE, 15):
        return {
            "ok": True,
            "message": "A manual trade request is already pending. Waiting for the bot to pick it up.",
        }

    payload = {
        "action": action,
        "source": "dashboard_api",
        "request_id": f"dashboard-api-{action}-{now}-{os.getpid()}",
        "requested_at": now,
        "market_id": market_id,
        "token_id": token_id,
        "side": side,
        "question": question or None,
        "trader_address": trader_address or None,
        "amount_usd": round(float(amount_usd), 6) if action == "buy_more" and amount_usd is not None else None,
    }

    try:
        with _request_lock:
            _write_atomic_json(MANUAL_TRADE_REQUEST_FILE, payload)
    except OSError as exc:
        return {"ok": False, "message": f"Failed to request manual trade: {exc}"}

    return {
        "ok": True,
        "message": (
            f"Manual buy request queued for ${float(amount_usd):.2f}."
            if action == "buy_more"
            else "Manual cash-out request queued."
        ),
    }


def _is_placeholder_username(username: str, wallet: str = "") -> bool:
    display = str(username or "").strip()
    if not display:
        return True
    normalized_wallet = str(wallet or "").strip().lower()
    normalized_username = display.lower()
    if normalized_wallet and normalized_username == normalized_wallet:
        return True
    if normalized_wallet and normalized_username.startswith(f"{normalized_wallet}-"):
        return normalized_username[len(normalized_wallet) + 1 :].isdigit()
    return False


def _identity_lookup() -> dict[str, str]:
    payload = _read_json_dict(IDENTITY_FILE)
    wallets = payload.get("wallets")
    if not isinstance(wallets, dict):
        return {}

    lookup: dict[str, str] = {}
    for wallet, entry in wallets.items():
        normalized_wallet = str(wallet or "").strip().lower()
        username = ""
        if isinstance(entry, dict):
            username = str(entry.get("username") or "").strip()
        if not normalized_wallet or _is_placeholder_username(username, normalized_wallet):
            continue
        lookup[normalized_wallet] = username
    return lookup


def _event_identity(event: dict[str, Any]) -> str:
    return f"{event.get('type', '')}|{event.get('trade_id', '')}|{event.get('ts', '')}"


def _recent_events(max_events: int) -> list[dict[str, Any]]:
    try:
        lines = deque(EVENT_FILE.read_text(encoding="utf-8").splitlines(), maxlen=max_events * 4)
    except OSError:
        return []

    identities = _identity_lookup()
    parsed: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip().lower()
        if event_type not in EVENT_TYPES:
            continue
        wallet = str(event.get("trader") or "").strip().lower()
        username = str(event.get("username") or "").strip()
        if wallet and not username:
            event["username"] = identities.get(wallet, "")
        parsed.append(event)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in parsed:
        key = _event_identity(event)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped[-max_events:]


def _query_rows(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    if not READ_ONLY_SQL_RE.match(sql or ""):
        raise ValueError("Only read-only SQL queries are allowed.")

    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _protected_best_wallets(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        f"""
        SELECT
          LOWER(trader_address) AS trader_address,
          ROUND(SUM(CASE WHEN {RESOLVED_SHADOW_ENTRY_WHERE} THEN COALESCE(shadow_pnl_usd, 0) ELSE 0 END), 3) AS pnl
        FROM trade_log
        GROUP BY LOWER(trader_address)
        HAVING SUM(CASE WHEN {RESOLVED_SHADOW_ENTRY_WHERE} THEN 1 ELSE 0 END) > 0
        ORDER BY pnl DESC, trader_address ASC
        LIMIT {BEST_WALLET_DROP_PROTECTION_LIMIT}
        """
    ).fetchall()
    return {
        str(row["trader_address"] or "").strip().lower()
        for row in rows
        if str(row["trader_address"] or "").strip() and float(row["pnl"] or 0) > 0
    }


def _reactivate_wallet(wallet_address: str) -> dict[str, Any]:
    wallet = wallet_address.strip().lower()
    if not wallet:
        return {"ok": False, "message": "Missing wallet address."}

    now_ts = int(time.time())
    conn = get_conn()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO wallet_watch_state (
                  wallet_address,
                  status,
                  status_reason,
                  dropped_at,
                  reactivated_at,
                  tracking_started_at,
                  updated_at
                ) VALUES (?, 'active', NULL, NULL, ?, ?, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                  status='active',
                  status_reason=NULL,
                  dropped_at=NULL,
                  reactivated_at=excluded.reactivated_at,
                  tracking_started_at=excluded.tracking_started_at,
                  updated_at=excluded.updated_at
                """,
                (wallet, now_ts, now_ts, now_ts),
            )
    finally:
        conn.close()
    return {"ok": True, "message": "Wallet reactivated."}


def _drop_wallet(wallet_address: str, reason: str = "manual dashboard drop") -> dict[str, Any]:
    wallet = wallet_address.strip().lower()
    normalized_reason = reason.strip() or "manual dashboard drop"
    if not wallet:
        return {"ok": False, "message": "Missing wallet address."}

    now_ts = int(time.time())
    conn = get_conn()
    try:
        if wallet in _protected_best_wallets(conn):
            return {"ok": False, "message": "This wallet is protected because it is currently one of the best shadow performers."}

        cursor_row = conn.execute(
            "SELECT last_source_ts FROM wallet_cursors WHERE wallet_address=?",
            (wallet,),
        ).fetchone()
        last_source_ts = int(cursor_row["last_source_ts"] or 0) if cursor_row else 0

        with conn:
            conn.execute(
                """
                INSERT INTO wallet_watch_state (
                  wallet_address,
                  status,
                  status_reason,
                  dropped_at,
                  last_source_ts_at_status,
                  updated_at
                ) VALUES (?, 'dropped', ?, ?, ?, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                  status='dropped',
                  status_reason=excluded.status_reason,
                  dropped_at=excluded.dropped_at,
                  last_source_ts_at_status=excluded.last_source_ts_at_status,
                  updated_at=excluded.updated_at
                """,
                (wallet, normalized_reason, now_ts, last_source_ts, now_ts),
            )
    finally:
        conn.close()
    return {"ok": True, "message": "Wallet dropped."}


def _ensure_position_edit_tables(conn: sqlite3.Connection) -> None:
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


def _normalize_position_status(raw: Any) -> str:
    normalized = str(raw or "").strip().lower()
    if normalized not in EDITABLE_POSITION_STATUSES:
        raise ValueError(f"Unsupported position status: {raw}")
    return normalized


def _positive_number(value: Any, label: str) -> float:
    numeric = float(value)
    if numeric <= 0:
        raise ValueError(f"{label} must be a positive number")
    return round(numeric, 6)


def _save_position_manual_edit(payload: dict[str, Any]) -> dict[str, Any]:
    source_kind = str(payload.get("sourceKind") or payload.get("source_kind") or "").strip().lower()
    if source_kind not in {"trade_log", "position"}:
        raise ValueError("Missing or unsupported manual edit source.")

    market_id = str(payload.get("marketId") or payload.get("market_id") or "").strip()
    token_id = str(payload.get("tokenId") or payload.get("token_id") or "").strip()
    side = str(payload.get("side") or "").strip().lower()
    real_money = 1 if bool(payload.get("realMoney") or payload.get("real_money")) else 0
    source_trade_log_id_raw = payload.get("sourceTradeLogId", payload.get("source_trade_log_id"))
    source_trade_log_id = int(source_trade_log_id_raw) if source_trade_log_id_raw is not None else None
    entry_price = _positive_number(payload.get("entryPrice", payload.get("entry_price")), "Entry")
    shares = _positive_number(payload.get("shares"), "Shares")
    size_usd = _positive_number(payload.get("sizeUsd", payload.get("size_usd")), "Total")
    status = _normalize_position_status(payload.get("status"))

    if not market_id:
        raise ValueError("Missing market id for manual position edit")
    if not side:
        raise ValueError("Missing side for manual position edit")

    now_ts = int(time.time())
    conn = get_conn()
    try:
        with conn:
            _ensure_position_edit_tables(conn)
            if source_kind == "position":
                conn.execute(
                    """
                    INSERT INTO position_manual_edits (
                      market_id,
                      token_id,
                      side,
                      real_money,
                      entry_price,
                      shares,
                      size_usd,
                      status,
                      updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(market_id, token_id, side, real_money) DO UPDATE SET
                      entry_price=excluded.entry_price,
                      shares=excluded.shares,
                      size_usd=excluded.size_usd,
                      status=excluded.status,
                      updated_at=excluded.updated_at
                    """,
                    (market_id, token_id, side, real_money, entry_price, shares, size_usd, status, now_ts),
                )
            if source_trade_log_id is not None:
                conn.execute(
                    """
                    INSERT INTO trade_log_manual_edits (
                      trade_log_id,
                      entry_price,
                      shares,
                      size_usd,
                      status,
                      updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_log_id) DO UPDATE SET
                      entry_price=excluded.entry_price,
                      shares=excluded.shares,
                      size_usd=excluded.size_usd,
                      status=excluded.status,
                      updated_at=excluded.updated_at
                    """,
                    (source_trade_log_id, entry_price, shares, size_usd, status, now_ts),
                )
    finally:
        conn.close()

    return {"ok": True, "message": "Position edit saved."}


def _live_trading_enabled_in_config() -> bool:
    return str(_read_safe_env_values().get("USE_REAL_MONEY", "false")).strip().lower() == "true"


def _current_bot_mode() -> str:
    return str(_bot_state_snapshot().get("mode") or "").strip().lower()


def _shadow_restart_command(keep_wallets: bool) -> list[str]:
    command = [preferred_python_executable(), str(RESTART_SHADOW_SCRIPT), active_env_flag()]
    if not keep_wallets:
        command.append("--clear-wallets")
    return command


def _spawn_shadow_restart_process(keep_wallets: bool) -> dict[str, Any]:
    command = _shadow_restart_command(keep_wallets)
    SHADOW_RESTART_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_handle = SHADOW_RESTART_LOG.open("a", encoding="utf-8")
    popen_kwargs: dict[str, Any] = {
        "cwd": str(REPO_ROOT),
        "env": runtime_env(),
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        creationflags = 0
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        log_handle.close()
        return {"ok": False, "message": f"Shadow restart failed to launch: {exc}"}
    finally:
        log_handle.close()

    logger.info(
        "Launched shadow restart helper pid=%s command=%s log=%s",
        getattr(process, "pid", "?"),
        command,
        SHADOW_RESTART_LOG,
    )
    return {"ok": True, "message": "Shadow restart helper launched."}


def _launch_shadow_restart(keep_wallets: bool) -> dict[str, Any]:
    if _live_trading_enabled_in_config() or _current_bot_mode() == "live" or use_real_money():
        return {
            "ok": False,
            "message": "Restart Shadow is blocked while live trading is enabled or the running bot is live.",
        }

    def _deferred_launch() -> None:
        if SHADOW_RESTART_DELAY_SECONDS > 0:
            time.sleep(SHADOW_RESTART_DELAY_SECONDS)
        result = _spawn_shadow_restart_process(keep_wallets)
        if not result.get("ok"):
            logger.error("Shadow restart helper launch failed: %s", result.get("message"))

    threading.Thread(
        target=_deferred_launch,
        name="shadow-restart-launcher",
        daemon=True,
    ).start()

    return {
        "ok": True,
        "message": (
            "Shadow restart requested. The API should come back after the bot resets and starts again. "
            f"Helper log: {SHADOW_RESTART_LOG.relative_to(REPO_ROOT)}"
        ),
    }


class DashboardApiHandler(BaseHTTPRequestHandler):
    server_version = "KellyWatcherDashboardAPI/1.0"

    def log_message(self, format: str, *args: object) -> None:
        logger.info("dashboard_api %s - %s", self.address_string(), format % args)

    def _set_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._set_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _authorized(self) -> bool:
        token = getattr(self.server, "api_token", None)
        if not token:
            return True
        authorization = str(self.headers.get("Authorization") or "").strip()
        return authorization == f"Bearer {token}"

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send_json(401, {"ok": False, "message": "Unauthorized"})
        return False

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "host": getattr(self.server, "api_host", _api_host()),
                    "port": getattr(self.server, "api_port", _api_port()),
                    "auth_required": bool(getattr(self.server, "api_token", None)),
                },
            )
            return

        if not self._require_auth():
            return

        if path == "/api/bot-state":
            self._send_json(200, {"state": _bot_state_snapshot()})
            return

        if path == "/api/identities":
            self._send_json(200, {"wallets": _identity_lookup()})
            return

        if path == "/api/events":
            query = parse_qs(parsed.query)
            try:
                max_events = max(1, min(int(query.get("max", ["50"])[0]), 1000))
            except (TypeError, ValueError):
                max_events = 50
            self._send_json(200, {"events": _recent_events(max_events)})
            return

        if path == "/api/config":
            self._send_json(200, _config_snapshot())
            return

        self._send_json(404, {"ok": False, "message": f"Unknown endpoint: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if not self._require_auth():
            return

        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "message": "Invalid JSON body"})
            return

        try:
            if path == "/api/query":
                sql = str(body.get("sql") or "")
                params = body.get("params") or []
                if not isinstance(params, list):
                    raise ValueError("Query params must be a list.")
                self._send_json(200, {"rows": _query_rows(sql, params)})
                return

            if path == "/api/config/value":
                key = str(body.get("key") or "").strip()
                value = str(body.get("value") or "").strip()
                _write_env_value(key, value)
                self._send_json(200, _config_snapshot())
                return

            if path == "/api/manual-retrain":
                self._send_json(200, _manual_retrain_response())
                return

            if path == "/api/manual-trade":
                self._send_json(200, _manual_trade_response(body))
                return

            if path == "/api/wallets/drop":
                wallet_address = str(body.get("walletAddress") or body.get("wallet_address") or "").strip()
                reason = str(body.get("reason") or "manual dashboard drop").strip()
                self._send_json(200, _drop_wallet(wallet_address, reason))
                return

            if path == "/api/wallets/reactivate":
                wallet_address = str(body.get("walletAddress") or body.get("wallet_address") or "").strip()
                self._send_json(200, _reactivate_wallet(wallet_address))
                return

            if path == "/api/positions/manual-edit":
                self._send_json(200, _save_position_manual_edit(body))
                return

            if path == "/api/shadow/restart":
                keep_wallets = bool(body.get("keepWallets", body.get("keep_wallets", True)))
                self._send_json(202, _launch_shadow_restart(keep_wallets))
                return
        except ValueError as exc:
            self._send_json(400, {"ok": False, "message": str(exc)})
            return
        except Exception as exc:
            logger.exception("Dashboard API request failed: %s", path)
            self._send_json(500, {"ok": False, "message": str(exc)})
            return

        self._send_json(404, {"ok": False, "message": f"Unknown endpoint: {path}"})


class DashboardApiServer:
    def __init__(self) -> None:
        self.host = _api_host()
        self.port = _api_port()
        self.token = _api_token()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self.port <= 0:
            logger.info("Dashboard API disabled because DASHBOARD_API_PORT=%s", self.port)
            return False

        httpd = ThreadingHTTPServer((self.host, self.port), DashboardApiHandler)
        httpd.daemon_threads = True
        httpd.api_host = self.host  # type: ignore[attr-defined]
        httpd.api_port = self.port  # type: ignore[attr-defined]
        httpd.api_token = self.token  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name="dashboard-api-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Dashboard API listening on http://%s:%s%s",
            self.host,
            self.port,
            " (token auth enabled)" if self.token else "",
        )
        return True

    def stop(self) -> None:
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._httpd = None
            self._thread = None


def start_dashboard_api_server() -> DashboardApiServer:
    server = DashboardApiServer()
    server.start()
    return server
