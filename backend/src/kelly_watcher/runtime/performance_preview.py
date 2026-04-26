from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kelly_watcher.config import shadow_bankroll_usd
from kelly_watcher.data.db import get_conn, get_conn_for_path
from kelly_watcher.runtime_paths import BOT_STATE_FILE
from kelly_watcher.engine.segment_policy import SEGMENT_FALLBACK, SEGMENT_IDS
from kelly_watcher.engine.shadow_evidence import read_shadow_evidence_epoch
from kelly_watcher.engine.trade_contract import EXECUTED_ENTRY_SQL, OPEN_EXECUTED_ENTRY_SQL

logger = logging.getLogger(__name__)
_EDITABLE_STATUSES = frozenset({"open", "waiting", "win", "lose", "exit"})
_ROUTED_SEGMENT_IDS = frozenset({*SEGMENT_IDS, SEGMENT_FALLBACK})

_SHADOW_OPEN_POSITIONS_SQL = f"""
SELECT
  'trade_log' AS source_kind,
  tl.id AS source_trade_log_id,
  tl.market_id,
  COALESCE(tl.token_id, '') AS token_id,
  COALESCE(NULLIF(TRIM(tl.segment_id), ''), '') AS segment_id,
  tl.side,
  tl.real_money,
  ROUND(COALESCE(tl.remaining_entry_size_usd, tl.actual_entry_size_usd), 3) AS size_usd,
  ROUND(COALESCE(tl.remaining_entry_shares, tl.actual_entry_shares, tl.source_shares), 6) AS shares,
  ROUND(
    CASE
      WHEN COALESCE(tl.remaining_entry_shares, 0) > 1e-9 THEN tl.remaining_entry_size_usd / tl.remaining_entry_shares
      ELSE tl.actual_entry_price
    END,
    3
  ) AS entry_price,
  ROUND(tl.confidence, 3) AS confidence,
  tl.placed_at AS entered_at,
  COALESCE(NULLIF(tl.market_close_ts, 0), 0) AS market_close_ts,
  COALESCE(NULLIF(tl.market_close_ts, 0), 0) AS resolution_ts,
  'open' AS status,
  NULL AS exit_size_usd,
  NULL AS pnl_usd
FROM trade_log tl
WHERE tl.real_money = 0
  AND {OPEN_EXECUTED_ENTRY_SQL}
ORDER BY tl.placed_at DESC, tl.id DESC
"""

_LIVE_POSITIONS_SQL = f"""
SELECT
  'position' AS source_kind,
  COALESCE(
    (
      SELECT tl.id
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND {EXECUTED_ENTRY_SQL}
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.id
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND {EXECUTED_ENTRY_SQL}
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    )
  ) AS source_trade_log_id,
  p.market_id,
  COALESCE(p.token_id, '') AS token_id,
  '' AS segment_id,
  p.side,
  p.real_money,
  ROUND(p.size_usd, 3) AS size_usd,
  ROUND(
    CASE
      WHEN p.avg_price > 0 THEN p.size_usd / p.avg_price
      ELSE COALESCE(
        (
          SELECT tl.actual_entry_shares
          FROM trade_log tl
          WHERE tl.market_id = p.market_id
            AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
            AND {EXECUTED_ENTRY_SQL}
            AND tl.placed_at <= p.entered_at
          ORDER BY tl.placed_at DESC, tl.id DESC
          LIMIT 1
        ),
        (
          SELECT tl.actual_entry_shares
          FROM trade_log tl
          WHERE tl.market_id = p.market_id
            AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
            AND {EXECUTED_ENTRY_SQL}
          ORDER BY tl.placed_at DESC, tl.id DESC
          LIMIT 1
        ),
        0
      )
    END,
    6
  ) AS shares,
  ROUND(
    CASE
      WHEN p.avg_price > 0 THEN p.avg_price
      ELSE COALESCE(
        (
          SELECT tl.actual_entry_price
          FROM trade_log tl
          WHERE tl.market_id = p.market_id
            AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
            AND {EXECUTED_ENTRY_SQL}
            AND tl.placed_at <= p.entered_at
          ORDER BY tl.placed_at DESC, tl.id DESC
          LIMIT 1
        ),
        (
          SELECT tl.actual_entry_price
          FROM trade_log tl
          WHERE tl.market_id = p.market_id
            AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
            AND {EXECUTED_ENTRY_SQL}
          ORDER BY tl.placed_at DESC, tl.id DESC
          LIMIT 1
        ),
        0
      )
    END,
    3
  ) AS entry_price,
  ROUND(
    COALESCE(
      (
        SELECT tl.confidence
        FROM trade_log tl
        WHERE tl.market_id = p.market_id
          AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
          AND {EXECUTED_ENTRY_SQL}
          AND tl.placed_at <= p.entered_at
        ORDER BY tl.placed_at DESC, tl.id DESC
        LIMIT 1
      ),
      (
        SELECT tl.confidence
        FROM trade_log tl
        WHERE tl.market_id = p.market_id
          AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
          AND {EXECUTED_ENTRY_SQL}
        ORDER BY tl.placed_at DESC, tl.id DESC
        LIMIT 1
      )
    ),
    3
  ) AS confidence,
  p.entered_at,
  COALESCE(
    (
      SELECT tl.market_close_ts
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND {EXECUTED_ENTRY_SQL}
        AND tl.market_close_ts IS NOT NULL
        AND tl.market_close_ts > 0
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.market_close_ts
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND {EXECUTED_ENTRY_SQL}
        AND tl.market_close_ts IS NOT NULL
        AND tl.market_close_ts > 0
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    0
  ) AS market_close_ts,
  COALESCE(
    (
      SELECT tl.market_close_ts
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND {EXECUTED_ENTRY_SQL}
        AND tl.market_close_ts IS NOT NULL
        AND tl.market_close_ts > 0
        AND tl.placed_at <= p.entered_at
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    (
      SELECT tl.market_close_ts
      FROM trade_log tl
      WHERE tl.market_id = p.market_id
        AND ((p.token_id <> '' AND tl.token_id = p.token_id) OR (p.token_id = '' AND LOWER(tl.side) = LOWER(p.side)))
        AND {EXECUTED_ENTRY_SQL}
        AND tl.market_close_ts IS NOT NULL
        AND tl.market_close_ts > 0
      ORDER BY tl.placed_at DESC, tl.id DESC
      LIMIT 1
    ),
    0
  ) AS resolution_ts,
  'open' AS status,
  NULL AS exit_size_usd,
  NULL AS pnl_usd
FROM positions p
ORDER BY p.entered_at DESC
"""

_RESOLVED_POSITIONS_SQL = f"""
SELECT
  'trade_log' AS source_kind,
  tl.id AS source_trade_log_id,
  tl.market_id,
  COALESCE(tl.token_id, '') AS token_id,
  COALESCE(NULLIF(TRIM(tl.segment_id), ''), '') AS segment_id,
  tl.side,
  tl.real_money,
  ROUND(tl.actual_entry_size_usd, 3) AS size_usd,
  ROUND(tl.actual_entry_shares, 6) AS shares,
  ROUND(tl.actual_entry_price, 3) AS entry_price,
  ROUND(tl.confidence, 3) AS confidence,
  tl.placed_at AS entered_at,
  COALESCE(NULLIF(tl.market_close_ts, 0), tl.resolved_at, tl.placed_at) AS market_close_ts,
  COALESCE(NULLIF(tl.exited_at, 0), NULLIF(tl.resolved_at, 0), NULLIF(tl.market_close_ts, 0), tl.placed_at) AS resolution_ts,
  CASE
    WHEN tl.exited_at IS NOT NULL THEN 'exit'
    WHEN (CASE WHEN tl.real_money = 0 THEN tl.shadow_pnl_usd ELSE tl.actual_pnl_usd END) > 0 THEN 'win'
    ELSE 'lose'
  END AS status,
  ROUND(tl.exit_size_usd, 3) AS exit_size_usd,
  ROUND(CASE WHEN tl.real_money = 0 THEN tl.shadow_pnl_usd ELSE tl.actual_pnl_usd END, 3) AS pnl_usd
FROM trade_log tl
WHERE {EXECUTED_ENTRY_SQL}
  AND (CASE WHEN tl.real_money = 0 THEN tl.shadow_pnl_usd ELSE tl.actual_pnl_usd END) IS NOT NULL
ORDER BY COALESCE(NULLIF(tl.exited_at, 0), NULLIF(tl.resolved_at, 0), NULLIF(tl.market_close_ts, 0), tl.placed_at) DESC, tl.id DESC
"""

_TRADE_LOG_MANUAL_EDITS_SQL = """
SELECT
  trade_log_id,
  entry_price,
  shares,
  size_usd,
  status,
  updated_at
FROM trade_log_manual_edits
"""

_POSITION_MANUAL_EDITS_SQL = """
SELECT
  market_id,
  token_id,
  LOWER(side) AS side,
  real_money,
  entry_price,
  shares,
  size_usd,
  status,
  updated_at
FROM position_manual_edits
"""


@dataclass(frozen=True)
class PerformancePreviewSummary:
    title: str
    mode: str
    total_pnl: float
    current_balance: float | None
    current_equity: float | None
    return_pct: float | None
    win_rate: float
    profit_factor: float | None
    expectancy_usd: float | None
    expectancy_pct: float | None
    exposure_pct: float | None
    max_drawdown_pct: float | None
    resolved: int
    avg_confidence: float | None
    avg_total: float | None
    acted: int
    wins: int
    data_warning: str = ""
    routed_history_status: str = "empty"
    routed_acted: int = 0
    routed_resolved: int = 0
    routed_wins: int = 0
    routed_total_pnl: float | None = None
    routed_return_pct: float | None = None
    routed_win_rate: float | None = None
    routed_profit_factor: float | None = None
    routed_expectancy_usd: float | None = None
    routed_expectancy_pct: float | None = None
    routed_avg_confidence: float | None = None
    routed_avg_total: float | None = None
    routed_legacy_acted: int = 0
    routed_legacy_resolved: int = 0
    routed_coverage_pct: float | None = None
    shadow_evidence_epoch_started_at: int = 0
    shadow_evidence_epoch_source: str = ""


def _round_to(value: float, digits: int) -> float:
    return float(f"{value:.{digits}f}")


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _format_dollar(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}${value:.3f}"


def _format_balance(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "-"
    return f"${value:.3f}"


def _format_pct(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "-"
    return f"{value * 100:.3f}%"


def _format_ratio(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "-"
    if math.isinf(value):
        return "inf"
    return f"{value:.2f}"


def _format_expectancy(value_usd: float | None, value_pct: float | None) -> str:
    parts: list[str] = []
    if value_usd is not None and not math.isnan(value_usd):
        parts.append(_format_dollar(value_usd))
    if value_pct is not None and not math.isnan(value_pct):
        parts.append(_format_pct(value_pct))
    return " / ".join(parts) if parts else "-"


def _format_timestamp(value: int | None) -> str:
    if not value:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(value)))
    except Exception:
        return str(value)


def _safe_fetch_dicts(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql).fetchall()]
    except sqlite3.OperationalError:
        return []


def _safe_read_bot_state() -> dict[str, Any]:
    try:
        payload = json.loads(BOT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_manual_status(raw: Any) -> str | None:
    normalized = str(raw or "").strip().lower()
    return normalized if normalized in _EDITABLE_STATUSES else None


def _position_edit_key(market_id: Any, token_id: Any, side: Any, real_money: Any) -> str:
    return (
        f"{int(real_money or 0)}:{str(market_id or '').strip()}:"
        f"{str(token_id or '').strip()}:{str(side or '').strip().lower()}"
    )


def _compute_position_profit(row: dict[str, Any]) -> float | None:
    status = str(row.get("status") or "").strip().lower()
    size_usd = float(row.get("size_usd") or 0.0)
    shares = row.get("shares")
    if status in {"open", "waiting"}:
        return None
    if status == "win":
        return None if shares is None else float(shares) - size_usd
    if status == "lose":
        return -size_usd
    exit_size_usd = row.get("exit_size_usd")
    return float(exit_size_usd if exit_size_usd is not None else size_usd) - size_usd


def _normalize_segment_id(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _is_routed_segment_id(raw: Any) -> bool:
    return _normalize_segment_id(raw) in _ROUTED_SEGMENT_IDS


def _position_sort_key(row: dict[str, Any]) -> tuple[float, float, str]:
    return (
        float(row.get("resolution_ts") or row.get("market_close_ts") or row.get("entered_at") or 0),
        float(row.get("entered_at") or 0),
        str(row.get("market_id") or ""),
    )


def _compute_max_drawdown_pct(starting_bankroll: float | None, resolved_rows: list[dict[str, Any]]) -> float | None:
    if starting_bankroll is None or starting_bankroll <= 0:
        return None

    running_equity = float(starting_bankroll)
    peak_equity = float(starting_bankroll)
    max_drawdown = 0.0
    for row in sorted(resolved_rows, key=_position_sort_key):
        running_equity += float(row.get("pnl_usd") or 0.0)
        peak_equity = max(peak_equity, running_equity)
        if peak_equity > 0:
            max_drawdown = max(max_drawdown, (peak_equity - running_equity) / peak_equity)
    return _round_to(max_drawdown, 4)


def _slice_summary(
    rows: list[dict[str, Any]],
    *,
    starting_bankroll: float | None = None,
) -> dict[str, float | int | None]:
    acted = len(rows)
    resolved_rows = [row for row in rows if row.get("status") in {"win", "lose", "exit"}]
    wins = sum(1 for row in resolved_rows if float(row.get("pnl_usd") or 0.0) > 0)
    confidence_rows = [row for row in rows if row.get("confidence") is not None]
    total_pnl = _round_to(sum(float(row.get("pnl_usd") or 0.0) for row in resolved_rows), 3) if resolved_rows else None
    avg_confidence = (
        _round_to(sum(float(row.get("confidence") or 0.0) for row in confidence_rows) / len(confidence_rows), 3)
        if confidence_rows
        else None
    )
    avg_total = (
        _round_to(sum(float(row.get("size_usd") or 0.0) for row in rows) / acted, 3)
        if acted
        else None
    )
    gross_profit = _round_to(
        sum(max(float(row.get("pnl_usd") or 0.0), 0.0) for row in resolved_rows),
        3,
    )
    gross_loss = _round_to(
        sum(abs(min(float(row.get("pnl_usd") or 0.0), 0.0)) for row in resolved_rows),
        3,
    )
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else math.inf if gross_profit > 0 else None
    expectancy_usd = _round_to(float(total_pnl) / len(resolved_rows), 3) if resolved_rows and total_pnl is not None else None
    per_trade_returns = [
        float(row.get("pnl_usd") or 0.0) / float(row.get("size_usd") or 0.0)
        for row in resolved_rows
        if float(row.get("size_usd") or 0.0) > 0
    ]
    expectancy_pct = (
        _round_to(sum(per_trade_returns) / len(per_trade_returns), 4)
        if per_trade_returns
        else None
    )
    return_pct = (
        _round_to(float(total_pnl) / starting_bankroll, 4)
        if total_pnl is not None and starting_bankroll is not None and starting_bankroll > 0
        else None
    )
    win_rate = _round_to(wins / len(resolved_rows), 6) if resolved_rows else None
    return {
        "acted": acted,
        "resolved": len(resolved_rows),
        "wins": wins,
        "total_pnl": total_pnl,
        "return_pct": return_pct,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy_usd": expectancy_usd,
        "expectancy_pct": expectancy_pct,
        "avg_confidence": avg_confidence,
        "avg_total": avg_total,
    }


def _normalize_effective_position(
    row: dict[str, Any],
    now_ts: float,
    trade_log_edits: dict[int, dict[str, Any]],
    position_edits: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source_trade_log_id = row.get("source_trade_log_id")
    trade_edit = trade_log_edits.get(int(source_trade_log_id)) if source_trade_log_id is not None else None
    position_edit = position_edits.get(
        _position_edit_key(row.get("market_id"), row.get("token_id"), row.get("side"), row.get("real_money"))
    )
    edit = position_edit if row.get("source_kind") == "position" and position_edit is not None else trade_edit

    entry_price = (
        float(edit["entry_price"])
        if edit and edit.get("entry_price") is not None
        else float(row.get("entry_price") or 0.0)
    )
    shares = float(edit["shares"]) if edit and edit.get("shares") is not None else row.get("shares")
    size_usd = float(edit["size_usd"]) if edit and edit.get("size_usd") is not None else float(row.get("size_usd") or 0.0)
    status_override = _normalize_manual_status(edit.get("status")) if edit else None
    base_status = str(row.get("status") or "").strip().lower()
    market_close_ts = int(row.get("market_close_ts") or 0)
    status = status_override or ("waiting" if base_status == "open" and market_close_ts > 0 and market_close_ts <= now_ts else base_status)
    base_resolution_ts = int(
        row.get("resolution_ts")
        or row.get("market_close_ts")
        or (edit.get("updated_at") if edit and edit.get("updated_at") is not None else 0)
        or row.get("entered_at")
        or 0
    )
    resolution_ts = 0 if status == "open" else (market_close_ts or base_resolution_ts) if status == "waiting" else base_resolution_ts
    exit_size_usd = (
        _round_to(float(row.get("exit_size_usd") if row.get("exit_size_usd") is not None else size_usd), 3)
        if status == "exit"
        else None
    )

    normalized = dict(row)
    normalized.update(
        {
            "entry_price": _round_to(entry_price, 3),
            "shares": _round_to(float(shares), 6) if shares is not None else None,
            "size_usd": _round_to(size_usd, 3),
            "status": status,
            "resolution_ts": resolution_ts,
            "exit_size_usd": exit_size_usd,
        }
    )
    persisted_pnl_usd = _finite_float(row.get("pnl_usd"))
    if row.get("source_kind") == "trade_log" and edit is None and persisted_pnl_usd is not None:
        normalized["pnl_usd"] = _round_to(persisted_pnl_usd, 3)
    else:
        normalized["pnl_usd"] = _compute_position_profit(normalized)
    return normalized


def compute_tracker_preview_summary(
    *,
    now_ts: float | None = None,
    mode: str | None = None,
    db_path: Path | None = None,
    use_bot_state_balance: bool = True,
    since_ts: int | None = None,
    apply_shadow_evidence_epoch: bool = False,
) -> PerformancePreviewSummary:
    bot_state = _safe_read_bot_state()
    stored_mode = str(bot_state.get("mode") or "").strip().lower()
    requested_mode = str(mode or "").strip().lower()
    active_mode = "live" if requested_mode == "live" or (not requested_mode and stored_mode == "live") else "shadow"
    active_real_money = 1 if active_mode == "live" else 0
    active_title = "Live tracker" if active_mode == "live" else "Shadow tracker"
    shadow_epoch_started_at = 0
    shadow_epoch_source = ""
    effective_since_ts = max(int(since_ts or 0), 0)
    if active_mode == "shadow" and apply_shadow_evidence_epoch:
        epoch_state = read_shadow_evidence_epoch()
        shadow_epoch_started_at = max(int(epoch_state.get("shadow_evidence_epoch_started_at") or 0), 0)
        shadow_epoch_source = str(epoch_state.get("shadow_evidence_epoch_source") or "").strip().lower()
        effective_since_ts = max(
            effective_since_ts,
            shadow_epoch_started_at,
            _latest_applied_replay_promotion_at(db_path),
        )
    bankroll = bot_state.get("bankroll_usd")
    current_balance = (
        float(bankroll)
        if use_bot_state_balance and stored_mode == active_mode and bankroll is not None
        else None
    )
    effective_now_ts = float(now_ts if now_ts is not None else time.time())

    conn = get_conn_for_path(db_path, apply_runtime_pragmas=False) if db_path is not None else get_conn()
    try:
        shadow_open_positions = _safe_fetch_dicts(conn, _SHADOW_OPEN_POSITIONS_SQL)
        live_positions = _safe_fetch_dicts(conn, _LIVE_POSITIONS_SQL)
        resolved_positions = _safe_fetch_dicts(conn, _RESOLVED_POSITIONS_SQL)
        trade_log_edits = {
            int(row["trade_log_id"]): row
            for row in _safe_fetch_dicts(conn, _TRADE_LOG_MANUAL_EDITS_SQL)
            if row.get("trade_log_id") is not None
        }
        position_edits = {
            _position_edit_key(row.get("market_id"), row.get("token_id"), row.get("side"), row.get("real_money")): row
            for row in _safe_fetch_dicts(conn, _POSITION_MANUAL_EDITS_SQL)
        }
    finally:
        conn.close()

    active_open_positions = (
        [row for row in live_positions if int(row.get("real_money") or 0) == active_real_money]
        if active_mode == "live"
        else shadow_open_positions
    )
    active_resolved_positions = [
        row for row in resolved_positions if int(row.get("real_money") or 0) == active_real_money
    ]
    all_effective_positions = [
        _normalize_effective_position(row, effective_now_ts, trade_log_edits, position_edits)
        for row in [*active_open_positions, *active_resolved_positions]
    ]
    effective_positions = all_effective_positions
    if active_mode == "shadow" and effective_since_ts > 0:
        effective_positions = [
            row
            for row in all_effective_positions
            if int(row.get("entered_at") or 0) >= effective_since_ts
        ]

    acted = len(effective_positions)
    open_rows = [row for row in effective_positions if row.get("status") == "open"]
    waiting_rows = [row for row in effective_positions if row.get("status") == "waiting"]
    resolved_rows = [row for row in effective_positions if row.get("status") in {"win", "lose", "exit"}]
    account_open_rows = [row for row in all_effective_positions if row.get("status") == "open"]
    account_waiting_rows = [row for row in all_effective_positions if row.get("status") == "waiting"]
    account_resolved_rows = [row for row in all_effective_positions if row.get("status") in {"win", "lose", "exit"}]
    wins = sum(1 for row in resolved_rows if float(row.get("pnl_usd") or 0.0) > 0)
    total_pnl = _round_to(sum(float(row.get("pnl_usd") or 0.0) for row in resolved_rows), 3) if resolved_rows else 0.0
    account_total_pnl = (
        _round_to(sum(float(row.get("pnl_usd") or 0.0) for row in account_resolved_rows), 3)
        if account_resolved_rows
        else 0.0
    )
    confidence_rows = [row for row in effective_positions if row.get("confidence") is not None]
    avg_confidence = (
        _round_to(sum(float(row.get("confidence") or 0.0) for row in confidence_rows) / len(confidence_rows), 3)
        if confidence_rows
        else None
    )
    avg_total = (
        _round_to(sum(float(row.get("size_usd") or 0.0) for row in effective_positions) / acted, 3)
        if acted
        else None
    )
    win_rate = wins / len(resolved_rows) if resolved_rows else 0.0
    deployed_capital = _round_to(
        sum(float(row.get("size_usd") or 0.0) for row in [*account_open_rows, *account_waiting_rows]),
        3,
    )
    if current_balance is None and active_mode == "shadow":
        current_balance = _round_to(shadow_bankroll_usd() + account_total_pnl - deployed_capital, 3)
    current_equity = _round_to(current_balance + deployed_capital, 3) if current_balance is not None else None
    starting_bankroll = (
        _round_to(current_equity - account_total_pnl, 3)
        if current_equity is not None
        else _round_to(shadow_bankroll_usd(), 3) if active_mode == "shadow" else None
    )
    return_pct = (
        _round_to(total_pnl / starting_bankroll, 4)
        if starting_bankroll is not None and starting_bankroll > 0
        else None
    )
    gross_profit = _round_to(
        sum(max(float(row.get("pnl_usd") or 0.0), 0.0) for row in resolved_rows),
        3,
    )
    gross_loss = _round_to(
        sum(abs(min(float(row.get("pnl_usd") or 0.0), 0.0)) for row in resolved_rows),
        3,
    )
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else math.inf if gross_profit > 0 else None
    expectancy_usd = _round_to(total_pnl / len(resolved_rows), 3) if resolved_rows else None
    per_trade_returns = [
        float(row.get("pnl_usd") or 0.0) / float(row.get("size_usd") or 0.0)
        for row in resolved_rows
        if float(row.get("size_usd") or 0.0) > 0
    ]
    expectancy_pct = (
        _round_to(sum(per_trade_returns) / len(per_trade_returns), 4)
        if per_trade_returns
        else None
    )
    exposure_pct = (
        _round_to(deployed_capital / current_equity, 4)
        if current_equity is not None and current_equity > 0
        else None
    )
    max_drawdown_pct = _compute_max_drawdown_pct(starting_bankroll, resolved_rows)
    data_warning = ""
    if bot_state.get("db_integrity_known") and not bot_state.get("db_integrity_ok"):
        detail = str(bot_state.get("db_integrity_message") or "").splitlines()[0].strip()
        data_warning = (
            "WARNING: SQLite integrity check failed; performance numbers may be unreliable"
            + (f" ({detail})" if detail else "")
        )

    routed_history_status = "empty"
    routed_acted = 0
    routed_resolved = 0
    routed_wins = 0
    routed_total_pnl = None
    routed_return_pct = None
    routed_win_rate = None
    routed_profit_factor = None
    routed_expectancy_usd = None
    routed_expectancy_pct = None
    routed_avg_confidence = None
    routed_avg_total = None
    routed_legacy_acted = 0
    routed_legacy_resolved = 0
    routed_coverage_pct = None

    if active_mode == "shadow":
        routed_rows = [row for row in effective_positions if _is_routed_segment_id(row.get("segment_id"))]
        legacy_rows = [row for row in effective_positions if not _is_routed_segment_id(row.get("segment_id"))]
        legacy_resolved_rows = [row for row in legacy_rows if row.get("status") in {"win", "lose", "exit"}]
        routed_slice = _slice_summary(routed_rows, starting_bankroll=starting_bankroll)
        routed_acted = int(routed_slice["acted"] or 0)
        routed_resolved = int(routed_slice["resolved"] or 0)
        routed_wins = int(routed_slice["wins"] or 0)
        routed_total_pnl = routed_slice["total_pnl"] if routed_slice["total_pnl"] is not None else None
        routed_return_pct = routed_slice["return_pct"] if routed_slice["return_pct"] is not None else None
        routed_win_rate = routed_slice["win_rate"] if routed_slice["win_rate"] is not None else None
        routed_profit_factor = (
            float(routed_slice["profit_factor"])
            if routed_slice["profit_factor"] is not None
            else None
        )
        routed_expectancy_usd = (
            float(routed_slice["expectancy_usd"])
            if routed_slice["expectancy_usd"] is not None
            else None
        )
        routed_expectancy_pct = (
            float(routed_slice["expectancy_pct"])
            if routed_slice["expectancy_pct"] is not None
            else None
        )
        routed_avg_confidence = (
            float(routed_slice["avg_confidence"])
            if routed_slice["avg_confidence"] is not None
            else None
        )
        routed_avg_total = float(routed_slice["avg_total"]) if routed_slice["avg_total"] is not None else None
        routed_legacy_acted = len(legacy_rows)
        routed_legacy_resolved = len(legacy_resolved_rows)
        total_routed_history = routed_resolved + routed_legacy_resolved
        routed_coverage_pct = (
            _round_to(routed_resolved / total_routed_history, 4)
            if total_routed_history > 0
            else None
        )
        if routed_resolved <= 0 and routed_legacy_resolved > 0:
            routed_history_status = "legacy_only"
        elif routed_resolved > 0 and routed_legacy_resolved > 0:
            routed_history_status = "mixed"
        elif routed_resolved > 0:
            routed_history_status = "routed_only"

    return PerformancePreviewSummary(
        title=active_title,
        mode=active_mode,
        total_pnl=total_pnl,
        current_balance=current_balance,
        current_equity=current_equity,
        return_pct=return_pct,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy_usd=expectancy_usd,
        expectancy_pct=expectancy_pct,
        exposure_pct=exposure_pct,
        max_drawdown_pct=max_drawdown_pct,
        resolved=len(resolved_rows),
        avg_confidence=avg_confidence,
        avg_total=avg_total,
        acted=acted,
        wins=wins,
        data_warning=data_warning,
        routed_history_status=routed_history_status,
        routed_acted=routed_acted,
        routed_resolved=routed_resolved,
        routed_wins=routed_wins,
        routed_total_pnl=routed_total_pnl,
        routed_return_pct=routed_return_pct,
        routed_win_rate=routed_win_rate,
        routed_profit_factor=routed_profit_factor,
        routed_expectancy_usd=routed_expectancy_usd,
        routed_expectancy_pct=routed_expectancy_pct,
        routed_avg_confidence=routed_avg_confidence,
        routed_avg_total=routed_avg_total,
        routed_legacy_acted=routed_legacy_acted,
        routed_legacy_resolved=routed_legacy_resolved,
        routed_coverage_pct=routed_coverage_pct,
        shadow_evidence_epoch_started_at=shadow_epoch_started_at,
        shadow_evidence_epoch_source=shadow_epoch_source,
    )


def _latest_applied_replay_promotion_at(db_path: Path | None = None) -> int:
    conn = get_conn_for_path(db_path, apply_runtime_pragmas=False) if db_path is not None else get_conn()
    try:
        try:
            row = conn.execute(
                """
                SELECT applied_at
                FROM replay_promotions
                WHERE status='applied'
                  AND applied_at > 0
                ORDER BY applied_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return 0
            raise
        return max(int(row["applied_at"] or 0), 0) if row is not None else 0
    finally:
        conn.close()


def render_tracker_preview_message(summary: PerformancePreviewSummary | None = None) -> str:
    if summary is None:
        epoch_state = read_shadow_evidence_epoch()
        resolved_summary = compute_tracker_preview_summary(
            since_ts=int(epoch_state.get("shadow_evidence_epoch_started_at") or 0),
            apply_shadow_evidence_epoch=False,
        )
    else:
        resolved_summary = summary
    balance_label = "Estimated shadow bankroll" if resolved_summary.mode == "shadow" else "Current balance"
    equity_label = "Estimated paper equity" if resolved_summary.mode == "shadow" else "Current equity"
    disclaimer = (
        "Shadow/paper estimates only; /balance does not read a live wallet balance."
        if resolved_summary.mode == "shadow"
        else "Live balance and equity are shown where available."
    )
    integrity_blocked = (
        resolved_summary.mode == "shadow"
        and "sqlite integrity check failed" in str(resolved_summary.data_warning or "").strip().lower()
    )
    if integrity_blocked:
        lines = [
            f"{resolved_summary.title} performance",
            resolved_summary.data_warning,
            disclaimer,
            "Preview blocked: shadow performance numbers are not trustworthy until Recover DB or Restart Shadow restores a clean ledger.",
        ]
        if resolved_summary.shadow_evidence_epoch_started_at > 0:
            scope_label = _format_timestamp(resolved_summary.shadow_evidence_epoch_started_at)
            source_label = (
                f" ({resolved_summary.shadow_evidence_epoch_source})"
                if resolved_summary.shadow_evidence_epoch_source
                else ""
            )
            lines.append(f"Shadow evidence epoch: since {scope_label}{source_label}")
            lines.append("Balance, equity, and exposure still include all open shadow positions across the account.")
        lines.extend(
            [
                "Routed fixed-segment shadow only",
                (
                    "Routed coverage: "
                    + (
                        _format_pct(resolved_summary.routed_coverage_pct)
                        if resolved_summary.routed_coverage_pct is not None
                        else "-"
                    )
                    + f" ({resolved_summary.routed_resolved} routed resolved, "
                    + f"{resolved_summary.routed_legacy_resolved} legacy/unassigned resolved excluded)"
                ),
                f"Routed history: {resolved_summary.routed_history_status}",
            ]
        )
        bot_state = _safe_read_bot_state()
        routed_block_reason = str(bot_state.get("routed_shadow_block_reason") or "").strip()
        if routed_block_reason:
            lines.append(f"Routed gate: {routed_block_reason}")
        return "\n".join(line for line in lines if line)

    lines = [
        f"{resolved_summary.title} performance",
        resolved_summary.data_warning,
        disclaimer,
        f"Total P&L: {_format_dollar(resolved_summary.total_pnl)}",
        f"Return %: {_format_pct(resolved_summary.return_pct)}",
        f"{balance_label}: {_format_balance(resolved_summary.current_balance)}",
        f"{equity_label}: {_format_balance(resolved_summary.current_equity)}",
        f"Win rate: {_format_pct(resolved_summary.win_rate)}",
        f"Profit factor: {_format_ratio(resolved_summary.profit_factor)}",
        f"Expectancy: {_format_expectancy(resolved_summary.expectancy_usd, resolved_summary.expectancy_pct)}",
        f"Resolved: {resolved_summary.resolved}",
        f"Exposure: {_format_pct(resolved_summary.exposure_pct)}",
        f"Max drawdown: {_format_pct(resolved_summary.max_drawdown_pct)}",
        f"Avg confidence: {_format_pct(resolved_summary.avg_confidence)}",
        f"Avg total: {_format_dollar(resolved_summary.avg_total)}",
    ]
    if resolved_summary.mode == "shadow":
        if resolved_summary.shadow_evidence_epoch_started_at > 0:
            scope_label = _format_timestamp(resolved_summary.shadow_evidence_epoch_started_at)
            source_label = (
                f" ({resolved_summary.shadow_evidence_epoch_source})"
                if resolved_summary.shadow_evidence_epoch_source
                else ""
            )
            lines.append(f"Shadow evidence epoch: since {scope_label}{source_label}")
            lines.append("Balance, equity, and exposure still include all open shadow positions across the account.")
        lines.extend(
            [
                "Routed fixed-segment shadow only",
                (
                    "Routed coverage: "
                    + (
                        _format_pct(resolved_summary.routed_coverage_pct)
                        if resolved_summary.routed_coverage_pct is not None
                        else "-"
                    )
                    + f" ({resolved_summary.routed_resolved} routed resolved, "
                    + f"{resolved_summary.routed_legacy_resolved} legacy/unassigned resolved excluded)"
                ),
                f"Routed P&L: {_format_dollar(resolved_summary.routed_total_pnl)}",
                f"Routed P&L / bankroll: {_format_pct(resolved_summary.routed_return_pct)}",
                f"Routed profit factor: {_format_ratio(resolved_summary.routed_profit_factor)}",
                f"Routed expectancy: {_format_expectancy(resolved_summary.routed_expectancy_usd, resolved_summary.routed_expectancy_pct)}",
                f"Routed history: {resolved_summary.routed_history_status}",
            ]
        )
        bot_state = _safe_read_bot_state()
        routed_block_reason = str(bot_state.get("routed_shadow_block_reason") or "").strip()
        if routed_block_reason:
            lines.append(f"Routed gate: {routed_block_reason}")
    return "\n".join(line for line in lines if line)
