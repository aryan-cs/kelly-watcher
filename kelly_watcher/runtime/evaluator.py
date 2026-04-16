from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from kelly_watcher.integrations.alerter import send_alert
from kelly_watcher.engine.beliefs import invalidate_belief_cache, sync_belief_priors
from config import entry_fixed_cost_usd, settlement_fixed_cost_usd
from kelly_watcher.data.db import DB_PATH, database_integrity_state, get_conn, get_conn_for_path
from kelly_watcher.engine.economics import build_entry_economics
from kelly_watcher.runtime.performance_preview import compute_tracker_preview_summary
from kelly_watcher.engine.segment_policy import SEGMENT_FALLBACK, SEGMENT_IDS
from kelly_watcher.engine.shadow_evidence import read_shadow_evidence_epoch
from kelly_watcher.engine.trade_contract import (
    EXECUTED_ENTRY_SQL,
    OBSERVED_BUY_SQL,
    OPEN_EXECUTED_ENTRY_SQL,
    REALIZED_CLOSE_TS_SQL,
    RESOLVED_EXECUTED_ENTRY_SQL,
    is_fill_aware_executed_buy,
    remaining_entry_shares_expr,
    remaining_entry_size_expr,
)

logger = logging.getLogger(__name__)
SEGMENT_SHADOW_MIN_RESOLVED = 20
SEGMENT_SHADOW_MAX_CALIBRATION_GAP = 0.10
SEGMENT_SHADOW_MAX_FILL_COST_SLIPPAGE_USD = 0.05
SEGMENT_SHADOW_MAX_FILL_COST_OVERSHOOT_RATIO = 0.50
UNASSIGNED_SEGMENT_ID = "unassigned"

CLOB_API = "https://clob.polymarket.com"
SPORTS_PAGE_BASE = "https://polymarket.com/sports"
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">(.*?)</script>'
)
_TEAM_WIN_QUESTION_RE = re.compile(
    r"^will\s+(.+?)\s+win(?:\s+on\s+\d{4}-\d{2}-\d{2})?\??$",
    re.IGNORECASE,
)
_DRAW_QUESTION_RE = re.compile(r"^will\s+.+?\s+end in a draw\??$", re.IGNORECASE)
_SPREAD_QUESTION_RE = re.compile(r"^spread:\s+(.+?)\s+\(([-+]?\d+(?:\.\d+)?)\)\s*$", re.IGNORECASE)
_SPORTS_ROUTE_ALIASES = {
    "atp": ("tennis", "atp"),
    "blast": ("esports", "blast"),
    "bun": ("bundesliga", "bun"),
    "cfb": ("cfb",),
    "cbb": ("cbb",),
    "cs": ("esports", "cs"),
    "epl": ("epl",),
    "esl": ("esports", "esl"),
    "esports": ("esports",),
    "f1": ("f1",),
    "ipl": ("ipl",),
    "iem": ("esports", "iem"),
    "laliga": ("laliga",),
    "ligue1": ("ligue1",),
    "nba": ("nba",),
    "nfl": ("nfl",),
    "nhl": ("nhl",),
    "mlb": ("mlb",),
    "mls": ("mls",),
    "ncaa": ("cbb", "cfb"),
    "pgl": ("esports", "pgl"),
    "seriea": ("seriea",),
    "uecl": ("uecl",),
    "uel": ("uel",),
    "ucl": ("ucl",),
    "ufc": ("ufc",),
    "val": ("valorant", "esports"),
    "valorant": ("valorant", "esports"),
    "vct": ("valorant", "esports"),
    "wnba": ("wnba",),
    "wta": ("tennis", "wta"),
}
PREMATURE_RESOLUTION_WHERE_SQL = """
outcome IS NOT NULL
AND resolution_json IS NOT NULL
AND (
    COALESCE(json_extract(resolution_json, '$.closed'), 0) IN (0, 'false', '0')
    OR (
        json_extract(resolution_json, '$.source') = 'sports_page'
        AND COALESCE(json_extract(resolution_json, '$.ended'), 0) IN (0, 'false', '0')
        AND (
            json_extract(resolution_json, '$.finishedTimestamp') IS NULL
            OR json_extract(resolution_json, '$.finishedTimestamp') = ''
        )
        AND COALESCE(
            json_extract(resolution_json, '$.period'),
            json_extract(resolution_json, '$.score.period'),
            ''
        ) NOT IN ('FT', 'FINAL', 'VFT', 'FINISHED', 'COMPLETE', 'COMPLETED', 'ENDED')
    )
)
"""


def _snapshot_fee_rate_bps(snapshot_json: Any) -> int:
    if not snapshot_json:
        return 0
    snapshot = snapshot_json
    if isinstance(snapshot_json, str):
        try:
            snapshot = json.loads(snapshot_json)
        except Exception:
            return 0
    if not isinstance(snapshot, dict):
        return 0
    try:
        return max(int(round(float(snapshot.get("fee_rate_bps") or 0))), 0)
    except (TypeError, ValueError):
        return 0


def _counterfactual_return_with_fees(row, *, won: bool) -> float | None:
    price = float(row["price_at_signal"] or 0.0)
    gross_size = float(row["signal_size_usd"] or 0.0)
    if gross_size <= 0 or not (0.0 < price < 1.0):
        return None

    entry_economics = build_entry_economics(
        gross_price=price,
        gross_shares=gross_size / price,
        gross_spent_usd=gross_size,
        fee_rate_bps=_snapshot_fee_rate_bps(row["snapshot_json"]),
        fixed_cost_usd=entry_fixed_cost_usd(),
        include_expected_exit_fee_in_sizing=False,
        expected_close_fixed_cost_usd=0.0,
    )
    if entry_economics.net_shares <= 0 or entry_economics.total_cost_usd <= 0:
        return None

    resolution_fixed_cost = settlement_fixed_cost_usd() if won and entry_economics.net_shares > 1e-9 else 0.0
    payout = entry_economics.net_shares if won else 0.0
    return round(
        (payout - entry_economics.total_cost_usd - resolution_fixed_cost) / entry_economics.total_cost_usd,
        6,
    )


def _profit_factor_from_totals(gross_profit_usd: float, gross_loss_usd: float) -> float | None:
    if gross_profit_usd <= 1e-9 and gross_loss_usd <= 1e-9:
        return None
    if gross_loss_usd <= 1e-9:
        return float("inf") if gross_profit_usd > 1e-9 else None
    return gross_profit_usd / gross_loss_usd


def _profit_factor_text(profit_factor: float | None) -> str:
    if profit_factor is None:
        return "-"
    if profit_factor == float("inf"):
        return "inf"
    return f"{profit_factor:.2f}"


def _segment_shadow_health(
    *,
    resolved: int,
    min_resolved: int,
    total_pnl_usd: float,
    expectancy_usd: float | None,
    profit_factor: float | None,
    calibration_gap: float | None,
    avg_fill_cost_slippage_usd: float | None,
    max_fill_cost_slippage_usd: float | None,
) -> tuple[str, list[str]]:
    if resolved < min_resolved:
        return "insufficient", []

    failure_reasons: list[str] = []
    quality_gate_resolved = max(int(min_resolved or 0), SEGMENT_SHADOW_MIN_RESOLVED)
    if total_pnl_usd < -1e-9:
        failure_reasons.append(f"pnl ${total_pnl_usd:.2f}")
    if expectancy_usd is not None and expectancy_usd < -1e-9:
        failure_reasons.append(f"exp ${expectancy_usd:.3f}")
    if profit_factor is not None and profit_factor < 1.0 - 1e-9:
        failure_reasons.append(f"pf {profit_factor:.2f}")
    if (
        resolved >= quality_gate_resolved
        and calibration_gap is not None
        and calibration_gap > SEGMENT_SHADOW_MAX_CALIBRATION_GAP + 1e-9
    ):
        failure_reasons.append(f"cal {calibration_gap:.3f}")
    if (
        resolved >= quality_gate_resolved
        and
        avg_fill_cost_slippage_usd is not None
        and max_fill_cost_slippage_usd is not None
        and avg_fill_cost_slippage_usd > max_fill_cost_slippage_usd + 1e-9
    ):
        failure_reasons.append(
            f"fill slip ${avg_fill_cost_slippage_usd:.3f} > ${max_fill_cost_slippage_usd:.3f}"
        )

    if failure_reasons:
        return "blocked", failure_reasons
    return "ready", []


def _segment_shadow_summary(
    *,
    status: str,
    total_segments: int,
    positive_count: int,
    negative_count: int,
    insufficient_count: int,
    min_resolved: int,
    blocked_segments: list[dict[str, Any]],
    routed_resolved: int = 0,
    legacy_unassigned_resolved: int = 0,
) -> str:
    if total_segments <= 0:
        return "no champion shadow segment data yet"

    parts: list[str] = []
    if routed_resolved > 0:
        parts.append(f"{routed_resolved} fixed-segment resolved")
    elif legacy_unassigned_resolved > 0:
        parts.append("all resolved rows predate fixed segment routing")
    if positive_count > 0:
        parts.append(f"{positive_count} segment(s) non-negative with >= {min_resolved} resolved")
    if negative_count > 0:
        blocked_text = ", ".join(
            f"{row['segment_id']} ({'; '.join(row['failure_reasons'])})"
            for row in blocked_segments[:3]
        )
        parts.append(f"blocked by {blocked_text}")
    if insufficient_count > 0:
        parts.append(f"{insufficient_count} fixed segment(s) below {min_resolved} resolved")
    if legacy_unassigned_resolved > 0:
        parts.append(f"{legacy_unassigned_resolved} legacy/unassigned resolved")
    if not parts:
        parts.append(status)
    return " | ".join(parts)


def compute_segment_shadow_report(
    *,
    mode: str = "shadow",
    min_resolved: int = SEGMENT_SHADOW_MIN_RESOLVED,
    since_ts: int | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    pnl_column = "shadow_pnl_usd" if mode == "shadow" else "actual_pnl_usd"
    real_money = 0 if mode == "shadow" else 1
    minimum_resolved = max(int(min_resolved or 0), 0)
    where_clauses = [
        "real_money=?",
        OBSERVED_BUY_SQL,
    ]
    params: list[Any] = [real_money]
    if since_ts is not None and int(since_ts or 0) > 0:
        where_clauses.append("placed_at >= ?")
        params.append(int(since_ts or 0))

    conn = get_conn_for_path(db_path, apply_runtime_pragmas=False) if db_path is not None else get_conn()
    rows = conn.execute(
        f"""
        SELECT
            COALESCE(NULLIF(TRIM(segment_id), ''), ?) AS segment_id,
            COUNT(*) AS signals,
            SUM(CASE WHEN {EXECUTED_ENTRY_SQL} THEN 1 ELSE 0 END) AS acted,
            SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} THEN 1 ELSE 0 END) AS resolved,
            SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} AND {pnl_column} > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} THEN COALESCE({pnl_column}, 0) ELSE 0 END), 6) AS total_pnl_usd,
            ROUND(SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} AND COALESCE({pnl_column}, 0) > 0 THEN COALESCE({pnl_column}, 0) ELSE 0 END), 6) AS gross_profit_usd,
            ROUND(ABS(SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} AND COALESCE({pnl_column}, 0) < 0 THEN COALESCE({pnl_column}, 0) ELSE 0 END)), 6) AS gross_loss_usd,
            ROUND(AVG(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} THEN confidence END), 6) AS avg_confidence,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} THEN expected_edge END), 6) AS avg_expected_edge,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} THEN expected_fill_cost_usd END), 6) AS avg_expected_fill_cost_usd,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} THEN expected_exit_fee_usd END), 6) AS avg_expected_exit_fee_usd,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} THEN expected_close_fixed_cost_usd END), 6) AS avg_expected_close_fixed_cost_usd,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} THEN COALESCE(entry_fee_usd, 0) + COALESCE(entry_fixed_cost_usd, 0) END), 6) AS avg_realized_fill_cost_usd,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} AND expected_fill_cost_usd IS NOT NULL THEN (COALESCE(entry_fee_usd, 0) + COALESCE(entry_fixed_cost_usd, 0)) - expected_fill_cost_usd END), 6) AS avg_fill_cost_slippage_usd,
            ROUND(AVG(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} THEN {pnl_column} END), 6) AS expectancy_usd,
            ROUND(AVG(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} THEN {pnl_column} / NULLIF(actual_entry_size_usd, 0) END), 6) AS expectancy_pct,
            ROUND(AVG(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} AND outcome IS NOT NULL THEN ((confidence - CAST(outcome AS REAL)) * (confidence - CAST(outcome AS REAL))) END), 6) AS brier_score
        FROM trade_log
        WHERE {" AND ".join(where_clauses)}
        GROUP BY COALESCE(NULLIF(TRIM(segment_id), ''), ?)
        ORDER BY segment_id ASC
        """,
        (UNASSIGNED_SEGMENT_ID, *params, UNASSIGNED_SEGMENT_ID),
    ).fetchall()
    conn.close()

    raw_rows = {
        str(row["segment_id"] or "").strip() or UNASSIGNED_SEGMENT_ID: dict(row)
        for row in rows
    }
    fixed_segments: list[dict[str, Any]] = []
    extra_segments: list[dict[str, Any]] = []
    legacy_unassigned_segment: dict[str, Any] | None = None
    positive_count = 0
    negative_count = 0
    ready_count = 0
    blocked_segments: list[dict[str, Any]] = []

    def _segment_row(raw: dict[str, Any] | None, *, segment_id: str, health: str | None = None, failure_reasons: list[str] | None = None) -> dict[str, Any]:
        row = raw or {}
        resolved = int(row.get("resolved") or 0)
        wins = int(row.get("wins") or 0)
        win_rate = round((wins / resolved), 6) if resolved else None
        gross_profit_usd = float(row.get("gross_profit_usd") or 0.0)
        gross_loss_usd = float(row.get("gross_loss_usd") or 0.0)
        total_pnl_usd = float(row.get("total_pnl_usd") or 0.0)
        expectancy_usd = float(row["expectancy_usd"]) if row.get("expectancy_usd") is not None else None
        profit_factor = _profit_factor_from_totals(gross_profit_usd, gross_loss_usd)
        avg_confidence = float(row["avg_confidence"]) if row.get("avg_confidence") is not None else None
        calibration_gap = (
            round(abs(avg_confidence - win_rate), 6)
            if avg_confidence is not None and win_rate is not None
            else None
        )
        avg_expected_fill_cost_usd = (
            round(float(row["avg_expected_fill_cost_usd"]), 6)
            if row.get("avg_expected_fill_cost_usd") is not None
            else None
        )
        avg_realized_fill_cost_usd = (
            round(float(row["avg_realized_fill_cost_usd"]), 6)
            if row.get("avg_realized_fill_cost_usd") is not None
            else None
        )
        avg_fill_cost_slippage_usd = (
            round(float(row["avg_fill_cost_slippage_usd"]), 6)
            if row.get("avg_fill_cost_slippage_usd") is not None
            else None
        )
        max_fill_cost_slippage_usd = None
        if avg_fill_cost_slippage_usd is not None:
            expected_fill_baseline = abs(avg_expected_fill_cost_usd or 0.0)
            max_fill_cost_slippage_usd = round(
                max(
                    SEGMENT_SHADOW_MAX_FILL_COST_SLIPPAGE_USD,
                    expected_fill_baseline * SEGMENT_SHADOW_MAX_FILL_COST_OVERSHOOT_RATIO,
                ),
                6,
            )
        resolved_health, resolved_failure_reasons = (
            health,
            list(failure_reasons or []),
        ) if health is not None else _segment_shadow_health(
            resolved=resolved,
            min_resolved=minimum_resolved,
            total_pnl_usd=total_pnl_usd,
            expectancy_usd=expectancy_usd,
            profit_factor=profit_factor,
            calibration_gap=calibration_gap,
            avg_fill_cost_slippage_usd=avg_fill_cost_slippage_usd,
            max_fill_cost_slippage_usd=max_fill_cost_slippage_usd,
        )
        return {
            "segment_id": segment_id,
            "signals": int(row.get("signals") or 0),
            "acted": int(row.get("acted") or 0),
            "resolved": resolved,
            "wins": wins,
            "win_rate": win_rate,
            "total_pnl_usd": round(total_pnl_usd, 6),
            "gross_profit_usd": round(gross_profit_usd, 6),
            "gross_loss_usd": round(gross_loss_usd, 6),
            "profit_factor": (
                round(float(profit_factor), 6)
                if profit_factor is not None and profit_factor != float("inf")
                else None
            ),
            "profit_factor_text": _profit_factor_text(profit_factor),
            "expectancy_usd": round(expectancy_usd, 6) if expectancy_usd is not None else None,
            "expectancy_pct": (
                round(float(row["expectancy_pct"]), 6)
                if row.get("expectancy_pct") is not None
                else None
            ),
            "avg_confidence": round(avg_confidence, 6) if avg_confidence is not None else None,
            "calibration_gap": calibration_gap,
            "brier_score": (
                round(float(row["brier_score"]), 6)
                if row.get("brier_score") is not None
                else None
            ),
            "avg_expected_edge": (
                round(float(row["avg_expected_edge"]), 6)
                if row.get("avg_expected_edge") is not None
                else None
            ),
            "avg_expected_fill_cost_usd": avg_expected_fill_cost_usd,
            "avg_realized_fill_cost_usd": avg_realized_fill_cost_usd,
            "avg_fill_cost_slippage_usd": avg_fill_cost_slippage_usd,
            "max_fill_cost_slippage_usd": max_fill_cost_slippage_usd,
            "avg_expected_exit_fee_usd": (
                round(float(row["avg_expected_exit_fee_usd"]), 6)
                if row.get("avg_expected_exit_fee_usd") is not None
                else None
            ),
            "avg_expected_close_fixed_cost_usd": (
                round(float(row["avg_expected_close_fixed_cost_usd"]), 6)
                if row.get("avg_expected_close_fixed_cost_usd") is not None
                else None
            ),
            "health": resolved_health,
            "failure_reasons": resolved_failure_reasons,
        }

    for segment_id in (*SEGMENT_IDS, SEGMENT_FALLBACK):
        segment_row = _segment_row(raw_rows.pop(segment_id, None), segment_id=segment_id)
        fixed_segments.append(segment_row)
        if int(segment_row["resolved"] or 0) >= minimum_resolved:
            ready_count += 1
        if segment_row["health"] == "ready":
            positive_count += 1
        elif segment_row["health"] == "blocked":
            negative_count += 1
            blocked_segments.append(segment_row)

    if UNASSIGNED_SEGMENT_ID in raw_rows:
        legacy_unassigned_segment = _segment_row(
            raw_rows.pop(UNASSIGNED_SEGMENT_ID),
            segment_id=UNASSIGNED_SEGMENT_ID,
            health="legacy",
            failure_reasons=["rows predate fixed segment routing"],
        )

    for segment_id, raw in sorted(raw_rows.items()):
        extra_segments.append(
            _segment_row(
                raw,
                segment_id=segment_id,
                health="unexpected",
                failure_reasons=[f"unexpected segment_id value: {segment_id}"],
            )
        )

    blocked_segments.sort(
        key=lambda row: (
            float(row.get("total_pnl_usd") or 0.0),
            float(row.get("expectancy_usd") or 0.0),
            str(row.get("segment_id") or ""),
        )
    )
    fixed_segments.sort(
        key=lambda row: (
            {"blocked": 0, "insufficient": 1, "ready": 2}.get(str(row.get("health") or ""), 3),
            -int(row.get("resolved") or 0),
            str(row.get("segment_id") or ""),
        )
    )
    extra_segments.sort(
        key=lambda row: (
            {"unexpected": 0}.get(str(row.get("health") or ""), 1),
            str(row.get("segment_id") or ""),
        )
    )

    segments = list(fixed_segments)
    if legacy_unassigned_segment is not None:
        segments.append(legacy_unassigned_segment)
    segments.extend(extra_segments)

    total_segments = len(fixed_segments)
    routed_signals = sum(int(row.get("signals") or 0) for row in fixed_segments)
    routed_acted = sum(int(row.get("acted") or 0) for row in fixed_segments)
    routed_resolved = sum(int(row.get("resolved") or 0) for row in fixed_segments)
    legacy_unassigned_signals = int((legacy_unassigned_segment or {}).get("signals") or 0)
    legacy_unassigned_acted = int((legacy_unassigned_segment or {}).get("acted") or 0)
    insufficient_count = max(total_segments - ready_count, 0)
    if total_segments <= 0:
        status = "no_data"
    elif negative_count > 0:
        status = "blocked"
    elif ready_count <= 0:
        status = "insufficient"
    elif insufficient_count > 0:
        status = "mixed"
    else:
        status = "ready"

    legacy_unassigned_resolved = int((legacy_unassigned_segment or {}).get("resolved") or 0)
    history_status = "empty"
    if routed_resolved <= 0 and legacy_unassigned_resolved > 0:
        history_status = "legacy_only"
        if status == "insufficient":
            status = "legacy_only"
    elif routed_resolved > 0 and legacy_unassigned_resolved > 0:
        history_status = "mixed"
    elif routed_resolved > 0:
        history_status = "routed_only"
    elif legacy_unassigned_resolved > 0:
        history_status = "legacy_only"

    total_resolved_history = routed_resolved + legacy_unassigned_resolved
    routed_coverage_pct = (
        round(routed_resolved / total_resolved_history, 6)
        if total_resolved_history > 0
        else None
    )
    return {
        "mode": mode,
        "min_resolved": minimum_resolved,
        "scope": "since_ts" if since_ts is not None and int(since_ts or 0) > 0 else "all_history",
        "since_ts": int(since_ts or 0),
        "status": status,
        "history_status": history_status,
        "total_segments": total_segments,
        "ready_count": ready_count,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "blocked_count": negative_count,
        "insufficient_count": insufficient_count,
        "routed_signals": routed_signals,
        "routed_acted": routed_acted,
        "routed_resolved": routed_resolved,
        "legacy_unassigned_signals": legacy_unassigned_signals,
        "legacy_unassigned_acted": legacy_unassigned_acted,
        "legacy_unassigned_resolved": legacy_unassigned_resolved,
        "routed_coverage_pct": routed_coverage_pct,
        "segments": segments,
        "summary": _segment_shadow_summary(
            status=status,
            total_segments=total_segments,
            positive_count=positive_count,
            negative_count=negative_count,
            insufficient_count=insufficient_count,
            min_resolved=minimum_resolved,
            blocked_segments=blocked_segments,
            routed_resolved=routed_resolved,
            legacy_unassigned_resolved=legacy_unassigned_resolved,
        ),
    }


def resolve_shadow_trades(
    *,
    trade_id: str | None = None,
    market_id: str | None = None,
    question_contains: str | None = None,
    forced_outcome: str | None = None,
) -> list[dict]:
    conn = get_conn()
    where_clauses = [
        "outcome IS NULL",
        "COALESCE(source_action, 'buy')='buy'",
        "real_money=0",
    ]
    params: list[Any] = []
    if trade_id:
        where_clauses.append("trade_id=?")
        params.append(str(trade_id).strip())
    if market_id:
        where_clauses.append("market_id=?")
        params.append(str(market_id).strip())
    if question_contains:
        where_clauses.append("LOWER(question) LIKE ?")
        params.append(f"%{str(question_contains).strip().lower()}%")
    unresolved = conn.execute(
        f"""
        SELECT id, trade_id, market_id, question, market_url, market_metadata_json,
               trader_address, trader_name,
               token_id, side, price_at_signal, signal_size_usd,
               actual_entry_price, actual_entry_shares, actual_entry_size_usd,
               snapshot_json,
               remaining_entry_shares, remaining_entry_size_usd, realized_exit_pnl_usd,
               shadow_pnl_usd, actual_pnl_usd, resolution_fixed_cost_usd,
               real_money, skipped, source_action, exited_at, source_shares
        FROM trade_log
        WHERE {" AND ".join(where_clauses)}
        """,
        params,
    ).fetchall()
    conn.close()

    if not unresolved:
        return []

    resolved_rows: list[dict] = []
    market_cache: dict[str, dict | None] = {}
    sports_page_cache: dict[str, dict[str, Any] | None] = {}
    normalized_forced_outcome = str(forced_outcome or "").strip()
    if normalized_forced_outcome and not (trade_id or market_id or question_contains):
        raise ValueError("forced_outcome requires trade_id, market_id, or question_contains")
    with httpx.Client(timeout=10.0) as client:
        for row in unresolved:
            try:
                now_ts = int(time.time())
                result: str | None = None
                resolution_payload: dict[str, Any] | None = None

                if normalized_forced_outcome:
                    result = normalized_forced_outcome
                    resolution_payload = {
                        "source": "manual_override",
                        "closed": True,
                        "forcedOutcome": normalized_forced_outcome,
                    }
                else:
                    sports_snapshot = _fetch_sports_page_snapshot(client, row, sports_page_cache)
                    if sports_snapshot is not None:
                        result = _resolve_from_sports_page(row, sports_snapshot)
                        if result is not None:
                            resolution_payload = _sports_resolution_payload(row, sports_snapshot)

                    if result is None:
                        market = _fetch_market(client, str(row["market_id"]), market_cache)
                        if market and _market_is_closed(market):
                            result = _winning_outcome(market)
                            if result is None:
                                logger.info(
                                    "Closed market %s is missing an explicit winner after sports-page fallback",
                                    str(row["market_id"])[:12],
                                )
                            else:
                                resolution_payload = market

                if result is None:
                    continue

                won = str(row["side"]).strip().lower() == str(result).strip().lower()
                fill_aware = is_fill_aware_executed_buy(row)
                price = float(
                    row["actual_entry_price"] if row["actual_entry_price"] is not None else row["price_at_signal"]
                )
                if row["skipped"]:
                    unit_return = _counterfactual_return_with_fees(row, won=won)
                    if unit_return is None:
                        if won and price > 0:
                            unit_return = round((1.0 - price) / price, 6)
                        elif price > 0:
                            unit_return = round(-price / price, 6)
                        else:
                            unit_return = -1.0
                elif won and price > 0:
                    unit_return = round((1.0 - price) / price, 6)
                elif price > 0:
                    unit_return = round(-price / price, 6)
                else:
                    unit_return = -1.0
                pnl = None
                resolution_fixed_cost = 0.0
                if fill_aware:
                    existing_pnl = row["actual_pnl_usd"] if row["real_money"] == 1 else row["shadow_pnl_usd"]
                    if existing_pnl is not None and row["exited_at"] is not None:
                        pnl = round(float(existing_pnl), 2)
                        resolution_fixed_cost = float(row["resolution_fixed_cost_usd"] or 0.0)
                    else:
                        remaining_shares = float(
                            row["remaining_entry_shares"]
                            if row["remaining_entry_shares"] is not None
                            else row["actual_entry_shares"]
                            if row["actual_entry_shares"] is not None
                            else row["source_shares"] or 0.0
                        )
                        remaining_size = float(
                            row["remaining_entry_size_usd"]
                            if row["remaining_entry_size_usd"] is not None
                            else row["actual_entry_size_usd"]
                            if row["actual_entry_size_usd"] is not None
                            else row["signal_size_usd"] or 0.0
                        )
                        realized_exit_pnl = float(row["realized_exit_pnl_usd"] or 0.0)
                        resolution_fixed_cost = (
                            round(settlement_fixed_cost_usd(), 6)
                            if won and remaining_shares > 1e-9
                            else 0.0
                        )
                        payout = remaining_shares if won else 0.0
                        pnl = round(realized_exit_pnl + payout - remaining_size - resolution_fixed_cost, 2)

                conn = get_conn()
                conn.execute(
                    """
                    UPDATE trade_log
                    SET outcome=?, market_resolved_outcome=?, counterfactual_return=?,
                        shadow_pnl_usd=COALESCE(?, shadow_pnl_usd),
                        actual_pnl_usd=COALESCE(?, actual_pnl_usd),
                        resolution_fixed_cost_usd=CASE
                            WHEN ? IS NOT NULL AND exited_at IS NULL THEN ?
                            ELSE resolution_fixed_cost_usd
                        END,
                        remaining_entry_shares=CASE
                            WHEN ? IS NOT NULL AND exited_at IS NULL THEN 0
                            ELSE remaining_entry_shares
                        END,
                        remaining_entry_size_usd=CASE
                            WHEN ? IS NOT NULL AND exited_at IS NULL THEN 0
                            ELSE remaining_entry_size_usd
                        END,
                        remaining_source_shares=CASE
                            WHEN ? IS NOT NULL AND exited_at IS NULL THEN 0
                            ELSE remaining_source_shares
                        END,
                        label_applied_at=?,
                        resolved_at=COALESCE(resolved_at, ?),
                        resolution_json=?
                    WHERE id=?
                    """,
                    (
                        1 if won else 0,
                        str(result).strip().lower(),
                        unit_return,
                        pnl if row["real_money"] == 0 and fill_aware else None,
                        pnl if row["real_money"] == 1 and fill_aware else None,
                        pnl if fill_aware else None,
                        resolution_fixed_cost,
                        pnl if fill_aware else None,
                        pnl if fill_aware else None,
                        pnl if fill_aware else None,
                        now_ts,
                        now_ts,
                        json.dumps(resolution_payload, separators=(",", ":"), default=str),
                        row["id"],
                    ),
                )
                token_id_str = str(row["token_id"] or "").strip().lower()
                side_str = str(row["side"] or "").strip().lower()
                if token_id_str:
                    deleted = conn.execute(
                        "DELETE FROM positions WHERE market_id=? AND LOWER(token_id)=? AND real_money=?",
                        (row["market_id"], token_id_str, row["real_money"]),
                    ).rowcount
                    if deleted == 0:
                        conn.execute(
                            "DELETE FROM positions WHERE market_id=? AND LOWER(side)=? AND real_money=?",
                            (row["market_id"], side_str, row["real_money"]),
                        )
                else:
                    conn.execute(
                        "DELETE FROM positions WHERE market_id=? AND LOWER(side)=? AND real_money=?",
                        (row["market_id"], side_str, row["real_money"]),
                    )
                conn.commit()
                conn.close()

                resolved_rows.append(
                    {
                        "trade_id": row["trade_id"],
                        "market_id": row["market_id"],
                        "question": row["question"],
                        "market_url": row["market_url"],
                        "trader_address": row["trader_address"],
                        "trader_name": row["trader_name"],
                        "side": row["side"],
                        "real_money": row["real_money"],
                        "executed": bool(fill_aware),
                        "won": won,
                        "pnl": float(pnl or 0.0),
                        "market_resolved_outcome": str(result).strip().lower(),
                    }
                )
            except Exception as exc:
                logger.error("Resolution check failed for %s: %s", row["market_id"][:12], exc)

    if resolved_rows:
        logger.info("Resolved %s trades", len(resolved_rows))
    sync_belief_priors()
    return resolved_rows


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve unresolved shadow trades")
    parser.add_argument("--trade-id", help="Resolve a specific unresolved trade by trade_id")
    parser.add_argument("--market-id", help="Resolve unresolved trades for one market_id")
    parser.add_argument(
        "--question-contains",
        help="Resolve unresolved trades whose question contains this text (case-insensitive)",
    )
    parser.add_argument(
        "--force-outcome",
        help="Manually force the resolved outcome label for the selected unresolved trade(s)",
    )
    return parser


def _fetch_sports_page_snapshot(
    client: httpx.Client,
    row,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    meta = _row_market_metadata(row)
    event_slug = _sports_event_slug(row, meta)
    if not event_slug:
        return None

    cache_key = event_slug.lower()
    if cache_key in cache:
        return cache[cache_key]

    for league_slug in _sports_route_candidates(event_slug, meta):
        url = f"{SPORTS_PAGE_BASE}/{league_slug}/{event_slug}"
        try:
            response = client.get(url, follow_redirects=True)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            match = _NEXT_DATA_RE.search(response.text)
            if match is None:
                continue
            payload = json.loads(match.group(1))
            page_props = payload.get("props", {}).get("pageProps", {})
            if not isinstance(page_props, dict):
                continue
            if page_props.get("statusCode") == 404:
                continue
            event = page_props.get("event", {})
            if not isinstance(event, dict):
                continue
            cache[cache_key] = page_props
            return page_props
        except Exception as exc:
            logger.debug("Sports page fetch failed for %s via %s: %s", cache_key, league_slug, exc)

    cache[cache_key] = None
    return None


def _resolve_from_sports_page(row, snapshot: dict[str, Any]) -> str | None:
    if not _sports_snapshot_is_ended(snapshot):
        return None

    market = _sports_snapshot_market(snapshot, str(row["market_id"] or ""))
    if market is not None:
        outcome = _resolve_sports_market(row, market, snapshot)
        if outcome is not None:
            return outcome

    return _resolve_basic_sports_question(row, snapshot)


def _sports_resolution_payload(row, snapshot: dict[str, Any]) -> dict[str, Any]:
    market = _sports_snapshot_market(snapshot, str(row["market_id"] or ""))
    event = snapshot.get("event", {})
    if not isinstance(event, dict):
        event = {}
    score = event.get("score", snapshot.get("score"))
    period = event.get("period") or snapshot.get("period")
    if not period and isinstance(score, dict):
        period = score.get("period") or score.get("status")
    finished_timestamp = event.get("finishedTimestamp") or snapshot.get("finishedTimestamp")
    payload: dict[str, Any] = {
        "source": "sports_page",
        "closed": True,
        "ended": _sports_snapshot_is_ended(snapshot),
        "score": score,
        "period": period,
        "finishedTimestamp": finished_timestamp,
        "canonicalUrl": snapshot.get("canonicalUrl"),
    }

    event_slug = str(event.get("slug") or "").strip()
    if event_slug:
        payload["eventSlug"] = event_slug

    teams = _snapshot_teams(snapshot, market)
    if teams:
        payload["teams"] = teams

    if isinstance(market, dict):
        payload["market"] = {
            "conditionId": market.get("conditionId"),
            "question": market.get("question"),
            "slug": market.get("slug"),
            "closed": market.get("closed"),
            "acceptingOrders": market.get("acceptingOrders"),
            "sportsMarketType": market.get("sportsMarketType"),
            "line": market.get("line"),
            "outcomes": _parse_text_list(market.get("outcomes")),
            "outcomePrices": _parse_text_list(market.get("outcomePrices")),
        }

    return payload


def _shadow_report_scope(
    *,
    since_ts: int | None = None,
    apply_shadow_evidence_epoch: bool = False,
    db_path: Path | None = None,
) -> tuple[int, int, str]:
    effective_since_ts = max(int(since_ts or 0), 0)
    epoch_started_at = 0
    epoch_source = ""
    if apply_shadow_evidence_epoch:
        epoch_state = read_shadow_evidence_epoch()
        epoch_started_at = max(int(epoch_state.get("shadow_evidence_epoch_started_at") or 0), 0)
        epoch_source = str(epoch_state.get("shadow_evidence_epoch_source") or "").strip().lower()
        effective_since_ts = max(
            effective_since_ts,
            epoch_started_at,
            _latest_applied_replay_promotion_at(db_path=db_path),
        )
    return effective_since_ts, epoch_started_at, epoch_source


def _latest_applied_replay_promotion_at(*, db_path: Path | None = None) -> int:
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


def _current_shadow_segment_report_since_ts() -> int | None:
    epoch_started_at, _, _epoch_source = _shadow_report_scope(apply_shadow_evidence_epoch=True)
    applied_at = _latest_applied_replay_promotion_at()
    effective_since_ts = max(int(epoch_started_at or 0), int(applied_at or 0))
    return effective_since_ts if effective_since_ts > 0 else None


def compute_performance_report(
    mode: str = "shadow",
    *,
    db_path: Path | None = None,
    since_ts: int | None = None,
    apply_shadow_evidence_epoch: bool = False,
) -> dict:
    pnl_column = "shadow_pnl_usd" if mode == "shadow" else "actual_pnl_usd"
    real_money = 0 if mode == "shadow" else 1
    effective_since_ts = 0
    epoch_started_at = 0
    epoch_source = ""
    if mode == "shadow":
        effective_since_ts, epoch_started_at, epoch_source = _shadow_report_scope(
            since_ts=since_ts,
            apply_shadow_evidence_epoch=apply_shadow_evidence_epoch,
            db_path=db_path,
        )
    conn = get_conn_for_path(db_path, apply_runtime_pragmas=False) if db_path is not None else get_conn()
    where_clauses = ["real_money=?"]
    params: list[Any] = [real_money]
    if mode == "shadow" and effective_since_ts > 0:
        where_clauses.append("placed_at >= ?")
        params.append(effective_since_ts)
    where_sql = " AND ".join(where_clauses)

    summary = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN {OBSERVED_BUY_SQL} THEN 1 ELSE 0 END) AS total_signals,
            SUM(CASE WHEN {EXECUTED_ENTRY_SQL} THEN 1 ELSE 0 END) AS acted,
            SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} THEN 1 ELSE 0 END) AS resolved,
            SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} AND {pnl_column} > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(SUM(CASE WHEN {EXECUTED_ENTRY_SQL} THEN COALESCE({pnl_column}, 0) ELSE 0 END), 2) AS total_pnl,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} THEN confidence END), 3) AS avg_confidence,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} THEN actual_entry_size_usd END), 2) AS avg_size
        FROM trade_log
        WHERE {where_sql}
        """,
        tuple(params),
    ).fetchone()

    traders = conn.execute(
        f"""
        SELECT trader_address,
               COUNT(*) AS n,
               SUM(CASE WHEN {pnl_column} > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM({pnl_column}), 2) AS pnl
        FROM trade_log
        WHERE {where_sql}
          AND {RESOLVED_EXECUTED_ENTRY_SQL}
        GROUP BY trader_address
        ORDER BY pnl DESC
        LIMIT 10
        """,
        tuple(params),
    ).fetchall()

    week_ago = int(time.time()) - 7 * 86400
    weekly = conn.execute(
        f"""
        SELECT ROUND(SUM({pnl_column}), 2) AS pnl
        FROM trade_log
        WHERE {where_sql}
          AND {RESOLVED_EXECUTED_ENTRY_SQL}
          AND {REALIZED_CLOSE_TS_SQL} > ?
        """,
        (*params, week_ago),
    ).fetchone()

    daily_rows = conn.execute(
        f"""
        SELECT strftime('%Y-%m-%d', datetime({REALIZED_CLOSE_TS_SQL}, 'unixepoch', 'localtime')) AS day,
               SUM({pnl_column}) AS day_pnl
        FROM trade_log
        WHERE {where_sql}
          AND {RESOLVED_EXECUTED_ENTRY_SQL}
        GROUP BY day
        ORDER BY day
        """,
        tuple(params),
    ).fetchall()
    all_time_resolved = 0
    if mode == "shadow" and effective_since_ts > 0:
        all_time_row = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM trade_log
            WHERE real_money=?
              AND {RESOLVED_EXECUTED_ENTRY_SQL}
            """,
            (real_money,),
        ).fetchone()
        all_time_resolved = max(int(all_time_row["n"] or 0), 0)
    conn.close()

    resolved = int(summary["resolved"] or 0)
    wins = int(summary["wins"] or 0)
    day_pnls = [float(row["day_pnl"]) for row in daily_rows if row["day_pnl"] is not None]
    sharpe = float(np.mean(day_pnls) / (np.std(day_pnls) + 1e-6)) if len(day_pnls) > 1 else 0.0
    preview = compute_tracker_preview_summary(
        mode=mode,
        db_path=db_path,
        use_bot_state_balance=(db_path is None and not (mode == "shadow" and effective_since_ts > 0)),
        since_ts=effective_since_ts if mode == "shadow" and effective_since_ts > 0 else None,
        apply_shadow_evidence_epoch=False,
    )
    legacy_resolved_excluded = max(all_time_resolved - resolved, 0) if mode == "shadow" else 0

    return {
        "mode": mode,
        "scope": "current_evidence_window" if mode == "shadow" and effective_since_ts > 0 else "all_history",
        "since_ts": effective_since_ts,
        "shadow_evidence_epoch_started_at": epoch_started_at,
        "shadow_evidence_epoch_source": epoch_source,
        "total_signals": int(summary["total_signals"] or 0),
        "acted": int(summary["acted"] or 0),
        "resolved": resolved,
        "all_time_resolved": max(all_time_resolved, resolved) if mode == "shadow" else resolved,
        "legacy_resolved_excluded": legacy_resolved_excluded,
        "win_rate": round((wins / resolved) if resolved else 0.0, 3),
        "total_pnl_usd": float(summary["total_pnl"] or 0.0),
        "current_balance_usd": preview.current_balance,
        "current_equity_usd": preview.current_equity,
        "return_pct": preview.return_pct,
        "profit_factor": preview.profit_factor,
        "expectancy_usd": preview.expectancy_usd,
        "expectancy_pct": preview.expectancy_pct,
        "exposure_pct": preview.exposure_pct,
        "max_drawdown_pct": preview.max_drawdown_pct,
        "weekly_pnl_usd": float(weekly["pnl"] or 0.0),
        "avg_confidence": float(summary["avg_confidence"] or 0.0),
        "avg_size_usd": float(summary["avg_size"] or 0.0),
        "sharpe": round(sharpe, 3),
        "top_traders": [dict(row) for row in traders],
        "daily_pnls": [
            {"day": row["day"], "pnl": float(row["day_pnl"] or 0.0)}
            for row in daily_rows
        ],
        "data_warning": preview.data_warning,
    }


def persist_performance_snapshot(mode: str) -> None:
    report = compute_performance_report(
        mode,
        apply_shadow_evidence_epoch=(mode == "shadow"),
    )
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO perf_snapshots (
            snapshot_at, mode, scope, since_ts, epoch_started_at, epoch_source,
            legacy_resolved_excluded, n_signals, n_acted, n_resolved,
            win_rate, total_pnl_usd, avg_confidence, sharpe
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(time.time()),
            mode,
            str(report.get("scope") or "all_history"),
            int(report.get("since_ts") or 0),
            int(report.get("shadow_evidence_epoch_started_at") or 0),
            str(report.get("shadow_evidence_epoch_source") or ""),
            int(report.get("legacy_resolved_excluded") or 0),
            report["total_signals"],
            report["acted"],
            report["resolved"],
            report["win_rate"],
            report["total_pnl_usd"],
            report["avg_confidence"],
            report["sharpe"],
        ),
    )
    conn.commit()
    conn.close()


def daily_report() -> None:
    integrity = database_integrity_state()
    if integrity.get("db_integrity_known") and not integrity.get("db_integrity_ok"):
        detail = str(integrity.get("db_integrity_message") or "").splitlines()[0].strip()
        message = "daily performance report skipped: SQLite integrity check failed; shadow history is not trustworthy"
        if detail:
            message += f" ({detail})"
        send_alert(message, kind="report")
        return

    resolve_shadow_trades()
    shadow = compute_performance_report("shadow", apply_shadow_evidence_epoch=True)
    live = compute_performance_report("live")
    shadow_segments = compute_segment_shadow_report(
        mode="shadow",
        since_ts=_current_shadow_segment_report_since_ts(),
    )
    persist_performance_snapshot("shadow")
    persist_performance_snapshot("live")

    def _fmt_pct(value: float | None, digits: int = 1) -> str:
        if value is None:
            return "-"
        return f"{value * 100:.{digits}f}%"

    def _fmt_ratio(value: float | None) -> str:
        if value is None:
            return "-"
        if value == float("inf"):
            return "inf"
        return f"{value:.2f}"

    def _fmt_expectancy(report: dict) -> str:
        parts: list[str] = []
        expectancy_usd = report.get("expectancy_usd")
        expectancy_pct = report.get("expectancy_pct")
        if expectancy_usd is not None:
            parts.append(f"${float(expectancy_usd):+.2f}")
        if expectancy_pct is not None:
            parts.append(_fmt_pct(float(expectancy_pct)))
        return " / ".join(parts) if parts else "-"

    def _fmt_scope(report: dict[str, Any]) -> str:
        if str(report.get("scope") or "").strip() != "current_evidence_window":
            return "all history"
        since_ts = max(int(report.get("since_ts") or 0), 0)
        if since_ts <= 0:
            return "current evidence window"
        source = str(report.get("shadow_evidence_epoch_source") or "").strip()
        source_suffix = f" ({source})" if source else ""
        return f"current evidence window since {datetime.fromtimestamp(since_ts).strftime('%Y-%m-%d %H:%M')}{source_suffix}"

    lines = [
        "daily performance report",
        f"shadow scope: {_fmt_scope(shadow)}",
        (
            f"shadow: {shadow['resolved']} resolved | win rate {shadow['win_rate']:.0%} | "
            f"pnl ${shadow['total_pnl_usd']:.2f} | return {_fmt_pct(shadow['return_pct'])} | "
            f"7d ${shadow['weekly_pnl_usd']:.2f}"
        ),
        (
            f"shadow: profit factor {_fmt_ratio(shadow['profit_factor'])} | "
            f"expectancy {_fmt_expectancy(shadow)} | exposure {_fmt_pct(shadow['exposure_pct'])}"
        ),
        (
            f"shadow: max drawdown {_fmt_pct(shadow['max_drawdown_pct'])} | "
            f"sharpe {shadow['sharpe']:.2f} | avg confidence {shadow['avg_confidence']:.3f} | "
            f"avg total ${shadow['avg_size_usd']:.2f}"
        ),
    ]
    if int(shadow.get("legacy_resolved_excluded") or 0) > 0:
        lines.append(
            "shadow scope: "
            f"{int(shadow['legacy_resolved_excluded'])} legacy/all-time resolved trades excluded from the current window"
        )

    if live["acted"] > 0:
        lines.extend(
            [
                (
                    f"live: {live['resolved']} resolved | win rate {live['win_rate']:.0%} | "
                    f"pnl ${live['total_pnl_usd']:.2f} | return {_fmt_pct(live['return_pct'])}"
                ),
                (
                    f"live: profit factor {_fmt_ratio(live['profit_factor'])} | "
                    f"expectancy {_fmt_expectancy(live)} | exposure {_fmt_pct(live['exposure_pct'])}"
                ),
                (
                    f"live: max drawdown {_fmt_pct(live['max_drawdown_pct'])} | "
                    f"avg confidence {live['avg_confidence']:.3f} | avg total ${live['avg_size_usd']:.2f}"
                ),
            ]
        )

    if shadow["top_traders"]:
        lines.append("top shadow traders by pnl:")
        for trader in shadow["top_traders"][:3]:
            lines.append(
                f"- {trader['trader_address'][:10]}... "
                f"{trader['wins']}/{trader['n']} | ${float(trader['pnl'] or 0.0):.2f}"
            )

    if shadow_segments["total_segments"] > 0:
        lines.append(
            "shadow segments: "
            f"{shadow_segments['status']} | {shadow_segments['summary']}"
        )

    send_alert("\n".join(lines), kind="report")


def cleanup_premature_resolutions(backup_path: Path | None = None) -> dict[str, int | str]:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_target = backup_path or DB_PATH.with_name(f"{DB_PATH.stem}.premature_cleanup_{timestamp}.bak")
    backup_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB_PATH, backup_target)

    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT
            id,
            real_money,
            skipped,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            source_shares,
            realized_exit_shares,
            realized_exit_size_usd,
            exited_at
        FROM trade_log
        WHERE {PREMATURE_RESOLUTION_WHERE_SQL}
        ORDER BY id
        """
    ).fetchall()

    if not rows:
        conn.close()
        return {
            "backup_path": str(backup_target),
            "rows_cleaned": 0,
            "skipped_rows_cleaned": 0,
            "open_positions_reopened": 0,
            "exited_rows_preserved": 0,
            "belief_rows_reapplied": 0,
        }

    skipped_rows_cleaned = 0
    open_positions_reopened = 0
    exited_rows_preserved = 0
    for row in rows:
        fill_aware = is_fill_aware_executed_buy(row)
        exited_at = row["exited_at"]
        restored_resolved_at = int(exited_at) if exited_at is not None else None

        conn.execute(
            """
            UPDATE trade_log
            SET outcome=NULL,
                market_resolved_outcome=NULL,
                counterfactual_return=NULL,
                label_applied_at=NULL,
                resolution_json=NULL,
                resolved_at=?
            WHERE id=?
            """,
            (restored_resolved_at, int(row["id"])),
        )

        if bool(row["skipped"]):
            skipped_rows_cleaned += 1
            continue

        if fill_aware and exited_at is None:
            actual_shares = max(float(row["actual_entry_shares"] or 0.0), 0.0)
            actual_size = max(float(row["actual_entry_size_usd"] or 0.0), 0.0)
            source_shares = max(float(row["source_shares"] or 0.0), 0.0)
            realized_exit_shares = max(float(row["realized_exit_shares"] or 0.0), 0.0)
            realized_exit_size = max(float(row["realized_exit_size_usd"] or 0.0), 0.0)

            remaining_shares = max(round(actual_shares - realized_exit_shares, 6), 0.0)
            remaining_size = max(round(actual_size - realized_exit_size, 6), 0.0)
            if actual_shares > 1e-9:
                remaining_source = max(round(source_shares * (remaining_shares / actual_shares), 6), 0.0)
            else:
                remaining_source = source_shares

            conn.execute(
                """
                UPDATE trade_log
                SET shadow_pnl_usd=NULL,
                    actual_pnl_usd=NULL,
                    remaining_entry_shares=?,
                    remaining_entry_size_usd=?,
                    remaining_source_shares=?
                WHERE id=?
                """,
                (
                    remaining_shares,
                    remaining_size,
                    remaining_source,
                    int(row["id"]),
                ),
            )
            open_positions_reopened += 1
        elif fill_aware and exited_at is not None:
            exited_rows_preserved += 1

    _rebuild_shadow_positions(conn)
    conn.execute("DELETE FROM belief_updates")
    conn.execute("DELETE FROM belief_priors")
    conn.commit()
    conn.close()

    invalidate_belief_cache()
    belief_rows_reapplied = sync_belief_priors()
    logger.info(
        "Cleaned %s premature resolutions (%s skipped, %s reopened, %s exited preserved)",
        len(rows),
        skipped_rows_cleaned,
        open_positions_reopened,
        exited_rows_preserved,
    )
    return {
        "backup_path": str(backup_target),
        "rows_cleaned": len(rows),
        "skipped_rows_cleaned": skipped_rows_cleaned,
        "open_positions_reopened": open_positions_reopened,
        "exited_rows_preserved": exited_rows_preserved,
        "belief_rows_reapplied": belief_rows_reapplied,
    }


def _winning_outcome(market: dict) -> str | None:
    winner_tokens: list[str] = []
    for token in market.get("tokens", []):
        if not isinstance(token, dict):
            continue
        if not _truthy_market_flag(token.get("winner", False)):
            continue
        outcome = str(token.get("outcome", "")).strip()
        if outcome:
            winner_tokens.append(outcome)

    if len(winner_tokens) == 1:
        return winner_tokens[0]
    if len(winner_tokens) > 1:
        logger.warning("Closed market reported multiple winning tokens: %s", winner_tokens)
        return None

    winner = str(market.get("winner") or market.get("resolvedOutcome") or "").strip()
    if winner:
        return winner
    return None


def _row_market_metadata(row) -> dict[str, Any] | None:
    raw = row["market_metadata_json"]
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _sports_event_slug(row, meta: dict[str, Any] | None) -> str | None:
    if meta is not None:
        events = meta.get("events", [])
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                slug = str(event.get("slug") or "").strip()
                if slug:
                    return slug
        slug = str(meta.get("slug") or meta.get("marketSlug") or "").strip()
        if slug:
            return slug
        nested_event = meta.get("event")
        if isinstance(nested_event, dict):
            slug = str(nested_event.get("slug") or nested_event.get("marketSlug") or "").strip()
            if slug:
                return slug

    market_url = str(row["market_url"] or "").strip()
    if "/event/" not in market_url:
        return None
    slug = market_url.rsplit("/event/", 1)[-1].strip().strip("/")
    return slug or None


def _sports_route_candidates(event_slug: str, meta: dict[str, Any] | None) -> list[str]:
    candidates: list[str] = []

    def _add(value: str | None) -> None:
        text = str(value or "").strip().lower()
        if not text or text in candidates:
            return
        candidates.append(text)

    prefix = event_slug.split("-", 1)[0].strip().lower()
    for route in _SPORTS_ROUTE_ALIASES.get(prefix, ()):
        _add(route)

    if meta is not None:
        for key in ("sportsMarketType", "seriesSlug", "leagueSlug", "sportSlug"):
            value = str(meta.get(key) or "").strip().lower()
            if value:
                _add(value)

    _add(prefix)
    return candidates


def _sports_snapshot_is_ended(snapshot: dict[str, Any]) -> bool:
    event = snapshot.get("event")
    if not isinstance(event, dict):
        event = {}

    for source in (event, snapshot):
        if not isinstance(source, dict):
            continue
        if _truthy_market_flag(source.get("ended", False)):
            return True
        if str(source.get("finishedTimestamp") or "").strip():
            return True
        period = str(
            source.get("period") or source.get("status") or source.get("gameStatus") or ""
        ).strip().upper()
        if period in {"FT", "FINAL", "VFT", "FINISHED", "COMPLETE", "COMPLETED", "ENDED"}:
            return True

    score_obj = event.get("score") or snapshot.get("score") or {}
    if isinstance(score_obj, dict):
        status = str(score_obj.get("status") or score_obj.get("period") or "").strip().upper()
        if status in {"FT", "FINAL", "VFT", "FINISHED", "COMPLETE", "COMPLETED", "ENDED"}:
            return True

    return False


def _sports_snapshot_market(snapshot: dict[str, Any], market_id: str) -> dict[str, Any] | None:
    event = snapshot.get("event", {})
    if not isinstance(event, dict):
        return None
    target = str(market_id or "").strip().lower()
    if not target:
        return None
    markets = event.get("markets", [])
    if not isinstance(markets, list):
        return None
    for market in markets:
        if not isinstance(market, dict):
            continue
        if str(market.get("conditionId") or "").strip().lower() == target:
            return market
    return None


def _resolve_sports_market(row, market: dict[str, Any], snapshot: dict[str, Any]) -> str | None:
    outcomes = _parse_text_list(market.get("outcomes"))
    teams = _snapshot_teams(snapshot, market)
    if len(teams) < 2:
        return None

    market_type = str(market.get("sportsMarketType") or "").strip().lower()
    if market_type in {"", "moneyline", "child_moneyline"}:
        return _resolve_basic_question_outcome(str(row["question"] or ""), outcomes, teams)
    if market_type == "totals":
        return _resolve_total_outcome(outcomes, teams, market.get("line"))
    if market_type == "spreads":
        return _resolve_spread_outcome(str(row["question"] or ""), outcomes, teams)
    if market_type == "both_teams_to_score":
        return _yes_no_outcome(outcomes, teams[0]["score"] > 0 and teams[1]["score"] > 0)
    return None


def _resolve_basic_sports_question(row, snapshot: dict[str, Any]) -> str | None:
    teams = _snapshot_teams(snapshot, None)
    if len(teams) < 2:
        return None
    return _resolve_basic_question_outcome(str(row["question"] or ""), [], teams)


def _snapshot_teams(snapshot: dict[str, Any], market: dict[str, Any] | None) -> list[dict[str, Any]]:
    for source in (market, snapshot.get("event")):
        if not isinstance(source, dict):
            continue
        teams = _coerce_teams(source.get("teams"))
        if teams:
            return teams

    event = snapshot.get("event", {})
    if not isinstance(event, dict):
        return []
    markets = event.get("markets", [])
    if not isinstance(markets, list):
        return []
    for market_entry in markets:
        if not isinstance(market_entry, dict):
            continue
        teams = _coerce_teams(market_entry.get("teams"))
        if teams:
            return teams

    score_obj = event.get("score") or snapshot.get("score")
    if isinstance(score_obj, dict):
        teams = _teams_from_score_obj(score_obj)
        if teams:
            return teams
    return []


def _teams_from_score_obj(score_obj: dict[str, Any]) -> list[dict[str, Any]]:
    teams: list[dict[str, Any]] = []
    for key in ("homeTeam", "awayTeam", "home", "away", "team1", "team2"):
        entry = score_obj.get(key)
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("teamName") or "").strip()
        score = _coerce_score(entry.get("score") or entry.get("goals") or entry.get("points"))
        if name and score is not None:
            teams.append({"name": name, "score": score})
    return teams[:2] if len(teams) >= 2 else []


def _coerce_teams(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    teams: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        score = _coerce_score(entry.get("score"))
        if score is None:
            continue
        teams.append({"name": name, "score": score, "hostStatus": entry.get("hostStatus")})
    return teams[:2]


def _coerce_score(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except Exception:
            return [text]
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [text]


def _resolve_basic_question_outcome(
    question: str,
    outcomes: list[str],
    teams: list[dict[str, Any]],
) -> str | None:
    winner, loser, draw = _winner_and_loser(teams)
    if draw:
        matched_draw = _match_outcome_name("draw", outcomes)
        if matched_draw is not None:
            return matched_draw
        if _DRAW_QUESTION_RE.match(question):
            return _yes_no_outcome(outcomes, True)
        return None

    matched_winner = _match_outcome_name(winner, outcomes)
    if matched_winner is not None:
        return matched_winner

    if _DRAW_QUESTION_RE.match(question):
        return _yes_no_outcome(outcomes, False)

    win_question = _TEAM_WIN_QUESTION_RE.match(question.strip())
    if win_question:
        target_team = win_question.group(1).strip()
        return _yes_no_outcome(outcomes, _same_name(target_team, winner))

    if outcomes:
        return None

    return winner


def _resolve_total_outcome(outcomes: list[str], teams: list[dict[str, Any]], line: object) -> str | None:
    try:
        threshold = float(line)
    except (TypeError, ValueError):
        return None
    total = teams[0]["score"] + teams[1]["score"]
    if abs(total - threshold) <= 1e-9:
        return None
    return _match_outcome_name("over", outcomes) if total > threshold else _match_outcome_name("under", outcomes)


def _resolve_spread_outcome(question: str, outcomes: list[str], teams: list[dict[str, Any]]) -> str | None:
    match = _SPREAD_QUESTION_RE.match(question.strip())
    if match is None:
        return None
    named_team = match.group(1).strip()
    try:
        line = float(match.group(2))
    except (TypeError, ValueError):
        return None

    named_entry = _find_team(teams, named_team)
    if named_entry is None:
        return None
    other_entry = next((team for team in teams if not _same_name(team["name"], named_team)), None)
    if other_entry is None:
        return None

    named_adjusted = named_entry["score"] + line
    if abs(named_adjusted - other_entry["score"]) <= 1e-9:
        return None
    winner_name = named_entry["name"] if named_adjusted > other_entry["score"] else other_entry["name"]
    return _match_outcome_name(winner_name, outcomes)


def _winner_and_loser(teams: list[dict[str, Any]]) -> tuple[str, str, bool]:
    if teams[0]["score"] > teams[1]["score"]:
        return teams[0]["name"], teams[1]["name"], False
    if teams[1]["score"] > teams[0]["score"]:
        return teams[1]["name"], teams[0]["name"], False
    return teams[0]["name"], teams[1]["name"], True


def _yes_no_outcome(outcomes: list[str], yes: bool) -> str | None:
    if not outcomes:
        return "yes" if yes else "no"
    target = "yes" if yes else "no"
    return _match_outcome_name(target, outcomes)


def _match_outcome_name(target: str, outcomes: list[str]) -> str | None:
    normalized_target = _normalize_text(target)
    for outcome in outcomes:
        if _normalize_text(outcome) == normalized_target:
            return outcome
    return None


def _find_team(teams: list[dict[str, Any]], target: str) -> dict[str, Any] | None:
    for team in teams:
        if _same_name(team["name"], target):
            return team
    return None


def _same_name(left: str, right: str) -> bool:
    return _normalize_text(left) == _normalize_text(right)


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _market_is_closed(market: dict) -> bool:
    return _truthy_market_flag(market.get("closed", False))


def _truthy_market_flag(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _fetch_market(client: httpx.Client, market_id: str, cache: dict[str, dict | None]) -> dict | None:
    key = market_id.lower()
    if key in cache:
        return cache[key]

    for attempt in range(3):
        try:
            response = client.get(f"{CLOB_API}/markets/{market_id}")
            if response.status_code == 429:
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code == 404:
                cache[key] = None
                return None
            response.raise_for_status()
            market = response.json()
            if not isinstance(market, dict):
                cache[key] = None
                return None
            cache[key] = market
            time.sleep(0.04)
            return market
        except Exception as exc:
            if attempt == 2:
                logger.error("Resolution check failed for %s: %s", market_id[:12], exc)
                cache[key] = None
                return None
            time.sleep(0.5 * (attempt + 1))

    cache[key] = None
    return None


def _rebuild_shadow_positions(conn) -> None:
    conn.execute("DELETE FROM positions WHERE real_money=0")
    rows = conn.execute(
        f"""
        SELECT
            market_id,
            LOWER(side) AS side,
            COALESCE(token_id, '') AS token_id,
            SUM({remaining_entry_size_expr()}) AS size_usd,
            SUM({remaining_entry_shares_expr()}) AS shares,
            MIN(placed_at) AS entered_at
        FROM trade_log
        WHERE real_money=0
          AND {OPEN_EXECUTED_ENTRY_SQL}
        GROUP BY market_id, COALESCE(token_id, ''), LOWER(side)
        """
    ).fetchall()

    for row in rows:
        size_usd = float(row["size_usd"] or 0.0)
        shares = float(row["shares"] or 0.0)
        if size_usd <= 0 or shares <= 0:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
            (
                row["market_id"],
                row["side"],
                size_usd,
                size_usd / shares,
                str(row["token_id"] or ""),
                int(row["entered_at"] or time.time()),
                0,
            ),
        )


if __name__ == "__main__":
    args = _build_cli_parser().parse_args()
    rows = resolve_shadow_trades(
        trade_id=args.trade_id,
        market_id=args.market_id,
        question_contains=args.question_contains,
        forced_outcome=args.force_outcome,
    )
    print(json.dumps(rows, indent=2, default=str))
