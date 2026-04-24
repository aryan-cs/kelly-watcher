from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from config import use_real_money
from db import DB_PATH, db_recovery_state, get_conn
from env_profile import (
    LEGACY_ENV_PATH,
    active_env_profile,
    env_path_for_profile,
    env_paths_for_profile,
    repo_env_path_for_profile,
)
from trade_contract import NON_CHALLENGER_EXPERIMENT_ARM_SQL
from runtime_paths import (
    BOT_STATE_FILE,
    DATA_DIR,
    EVENT_FILE,
    IDENTITY_CACHE_PATH,
    DB_RECOVERY_REQUEST_FILE,
    MANUAL_RETRAIN_REQUEST_FILE,
    MANUAL_TRADE_REQUEST_FILE,
    REPO_ROOT,
    SHADOW_RESET_REQUEST_FILE,
)

logger = logging.getLogger(__name__)

ENV_PROFILE = active_env_profile()
ENV_PATH = env_path_for_profile(ENV_PROFILE)
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
IDENTITY_FILE = IDENTITY_CACHE_PATH

SAFE_ENV_KEYS = {
    "WATCHED_WALLETS",
    "USE_REAL_MONEY",
    "POLL_INTERVAL_SECONDS",
    "MAX_MARKET_HORIZON",
    "HOT_WALLET_COUNT",
    "WARM_WALLET_COUNT",
    "WARM_POLL_INTERVAL_MULTIPLIER",
    "DISCOVERY_POLL_INTERVAL_MULTIPLIER",
    "WALLET_INACTIVITY_LIMIT",
    "WALLET_SLOW_DROP_MAX_TRACKING_AGE",
    "WALLET_PERFORMANCE_DROP_MIN_TRADES",
    "WALLET_PERFORMANCE_DROP_MAX_WIN_RATE",
    "WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN",
    "WALLET_UNCOPYABLE_PENALTY_MIN_BUYS",
    "WALLET_UNCOPYABLE_PENALTY_WEIGHT",
    "WALLET_UNCOPYABLE_DROP_MIN_BUYS",
    "WALLET_UNCOPYABLE_DROP_MAX_SKIP_RATE",
    "WALLET_UNCOPYABLE_DROP_MAX_RESOLVED_COPIED",
    "WALLET_COLD_START_MIN_OBSERVED_BUYS",
    "WALLET_DISCOVERY_MIN_OBSERVED_BUYS",
    "WALLET_DISCOVERY_MIN_RESOLVED_BUYS",
    "WALLET_DISCOVERY_SIZE_MULTIPLIER",
    "WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS",
    "WALLET_PROBATION_SIZE_MULTIPLIER",
    "WALLET_LOCAL_PERFORMANCE_PENALTY_MIN_RESOLVED_COPIED_BUYS",
    "WALLET_LOCAL_PERFORMANCE_PENALTY_MAX_AVG_RETURN",
    "WALLET_LOCAL_PERFORMANCE_PENALTY_SIZE_MULTIPLIER",
    "WALLET_LOCAL_DROP_MIN_RESOLVED_COPIED_BUYS",
    "WALLET_LOCAL_DROP_MAX_AVG_RETURN",
    "WALLET_LOCAL_DROP_MAX_TOTAL_PNL_USD",
    "WALLET_QUALITY_SIZE_MIN_MULTIPLIER",
    "WALLET_QUALITY_SIZE_MAX_MULTIPLIER",
    "MAX_SOURCE_TRADE_AGE",
    "MAX_FEED_STALENESS",
    "MAX_ORDERBOOK_STALENESS",
    "MIN_EXECUTION_WINDOW",
    "MIN_CONFIDENCE",
    "ALLOWED_ENTRY_PRICE_BANDS",
    "ALLOWED_TIME_TO_CLOSE_BANDS",
    "ALLOW_HEURISTIC",
    "HEURISTIC_MIN_ENTRY_PRICE",
    "HEURISTIC_MAX_ENTRY_PRICE",
    "HEURISTIC_ALLOWED_ENTRY_PRICE_BANDS",
    "HEURISTIC_MIN_TIME_TO_CLOSE",
    "ALLOW_XGBOOST",
    "MODEL_EDGE_MID_CONFIDENCE",
    "MODEL_EDGE_HIGH_CONFIDENCE",
    "MODEL_EDGE_MID_THRESHOLD",
    "MODEL_EDGE_HIGH_THRESHOLD",
    "XGBOOST_ALLOWED_ENTRY_PRICE_BANDS",
    "MODEL_MIN_TIME_TO_CLOSE",
    "MIN_BET_USD",
    "ENTRY_FIXED_COST_USD",
    "EXIT_FIXED_COST_USD",
    "APPROVAL_FIXED_COST_USD",
    "SETTLEMENT_FIXED_COST_USD",
    "EXPECTED_CLOSE_FIXED_COST_USD",
    "INCLUDE_EXPECTED_EXIT_FEE_IN_SIZING",
    "MAX_BET_FRACTION",
    "MAX_MARKET_EXPOSURE_FRACTION",
    "MAX_TRADER_EXPOSURE_FRACTION",
    "MAX_TOTAL_OPEN_EXPOSURE_FRACTION",
    "EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION",
    "DUPLICATE_SIDE_OVERRIDE_MIN_SKIPS",
    "DUPLICATE_SIDE_OVERRIDE_MIN_AVG_RETURN",
    "EXPOSURE_OVERRIDE_MIN_SKIPS",
    "EXPOSURE_OVERRIDE_MIN_AVG_RETURN",
    "SHADOW_BANKROLL_USD",
    "MAX_DAILY_LOSS_PCT",
    "STOP_LOSS_ENABLED",
    "STOP_LOSS_MAX_LOSS_PCT",
    "STOP_LOSS_MIN_HOLD",
    "MAX_LIVE_DRAWDOWN_PCT",
    "MAX_LIVE_HEALTH_FAILURES",
    "LIVE_REQUIRE_SHADOW_HISTORY",
    "LIVE_MIN_SHADOW_RESOLVED",
    "LIVE_MIN_SHADOW_RESOLVED_SINCE_PROMOTION",
    "RETRAIN_BASE_CADENCE",
    "RETRAIN_HOUR_LOCAL",
    "RETRAIN_EARLY_CHECK_INTERVAL",
    "RETRAIN_MIN_NEW_LABELS",
    "RETRAIN_MIN_SAMPLES",
    "REPLAY_SEARCH_BASE_CADENCE",
    "REPLAY_SEARCH_HOUR_LOCAL",
    "REPLAY_SEARCH_SCHEDULE_HOUR_LOCAL",
    "REPLAY_SEARCH_LABEL_PREFIX",
    "REPLAY_SEARCH_NOTES",
    "REPLAY_SEARCH_BASE_POLICY_FILE",
    "REPLAY_SEARCH_BASE_POLICY_JSON",
    "REPLAY_SEARCH_GRID_FILE",
    "REPLAY_SEARCH_GRID_JSON",
    "REPLAY_SEARCH_CONSTRAINTS_FILE",
    "REPLAY_SEARCH_CONSTRAINTS_JSON",
    "REPLAY_SEARCH_SCORE_WEIGHTS_FILE",
    "REPLAY_SEARCH_SCORE_WEIGHTS_JSON",
    "REPLAY_SEARCH_TOP",
    "REPLAY_SEARCH_MAX_COMBOS",
    "REPLAY_SEARCH_WINDOW_DAYS",
    "REPLAY_SEARCH_WINDOW_COUNT",
    "REPLAY_AUTO_PROMOTE_ENABLED",
    "REPLAY_AUTO_PROMOTE",
    "REPLAY_AUTO_PROMOTE_MIN_SCORE_DELTA",
    "REPLAY_AUTO_PROMOTE_MIN_PNL_DELTA_USD",
    "LOG_LEVEL",
    "MODEL_PATH",
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
AND LOWER(COALESCE(experiment_arm, 'champion')) = 'champion'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
AND shadow_pnl_usd IS NOT NULL
"""

_env_lock = threading.Lock()
_request_lock = threading.Lock()
SHADOW_RESTART_WALLET_MODES = {"keep_active", "keep_all", "clear_all"}


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
    expected_env_path = env_path_for_profile(ENV_PROFILE, REPO_ROOT)
    if ENV_PATH != expected_env_path:
        return ENV_PATH
    paths = [path for path in env_paths_for_profile(ENV_PROFILE, REPO_ROOT) if path.exists()]
    if paths:
        return paths[0]
    if ENV_PATH.exists():
        return ENV_PATH
    repo_env_path = repo_env_path_for_profile(ENV_PROFILE, REPO_ROOT)
    if repo_env_path.exists():
        return repo_env_path
    if ENV_PROFILE == "dev" and LEGACY_ENV_PATH.exists():
        return LEGACY_ENV_PATH
    return ENV_EXAMPLE_PATH


def _source_env_paths() -> list[Path]:
    expected_env_path = env_path_for_profile(ENV_PROFILE, REPO_ROOT)
    if ENV_PATH != expected_env_path:
        return [ENV_PATH]
    paths = [path for path in env_paths_for_profile(ENV_PROFILE, REPO_ROOT) if path.exists()]
    if paths:
        return paths
    source_path = _source_env_path()
    return [source_path]


def _read_env_items() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in _source_env_paths():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            normalized_key = key.strip()
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            items.append((normalized_key, value.strip()))
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
        source_path = ENV_PATH if ENV_PATH.exists() else _source_env_path()
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

        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
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


def _request_payload_if_fresh(path: Path, max_age_seconds: int) -> dict[str, Any] | None:
    if not path.exists():
        return None

    payload = _read_json_dict(path)
    if not payload:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return None

    requested_at = int(payload.get("requested_at") or 0)
    pickup_failed_at = int(payload.get("pickup_failed_at") or 0)
    now_ts = int(time.time())
    freshness_anchor = max(requested_at, pickup_failed_at)
    if freshness_anchor > 0:
        is_fresh = (now_ts - freshness_anchor) <= max_age_seconds
    else:
        is_fresh = _request_is_recent(path, max_age_seconds)
    if is_fresh:
        return payload

    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return None


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
    temp_path.replace(path)


def _shadow_restart_pending_message(wallet_mode: str = "") -> str:
    mode = str(wallet_mode or "").strip().lower()
    if mode in {"keep_active", "keep_all", "clear_all"}:
        return f"Shadow restart requested ({mode}). Waiting for backend to restart."
    return "Shadow restart requested. Waiting for backend to restart."


def _db_recovery_pending_message(candidate_path: str = "") -> str:
    candidate_name = Path(str(candidate_path or "").strip()).name
    if candidate_name:
        return (
            f"Shadow DB recovery requested ({candidate_name}). "
            "Waiting for backend to restart."
        )
    return "Shadow DB recovery requested. Waiting for backend to restart."


def _manual_retrain_pending_message(payload: dict[str, Any]) -> str:
    source = str(payload.get("source") or "unknown").strip().lower() or "unknown"
    base = f"requested by {source}"
    pickup_error = str(payload.get("pickup_error") or "").strip()
    if pickup_error:
        return f"{base} | pickup failed: {pickup_error}"
    return base


def _manual_trade_pending_message(payload: dict[str, Any]) -> str:
    action = str(payload.get("action") or "").strip().lower().replace("_", " ") or "trade"
    target = str(payload.get("question") or payload.get("market_id") or "").strip()
    base = f"{action} {target}".strip()
    pickup_error = str(payload.get("pickup_error") or "").strip()
    if pickup_error:
        return f"{base} | pickup failed: {pickup_error}"
    return base


def _request_has_pickup_failure(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if int(payload.get("pickup_failed_at") or 0) > 0:
        return True
    return bool(str(payload.get("pickup_error") or "").strip())


def _persist_shadow_restart_pending_state(request_payload: dict[str, Any]) -> None:
    bot_state = _read_json_dict(BOT_STATE_FILE)
    bot_state.update(
        shadow_restart_pending=True,
        shadow_restart_kind="shadow_reset",
        shadow_restart_message=_shadow_restart_pending_message(str(request_payload.get("wallet_mode") or "")),
    )
    _write_atomic_json(BOT_STATE_FILE, bot_state)


def _persist_db_recovery_pending_state(request_payload: dict[str, Any]) -> None:
    bot_state = _read_json_dict(BOT_STATE_FILE)
    bot_state.update(
        shadow_restart_pending=True,
        shadow_restart_kind="db_recovery",
        shadow_restart_message=_db_recovery_pending_message(
            str(request_payload.get("candidate_path") or request_payload.get("candidatePath") or "")
        ),
    )
    _write_atomic_json(BOT_STATE_FILE, bot_state)


def _persist_shadow_restart_cleared_state(bot_state: dict[str, Any]) -> None:
    updated_state = dict(bot_state)
    updated_state.update(
        shadow_restart_pending=False,
        shadow_restart_kind="",
        shadow_restart_message="",
    )
    _write_atomic_json(BOT_STATE_FILE, updated_state)


def _persist_manual_request_cleared_state(
    bot_state: dict[str, Any],
    *,
    retrain: bool = False,
    trade: bool = False,
) -> None:
    updated_state = dict(bot_state)
    if retrain:
        updated_state.update(
            manual_retrain_pending=False,
            manual_retrain_requested_at=0,
            manual_retrain_message="",
        )
    if trade:
        updated_state.update(
            manual_trade_pending=False,
            manual_trade_requested_at=0,
            manual_trade_message="",
        )
    _write_atomic_json(BOT_STATE_FILE, updated_state)


def _bot_state_snapshot() -> dict[str, Any]:
    bot_state = _read_json_dict(BOT_STATE_FILE)
    bot_state.update(
        manual_retrain_pending=bool(bot_state.get("manual_retrain_pending")),
        manual_retrain_requested_at=int(bot_state.get("manual_retrain_requested_at") or 0),
        manual_retrain_message=str(bot_state.get("manual_retrain_message") or ""),
        shadow_restart_pending=bool(bot_state.get("shadow_restart_pending")),
        shadow_restart_kind=str(bot_state.get("shadow_restart_kind") or "").strip().lower(),
        shadow_restart_message=str(bot_state.get("shadow_restart_message") or ""),
        manual_trade_pending=bool(bot_state.get("manual_trade_pending")),
        manual_trade_requested_at=int(bot_state.get("manual_trade_requested_at") or 0),
        manual_trade_message=str(bot_state.get("manual_trade_message") or ""),
    )
    request_payload = _request_payload_if_fresh(MANUAL_RETRAIN_REQUEST_FILE, 900)
    if request_payload is not None:
        bot_state.update(
            manual_retrain_pending=True,
            manual_retrain_requested_at=int(request_payload.get("requested_at") or 0),
            manual_retrain_message=_manual_retrain_pending_message(request_payload),
        )
    elif bool(bot_state.get("manual_retrain_pending")):
        bot_state.update(
            manual_retrain_pending=False,
            manual_retrain_requested_at=0,
            manual_retrain_message="",
        )
        _persist_manual_request_cleared_state(bot_state, retrain=True)
    shadow_reset_request = _request_payload_if_fresh(SHADOW_RESET_REQUEST_FILE, 900)
    db_recovery_request = _request_payload_if_fresh(DB_RECOVERY_REQUEST_FILE, 900)
    if db_recovery_request is not None:
        bot_state.update(
            shadow_restart_pending=True,
            shadow_restart_kind="db_recovery",
            shadow_restart_message=_db_recovery_pending_message(
                str(
                    db_recovery_request.get("candidate_path")
                    or db_recovery_request.get("candidatePath")
                    or ""
                )
            ),
        )
    elif shadow_reset_request is not None:
        bot_state.update(
            shadow_restart_pending=True,
            shadow_restart_kind="shadow_reset",
            shadow_restart_message=_shadow_restart_pending_message(
                str(shadow_reset_request.get("wallet_mode") or "")
            ),
        )
    elif bool(bot_state.get("shadow_restart_pending")):
        started_at = int(bot_state.get("started_at") or 0)
        last_activity_at = int(bot_state.get("last_activity_at") or 0)
        heartbeat_window = _heartbeat_window_seconds(bot_state)
        shadow_restart_state_stale = (
            started_at <= 0
            or last_activity_at <= 0
            or (int(time.time()) - last_activity_at) > heartbeat_window
        )
        if shadow_restart_state_stale:
            bot_state.update(
                shadow_restart_pending=False,
                shadow_restart_kind="",
                shadow_restart_message="",
            )
            _persist_shadow_restart_cleared_state(bot_state)
    request_payload = _request_payload_if_fresh(MANUAL_TRADE_REQUEST_FILE, 900)
    if request_payload is not None:
        bot_state.update(
            manual_trade_pending=True,
            manual_trade_requested_at=int(request_payload.get("requested_at") or 0),
            manual_trade_message=_manual_trade_pending_message(request_payload),
        )
    elif bool(bot_state.get("manual_trade_pending")):
        bot_state.update(
            manual_trade_pending=False,
            manual_trade_requested_at=0,
            manual_trade_message="",
        )
        _persist_manual_request_cleared_state(bot_state, trade=True)
    return bot_state


def _heartbeat_window_seconds(bot_state: dict[str, Any]) -> int:
    try:
        poll_interval = float(bot_state.get("poll_interval") or 1)
    except (TypeError, ValueError):
        poll_interval = 1
    return int(max(poll_interval * 3, 30))


def _startup_block_reason(bot_state: dict[str, Any]) -> str:
    startup_blocked = bool(bot_state.get("startup_blocked"))
    startup_detail = str(bot_state.get("startup_detail") or "").strip()
    startup_failure_message = str(
        bot_state.get("startup_failure_message")
        or bot_state.get("startup_validation_message")
        or ""
    ).strip()
    if not startup_blocked and "startup blocked" not in startup_detail.lower():
        return ""
    detail = str(
        bot_state.get("startup_block_reason")
        or startup_detail
        or startup_failure_message
        or ""
    ).strip()
    if detail:
        return detail if detail.endswith((".", "!", "?")) else f"{detail}."
    return "backend startup is blocked."


def _blocked_shadow_mutation_response(
    action_label: str,
    bot_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    snapshot = dict(bot_state or _bot_state_snapshot())
    if bool(snapshot.get("shadow_restart_pending")):
        return {
            "ok": False,
            "message": f"{action_label} is unavailable while shadow restart is pending. Wait for the restart to finish first.",
        }
    startup_block = _startup_block_reason(snapshot)
    if startup_block:
        return {
            "ok": False,
            "message": f"{action_label} is unavailable because {startup_block}",
        }
    return None


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

    blocked_response = _blocked_shadow_mutation_response("Manual retrain", bot_state)
    if blocked_response is not None:
        return blocked_response

    snapshot_block_reason = _manual_retrain_shadow_snapshot_block_reason(bot_state)
    if snapshot_block_reason:
        return {
            "ok": False,
            "message": f"Manual retrain is unavailable because {snapshot_block_reason}",
        }

    if bool(bot_state.get("retrain_in_progress")):
        return {"ok": False, "message": "A retrain is already running."}

    existing_request = _request_payload_if_fresh(MANUAL_RETRAIN_REQUEST_FILE, 900)
    if existing_request is not None and not _request_has_pickup_failure(existing_request):
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


def _manual_retrain_shadow_snapshot_block_reason(bot_state: dict[str, Any]) -> str:
    if not bool(bot_state.get("shadow_snapshot_state_known")):
        return "the current shadow snapshot is still being evaluated."
    if bool(bot_state.get("shadow_snapshot_ready")):
        return ""
    detail = str(bot_state.get("shadow_snapshot_block_reason") or "").strip()
    if detail:
        return detail if detail.endswith((".", "!", "?")) else f"{detail}."
    status = str(bot_state.get("shadow_snapshot_status") or "").strip().lower()
    if status in {"", "checking"}:
        return "the current shadow snapshot is still being evaluated."
    return "the current shadow snapshot is not trustworthy yet."


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
    blocked_response = _blocked_shadow_mutation_response("Manual trade actions", bot_state)
    if blocked_response is not None:
        return blocked_response
    existing_request = _request_payload_if_fresh(MANUAL_TRADE_REQUEST_FILE, 900)
    if existing_request is not None and not _request_has_pickup_failure(existing_request):
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
        WHERE {NON_CHALLENGER_EXPERIMENT_ARM_SQL}
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


def _parse_bool_like(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("enabled must be a boolean value")


def _live_mode_enabled_from_payload(payload: dict[str, Any] | None = None) -> bool:
    if not isinstance(payload, dict):
        raise ValueError("live mode payload must be an object")
    if "enabled" in payload:
        return _parse_bool_like(payload.get("enabled"))
    if "value" in payload:
        return _parse_bool_like(payload.get("value"))
    raise ValueError("enabled must be provided")


def _live_mode_enable_block_reasons(bot_state: dict[str, Any] | None = None) -> list[str]:
    snapshot = dict(bot_state or _bot_state_snapshot())
    reasons: list[str] = []
    started_at = int(snapshot.get("started_at") or 0)
    last_activity_at = int(snapshot.get("last_activity_at") or 0)
    now_ts = int(time.time())
    if started_at <= 0 or last_activity_at <= 0:
        reasons.append("the shadow bot has not published fresh readiness state yet")
        return reasons
    if (now_ts - last_activity_at) > _heartbeat_window_seconds(snapshot):
        reasons.append("the bot state is stale; restart or refresh the shadow bot first")
        return reasons
    if bool(snapshot.get("shadow_restart_pending")):
        reasons.append("the bot is restarting; wait for the shadow runtime to settle first")
        return reasons
    startup_block = _startup_block_reason(snapshot)
    if startup_block:
        detail = startup_block.rstrip(".!?").strip()
        reasons.append(f"backend startup is blocked: {detail or 'unknown reason'}")
        return reasons

    db_integrity_known = bool(snapshot.get("db_integrity_known"))
    db_integrity_ok = bool(snapshot.get("db_integrity_ok"))
    db_integrity_message = str(snapshot.get("db_integrity_message") or "").strip()
    if not db_integrity_known:
        reasons.append("DB integrity readiness is unknown")
    elif not db_integrity_ok:
        detail = db_integrity_message.splitlines()[0].strip() if db_integrity_message else "unknown integrity failure"
        reasons.append(f"SQLite integrity check failed: {detail}")

    shadow_history_state_known = bool(snapshot.get("shadow_history_state_known"))
    if not shadow_history_state_known:
        reasons.append("shadow-history readiness is unknown")
    else:
        require_total_history = bool(snapshot.get("live_require_shadow_history_enabled"))
        total_resolved = max(int(snapshot.get("resolved_shadow_trade_count") or 0), 0)
        total_required = max(int(snapshot.get("live_min_shadow_resolved") or 0), 0)
        if require_total_history and total_required > 0 and not bool(snapshot.get("live_shadow_history_total_ready")):
            reasons.append(
                f"shadow history in the current evidence window is below the live gate ({total_resolved}/{total_required} resolved)"
            )
        since_required = max(int(snapshot.get("live_min_shadow_resolved_since_last_promotion") or 0), 0)
        resolved_since = max(int(snapshot.get("resolved_shadow_since_last_promotion") or 0), 0)
        if since_required > 0 and not bool(snapshot.get("live_shadow_history_ready")):
            baseline = str(snapshot.get("shadow_history_current_baseline_label") or "").strip()
            if not baseline:
                applied_at = int(snapshot.get("last_applied_replay_promotion_at") or 0)
                baseline = f"last promotion at {applied_at}" if applied_at > 0 else "the initial policy"
            reasons.append(
                f"post-promotion shadow history in the current evidence window is below the live gate ({resolved_since}/{since_required} since {baseline})"
            )

    segment_state_known = bool(snapshot.get("shadow_segment_state_known"))
    segment_status = str(snapshot.get("shadow_segment_status") or "").strip().lower()
    segment_total = max(int(snapshot.get("shadow_segment_total") or 0), 0)
    segment_ready_count = max(int(snapshot.get("shadow_segment_ready_count") or 0), 0)
    segment_blocked_count = max(int(snapshot.get("shadow_segment_blocked_count") or 0), 0)
    segment_block_reason = str(snapshot.get("shadow_segment_block_reason") or "").strip()
    if not segment_state_known:
        reasons.append("segment shadow readiness is unknown")
    elif (
        segment_status != "ready"
        or segment_total <= 0
        or segment_ready_count < segment_total
        or segment_blocked_count > 0
    ):
        if segment_block_reason:
            reasons.append(f"segment shadow readiness is blocked: {segment_block_reason}")
        elif segment_total <= 0:
            reasons.append("segment shadow readiness has no champion segment data yet")
        else:
            reasons.append(
                "segment shadow readiness is not ready "
                f"({segment_ready_count}/{segment_total} ready, {segment_blocked_count} blocked)"
            )
    return reasons


def _set_live_mode_response(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled = _live_mode_enabled_from_payload(payload)
    bot_state = _bot_state_snapshot()
    blocked = _blocked_shadow_mutation_response("Live mode changes", bot_state)
    if blocked:
        startup_block = _startup_block_reason(bot_state)
        if enabled and startup_block:
            return {
                "ok": False,
                "message": f"Live trading remains blocked: backend startup is blocked: {startup_block}",
            }
        return blocked
    if not enabled:
        _write_env_value("USE_REAL_MONEY", "false")
        return {
            "ok": True,
            "message": "Live Trading saved as OFF. Restart the bot to apply it safely.",
        }

    reasons = _live_mode_enable_block_reasons()
    if reasons:
        return {
            "ok": False,
            "message": "Live trading remains blocked: " + " | ".join(reasons),
        }

    _write_env_value("USE_REAL_MONEY", "true")
    return {
        "ok": True,
        "message": "Live Trading saved as ON. Restart the bot to apply it safely.",
    }


def _normalize_shadow_restart_wallet_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SHADOW_RESTART_WALLET_MODES:
        return normalized
    raise ValueError(
        "Invalid shadow restart wallet mode. Expected keep_active, keep_all, or clear_all."
    )


def _shadow_restart_command(wallet_mode: str) -> list[str]:
    wallet_mode = _normalize_shadow_restart_wallet_mode(wallet_mode)
    return [wallet_mode]


def _spawn_shadow_restart_process(wallet_mode: str) -> dict[str, Any]:
    wallet_mode = _normalize_shadow_restart_wallet_mode(wallet_mode)
    request_payload = {
        "wallet_mode": wallet_mode,
        "requested_at": int(time.time()),
        "request_id": f"shadow-reset-{int(time.time() * 1000)}-{os.getpid()}",
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _request_lock:
        SHADOW_RESET_REQUEST_FILE.write_text(
            json.dumps(request_payload, separators=(",", ":")),
            encoding="utf-8",
        )
        _persist_shadow_restart_pending_state(request_payload)
    logger.info("Queued shadow reset request %s", request_payload)
    return {"ok": True, "message": "Shadow reset queued."}


def _db_recovery_candidate_snapshot(bot_state: dict[str, Any] | None = None) -> dict[str, Any]:
    def _normalize_candidate(snapshot: dict[str, Any]) -> dict[str, Any]:
        candidate_ready = bool(snapshot.get("db_recovery_candidate_ready"))
        candidate_path = str(snapshot.get("db_recovery_candidate_path") or "").strip()
        source_path = str(snapshot.get("db_recovery_candidate_source_path") or "").strip()
        candidate_message = str(snapshot.get("db_recovery_candidate_message") or "").strip()
        candidate_mode = str(snapshot.get("db_recovery_candidate_mode") or "").strip().lower()
        class_reason = str(snapshot.get("db_recovery_candidate_class_reason") or "").strip()
        evidence_ready = bool(snapshot.get("db_recovery_candidate_evidence_ready"))

        if candidate_ready and candidate_path:
            if candidate_mode != "evidence_ready":
                candidate_mode = "integrity_only"
                evidence_ready = False
            else:
                evidence_ready = True
            if not class_reason:
                class_reason = (
                    "verified backup restores the ledger, but its shadow evidence is not ready for readiness claims"
                    if candidate_mode == "integrity_only"
                    else "verified backup is recoverable and its shadow evaluation passes the current evidence gate"
                )
        else:
            candidate_ready = False
            candidate_mode = "unavailable"
            evidence_ready = False
            if not class_reason:
                class_reason = candidate_message or "no verified backup candidate is ready"

        return {
            "db_recovery_candidate_ready": candidate_ready,
            "db_recovery_candidate_path": candidate_path,
            "db_recovery_candidate_source_path": source_path or str(DB_PATH),
            "db_recovery_candidate_message": candidate_message,
            "db_recovery_candidate_mode": candidate_mode,
            "db_recovery_candidate_evidence_ready": evidence_ready,
            "db_recovery_candidate_class_reason": class_reason,
        }

    snapshot = dict(bot_state or _bot_state_snapshot())
    state_known = bool(snapshot.get("db_recovery_state_known"))
    candidate_ready = bool(snapshot.get("db_recovery_candidate_ready"))
    candidate_path = str(snapshot.get("db_recovery_candidate_path") or "").strip()
    candidate_message = str(snapshot.get("db_recovery_candidate_message") or "").strip()
    candidate_mode = str(snapshot.get("db_recovery_candidate_mode") or "").strip()
    class_reason = str(snapshot.get("db_recovery_candidate_class_reason") or "").strip()
    if state_known or candidate_ready or candidate_path or candidate_message or candidate_mode or class_reason:
        return _normalize_candidate(snapshot)
    fallback = db_recovery_state()
    return _normalize_candidate(fallback)


def _db_recovery_request_message(candidate_state: dict[str, Any] | None = None) -> str:
    snapshot = dict(candidate_state or {})
    base = "DB recovery requested. The bot will restore the latest verified backup and restart shadow mode."
    candidate_mode = str(snapshot.get("db_recovery_candidate_mode") or "").strip().lower()
    class_reason = str(snapshot.get("db_recovery_candidate_class_reason") or "").strip()
    if candidate_mode == "evidence_ready":
        detail = class_reason or "verified backup is recoverable and its shadow evaluation passes the current evidence gate"
        return f"{base} Candidate class: evidence-ready. {detail}"
    if candidate_mode == "integrity_only":
        detail = class_reason or (
            "verified backup restores the ledger, but its shadow evidence is not ready for readiness claims"
        )
        return f"{base} Candidate class: integrity-only. {detail}"
    return base


def _canonical_db_recovery_request_path(path_text: str) -> str:
    raw_path = str(path_text or "").strip()
    if not raw_path:
        return ""
    try:
        return str(Path(raw_path).expanduser().resolve(strict=False))
    except Exception:
        return str(Path(raw_path).expanduser().absolute())


def _spawn_db_recovery_process(
    *,
    candidate_path: str,
    source_path: str,
    request_id: str = "",
    requested_at: int = 0,
    source: str = "dashboard",
) -> dict[str, Any]:
    request_payload = {
        "candidate_path": str(candidate_path or "").strip(),
        "source_path": str(source_path or "").strip() or str(DB_PATH),
        "requested_at": int(requested_at or time.time()),
        "request_id": str(request_id or f"db-recovery-{int(time.time() * 1000)}-{os.getpid()}").strip(),
        "source": str(source or "dashboard").strip() or "dashboard",
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _request_lock:
        DB_RECOVERY_REQUEST_FILE.write_text(
            json.dumps(request_payload, separators=(",", ":")),
            encoding="utf-8",
        )
        _persist_db_recovery_pending_state(request_payload)
    logger.info("Queued DB recovery request %s", request_payload)
    return {"ok": True, "message": "DB recovery queued."}


def _launch_shadow_restart(wallet_mode: str) -> dict[str, Any]:
    bot_state = _bot_state_snapshot()
    now = int(time.time())
    started_at = int(bot_state.get("started_at") or 0)
    last_activity_at = int(bot_state.get("last_activity_at") or 0)
    current_mode = str(bot_state.get("mode") or "").strip().lower()

    if _live_trading_enabled_in_config() or current_mode == "live" or use_real_money():
        return {
            "ok": False,
            "message": "Restart Shadow is blocked while live trading is enabled or the running bot is live.",
        }
    if started_at <= 0 or last_activity_at <= 0:
        return {
            "ok": False,
            "message": "Restart Shadow is unavailable because bot state is missing. Start the bot first.",
        }
    if (now - last_activity_at) > _heartbeat_window_seconds(bot_state):
        return {
            "ok": False,
            "message": "Restart Shadow is unavailable because the bot state looks stale. Restart or refresh the bot first.",
        }
    if bool(bot_state.get("shadow_restart_pending")) or _request_payload_if_fresh(SHADOW_RESET_REQUEST_FILE, 900) is not None:
        return {
            "ok": True,
            "message": "Shadow reset already requested. Waiting for the bot to restart.",
        }
    result = _spawn_shadow_restart_process(wallet_mode)
    if not result.get("ok"):
        logger.error("Shadow reset queue failed: %s", result.get("message"))
        return result
    return {
        "ok": True,
        "message": "Shadow reset requested. The bot will wipe state and restart itself.",
    }


def _db_recovery_preflight(bot_state: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    use_runtime_snapshot = bot_state is None
    snapshot = dict(bot_state or _bot_state_snapshot())
    current_mode = str(snapshot.get("mode") or "").strip().lower()
    if _live_trading_enabled_in_config() or current_mode == "live" or use_real_money():
        return (
            {
                "ok": False,
                "message": "DB recovery is blocked while live trading is enabled or the running bot is live.",
            },
            {},
        )
    if bool(snapshot.get("shadow_restart_pending")) or (
        use_runtime_snapshot and _request_payload_if_fresh(DB_RECOVERY_REQUEST_FILE, 900) is not None
    ):
        return (
            {
                "ok": True,
                "message": "DB recovery already requested. Waiting for the bot to restart.",
            },
            {},
        )
    candidate_state = _db_recovery_candidate_snapshot(snapshot)
    candidate_ready = bool(candidate_state.get("db_recovery_candidate_ready"))
    candidate_path = str(candidate_state.get("db_recovery_candidate_path") or "").strip()
    candidate_message = str(candidate_state.get("db_recovery_candidate_message") or "").strip()
    if not candidate_ready or not candidate_path:
        message = "DB recovery is unavailable because no verified backup candidate is ready."
        if candidate_message:
            message = f"{message} {candidate_message}"
        return ({"ok": False, "message": message}, {})
    return (None, candidate_state)


def _drop_wallet_response(wallet_address: str, reason: str = "manual dashboard drop") -> dict[str, Any]:
    blocked_response = _blocked_shadow_mutation_response("Wallet drop")
    if blocked_response is not None:
        return blocked_response
    return _drop_wallet(wallet_address, reason)


def _reactivate_wallet_response(wallet_address: str) -> dict[str, Any]:
    blocked_response = _blocked_shadow_mutation_response("Wallet reactivation")
    if blocked_response is not None:
        return blocked_response
    return _reactivate_wallet(wallet_address)


def _save_position_manual_edit_response(payload: dict[str, Any]) -> dict[str, Any]:
    blocked_response = _blocked_shadow_mutation_response("Manual position edits")
    if blocked_response is not None:
        return blocked_response
    return _save_position_manual_edit(payload)


def _config_value_response(key: str, value: str) -> tuple[int, dict[str, Any]]:
    normalized_key = str(key or "").strip()
    normalized_value = str(value or "").strip()
    if normalized_key == "USE_REAL_MONEY":
        result = _set_live_mode_response({"enabled": normalized_value})
        return (200 if result.get("ok") else 409, result)

    blocked_response = _blocked_shadow_mutation_response("Config editing")
    if blocked_response is not None:
        return (409, blocked_response)

    _write_env_value(normalized_key, normalized_value)
    return (200, _config_snapshot())


def _recover_db_response(_body: dict[str, Any] | None = None) -> dict[str, Any]:
    result, candidate_state = _db_recovery_preflight()
    if result is not None:
        return result
    return {
        "ok": True,
        "message": _db_recovery_request_message(candidate_state),
    }


def _launch_db_recovery(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    bot_state = _bot_state_snapshot()
    result, candidate_state = _db_recovery_preflight(bot_state)
    if result is not None:
        return result

    candidate_path = str(candidate_state.get("db_recovery_candidate_path") or "").strip()
    source_path = str(candidate_state.get("db_recovery_candidate_source_path") or "").strip() or str(DB_PATH)
    direct_candidate_path = str(
        (payload or {}).get("candidate_path")
        or (payload or {}).get("backup_path")
        or (payload or {}).get("db_recovery_candidate_path")
        or ""
    ).strip()
    if direct_candidate_path:
        direct_source_path = str(
            (payload or {}).get("candidate_source_path")
            or (payload or {}).get("source_path")
            or (payload or {}).get("db_recovery_candidate_source_path")
            or DB_PATH
        ).strip()
        if _canonical_db_recovery_request_path(direct_candidate_path) != _canonical_db_recovery_request_path(
            candidate_path
        ):
            return {
                "ok": False,
                "message": (
                    "DB recovery is blocked because direct candidate_path overrides must match "
                    "the current verified backup candidate."
                ),
            }
        if _canonical_db_recovery_request_path(direct_source_path) != _canonical_db_recovery_request_path(
            source_path
        ):
            return {
                "ok": False,
                "message": (
                    "DB recovery is blocked because direct source_path overrides must match "
                    "the current verified recovery source."
                ),
            }

    result = _spawn_db_recovery_process(
        candidate_path=candidate_path,
        source_path=source_path,
        request_id=str((payload or {}).get("request_id") or "").strip(),
        requested_at=int((payload or {}).get("requested_at") or 0),
        source=str((payload or {}).get("source") or "dashboard").strip() or "dashboard",
    )
    if not result.get("ok"):
        logger.error("DB recovery queue failed: %s", result.get("message"))
        return result
    return {
        "ok": True,
        "message": _db_recovery_request_message(candidate_state),
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
                status_code, payload = _config_value_response(key, value)
                self._send_json(status_code, payload)
                return

            if path == "/api/live-mode":
                result = _set_live_mode_response(body)
                self._send_json(200 if result.get("ok") else 409, result)
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
                self._send_json(200, _drop_wallet_response(wallet_address, reason))
                return

            if path == "/api/wallets/reactivate":
                wallet_address = str(body.get("walletAddress") or body.get("wallet_address") or "").strip()
                self._send_json(200, _reactivate_wallet_response(wallet_address))
                return

            if path == "/api/positions/manual-edit":
                self._send_json(200, _save_position_manual_edit_response(body))
                return

            if path == "/api/shadow/restart":
                raw_wallet_mode = body.get("walletMode", body.get("wallet_mode"))
                if raw_wallet_mode is None:
                    keep_wallets = body.get("keepWallets", body.get("keep_wallets", True))
                    wallet_mode = "keep_all" if bool(keep_wallets) else "clear_all"
                else:
                    wallet_mode = _normalize_shadow_restart_wallet_mode(raw_wallet_mode)
                result = _launch_shadow_restart(wallet_mode)
                self._send_json(202 if result.get("ok") else 500, result)
                return

            if path == "/api/shadow/recover-db":
                result = _launch_db_recovery()
                self._send_json(202 if result.get("ok") else 500, result)
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
