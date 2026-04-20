from __future__ import annotations

import json
import logging
import math
import mimetypes
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

from kelly_watcher.config import (
    trade_log_archive_batch_rows,
    trade_log_archive_enabled,
    trade_log_archive_min_age_days,
    trade_log_archive_vacuum_enabled,
    use_real_money,
)
from kelly_watcher.data.db import (
    archive_old_trade_log_rows,
    DB_PATH,
    database_integrity_state,
    db_recovery_state,
    delete_runtime_setting,
    get_conn,
    get_runtime_setting,
    get_trade_log_read_conn,
    managed_wallet_registry_state,
    load_wallet_promotion_state,
    load_runtime_settings,
    set_runtime_setting,
    trade_log_archive_state,
)
from kelly_watcher.env_profile import ENV_ONLY_KEYS, LEGACY_ENV_PATH, active_env_path
from kelly_watcher.engine.trade_contract import NON_CHALLENGER_EXPERIMENT_ARM_SQL
from kelly_watcher.engine.wallet_trust import get_wallet_trust_state
from kelly_watcher.runtime import performance_preview as performance_preview_runtime
from kelly_watcher.runtime.wallet_discovery import (
    load_wallet_discovery_candidates,
    refresh_wallet_discovery_candidates,
)
from kelly_watcher.runtime_paths import (
    BOT_STATE_FILE,
    DATA_DIR,
    EVENT_FILE,
    IDENTITY_CACHE_PATH,
    DB_RECOVERY_REQUEST_FILE,
    MANUAL_RETRAIN_REQUEST_FILE,
    MANUAL_TRADE_REQUEST_FILE,
    REPO_ROOT,
    SHADOW_RESET_REQUEST_FILE,
    TRADE_LOG_ARCHIVE_REQUEST_FILE,
)

logger = logging.getLogger(__name__)
_DB_RECOVERY_STATE_CACHE: dict[str, Any] | None = None
_DB_RECOVERY_STATE_CACHE_AT = 0.0
_DB_RECOVERY_STATE_CACHE_TTL_SECONDS = 30.0
_DB_RECOVERY_STATE_CACHE_LOCK = threading.Lock()
_MANAGED_WALLET_TRUST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_MANAGED_WALLET_TRUST_CACHE_TTL_SECONDS = 10.0
_MANAGED_WALLET_TRUST_CACHE_LOCK = threading.Lock()

ENV_PROFILE = "default"
ENV_PATH = active_env_path()
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
    "TRADE_LOG_ARCHIVE_ENABLED",
    "TRADE_LOG_ARCHIVE_MIN_AGE_DAYS",
    "TRADE_LOG_ARCHIVE_BATCH_ROWS",
    "TRADE_LOG_ARCHIVE_VACUUM",
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


def _dashboard_web_dist_path() -> Path:
    return REPO_ROOT / "dashboard-web" / "dist"


def _resolve_dashboard_web_asset_path(request_path: str, dist_root: Path | None = None) -> Path | None:
    root = (dist_root or _dashboard_web_dist_path()).resolve()
    if not root.exists() or not root.is_dir():
        return None

    normalized = str(request_path or "/").split("?", 1)[0].strip()
    relative_path = normalized.lstrip("/") or "index.html"
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None

    if candidate.is_file():
        return candidate

    if "." in Path(relative_path).name:
        return None

    index_file = (root / "index.html").resolve()
    try:
        index_file.relative_to(root)
    except ValueError:
        return None
    return index_file if index_file.is_file() else None


def _dashboard_web_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix in {".js", ".mjs"}:
        return "text/javascript; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml; charset=utf-8"

    content_type = mimetypes.guess_type(str(path.name))[0] or "application/octet-stream"
    if content_type.startswith("text/") or content_type in {"application/javascript", "image/svg+xml"}:
        return f"{content_type}; charset=utf-8"
    return content_type


def _source_env_path() -> Path:
    if ENV_PATH.exists():
        return ENV_PATH
    repo_env_path = REPO_ROOT / ".env"
    if repo_env_path.exists():
        return repo_env_path
    if LEGACY_ENV_PATH.exists():
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
    for key, value in load_runtime_settings().items():
        if key in SAFE_ENV_KEYS:
            safe_values[key] = value
    return safe_values


def _cached_db_recovery_state(*, force: bool = False) -> dict[str, Any]:
    global _DB_RECOVERY_STATE_CACHE, _DB_RECOVERY_STATE_CACHE_AT
    now = time.time()
    with _DB_RECOVERY_STATE_CACHE_LOCK:
        if (
            not force
            and _DB_RECOVERY_STATE_CACHE is not None
            and (now - _DB_RECOVERY_STATE_CACHE_AT) < _DB_RECOVERY_STATE_CACHE_TTL_SECONDS
        ):
            return dict(_DB_RECOVERY_STATE_CACHE)
    state = dict(db_recovery_state())
    with _DB_RECOVERY_STATE_CACHE_LOCK:
        _DB_RECOVERY_STATE_CACHE = dict(state)
        _DB_RECOVERY_STATE_CACHE_AT = now
    return state


def _config_snapshot() -> dict[str, Any]:
    safe_values = _read_safe_env_values()
    legacy_bootstrap_watched_wallets = [
        wallet.strip().lower()
        for wallet in str(safe_values.get("WATCHED_WALLETS", "") or "").split(",")
        if wallet.strip()
    ]
    registry_state = _managed_wallet_registry_snapshot()
    wallet_registry_source = _wallet_registry_source()
    live_wallets = _wallet_registry_addresses()
    runtime_settings = load_runtime_settings()
    rows: list[dict[str, str]] = []
    for key, value in sorted(runtime_settings.items()):
        redacted = "************" if SECRET_KEY_RE.search(key) else (value or "unset")
        rows.append({"key": key, "value": redacted, "source": "runtime_settings"})
    for key, value in _read_env_items():
        if key == "WATCHED_WALLETS" or key in runtime_settings:
            continue
        redacted = "************" if SECRET_KEY_RE.search(key) else (value or "unset")
        rows.append({"key": key, "value": redacted, "source": "env"})
    return {
        "safe_values": safe_values,
        "watched_wallets": live_wallets,
        "live_wallets": live_wallets,
        "live_wallet_count": len(live_wallets),
        "wallet_registry_source": wallet_registry_source,
        "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
        "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
        "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
        "legacy_bootstrap_watched_wallets": legacy_bootstrap_watched_wallets,
        "rows": rows,
    }


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _sqlite_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.DatabaseError:
        return set()
    return {str(row["name"] or "").strip() for row in rows if str(row["name"] or "").strip()}


def _sqlite_fetch_rows(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _recent_training_runs(limit: int = 12) -> list[dict[str, Any]]:
    conn = None
    try:
        conn = get_conn()
        if not _sqlite_table_exists(conn, "retrain_runs"):
            return []
        columns = _sqlite_table_columns(conn, "retrain_runs")
        if not columns:
            return []
        rows = conn.execute(
            """
            SELECT *
            FROM retrain_runs
            ORDER BY
                CASE
                    WHEN finished_at > 0 THEN finished_at
                    ELSE started_at
                END DESC,
                id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        payload: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            payload.append(
                {
                    "run_id": str(item.get("id") or ""),
                    "started_at": int(item.get("started_at") or 0),
                    "finished_at": int(item.get("finished_at") or 0),
                    "scorer": str(item.get("scorer") or item.get("backend") or "xgboost"),
                    "backend": str(item.get("backend") or "xgboost"),
                    "log_loss": float(item.get("log_loss")) if item.get("log_loss") is not None else None,
                    "brier": float(item.get("brier")) if item.get("brier") is not None else None,
                    "deployed": bool(item.get("deployed")),
                    "deployed_at": int(item.get("deployed_at") or 0) if "deployed_at" in columns else 0,
                    "status": str(item.get("status") or ""),
                    "note": str(item.get("message") or item.get("note") or ""),
                }
            )
        return payload
    except sqlite3.DatabaseError as exc:
        logger.warning("Training run history unavailable: %s", exc)
        return []
    finally:
        if conn is not None:
            conn.close()


def _finite_float_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _trade_log_context_map(trade_log_ids: list[int]) -> dict[int, dict[str, Any]]:
    ids = sorted({int(value) for value in trade_log_ids if int(value or 0) > 0})
    if not ids:
        return {}

    conn = get_trade_log_read_conn()
    try:
        rows_by_id: dict[int, dict[str, Any]] = {}
        for start in range(0, len(ids), 400):
            chunk = ids[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT
                  id,
                  trade_id,
                  question,
                  COALESCE(trader_name, '') AS trader_name,
                  COALESCE(trader_address, '') AS trader_address
                FROM trade_log
                WHERE id IN ({placeholders})
                """,
                tuple(chunk),
            ).fetchall()
            rows_by_id.update(
                {
                    int(row["id"]): {
                        "trade_id": str(row["trade_id"] or "").strip(),
                        "question": str(row["question"] or "").strip(),
                        "trader_name": str(row["trader_name"] or "").strip(),
                        "trader_address": str(row["trader_address"] or "").strip(),
                    }
                    for row in rows
                }
            )
        return rows_by_id
    finally:
        conn.close()


def _performance_position_payload(
    row: dict[str, Any],
    context_map: dict[int, dict[str, Any]],
    *,
    status: str,
) -> dict[str, Any]:
    source_trade_log_id = int(row.get("source_trade_log_id") or 0)
    context = context_map.get(source_trade_log_id, {})
    entry_price = _finite_float_or_none(row.get("entry_price")) or 0.0
    size_usd = _finite_float_or_none(row.get("size_usd")) or 0.0
    shares = _finite_float_or_none(row.get("shares"))
    confidence = _finite_float_or_none(row.get("confidence"))
    pnl_usd = _finite_float_or_none(row.get("pnl_usd"))
    side_raw = str(row.get("side") or "").strip()
    normalized_side = side_raw.lower()
    if normalized_side in {"yes", "up", "buy", "long"}:
        display_side = "YES"
    elif normalized_side in {"no", "down", "sell", "short"}:
        display_side = "NO"
    else:
        display_side = side_raw.upper() if len(side_raw) <= 6 else side_raw
    potential_profit = (shares - size_usd) if shares is not None else None
    return_ratio = (pnl_usd / size_usd) if pnl_usd is not None and size_usd > 0 else None
    return {
        "trade_id": str(context.get("trade_id") or source_trade_log_id or "").strip(),
        "market_id": str(row.get("market_id") or "").strip(),
        "token_id": str(row.get("token_id") or "").strip(),
        "trader_address": str(context.get("trader_address") or "").strip(),
        "question": str(context.get("question") or row.get("market_id") or "").strip(),
        "username": str(context.get("trader_name") or "-").strip() or "-",
        "side": display_side,
        "entry_ts": int(row.get("entered_at") or 0),
        "exit_ts": int(row.get("resolution_ts") or row.get("market_close_ts") or 0),
        "price": round(entry_price, 6),
        "total": round(size_usd, 3),
        "confidence": confidence,
        "pnl": round(pnl_usd, 3) if pnl_usd is not None else 0.0,
        "return_ratio": round(return_ratio, 6) if return_ratio is not None else None,
        "potential_profit": round(float(potential_profit), 3) if potential_profit is not None else None,
        "status": status,
    }


def _performance_snapshot() -> dict[str, Any]:
    bot_state = _bot_state_snapshot()
    now_ts = time.time()
    requested_mode = str(bot_state.get("mode") or "").strip().lower() or "shadow"
    preview = performance_preview_runtime.compute_tracker_preview_summary(
        now_ts=now_ts,
        mode=requested_mode,
    )
    active_real_money = 1 if preview.mode == "live" else 0

    conn = get_trade_log_read_conn()
    try:
        shadow_open_positions = performance_preview_runtime._safe_fetch_dicts(
            conn,
            performance_preview_runtime._SHADOW_OPEN_POSITIONS_SQL,
        )
        live_positions = performance_preview_runtime._safe_fetch_dicts(
            conn,
            performance_preview_runtime._LIVE_POSITIONS_SQL,
        )
        resolved_positions = performance_preview_runtime._safe_fetch_dicts(
            conn,
            performance_preview_runtime._RESOLVED_POSITIONS_SQL,
        )
        trade_log_edits = {
            int(row["trade_log_id"]): row
            for row in performance_preview_runtime._safe_fetch_dicts(
                conn,
                performance_preview_runtime._TRADE_LOG_MANUAL_EDITS_SQL,
            )
            if row.get("trade_log_id") is not None
        }
        position_edits = {
            performance_preview_runtime._position_edit_key(
                row.get("market_id"),
                row.get("token_id"),
                row.get("side"),
                row.get("real_money"),
            ): row
            for row in performance_preview_runtime._safe_fetch_dicts(
                conn,
                performance_preview_runtime._POSITION_MANUAL_EDITS_SQL,
            )
        }
    finally:
        conn.close()

    active_open_positions = (
        [row for row in live_positions if int(row.get("real_money") or 0) == active_real_money]
        if preview.mode == "live"
        else shadow_open_positions
    )
    active_resolved_positions = [
        row for row in resolved_positions if int(row.get("real_money") or 0) == active_real_money
    ]
    effective_positions = [
        performance_preview_runtime._normalize_effective_position(
            row,
            now_ts,
            trade_log_edits,
            position_edits,
        )
        for row in [*active_open_positions, *active_resolved_positions]
    ]
    current_rows = [row for row in effective_positions if row.get("status") in {"open", "waiting"}]
    past_rows = [row for row in effective_positions if row.get("status") in {"win", "lose", "exit"}]
    context_map = _trade_log_context_map(
        [
            int(row.get("source_trade_log_id") or 0)
            for row in effective_positions
            if int(row.get("source_trade_log_id") or 0) > 0
        ]
    )

    current_positions = [
        _performance_position_payload(row, context_map, status="current")
        for row in sorted(
            current_rows,
            key=lambda item: (
                int(item.get("entered_at") or 0),
                str(item.get("market_id") or ""),
            ),
            reverse=True,
        )
    ]
    past_positions = [
        _performance_position_payload(row, context_map, status="past")
        for row in sorted(
            past_rows,
            key=performance_preview_runtime._position_sort_key,
            reverse=True,
        )
    ]

    current_exposure_usd = round(sum(float(row.get("total") or 0.0) for row in current_positions), 3)
    open_pnl_usd = round(sum(float(row.get("pnl") or 0.0) for row in current_positions), 3)
    realized_pnl_usd = _finite_float_or_none(preview.total_pnl) or 0.0
    current_balance_usd = _finite_float_or_none(preview.current_equity)
    available_cash_usd = _finite_float_or_none(preview.current_balance)
    if current_balance_usd is None and available_cash_usd is not None:
        current_balance_usd = round(available_cash_usd + current_exposure_usd, 3)
    if available_cash_usd is None and current_balance_usd is not None:
        available_cash_usd = round(current_balance_usd - current_exposure_usd, 3)
    starting_balance_usd = (
        round(current_balance_usd - realized_pnl_usd - open_pnl_usd, 3)
        if current_balance_usd is not None
        else None
    )

    balance_curve: list[dict[str, Any]] = []
    running_balance = starting_balance_usd
    if running_balance is not None:
        for row in sorted(past_rows, key=performance_preview_runtime._position_sort_key):
            running_balance = round(running_balance + float(row.get("pnl_usd") or 0.0), 3)
            balance_curve.append(
                {
                    "ts": int(row.get("resolution_ts") or row.get("market_close_ts") or row.get("entered_at") or 0),
                    "balance": running_balance,
                }
            )

    return {
        "ok": True,
        "mode": preview.mode,
        "starting_balance_usd": starting_balance_usd,
        "current_balance_usd": current_balance_usd,
        "available_cash_usd": available_cash_usd,
        "current_exposure_usd": current_exposure_usd,
        "realized_pnl_usd": realized_pnl_usd,
        "open_pnl_usd": open_pnl_usd,
        "net_pnl_usd": round(realized_pnl_usd + open_pnl_usd, 3),
        "return_pct": _finite_float_or_none(preview.return_pct),
        "win_rate": _finite_float_or_none(preview.win_rate),
        "profit_factor": _finite_float_or_none(preview.profit_factor),
        "expectancy_usd": _finite_float_or_none(preview.expectancy_usd),
        "max_drawdown_pct": _finite_float_or_none(preview.max_drawdown_pct),
        "avg_confidence": _finite_float_or_none(preview.avg_confidence),
        "resolved_count": int(preview.resolved or 0),
        "current_position_count": len(current_positions),
        "current_positions": current_positions,
        "past_positions": past_positions,
        "balance_curve": balance_curve,
        "data_warning": str(preview.data_warning or "").strip(),
    }


def _managed_wallet_registry_snapshot() -> dict[str, Any]:
    try:
        return dict(managed_wallet_registry_state())
    except sqlite3.DatabaseError as exc:
        return {
            "managed_wallet_registry_available": False,
            "managed_wallet_registry_status": "unreadable",
            "managed_wallet_registry_error": str(exc).splitlines()[0].strip(),
            "managed_wallets": [],
            "managed_wallet_count": 0,
            "managed_wallet_total_count": 0,
            "managed_wallet_registry_updated_at": 0,
        }


def _wallet_registry_source_from_state(registry_state: dict[str, Any] | None = None) -> str:
    state = dict(registry_state or _managed_wallet_registry_snapshot())
    status = str(state.get("managed_wallet_registry_status") or "").strip().lower()
    if status in {"ready", "empty"}:
        return "managed_wallets"
    return "unavailable"


def _wallet_registry_addresses_from_state(registry_state: dict[str, Any] | None = None) -> list[str]:
    state = dict(registry_state or _managed_wallet_registry_snapshot())
    status = str(state.get("managed_wallet_registry_status") or "").strip().lower()
    if status not in {"ready", "empty"}:
        return []
    wallets = state.get("managed_wallets") or []
    seen: set[str] = set()
    normalized: list[str] = []
    for value in wallets if isinstance(wallets, list) else []:
        wallet = str(value or "").strip().lower()
        if not wallet or wallet in seen:
            continue
        seen.add(wallet)
        normalized.append(wallet)
    return normalized


def _discovery_candidate_gate_status(row: dict[str, Any]) -> str:
    accepted = bool(row.get("accepted"))
    if accepted:
        return "ready"
    reason = str(row.get("reject_reason") or "").strip().lower()
    if not reason:
        return "review_error"
    if reason.startswith("analysis_failed"):
        return "review_error"
    if "local_copy" in reason or "local performance" in reason:
        return "review_local_performance"
    if "conviction" in reason:
        return "review_conviction"
    if any(token in reason for token in ("avg_buy_size", "large_buy", "buy_size")):
        return "review_size"
    if any(token in reason for token in ("lead", "late_buy", "last_trade_age")):
        return "review_timing"
    return "review_sample"


def _discovery_candidate_rows(
    limit: int | None = None,
    *,
    discovery_last_scan_at: int = 0,
    now_ts: int | None = None,
) -> list[dict[str, Any]]:
    candidates = load_wallet_discovery_candidates(limit=limit)
    effective_now_ts = int(now_ts if now_ts is not None else time.time())
    wallets = [
        str(row.get("wallet_address") or "").strip().lower()
        for row in candidates
        if str(row.get("wallet_address") or "").strip()
    ]
    policy_metrics_map = _wallet_policy_metrics_rows(wallets)
    promotion_state_map = load_wallet_promotion_state(wallets)
    trust_state_map = _wallet_trust_snapshot_map(wallets)
    watch_state_map = _wallet_watch_state_map(wallets)
    tracked_wallets = set(_wallet_registry_addresses())
    enriched: list[dict[str, Any]] = []
    for row in candidates:
        wallet = str(row.get("wallet_address") or "").strip().lower()
        if not wallet:
            continue
        policy_metrics = policy_metrics_map.get(wallet, {})
        promotion_state = promotion_state_map.get(wallet, {})
        trust_state = trust_state_map.get(wallet, {})
        watch_state = watch_state_map.get(wallet, {})
        candidate_updated_at = int(row.get("updated_at") or 0)
        payload = dict(row)
        payload["wallet_address"] = wallet
        payload["copyability_gate_status"] = str(
            row.get("copyability_gate_status") or _discovery_candidate_gate_status(row)
        ).strip()
        payload["promoted"] = bool(promotion_state.get("is_auto_promoted"))
        payload["promoted_at"] = int(promotion_state.get("promoted_at") or 0)
        payload["tracked"] = wallet in tracked_wallets
        payload["watch_status"] = str(watch_state.get("status") or "").strip().lower()
        payload["watch_status_reason"] = str(watch_state.get("status_reason") or "").strip()
        payload["watch_dropped_at"] = int(watch_state.get("dropped_at") or 0)
        payload["watch_reactivated_at"] = int(watch_state.get("reactivated_at") or 0)
        payload["watch_tracking_started_at"] = int(watch_state.get("tracking_started_at") or 0)
        payload["watch_last_source_ts_at_status"] = int(watch_state.get("last_source_ts_at_status") or 0)
        payload["watch_updated_at"] = int(watch_state.get("updated_at") or 0)
        payload["candidate_updated_at"] = candidate_updated_at
        payload["wallet_discovery_last_scan_at"] = max(int(discovery_last_scan_at or 0), 0)
        payload["candidate_age_seconds"] = (
            max(effective_now_ts - candidate_updated_at, 0)
            if candidate_updated_at > 0
            else None
        )
        payload["candidate_is_stale"] = False
        payload["candidate_stale_reason"] = ""
        if candidate_updated_at <= 0:
            payload["candidate_is_stale"] = True
            payload["candidate_stale_reason"] = "candidate row is missing a discovery refresh timestamp"
        elif discovery_last_scan_at > 0 and candidate_updated_at < discovery_last_scan_at:
            payload["candidate_is_stale"] = True
            payload["candidate_stale_reason"] = "candidate row predates the latest discovery scan"
        payload["post_promotion_baseline_at"] = int(
            policy_metrics.get("post_promotion_baseline_at")
            or promotion_state.get("baseline_at")
            or 0
        )
        payload["post_promotion_evidence_ready"] = bool(policy_metrics.get("post_promotion_evidence_ready") or False)
        payload["post_promotion_evidence_note"] = str(policy_metrics.get("post_promotion_evidence_note") or "").strip()
        payload["post_promotion_total_buy_signals"] = int(policy_metrics.get("post_promotion_total_buy_signals") or 0)
        payload["post_promotion_uncopyable_skip_rate"] = float(
            policy_metrics.get("post_promotion_uncopyable_skip_rate") or 0.0
        )
        payload["post_promotion_resolved_copied_count"] = int(
            policy_metrics.get("post_promotion_resolved_copied_count") or 0
        )
        payload["post_promotion_resolved_copied_avg_return"] = (
            float(policy_metrics["post_promotion_resolved_copied_avg_return"])
            if policy_metrics.get("post_promotion_resolved_copied_avg_return") is not None
            else None
        )
        payload["post_promotion_resolved_copied_total_pnl_usd"] = float(
            policy_metrics.get("post_promotion_resolved_copied_total_pnl_usd") or 0.0
        )
        payload["local_quality_score"] = (
            float(policy_metrics["local_quality_score"])
            if policy_metrics.get("local_quality_score") is not None
            else None
        )
        payload["local_weight"] = float(policy_metrics.get("local_weight") or 0.0)
        payload["local_drop_ready"] = bool(policy_metrics.get("local_drop_ready") or False)
        payload["local_drop_reason"] = str(policy_metrics.get("local_drop_reason") or "").strip()
        payload["trust_tier"] = str(trust_state.get("trust_tier") or "").strip()
        payload["trust_size_multiplier"] = (
            float(trust_state["trust_size_multiplier"])
            if trust_state.get("trust_size_multiplier") is not None
            else None
        )
        payload["trust_note"] = str(trust_state.get("trust_note") or "").strip()
        payload["wallet_family"] = str(trust_state.get("wallet_family") or "").strip()
        payload["wallet_family_multiplier"] = (
            float(trust_state["wallet_family_multiplier"])
            if trust_state.get("wallet_family_multiplier") is not None
            else None
        )
        payload["wallet_family_note"] = str(trust_state.get("wallet_family_note") or "").strip()
        enriched.append(payload)
    return enriched


def _wallet_registry_source() -> str:
    return _wallet_registry_source_from_state(_managed_wallet_registry_snapshot())


def _wallet_registry_addresses() -> list[str]:
    return _wallet_registry_addresses_from_state(_managed_wallet_registry_snapshot())


def _wallet_watch_state_map(wallet_addresses: list[str]) -> dict[str, dict[str, Any]]:
    wallets = [wallet.strip().lower() for wallet in wallet_addresses if str(wallet or "").strip()]
    if not wallets:
        return {}
    placeholders = ",".join("?" for _ in wallets)
    try:
        rows = _sqlite_fetch_rows(
            f"""
            SELECT
              wallet_address,
              status,
              status_reason,
              dropped_at,
              reactivated_at,
              tracking_started_at,
              last_source_ts_at_status,
              updated_at
            FROM wallet_watch_state
            WHERE wallet_address IN ({placeholders})
            """,
            wallets,
        )
    except sqlite3.DatabaseError:
        return {}

    return {str(row["wallet_address"] or "").strip().lower(): row for row in rows}


def _discover_candidate_map(limit: int | None = None) -> dict[str, dict[str, Any]]:
    try:
        rows = load_wallet_discovery_candidates(limit=limit)
    except sqlite3.DatabaseError:
        return {}
    return {
        str(row.get("wallet_address") or "").strip().lower(): row
        for row in rows
        if str(row.get("wallet_address") or "").strip()
    }


def _wallet_policy_metrics_rows(wallet_addresses: list[str]) -> dict[str, dict[str, Any]]:
    wallets = [str(wallet or "").strip().lower() for wallet in wallet_addresses if str(wallet or "").strip()]
    if not wallets:
        return {}

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        if not _sqlite_table_exists(conn, "wallet_policy_metrics"):
            return {}
        rows = conn.execute(
            f"""
            SELECT
              wallet_address,
              local_quality_score,
              local_weight,
              local_drop_ready,
              local_drop_reason,
              post_promotion_baseline_at,
              post_promotion_source,
              post_promotion_reason,
              post_promotion_total_buy_signals,
              post_promotion_uncopyable_skips,
              post_promotion_timing_skips,
              post_promotion_liquidity_skips,
              post_promotion_uncopyable_skip_rate,
              post_promotion_resolved_copied_count,
              post_promotion_resolved_copied_win_rate,
              post_promotion_resolved_copied_avg_return,
              post_promotion_resolved_copied_total_pnl_usd,
              post_promotion_last_resolved_at,
              post_promotion_evidence_ready,
              post_promotion_evidence_note,
              updated_at
            FROM wallet_policy_metrics
            WHERE wallet_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    return {
        str(row["wallet_address"] or "").strip().lower(): dict(row)
        for row in rows
        if str(row["wallet_address"] or "").strip()
    }


def _wallet_trust_snapshot_map(wallet_addresses: list[str]) -> dict[str, dict[str, Any]]:
    wallets = [str(wallet or "").strip().lower() for wallet in wallet_addresses if str(wallet or "").strip()]
    if not wallets:
        return {}

    now = time.time()
    snapshots: dict[str, dict[str, Any]] = {}
    stale_wallets: list[str] = []
    with _MANAGED_WALLET_TRUST_CACHE_LOCK:
        for wallet in wallets:
            cached = _MANAGED_WALLET_TRUST_CACHE.get(wallet)
            if cached and now - float(cached[0]) <= _MANAGED_WALLET_TRUST_CACHE_TTL_SECONDS:
                snapshots[wallet] = dict(cached[1])
            else:
                stale_wallets.append(wallet)

    if stale_wallets:
        refreshed_at = time.time()
        refreshed: dict[str, dict[str, Any]] = {}
        for wallet in stale_wallets:
            try:
                state = get_wallet_trust_state(wallet)
                refreshed[wallet] = {
                    "trust_tier": str(state.tier or "").strip(),
                    "trust_size_multiplier": float(state.size_multiplier),
                    "trust_note": str(state.tier_note or "").strip(),
                    "wallet_family": str(state.family or "").strip(),
                    "wallet_family_multiplier": float(state.family_multiplier),
                    "wallet_family_note": str(state.family_note or "").strip(),
                }
            except sqlite3.DatabaseError:
                refreshed[wallet] = {}
        with _MANAGED_WALLET_TRUST_CACHE_LOCK:
            for wallet, snapshot in refreshed.items():
                _MANAGED_WALLET_TRUST_CACHE[wallet] = (refreshed_at, dict(snapshot))
        snapshots.update(refreshed)

    return snapshots


def _managed_wallet_rows(limit: int | None = None) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        if _sqlite_table_exists(conn, "managed_wallets"):
            columns = _sqlite_table_columns(conn, "managed_wallets")
            select_parts = [
                "wallet_address",
                "tracking_enabled" if "tracking_enabled" in columns else "1 AS tracking_enabled",
                "source" if "source" in columns else "'managed_wallets' AS source",
                "added_at" if "added_at" in columns else "0 AS added_at",
                "updated_at" if "updated_at" in columns else "0 AS updated_at",
                "disabled_at" if "disabled_at" in columns else "0 AS disabled_at",
                "disabled_reason" if "disabled_reason" in columns else "'' AS disabled_reason",
                "metadata_json" if "metadata_json" in columns else "'{}' AS metadata_json",
            ]
            query = (
                "SELECT\n  "
                + ", ".join(select_parts)
                + "\nFROM managed_wallets\n"
                + "ORDER BY COALESCE(tracking_enabled, 0) DESC, "
                + "COALESCE(updated_at, added_at, 0) DESC, wallet_address ASC\n"
                + "LIMIT ?"
            )
            rows = conn.execute(query, (max(int(limit or 250), 1),)).fetchall()
            registry_source = "managed_wallets"
        else:
            return []
    finally:
        conn.close()

    identities = _identity_lookup()
    discovery_map = _discover_candidate_map(limit)
    watch_state_map = _wallet_watch_state_map([str(row["wallet_address"] or "") for row in rows])
    policy_metrics_map = _wallet_policy_metrics_rows([str(row["wallet_address"] or "") for row in rows])
    promotion_state_map = load_wallet_promotion_state([str(row["wallet_address"] or "") for row in rows])
    trust_state_map = _wallet_trust_snapshot_map([str(row["wallet_address"] or "") for row in rows])

    payloads: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        wallet = str(row_dict.get("wallet_address") or "").strip().lower()
        if not wallet:
            continue
        identity = identities.get(wallet, "")
        discovery = discovery_map.get(wallet, {})
        watch_state = watch_state_map.get(wallet, {})
        policy_metrics = policy_metrics_map.get(wallet, {})
        promotion_state = promotion_state_map.get(wallet, {})
        trust_state = trust_state_map.get(wallet, {})
        source = str(row_dict.get("source") or registry_source).strip()
        tracking_enabled = bool(row_dict.get("tracking_enabled")) if "tracking_enabled" in row_dict else str(row_dict.get("status") or "").strip().lower() != "dropped"
        watch_status = str(watch_state.get("status") or row_dict.get("status") or "").strip().lower()
        payload: dict[str, Any] = {
            "wallet_address": wallet,
            "username": identity or str(discovery.get("username") or "").strip(),
            "registry_source": registry_source,
            "source": source or registry_source,
            "tracking_enabled": tracking_enabled,
            "status": ("disabled" if not tracking_enabled else (watch_status or "active")),
            "status_reason": str(watch_state.get("status_reason") or row_dict.get("disabled_reason") or row_dict.get("status_reason") or "").strip(),
            "added_at": int(row_dict.get("added_at") or row_dict.get("tracking_started_at") or 0),
            "updated_at": int(row_dict.get("updated_at") or 0),
            "disabled_at": int(row_dict.get("disabled_at") or row_dict.get("dropped_at") or 0),
            "disabled_reason": str(row_dict.get("disabled_reason") or row_dict.get("status_reason") or "").strip(),
            "tracking_started_at": int(row_dict.get("tracking_started_at") or 0),
            "last_source_ts_at_status": int(row_dict.get("last_source_ts_at_status") or 0),
            "post_promotion_promoted_at": int(promotion_state.get("promoted_at") or 0),
            "post_promotion_baseline_at": int(
                policy_metrics.get("post_promotion_baseline_at")
                or promotion_state.get("baseline_at")
                or 0
            ),
            "post_promotion_boundary_action": str(
                promotion_state.get("boundary_action")
                or promotion_state.get("event_action")
                or ("promote" if (policy_metrics.get("post_promotion_baseline_at") or promotion_state.get("baseline_at")) else "")
            ).strip(),
            "post_promotion_boundary_source": str(
                promotion_state.get("boundary_source")
                or promotion_state.get("event_source")
                or promotion_state.get("promotion_source")
                or ""
            ).strip(),
            "post_promotion_boundary_reason": str(
                promotion_state.get("boundary_reason")
                or promotion_state.get("event_reason")
                or promotion_state.get("promotion_reason")
                or ""
            ).strip(),
            "post_promotion_source": str(
                policy_metrics.get("post_promotion_source")
                or promotion_state.get("promotion_source")
                or ""
            ).strip(),
            "post_promotion_reason": str(
                policy_metrics.get("post_promotion_reason")
                or promotion_state.get("promotion_reason")
                or ""
            ).strip(),
            "post_promotion_total_buy_signals": int(policy_metrics.get("post_promotion_total_buy_signals") or 0),
            "post_promotion_uncopyable_skips": int(policy_metrics.get("post_promotion_uncopyable_skips") or 0),
            "post_promotion_timing_skips": int(policy_metrics.get("post_promotion_timing_skips") or 0),
            "post_promotion_liquidity_skips": int(policy_metrics.get("post_promotion_liquidity_skips") or 0),
            "post_promotion_uncopyable_skip_rate": float(policy_metrics.get("post_promotion_uncopyable_skip_rate") or 0.0),
            "post_promotion_resolved_copied_count": int(policy_metrics.get("post_promotion_resolved_copied_count") or 0),
            "post_promotion_resolved_copied_win_rate": (
                float(policy_metrics.get("post_promotion_resolved_copied_win_rate"))
                if policy_metrics.get("post_promotion_resolved_copied_win_rate") is not None
                else None
            ),
            "post_promotion_resolved_copied_avg_return": (
                float(policy_metrics.get("post_promotion_resolved_copied_avg_return"))
                if policy_metrics.get("post_promotion_resolved_copied_avg_return") is not None
                else None
            ),
            "post_promotion_resolved_copied_total_pnl_usd": float(
                policy_metrics.get("post_promotion_resolved_copied_total_pnl_usd") or 0.0
            ),
            "post_promotion_last_resolved_at": int(policy_metrics.get("post_promotion_last_resolved_at") or 0),
            "post_promotion_evidence_ready": bool(policy_metrics.get("post_promotion_evidence_ready") or False),
            "post_promotion_evidence_note": str(policy_metrics.get("post_promotion_evidence_note") or "").strip(),
            "trust_tier": str(trust_state.get("trust_tier") or "").strip(),
            "trust_size_multiplier": (
                float(trust_state["trust_size_multiplier"])
                if trust_state.get("trust_size_multiplier") is not None
                else None
            ),
            "trust_note": str(trust_state.get("trust_note") or "").strip(),
            "wallet_family": str(trust_state.get("wallet_family") or "").strip(),
            "wallet_family_multiplier": (
                float(trust_state["wallet_family_multiplier"])
                if trust_state.get("wallet_family_multiplier") is not None
                else None
            ),
            "wallet_family_note": str(trust_state.get("wallet_family_note") or "").strip(),
        }
        if discovery:
            payload.update(
                discovery_score=float(discovery.get("follow_score") or 0),
                discovery_accepted=bool(discovery.get("accepted")),
                discovery_reason=str(discovery.get("reject_reason") or "").strip(),
                discovery_style=str(discovery.get("style") or discovery.get("watch_style") or "").strip(),
                discovery_rank=discovery.get("leaderboard_rank"),
                discovery_sources=list(discovery.get("source_labels") or []),
                discovery_updated_at=int(discovery.get("updated_at") or 0),
            )
        payloads.append(payload)
    return payloads


def _managed_wallet_panel_rows(limit: int | None = None) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        if not _sqlite_table_exists(conn, "managed_wallets"):
            return []
        columns = _sqlite_table_columns(conn, "managed_wallets")
        select_parts = [
            "wallet_address",
            "tracking_enabled" if "tracking_enabled" in columns else "1 AS tracking_enabled",
            "added_at" if "added_at" in columns else "0 AS added_at",
            "updated_at" if "updated_at" in columns else "0 AS updated_at",
            "disabled_at" if "disabled_at" in columns else "0 AS disabled_at",
            "disabled_reason" if "disabled_reason" in columns else "'' AS disabled_reason",
        ]
        query = (
            "SELECT\n  "
            + ", ".join(select_parts)
            + "\nFROM managed_wallets\n"
            + "ORDER BY COALESCE(tracking_enabled, 0) DESC, "
            + "COALESCE(updated_at, added_at, 0) DESC, wallet_address ASC\n"
            + "LIMIT ?"
        )
        rows = conn.execute(query, (max(int(limit or 1000), 1),)).fetchall()
    finally:
        conn.close()

    wallets = [
        str(row["wallet_address"] or "").strip().lower()
        for row in rows
        if str(row["wallet_address"] or "").strip()
    ]
    identities = _identity_lookup()
    watch_state_map = _wallet_watch_state_map(wallets)
    policy_metrics_map = _wallet_policy_metrics_rows(wallets)

    payloads: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        wallet = str(row_dict.get("wallet_address") or "").strip().lower()
        if not wallet:
            continue
        watch_state = watch_state_map.get(wallet, {})
        policy_metrics = policy_metrics_map.get(wallet, {})
        tracking_enabled = bool(row_dict.get("tracking_enabled"))
        status = str(watch_state.get("status") or "").strip().lower()
        if not status:
            status = "active" if tracking_enabled else "disabled"
        payloads.append(
            {
                "wallet_address": wallet,
                "username": identities.get(wallet, ""),
                "tracking_enabled": tracking_enabled,
                "status": status,
                "status_reason": str(watch_state.get("status_reason") or row_dict.get("disabled_reason") or "").strip(),
                "added_at": int(row_dict.get("added_at") or 0),
                "updated_at": int(row_dict.get("updated_at") or 0),
                "disabled_at": int(row_dict.get("disabled_at") or watch_state.get("dropped_at") or 0),
                "disabled_reason": str(row_dict.get("disabled_reason") or watch_state.get("status_reason") or "").strip(),
                "tracking_started_at": int(watch_state.get("tracking_started_at") or row_dict.get("added_at") or 0),
                "last_source_ts_at_status": int(watch_state.get("last_source_ts_at_status") or 0),
                "post_promotion_uncopyable_skip_rate": (
                    float(policy_metrics.get("post_promotion_uncopyable_skip_rate"))
                    if policy_metrics.get("post_promotion_uncopyable_skip_rate") is not None
                    else None
                ),
                "post_promotion_resolved_copied_count": int(policy_metrics.get("post_promotion_resolved_copied_count") or 0),
                "post_promotion_resolved_copied_win_rate": (
                    float(policy_metrics.get("post_promotion_resolved_copied_win_rate"))
                    if policy_metrics.get("post_promotion_resolved_copied_win_rate") is not None
                    else None
                ),
                "post_promotion_resolved_copied_total_pnl_usd": float(
                    policy_metrics.get("post_promotion_resolved_copied_total_pnl_usd") or 0.0
                ),
            }
        )
    return payloads


def _wallet_page_unavailable_payload(
    registry_state: dict[str, Any],
    *,
    category: str | None = None,
) -> dict[str, Any]:
    source = _wallet_registry_source_from_state(registry_state)
    payload = {
        "ok": False,
        "source": source,
        "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
        "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
        "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
        "managed_wallet_count": int(registry_state.get("managed_wallet_count") or 0),
        "managed_wallet_total_count": int(registry_state.get("managed_wallet_total_count") or 0),
        "managed_wallet_registry_updated_at": int(registry_state.get("managed_wallet_registry_updated_at") or 0),
        "message": (
            "Managed wallet data is unavailable because the canonical managed_wallets table is missing or unreadable. "
            "Recover or reset the DB before trusting wallet inventory."
        ),
    }
    if category:
        payload.update(category=category, wallets=[], count=0)
    return payload


def _wallet_page_rows_response(category: str, limit: int | None = None) -> dict[str, Any]:
    registry_state = _managed_wallet_registry_snapshot()
    source = _wallet_registry_source_from_state(registry_state)
    if source != "managed_wallets":
        return _wallet_page_unavailable_payload(registry_state, category=category)

    wallets = _managed_wallet_panel_rows(limit=max(int(limit or 1000), 1))
    if category == "tracked":
        filtered = [
            row
            for row in wallets
            if str(row.get("status") or "").strip().lower() != "disabled"
        ]
        filtered.sort(
            key=lambda row: (
                int(row.get("tracking_started_at") or 0),
                float(row.get("post_promotion_resolved_copied_total_pnl_usd") or 0.0),
                str(row.get("wallet_address") or ""),
            ),
            reverse=True,
        )
    else:
        filtered = [
            row
            for row in wallets
            if str(row.get("status") or "").strip().lower() == "disabled"
        ]
        filtered.sort(
            key=lambda row: (
                int(row.get("disabled_at") or 0),
                int(row.get("updated_at") or 0),
                str(row.get("wallet_address") or ""),
            ),
            reverse=True,
        )

    return {
        "ok": True,
        "category": category,
        "source": source,
        "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
        "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
        "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
        "managed_wallet_count": int(registry_state.get("managed_wallet_count") or 0),
        "managed_wallet_total_count": int(registry_state.get("managed_wallet_total_count") or 0),
        "managed_wallet_registry_updated_at": int(registry_state.get("managed_wallet_registry_updated_at") or 0),
        "wallets": filtered,
        "count": len(filtered),
    }


def _wallet_summary_response(limit: int | None = None) -> dict[str, Any]:
    registry_state = _managed_wallet_registry_snapshot()
    source = _wallet_registry_source_from_state(registry_state)
    if source != "managed_wallets":
        payload = _wallet_page_unavailable_payload(registry_state)
        payload.update(
            tracked_count=0,
            dropped_count=0,
            discovery_candidate_count=int(_bot_state_snapshot().get("wallet_discovery_candidate_count") or 0),
            best_wallets=[],
            worst_wallets=[],
        )
        return payload

    wallets = _managed_wallet_panel_rows(limit=max(int(limit or 1000), 1))
    tracked_count = sum(
        1
        for row in wallets
        if str(row.get("status") or "").strip().lower() != "disabled"
    )
    dropped_count = sum(
        1
        for row in wallets
        if str(row.get("status") or "").strip().lower() == "disabled"
    )

    def _best_key(row: dict[str, Any]) -> tuple[float, int, str]:
        return (
            float(row.get("post_promotion_resolved_copied_total_pnl_usd") or 0.0),
            int(row.get("post_promotion_resolved_copied_count") or 0),
            str(row.get("username") or row.get("wallet_address") or ""),
        )

    def _worst_key(row: dict[str, Any]) -> tuple[float, int, str]:
        return (
            float(row.get("post_promotion_resolved_copied_total_pnl_usd") or 0.0),
            -int(row.get("post_promotion_resolved_copied_count") or 0),
            str(row.get("username") or row.get("wallet_address") or ""),
        )

    best_wallets = sorted(wallets, key=_best_key, reverse=True)[:8]
    worst_wallets = sorted(wallets, key=_worst_key)[:8]
    bot_state = _bot_state_snapshot()
    return {
        "ok": True,
        "source": source,
        "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
        "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
        "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
        "managed_wallet_count": len(wallets),
        "managed_wallet_total_count": int(registry_state.get("managed_wallet_total_count") or len(wallets)),
        "managed_wallet_registry_updated_at": int(registry_state.get("managed_wallet_registry_updated_at") or 0),
        "tracked_count": tracked_count,
        "dropped_count": dropped_count,
        "discovery_candidate_count": int(bot_state.get("wallet_discovery_candidate_count") or 0),
        "best_wallets": best_wallets,
        "worst_wallets": worst_wallets,
    }


def _wallet_membership_events(limit: int | None = None) -> tuple[list[dict[str, Any]], str]:
    conn = get_conn()
    try:
        if _sqlite_table_exists(conn, "wallet_membership_events"):
            rows = conn.execute(
                """
                SELECT
                  wallet_address,
                  action,
                  source,
                  reason,
                  payload_json,
                  created_at
                FROM wallet_membership_events
                ORDER BY created_at DESC, wallet_address ASC
                LIMIT ?
                """,
                (max(int(limit or 250), 1),),
            ).fetchall()
            source = "wallet_membership_events"
            events: list[dict[str, Any]] = []
            for row in rows:
                payload_json = str(row["payload_json"] or "{}")
                try:
                    payload = json.loads(payload_json)
                except json.JSONDecodeError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                events.append(
                    {
                        "wallet_address": str(row["wallet_address"] or "").strip().lower(),
                        "action": str(row["action"] or "").strip(),
                        "source": str(row["source"] or "").strip(),
                        "reason": str(row["reason"] or "").strip(),
                        "created_at": int(row["created_at"] or 0),
                        "payload": payload,
                    }
                )
            return events, source

        if _sqlite_table_exists(conn, "wallet_watch_state"):
            rows = conn.execute(
                """
                SELECT
                  wallet_address,
                  status,
                  status_reason,
                  dropped_at,
                  reactivated_at,
                  tracking_started_at,
                  updated_at
                FROM wallet_watch_state
                ORDER BY updated_at DESC, wallet_address ASC
                LIMIT ?
                """,
                (max(int(limit or 250), 1),),
            ).fetchall()
            events = []
            for row in rows:
                wallet = str(row["wallet_address"] or "").strip().lower()
                if not wallet:
                    continue
                created_at = int(row["updated_at"] or row["tracking_started_at"] or 0)
                events.append(
                    {
                        "wallet_address": wallet,
                        "action": str(row["status"] or "tracked").strip(),
                        "source": "wallet_watch_state",
                        "reason": str(row["status_reason"] or "").strip(),
                        "created_at": created_at,
                        "payload": {
                            "status": str(row["status"] or "").strip(),
                            "dropped_at": int(row["dropped_at"] or 0),
                            "reactivated_at": int(row["reactivated_at"] or 0),
                            "tracking_started_at": int(row["tracking_started_at"] or 0),
                        },
                    }
                )
            return events, "wallet_watch_state"
        return [], "unavailable"
    finally:
        conn.close()


def _wallet_registry_summary(limit: int | None = None) -> dict[str, Any]:
    registry_state = _managed_wallet_registry_snapshot()
    wallets = _managed_wallet_rows(limit)
    event_rows, event_source = _wallet_membership_events(limit)
    source = _wallet_registry_source_from_state(registry_state)
    if source != "managed_wallets":
        return {
            "ok": False,
            "source": source,
            "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
            "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
            "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
            "managed_wallet_count": int(registry_state.get("managed_wallet_count") or 0),
            "managed_wallet_total_count": int(registry_state.get("managed_wallet_total_count") or 0),
            "managed_wallet_registry_updated_at": int(registry_state.get("managed_wallet_registry_updated_at") or 0),
            "wallets": [],
            "count": 0,
            "events": event_rows,
            "event_source": event_source,
            "event_count": len(event_rows),
            "message": (
                "Managed wallet registry is unavailable because the canonical managed_wallets table is missing or unreadable. "
                "Recover or reset the DB before trusting wallet inventory."
            ),
        }
    return {
        "ok": True,
        "source": source,
        "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
        "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
        "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
        "managed_wallet_count": int(registry_state.get("managed_wallet_count") or 0),
        "managed_wallet_total_count": int(registry_state.get("managed_wallet_total_count") or 0),
        "managed_wallet_registry_updated_at": int(registry_state.get("managed_wallet_registry_updated_at") or 0),
        "wallets": wallets,
        "count": len(wallets),
        "events": event_rows,
        "event_source": event_source,
        "event_count": len(event_rows),
    }


def _discovery_candidates_response(limit: int | None = None) -> dict[str, Any]:
    integrity = database_integrity_state()
    registry_state = _managed_wallet_registry_snapshot()
    bot_state = _bot_state_snapshot()
    registry_status = str(registry_state.get("managed_wallet_registry_status") or "").strip().lower()
    if bool(integrity.get("db_integrity_known")) and not bool(integrity.get("db_integrity_ok")):
        detail = str(integrity.get("db_integrity_message") or "").splitlines()[0].strip()
        suffix = f": {detail}" if detail else ""
        return {
            "ok": False,
            "source": "wallet_discovery_candidates",
            "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
            "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
            "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
            "count": 0,
            "ready_count": 0,
            "review_count": 0,
            "candidates": [],
            "message": f"Discovery candidates are unavailable because SQLite integrity check failed{suffix}.",
        }
    if registry_status in {"missing", "unreadable"}:
        detail = str(registry_state.get("managed_wallet_registry_error") or "").strip()
        suffix = f": {detail}" if detail else ""
        return {
            "ok": False,
            "source": "wallet_discovery_candidates",
            "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
            "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
            "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
            "count": 0,
            "ready_count": 0,
            "review_count": 0,
            "candidates": [],
            "message": (
                "Discovery candidates are unavailable because the managed wallet registry is "
                + ("unreadable" if registry_status == "unreadable" else "missing")
                + suffix
                + "."
            ),
        }
    discovery_last_scan_at = int(bot_state.get("wallet_discovery_last_scan_at") or 0)
    candidates = _discovery_candidate_rows(limit=limit, discovery_last_scan_at=discovery_last_scan_at)
    accepted_count = sum(1 for row in candidates if bool(row.get("accepted")))
    stale_count = sum(1 for row in candidates if bool(row.get("candidate_is_stale")))
    tracked_count = sum(1 for row in candidates if bool(row.get("tracked")))
    dropped_count = sum(1 for row in candidates if str(row.get("watch_status") or "").strip().lower() == "dropped")
    reactivated_count = sum(1 for row in candidates if int(row.get("watch_reactivated_at") or 0) > 0)
    promoted_count = sum(1 for row in candidates if bool(row.get("promoted")))
    return {
        "ok": True,
        "source": "wallet_discovery_candidates",
        "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
        "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
        "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
        "wallet_discovery_last_scan_at": discovery_last_scan_at,
        "candidates": candidates,
        "count": len(candidates),
        "ready_count": accepted_count,
        "review_count": max(len(candidates) - accepted_count, 0),
        "stale_count": stale_count,
        "tracked_count": tracked_count,
        "dropped_count": dropped_count,
        "reactivated_count": reactivated_count,
        "promoted_count": promoted_count,
    }


def _discovery_scan_response() -> dict[str, Any]:
    blocked_response = _blocked_shadow_mutation_response("Wallet discovery scan")
    if blocked_response is not None:
        return blocked_response
    integrity = database_integrity_state()
    if bool(integrity.get("db_integrity_known")) and not bool(integrity.get("db_integrity_ok")):
        detail = str(integrity.get("db_integrity_message") or "").splitlines()[0].strip()
        suffix = f": {detail}" if detail else ""
        return {
            "ok": False,
            "message": f"Wallet discovery scan is unavailable because SQLite integrity check failed{suffix}.",
        }
    registry_state = _managed_wallet_registry_snapshot()
    registry_status = str(registry_state.get("managed_wallet_registry_status") or "").strip().lower()
    if registry_status in {"missing", "unreadable"}:
        detail = str(registry_state.get("managed_wallet_registry_error") or "").strip()
        suffix = f": {detail}" if detail else ""
        return {
            "ok": False,
            "managed_wallet_registry_status": str(registry_state.get("managed_wallet_registry_status") or "unknown"),
            "managed_wallet_registry_available": bool(registry_state.get("managed_wallet_registry_available")),
            "managed_wallet_registry_error": str(registry_state.get("managed_wallet_registry_error") or "").strip(),
            "message": (
                "Wallet discovery scan is unavailable because the managed wallet registry is "
                + ("unreadable" if registry_status == "unreadable" else "missing")
                + suffix
                + "."
            ),
        }

    try:
        summary = refresh_wallet_discovery_candidates(_wallet_registry_addresses())
    except Exception as exc:  # pragma: no cover - defensive network/runtime guard
        logger.exception("Wallet discovery scan failed")
        return {"ok": False, "message": f"Wallet discovery scan failed: {exc}"}

    return summary


def _write_env_value(key: str, value: str) -> None:
    if not VALID_ENV_KEY_RE.match(key):
        raise ValueError(f"Invalid config key: {key}")

    normalized_key = str(key or "").strip().upper()
    text_value = str(value or "").strip()
    if normalized_key not in ENV_ONLY_KEYS:
        set_runtime_setting(normalized_key, text_value)
        return

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
            if stripped.startswith(f"{normalized_key}="):
                updated.append(f"{normalized_key}={text_value}")
                found = True
            else:
                updated.append(line)

        if not found:
            if updated and updated[-1] != "":
                updated.append("")
            updated.append(f"{normalized_key}={text_value}")

        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        ENV_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _clear_env_value(key: str) -> None:
    if not VALID_ENV_KEY_RE.match(key):
        raise ValueError(f"Invalid config key: {key}")

    normalized_key = str(key or "").strip().upper()
    if normalized_key not in ENV_ONLY_KEYS:
        delete_runtime_setting(normalized_key)
        return

    with _env_lock:
        source_path = _source_env_path()
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

        updated = [
            line
            for line in lines
            if not line.strip().startswith(f"{normalized_key}=")
        ]

        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        ENV_PATH.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


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


def _normalize_trading_mode(value: Any, fallback: str = "shadow") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"shadow", "live"}:
        return normalized
    return fallback


def _effective_bot_mode(configured_mode: str, bot_state: dict[str, Any]) -> tuple[str, str]:
    configured = _normalize_trading_mode(configured_mode)
    if configured != "live":
        return "shadow", ""
    startup_block = _startup_block_reason(bot_state)
    if startup_block:
        return "shadow", f"configured live but forced shadow: {startup_block}"
    if bool(bot_state.get("shadow_restart_pending")):
        detail = str(bot_state.get("shadow_restart_message") or "").strip() or "shadow restart pending"
        return "shadow", f"configured live but forced shadow: {detail}"
    if bool(bot_state.get("db_integrity_known")) and not bool(bot_state.get("db_integrity_ok")):
        detail = str(bot_state.get("db_integrity_message") or "").splitlines()[0].strip() or "unknown integrity failure"
        return "shadow", f"configured live but forced shadow: SQLite integrity check failed: {detail}"
    return "live", ""


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


def _trade_log_archive_pending_message(payload: dict[str, Any]) -> str:
    requested_at = int(payload.get("requested_at") or 0)
    request_id = str(payload.get("request_id") or "").strip()
    base = "Trade log archive requested. Waiting for backend to process."
    if requested_at > 0:
        base = f"{base} Requested at {time.strftime('%-m/%-d %-I:%M %p', time.localtime(requested_at))}."
    if request_id:
        base = f"{base} request_id={request_id}"
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
        startup_failed=bool(bot_state.get("startup_failed")),
        startup_validation_failed=bool(bot_state.get("startup_validation_failed")),
        manual_retrain_pending=bool(bot_state.get("manual_retrain_pending")),
        manual_retrain_requested_at=int(bot_state.get("manual_retrain_requested_at") or 0),
        manual_retrain_message=str(bot_state.get("manual_retrain_message") or ""),
        shadow_restart_pending=bool(bot_state.get("shadow_restart_pending")),
        shadow_restart_kind=str(bot_state.get("shadow_restart_kind") or "").strip().lower(),
        shadow_restart_message=str(bot_state.get("shadow_restart_message") or ""),
        manual_trade_pending=bool(bot_state.get("manual_trade_pending")),
        manual_trade_requested_at=int(bot_state.get("manual_trade_requested_at") or 0),
        manual_trade_message=str(bot_state.get("manual_trade_message") or ""),
        trade_log_archive_enabled=bool(bot_state.get("trade_log_archive_enabled")),
        trade_log_archive_state_known=bool(bot_state.get("trade_log_archive_state_known")),
        trade_log_archive_pending=bool(bot_state.get("trade_log_archive_pending")),
        trade_log_archive_requested_at=int(bot_state.get("trade_log_archive_requested_at") or 0),
        trade_log_archive_request_message=str(bot_state.get("trade_log_archive_request_message") or ""),
        trade_log_archive_archive_exists=bool(bot_state.get("trade_log_archive_archive_exists")),
        trade_log_archive_last_vacuumed=bool(bot_state.get("trade_log_archive_last_vacuumed")),
        trade_log_archive_block_reason=str(bot_state.get("trade_log_archive_block_reason") or ""),
    )
    startup_failure_message = str(
        bot_state.get("startup_failure_message")
        or bot_state.get("startup_validation_message")
        or ""
    ).strip()
    startup_detail = str(bot_state.get("startup_detail") or "").strip()
    startup_failed = bool(bot_state.get("startup_failed"))
    startup_validation_failed = bool(bot_state.get("startup_validation_failed"))
    if (startup_failed or startup_validation_failed) and not bool(bot_state.get("startup_blocked")):
        bot_state["startup_blocked"] = True
        if not str(bot_state.get("startup_block_reason") or "").strip():
            bot_state["startup_block_reason"] = startup_failure_message or startup_detail
    if "db_recovery_inventory" not in bot_state or not isinstance(bot_state.get("db_recovery_inventory"), list):
        bot_state["db_recovery_inventory"] = []
    if not isinstance(bot_state.get("training_runs"), list) or not bot_state.get("training_runs"):
        bot_state["training_runs"] = _recent_training_runs()
    bot_state["db_recovery_inventory_count"] = max(int(bot_state.get("db_recovery_inventory_count") or 0), 0)
    needs_recovery_fallback = (
        not bot_state["db_recovery_inventory"]
        or not bool(bot_state.get("db_recovery_candidate_ready"))
        or not str(bot_state.get("db_recovery_candidate_path") or "").strip()
    )
    if needs_recovery_fallback:
        try:
            recovery_state = _cached_db_recovery_state()
        except Exception:
            recovery_state = {}
        for key in (
            "db_recovery_state_known",
            "db_recovery_candidate_ready",
            "db_recovery_candidate_path",
            "db_recovery_candidate_source_path",
            "db_recovery_candidate_message",
            "db_recovery_latest_verified_backup_path",
            "db_recovery_latest_verified_backup_at",
        ):
            if key in recovery_state:
                bot_state[key] = recovery_state[key]
        inventory = recovery_state.get("db_recovery_inventory")
        if isinstance(inventory, list):
            bot_state["db_recovery_inventory"] = inventory
            bot_state["db_recovery_inventory_count"] = max(
                int(recovery_state.get("db_recovery_inventory_count") or len(inventory)),
                0,
            )
    bot_state.update(_db_recovery_candidate_snapshot(bot_state))
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
    trade_log_archive_request = _request_payload_if_fresh(TRADE_LOG_ARCHIVE_REQUEST_FILE, 900)
    if trade_log_archive_request is not None:
        bot_state.update(
            trade_log_archive_pending=True,
            trade_log_archive_requested_at=int(trade_log_archive_request.get("requested_at") or 0),
            trade_log_archive_request_message=_trade_log_archive_pending_message(trade_log_archive_request),
            trade_log_archive_status="pending",
        )
    elif bool(bot_state.get("trade_log_archive_pending")):
        bot_state.update(
            trade_log_archive_pending=False,
            trade_log_archive_requested_at=0,
            trade_log_archive_request_message="",
        )
        _write_atomic_json(BOT_STATE_FILE, bot_state)
    configured_mode = _normalize_trading_mode(bot_state.get("configured_mode"), "")
    if not configured_mode:
        configured_mode = "live" if _live_trading_enabled_in_config() else _normalize_trading_mode(
            bot_state.get("mode"), "shadow"
        )
    mode, mode_block_reason = _effective_bot_mode(configured_mode, bot_state)
    bot_state.update(
        configured_mode=configured_mode,
        mode=mode,
        mode_block_reason=mode_block_reason,
    )
    return bot_state


def _heartbeat_window_seconds(bot_state: dict[str, Any]) -> int:
    try:
        poll_interval = float(bot_state.get("poll_interval") or 1)
    except (TypeError, ValueError):
        poll_interval = 1
    return int(max(poll_interval * 3, 30))


def _startup_block_reason(bot_state: dict[str, Any]) -> str:
    startup_blocked = bool(bot_state.get("startup_blocked"))
    startup_failed = bool(bot_state.get("startup_failed"))
    startup_validation_failed = bool(bot_state.get("startup_validation_failed"))
    startup_detail = str(bot_state.get("startup_detail") or "").strip()
    startup_failure_message = str(
        bot_state.get("startup_failure_message")
        or bot_state.get("startup_validation_message")
        or ""
    ).strip()
    if not startup_blocked and not startup_failed and not startup_validation_failed and "startup blocked" not in startup_detail.lower():
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


def _trade_log_archive_preserve_since_ts(bot_state: dict[str, Any] | None = None) -> int:
    snapshot = dict(bot_state or _bot_state_snapshot())
    baseline_at = max(
        int(snapshot.get("shadow_history_current_baseline_at") or 0),
        int(snapshot.get("shadow_evidence_epoch_started_at") or 0),
    )
    return max(baseline_at, 0)


def _trade_log_archive_state_payload(
    bot_state: dict[str, Any] | None = None,
    *,
    last_result: dict[str, Any] | None = None,
    archive_request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = dict(bot_state or _bot_state_snapshot())
    enabled = trade_log_archive_enabled()
    preserve_since_ts = _trade_log_archive_preserve_since_ts(snapshot)
    cutoff_ts = int(time.time()) - (trade_log_archive_min_age_days() * 86400) if enabled else 0
    payload = trade_log_archive_state(
        cutoff_ts=cutoff_ts,
        preserve_since_ts=preserve_since_ts,
    )
    payload["trade_log_archive_enabled"] = enabled
    payload["trade_log_archive_pending"] = bool(archive_request_payload is not None)
    payload["trade_log_archive_requested_at"] = int((archive_request_payload or {}).get("requested_at") or 0)
    payload["trade_log_archive_request_message"] = (
        _trade_log_archive_pending_message(archive_request_payload or {})
        if archive_request_payload is not None
        else ""
    )
    if not enabled:
        payload.update(
            trade_log_archive_status="disabled",
            trade_log_archive_eligible_row_count=0,
            trade_log_archive_cutoff_ts=0,
            trade_log_archive_message="trade_log archiving is disabled",
        )
    status = str(payload.get("trade_log_archive_status") or "").strip().lower()
    message = str(payload.get("trade_log_archive_message") or "").strip()
    payload["trade_log_archive_block_reason"] = (
        message if status.startswith("blocked") or status in {"error", "disabled"} else ""
    )
    if last_result is not None:
        payload.update(
            trade_log_archive_last_run_at=int(time.time()),
            trade_log_archive_last_candidate_count=max(int(last_result.get("candidate_count") or 0), 0),
            trade_log_archive_last_archived_count=max(int(last_result.get("archived_count") or 0), 0),
            trade_log_archive_last_deleted_count=max(int(last_result.get("deleted_count") or 0), 0),
            trade_log_archive_last_vacuumed=bool(last_result.get("vacuumed")),
            trade_log_archive_last_message=str(last_result.get("message") or "").strip(),
        )
    return payload


def _persist_trade_log_archive_state(
    bot_state: dict[str, Any] | None = None,
    *,
    last_result: dict[str, Any] | None = None,
    archive_request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated_state = _read_json_dict(BOT_STATE_FILE)
    updated_state.update(
        _trade_log_archive_state_payload(
            bot_state,
            last_result=last_result,
            archive_request_payload=archive_request_payload,
        )
    )
    _write_atomic_json(BOT_STATE_FILE, updated_state)
    return updated_state


def _blocked_query_response(
    bot_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    snapshot = dict(bot_state or _bot_state_snapshot())
    if bool(snapshot.get("shadow_restart_pending")):
        return {
            "ok": False,
            "message": "Dashboard queries are unavailable while shadow restart is pending. Wait for the restart to finish first.",
        }
    startup_block = _startup_block_reason(snapshot)
    if startup_block:
        return {
            "ok": False,
            "message": f"Dashboard queries are unavailable because {startup_block}",
        }
    db_integrity_known = bool(snapshot.get("db_integrity_known"))
    db_integrity_ok = bool(snapshot.get("db_integrity_ok"))
    if db_integrity_known and not db_integrity_ok:
        db_integrity_message = str(snapshot.get("db_integrity_message") or "").strip()
        detail = db_integrity_message.splitlines()[0].strip() if db_integrity_message else "unknown integrity failure"
        return {
            "ok": False,
            "message": f"Dashboard queries are unavailable because SQLite integrity check failed: {detail}",
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

    conn = get_trade_log_read_conn(DB_PATH, apply_runtime_pragmas=False)
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _protected_best_wallets() -> set[str]:
    conn = get_trade_log_read_conn(DB_PATH, apply_runtime_pragmas=False)
    try:
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
    finally:
        conn.close()


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
            conn.execute(
                """
                INSERT INTO wallet_membership_events (
                  wallet_address,
                  action,
                  source,
                  reason,
                  payload_json,
                  created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    wallet,
                    "reactivate",
                    "manual_web",
                    "wallet reactivated from web dashboard",
                    json.dumps(
                        {
                            "baseline_at": now_ts,
                            "reactivated_at": now_ts,
                            "boundary_kind": "reactivate",
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    now_ts,
                ),
            )
    finally:
        conn.close()
    return {"ok": True, "message": "Wallet reactivated."}


def _set_wallet_tracking_enabled(
    wallet_address: str,
    *,
    tracking_enabled: bool,
    action: str,
    reason: str,
) -> dict[str, Any]:
    wallet = wallet_address.strip().lower()
    if not wallet:
        return {"ok": False, "message": "Missing wallet address."}

    now_ts = int(time.time())
    conn = get_conn()
    try:
        existing = conn.execute(
            """
            SELECT wallet_address, source, metadata_json
            FROM managed_wallets
            WHERE wallet_address=?
            """,
            (wallet,),
        ).fetchone()
        if existing is None:
            return {"ok": False, "message": "Wallet is not in the managed wallet registry."}
        current_enabled = True
        enabled_row = conn.execute(
            "SELECT tracking_enabled FROM managed_wallets WHERE wallet_address=?",
            (wallet,),
        ).fetchone()
        if enabled_row is not None:
            current_enabled = bool(int(enabled_row["tracking_enabled"] or 0))
        if current_enabled == tracking_enabled:
            return {
                "ok": True,
                "message": (
                    "Wallet already enabled."
                    if tracking_enabled
                    else "Wallet already disabled."
                ),
            }

        with conn:
            conn.execute(
                """
                UPDATE managed_wallets
                SET tracking_enabled=?,
                    updated_at=?,
                    disabled_at=?,
                    disabled_reason=?
                WHERE wallet_address=?
                """,
                (
                    1 if tracking_enabled else 0,
                    now_ts,
                    None if tracking_enabled else now_ts,
                    "" if tracking_enabled else reason,
                    wallet,
                ),
            )
            conn.execute(
                """
                INSERT INTO wallet_membership_events (
                  wallet_address,
                  action,
                  source,
                  reason,
                  payload_json,
                  created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    wallet,
                    action,
                    "manual_web",
                    reason,
                    json.dumps(
                        {
                            "tracking_enabled": tracking_enabled,
                            "boundary_kind": action,
                            "changed_at": now_ts,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    now_ts,
                ),
            )
            if tracking_enabled:
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
                      tracking_started_at=CASE
                        WHEN COALESCE(wallet_watch_state.tracking_started_at, 0)=0 THEN excluded.tracking_started_at
                        ELSE wallet_watch_state.tracking_started_at
                      END,
                      updated_at=excluded.updated_at
                    """,
                    (wallet, now_ts, now_ts, now_ts),
                )
            else:
                cursor_row = conn.execute(
                    "SELECT last_source_ts FROM wallet_cursors WHERE wallet_address=?",
                    (wallet,),
                ).fetchone()
                last_source_ts = int(cursor_row["last_source_ts"] or 0) if cursor_row else 0
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
                    (wallet, reason, now_ts, last_source_ts, now_ts),
                )
    finally:
        conn.close()
    return {
        "ok": True,
        "message": "Wallet enabled." if tracking_enabled else "Wallet disabled.",
    }


def _enable_wallet(wallet_address: str) -> dict[str, Any]:
    return _set_wallet_tracking_enabled(
        wallet_address,
        tracking_enabled=True,
        action="enable",
        reason="wallet enabled from web dashboard",
    )


def _disable_wallet(wallet_address: str) -> dict[str, Any]:
    return _set_wallet_tracking_enabled(
        wallet_address,
        tracking_enabled=False,
        action="disable",
        reason="wallet disabled from web dashboard",
    )


def _drop_wallet(wallet_address: str, reason: str = "manual dashboard drop") -> dict[str, Any]:
    wallet = wallet_address.strip().lower()
    normalized_reason = reason.strip() or "manual dashboard drop"
    if not wallet:
        return {"ok": False, "message": "Missing wallet address."}

    now_ts = int(time.time())
    conn = get_conn()
    try:
        if wallet in _protected_best_wallets():
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
    if not enabled:
        _write_env_value("USE_REAL_MONEY", "false")
        if bool(bot_state.get("shadow_restart_pending")):
            return {
                "ok": True,
                "message": "Live Trading saved as OFF. Shadow restart is already pending and will continue in shadow mode.",
            }
        startup_block = _startup_block_reason(bot_state)
        if startup_block:
            return {
                "ok": True,
                "message": "Live Trading saved as OFF. The backend is currently blocked, but the persisted config is now shadow-only.",
            }
        return {
            "ok": True,
            "message": "Live Trading saved as OFF. Restart the bot to apply it safely.",
        }
    blocked = _blocked_shadow_mutation_response("Live mode changes", bot_state)
    if blocked:
        startup_block = _startup_block_reason(bot_state)
        if enabled and startup_block:
            return {
                "ok": False,
                "message": f"Live trading remains blocked: backend startup is blocked: {startup_block}",
            }
        return blocked

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


def _enable_wallet_response(wallet_address: str) -> dict[str, Any]:
    blocked_response = _blocked_shadow_mutation_response("Wallet enable")
    if blocked_response is not None:
        return blocked_response
    return _enable_wallet(wallet_address)


def _disable_wallet_response(wallet_address: str) -> dict[str, Any]:
    blocked_response = _blocked_shadow_mutation_response("Wallet disable")
    if blocked_response is not None:
        return blocked_response
    return _disable_wallet(wallet_address)


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
    if normalized_key == "WATCHED_WALLETS":
        snapshot = _config_snapshot()
        return (
            409,
            {
                "ok": False,
                "message": (
                    "Config editing for WATCHED_WALLETS is blocked because it is bootstrap-only after the "
                    "DB-backed wallet registry migration. "
                    "Use the wallet registry and shadow-reset controls in the web dashboard instead."
                ),
                **snapshot,
            },
        )

    _write_env_value(normalized_key, normalized_value)
    return (200, _config_snapshot())


def _config_clear_response(key: str) -> tuple[int, dict[str, Any]]:
    normalized_key = str(key or "").strip()
    if normalized_key == "USE_REAL_MONEY":
        result = _set_live_mode_response({"enabled": False})
        return (200 if result.get("ok") else 409, result)
    if normalized_key == "WATCHED_WALLETS":
        snapshot = _config_snapshot()
        return (
            409,
            {
                "ok": False,
                "message": (
                    "Config editing for WATCHED_WALLETS is blocked because it is bootstrap-only after the "
                    "DB-backed wallet registry migration. "
                    "Use the wallet registry and shadow-reset controls in the web dashboard instead."
                ),
                **snapshot,
            },
        )

    _clear_env_value(normalized_key)
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


def _trade_log_archive_preflight(bot_state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    snapshot = dict(bot_state or _bot_state_snapshot())
    blocked_response = _blocked_shadow_mutation_response("Trade log archive", snapshot)
    if blocked_response is not None:
        return blocked_response

    if not trade_log_archive_enabled():
        return {
            "ok": False,
            "message": "Trade log archive is disabled in config.",
        }

    integrity = database_integrity_state()
    if bool(integrity.get("db_integrity_known")) and not bool(integrity.get("db_integrity_ok")):
        detail = str(integrity.get("db_integrity_message") or "").splitlines()[0].strip()
        suffix = f" ({detail})" if detail else ""
        return {
            "ok": False,
            "message": f"Trade log archive is unavailable because SQLite integrity check failed{suffix}.",
        }

    if bool(snapshot.get("trade_log_archive_pending")) or _request_payload_if_fresh(TRADE_LOG_ARCHIVE_REQUEST_FILE, 900) is not None:
        return {
            "ok": True,
            "message": "Trade log archive already requested. Waiting for the bot to process it.",
        }

    return None


def _launch_trade_log_archive(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = _bot_state_snapshot()
    result = _trade_log_archive_preflight(snapshot)
    if result is not None:
        return result

    now = int(time.time())
    request_payload = {
        "requested_at": now,
        "request_id": str((payload or {}).get("request_id") or f"trade-log-archive-{now}-{os.getpid()}"),
        "source": str((payload or {}).get("source") or "dashboard").strip().lower() or "dashboard",
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _request_lock:
        TRADE_LOG_ARCHIVE_REQUEST_FILE.write_text(json.dumps(request_payload, separators=(",", ":")), encoding="utf-8")
        _persist_trade_log_archive_state(snapshot, archive_request_payload=request_payload)
    return {
        "ok": True,
        "message": "Trade log archive requested. The bot will process a bounded cleanup batch and refresh archive status.",
    }


class DashboardApiHandler(BaseHTTPRequestHandler):
    server_version = "KellyWatcherDashboardAPI/1.0"

    def log_message(self, format: str, *args: object) -> None:
        logger.info("dashboard_api %s - %s", self.address_string(), format % args)

    def _set_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_bytes(self, status: int, body: bytes, content_type: str, *, include_cors: bool = False) -> None:
        self.send_response(status)
        if include_cors:
            self._set_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", include_cors=True)

    def _send_file(self, status: int, file_path: Path) -> None:
        body = file_path.read_bytes()
        self._send_bytes(status, body, _dashboard_web_content_type(file_path))

    def _send_html(self, status: int, html: str) -> None:
        self._send_bytes(status, html.encode("utf-8"), "text/html; charset=utf-8")

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

        if not path.startswith("/api/"):
            asset_path = _resolve_dashboard_web_asset_path(path)
            if asset_path:
                self._send_file(200, asset_path)
                return
            self._send_html(
                404,
                (
                    "<!doctype html><html><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                    "<title>Kelly Watcher Dashboard</title></head><body>"
                    "<h1>dashboard-web build not found</h1>"
                    "<p>Run <code>npm install</code> and <code>npm run build</code> in "
                    "<code>dashboard-web</code>, then refresh this page.</p>"
                    "</body></html>"
                ),
            )
            return

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

        if path == "/api/performance":
            self._send_json(200, _performance_snapshot())
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

        if path in {
            "/api/wallets",
            "/api/wallets/summary",
            "/api/wallets/tracked",
            "/api/wallets/dropped",
            "/api/wallets/events",
            "/api/discovery/candidates",
        }:
            blocked = _blocked_query_response()
            if blocked:
                self._send_json(409, blocked)
                return
            query = parse_qs(parsed.query)
            try:
                limit = max(1, min(int(query.get("limit", ["250"])[0]), 1000))
            except (TypeError, ValueError):
                limit = 250
            if path == "/api/wallets":
                self._send_json(200, _wallet_registry_summary(limit))
                return
            if path == "/api/wallets/summary":
                self._send_json(200, _wallet_summary_response(limit))
                return
            if path == "/api/wallets/tracked":
                self._send_json(200, _wallet_page_rows_response("tracked", limit))
                return
            if path == "/api/wallets/dropped":
                self._send_json(200, _wallet_page_rows_response("dropped", limit))
                return
            if path == "/api/wallets/events":
                events, source = _wallet_membership_events(limit)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "source": source,
                        "events": events,
                        "count": len(events),
                    },
                )
                return
            if path == "/api/discovery/candidates":
                self._send_json(200, _discovery_candidates_response(limit))
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
                blocked = _blocked_query_response()
                if blocked:
                    self._send_json(409, blocked)
                    return
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

            if path == "/api/config/clear":
                key = str(body.get("key") or "").strip()
                status_code, payload = _config_clear_response(key)
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

            if path == "/api/discovery/scan":
                self._send_json(202, _discovery_scan_response())
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

            if path == "/api/wallets/enable":
                wallet_address = str(body.get("walletAddress") or body.get("wallet_address") or "").strip()
                self._send_json(200, _enable_wallet_response(wallet_address))
                return

            if path == "/api/wallets/disable":
                wallet_address = str(body.get("walletAddress") or body.get("wallet_address") or "").strip()
                self._send_json(200, _disable_wallet_response(wallet_address))
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

            if path == "/api/shadow/archive-trade-log":
                result = _launch_trade_log_archive(body)
                self._send_json(202 if result.get("ok") else 409, result)
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
