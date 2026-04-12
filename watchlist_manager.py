from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from config import (
    discovery_poll_interval_multiplier,
    hot_wallet_count,
    wallet_inactivity_limit_seconds,
    wallet_local_drop_max_avg_return,
    wallet_local_drop_max_total_pnl_usd,
    wallet_local_drop_min_resolved_copied_buys,
    wallet_uncopyable_drop_max_resolved_copied,
    wallet_uncopyable_drop_max_skip_rate,
    wallet_uncopyable_drop_min_buys,
    wallet_uncopyable_penalty_min_buys,
    wallet_uncopyable_penalty_weight,
    wallet_slow_drop_max_tracking_age_seconds,
    wallet_performance_drop_max_avg_return,
    wallet_performance_drop_max_win_rate,
    wallet_performance_drop_min_trades,
    warm_poll_interval_multiplier,
    warm_wallet_count,
)
from db import get_conn


@dataclass(frozen=True)
class RankedWallet:
    wallet: str
    follow_score: float
    last_source_ts: int
    cache_updated_at: int


@dataclass(frozen=True)
class WalletSkipMetrics:
    total_buy_signals: int
    uncopyable_skips: int
    timing_skips: int
    liquidity_skips: int
    resolved_copied_count: int

    @property
    def uncopyable_skip_rate(self) -> float:
        if self.total_buy_signals <= 0:
            return 0.0
        return self.uncopyable_skips / self.total_buy_signals


@dataclass(frozen=True)
class WalletPolicyMetrics:
    total_buy_signals: int = 0
    resolved_copied_count: int = 0
    resolved_copied_wins: int = 0
    resolved_copied_win_rate: float | None = None
    resolved_copied_avg_return: float | None = None
    resolved_copied_total_pnl_usd: float = 0.0
    recent_window_seconds: int = 14 * 86400
    recent_resolved_copied_count: int = 0
    recent_resolved_copied_wins: int = 0
    recent_resolved_copied_win_rate: float | None = None
    recent_resolved_copied_avg_return: float | None = None
    recent_resolved_copied_total_pnl_usd: float = 0.0
    last_resolved_at: int = 0
    local_quality_score: float | None = None
    local_weight: float = 0.0
    local_drop_ready: bool = False
    local_drop_reason: str | None = None


@dataclass(frozen=True)
class WatchTierSnapshot:
    hot: tuple[str, ...]
    warm: tuple[str, ...]
    discovery: tuple[str, ...]
    dropped: tuple[str, ...]
    ranked: tuple[RankedWallet, ...]
    refreshed_at: int


@dataclass(frozen=True)
class PollBatch:
    wallets: tuple[str, ...]
    trade_limit: int


HOT_WALLET_TRADE_FETCH_LIMIT = 30
WARM_WALLET_TRADE_FETCH_LIMIT = 20
DISCOVERY_WALLET_TRADE_FETCH_LIMIT = 12
_BEST_WALLET_DROP_PROTECTION_LIMIT = 5
_RECENT_LOCAL_POLICY_WINDOW_SECONDS = 14 * 86400
_RESOLVED_COPIED_BUY_SQL = """
skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
AND COALESCE(actual_pnl_usd, shadow_pnl_usd) IS NOT NULL
"""
_UNCOPYABLE_TIMING_SQL = """
(
    market_veto LIKE 'expires in <%'
    OR market_veto LIKE 'beyond max horizon %'
)
"""
_UNCOPYABLE_LIQUIDITY_SQL = """
(
    market_veto='missing order book'
    OR market_veto='no visible order book depth'
    OR skip_reason LIKE 'shadow simulation rejected the buy because the order book had no asks%'
    OR skip_reason LIKE 'shadow simulation rejected the buy because there was not enough ask depth%'
)
"""
_UNCOPYABLE_SKIP_SQL = f"""(
    {_UNCOPYABLE_TIMING_SQL}
    OR {_UNCOPYABLE_LIQUIDITY_SQL}
)"""
_RESOLVED_PNL_SQL = "COALESCE(actual_pnl_usd, shadow_pnl_usd)"


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize_wallets(wallet_addresses: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for wallet in wallet_addresses:
        address = str(wallet or "").strip().lower()
        if not address or address in seen:
            continue
        seen.add(address)
        normalized.append(address)
    return normalized


def _format_duration_label(seconds: float) -> str:
    if not math.isfinite(seconds):
        return "unlimited"
    rounded = int(max(seconds, 0))
    if rounded % 604800 == 0 and rounded >= 604800:
        return f"{rounded // 604800}w"
    if rounded % 86400 == 0 and rounded >= 86400:
        return f"{rounded // 86400}d"
    if rounded % 3600 == 0 and rounded >= 3600:
        return f"{rounded // 3600}h"
    if rounded % 60 == 0 and rounded >= 60:
        return f"{rounded // 60}m"
    return f"{rounded}s"


def _score_wallet(
    *,
    win_rate: float,
    n_trades: int,
    avg_return: float,
    realized_pnl_usd: float,
    volume_usd: float,
    avg_size_usd: float,
    open_positions: int,
    last_source_ts: int,
    cache_updated_at: int,
    now_ts: int,
    total_buy_signals: int,
    uncopyable_skip_rate: float,
    uncopyable_penalty_min_buys: int,
    uncopyable_penalty_weight: float,
    local_policy: WalletPolicyMetrics | None = None,
) -> float:
    shrunk_win_rate = ((max(n_trades, 0) * win_rate) + (20 * 0.5)) / (max(n_trades, 0) + 20)
    win_score = _clip((shrunk_win_rate - 0.45) / 0.25)
    sample_score = _clip(math.log1p(max(n_trades, 0)) / math.log1p(80))
    return_score = _clip((avg_return + 0.05) / 0.20)
    pnl_score = _clip(math.log1p(max(realized_pnl_usd, 0.0)) / math.log1p(5_000.0))
    volume_score = _clip(math.log1p(max(volume_usd, 0.0)) / math.log1p(50_000.0))
    size_score = _clip(math.log1p(max(avg_size_usd, 0.0)) / math.log1p(250.0))

    if last_source_ts > 0:
        activity_age_hours = max(now_ts - last_source_ts, 0) / 3600.0
        activity_score = _clip(1.0 - (activity_age_hours / 72.0))
    elif cache_updated_at > 0:
        cache_age_hours = max(now_ts - cache_updated_at, 0) / 3600.0
        activity_score = 0.35 if cache_age_hours <= 24 else 0.15 if cache_age_hours <= 72 else 0.0
    else:
        activity_score = 0.0

    open_score = _clip(open_positions / 3.0)
    freshness_penalty = 0.0
    if cache_updated_at > 0 and (now_ts - cache_updated_at) > 86400:
        freshness_penalty = 0.10
    uncopyable_penalty = 0.0
    if total_buy_signals >= uncopyable_penalty_min_buys and uncopyable_penalty_weight > 0:
        sample_weight = _clip(total_buy_signals / max(uncopyable_penalty_min_buys * 3.0, 1.0))
        uncopyable_penalty = uncopyable_penalty_weight * sample_weight * _clip(uncopyable_skip_rate)

    public_quality_score = (
        0.35 * win_score
        + 0.17 * return_score
        + 0.18 * sample_score
        + 0.10 * pnl_score
        + 0.10 * volume_score
        + 0.10 * size_score
    )
    local_quality_score = local_policy.local_quality_score if local_policy else None
    local_weight = _clip(local_policy.local_weight) if local_policy else 0.0
    quality_score = (
        ((1.0 - local_weight) * public_quality_score) + (local_weight * local_quality_score)
        if local_quality_score is not None
        else public_quality_score
    )
    composite = (
        0.70 * quality_score
        + 0.25 * activity_score
        + 0.05 * open_score
    ) - freshness_penalty - uncopyable_penalty
    return round(_clip(composite), 4)


def _wallet_status_rows(wallet_addresses: list[str]) -> dict[str, dict[str, int | str | None]]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return {}

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        rows = conn.execute(
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
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    return {
        str(row["wallet_address"] or "").strip().lower(): {
            "status": str(row["status"] or "active").strip().lower(),
            "status_reason": row["status_reason"],
            "dropped_at": int(row["dropped_at"] or 0),
            "reactivated_at": int(row["reactivated_at"] or 0),
            "tracking_started_at": int(row["tracking_started_at"] or 0),
            "last_source_ts_at_status": int(row["last_source_ts_at_status"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }
        for row in rows
    }


def _wallet_cursor_map(wallet_addresses: list[str]) -> dict[str, int]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return {}

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT wallet_address, last_source_ts
            FROM wallet_cursors
            WHERE wallet_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    return {
        str(row["wallet_address"] or "").strip().lower(): int(row["last_source_ts"] or 0)
        for row in rows
    }


def _wallet_logged_activity_map(wallet_addresses: list[str]) -> dict[str, int]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return {}

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT trader_address, MAX(placed_at) AS last_logged_ts
            FROM trade_log
            WHERE trader_address IN ({placeholders})
            GROUP BY trader_address
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    return {
        str(row["trader_address"] or "").strip().lower(): int(row["last_logged_ts"] or 0)
        for row in rows
    }


def _wallet_skip_metrics_map(wallet_addresses: list[str]) -> dict[str, WalletSkipMetrics]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return {}

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                LOWER(trader_address) AS trader_address,
                SUM(CASE WHEN COALESCE(source_action, 'buy')='buy' THEN 1 ELSE 0 END) AS total_buy_signals,
                SUM(CASE WHEN COALESCE(source_action, 'buy')='buy' AND {_UNCOPYABLE_TIMING_SQL} THEN 1 ELSE 0 END) AS timing_skips,
                SUM(CASE WHEN COALESCE(source_action, 'buy')='buy' AND {_UNCOPYABLE_LIQUIDITY_SQL} THEN 1 ELSE 0 END) AS liquidity_skips,
                SUM(CASE WHEN COALESCE(source_action, 'buy')='buy' AND {_UNCOPYABLE_SKIP_SQL} THEN 1 ELSE 0 END) AS uncopyable_skips,
                SUM(CASE WHEN {_RESOLVED_COPIED_BUY_SQL} THEN 1 ELSE 0 END) AS resolved_copied_count
            FROM trade_log
            WHERE trader_address IN ({placeholders})
            GROUP BY LOWER(trader_address)
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    metrics: dict[str, WalletSkipMetrics] = {}
    for row in rows:
        wallet = str(row["trader_address"] or "").strip().lower()
        if not wallet:
            continue
        metrics[wallet] = WalletSkipMetrics(
            total_buy_signals=int(row["total_buy_signals"] or 0),
            uncopyable_skips=int(row["uncopyable_skips"] or 0),
            timing_skips=int(row["timing_skips"] or 0),
            liquidity_skips=int(row["liquidity_skips"] or 0),
            resolved_copied_count=int(row["resolved_copied_count"] or 0),
        )
    return metrics


def _score_local_copied_performance(metrics: WalletPolicyMetrics) -> tuple[float | None, float]:
    resolved_count = max(metrics.resolved_copied_count, 0)
    recent_count = max(metrics.recent_resolved_copied_count, 0)
    if resolved_count <= 0 and recent_count <= 0:
        return None, 0.0

    def _window_quality(
        count: int,
        win_rate: float | None,
        avg_return: float | None,
        total_pnl_usd: float,
    ) -> float | None:
        if count <= 0 or win_rate is None or avg_return is None:
            return None
        win_score = _clip((win_rate - 0.45) / 0.25)
        return_score = _clip((avg_return + 0.05) / 0.20)
        pnl_score = _clip(0.5 + (math.atan(total_pnl_usd / 25.0) / math.pi))
        sample_score = _clip(math.log1p(count) / math.log1p(24))
        return (
            0.30 * win_score
            + 0.35 * return_score
            + 0.20 * pnl_score
            + 0.15 * sample_score
        )

    all_time_quality = _window_quality(
        resolved_count,
        metrics.resolved_copied_win_rate,
        metrics.resolved_copied_avg_return,
        metrics.resolved_copied_total_pnl_usd,
    )
    recent_quality = _window_quality(
        recent_count,
        metrics.recent_resolved_copied_win_rate,
        metrics.recent_resolved_copied_avg_return,
        metrics.recent_resolved_copied_total_pnl_usd,
    )
    if recent_quality is not None and all_time_quality is not None:
        quality = (0.65 * recent_quality) + (0.35 * all_time_quality)
    elif recent_quality is not None:
        quality = recent_quality
    else:
        quality = all_time_quality

    local_weight = _clip(
        (0.60 * _clip(resolved_count / 20.0))
        + (0.40 * _clip(recent_count / 8.0))
    )
    return quality, round(local_weight, 4)


def _local_drop_decision(metrics: WalletPolicyMetrics) -> tuple[bool, str | None]:
    minimum_trades = wallet_local_drop_min_resolved_copied_buys()
    if minimum_trades <= 0:
        return False, None

    max_avg_return = wallet_local_drop_max_avg_return()
    max_total_pnl_usd = wallet_local_drop_max_total_pnl_usd()
    recent_minimum = max(5, minimum_trades // 2)

    if metrics.recent_resolved_copied_count >= recent_minimum:
        recent_avg_return = metrics.recent_resolved_copied_avg_return
        if (
            recent_avg_return is not None
            and recent_avg_return <= max_avg_return
            and metrics.recent_resolved_copied_total_pnl_usd <= max_total_pnl_usd
        ):
            reason = (
                f"local_recent {metrics.recent_resolved_copied_count}r "
                f"{recent_avg_return * 100.0:.1f}%ret "
                f"${metrics.recent_resolved_copied_total_pnl_usd:.2f} pnl"
            )
            return True, reason
        return False, None

    all_time_avg_return = metrics.resolved_copied_avg_return
    if (
        metrics.resolved_copied_count >= minimum_trades
        and all_time_avg_return is not None
        and all_time_avg_return <= max_avg_return
        and metrics.resolved_copied_total_pnl_usd <= max_total_pnl_usd
    ):
        reason = (
            f"local_perf {metrics.resolved_copied_count}r "
            f"{all_time_avg_return * 100.0:.1f}%ret "
            f"${metrics.resolved_copied_total_pnl_usd:.2f} pnl"
        )
        return True, reason
    return False, None


def _row_optional_value(row, key: str):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _wallet_policy_metrics_from_row(row) -> WalletPolicyMetrics:
    resolved_copied_count = int(row["resolved_copied_count"] or 0)
    resolved_copied_wins = int(row["resolved_copied_wins"] or 0)
    recent_resolved_copied_count = int(row["recent_resolved_copied_count"] or 0)
    recent_resolved_copied_wins = int(row["recent_resolved_copied_wins"] or 0)
    resolved_copied_win_rate = _row_optional_value(row, "resolved_copied_win_rate")
    recent_resolved_copied_win_rate = _row_optional_value(row, "recent_resolved_copied_win_rate")
    resolved_copied_avg_return = _row_optional_value(row, "resolved_copied_avg_return")
    recent_resolved_copied_avg_return = _row_optional_value(row, "recent_resolved_copied_avg_return")
    local_quality_score = _row_optional_value(row, "local_quality_score")
    local_weight = _row_optional_value(row, "local_weight")
    local_drop_ready = _row_optional_value(row, "local_drop_ready")
    local_drop_reason = _row_optional_value(row, "local_drop_reason")
    return WalletPolicyMetrics(
        total_buy_signals=int(row["total_buy_signals"] or 0),
        resolved_copied_count=resolved_copied_count,
        resolved_copied_wins=resolved_copied_wins,
        resolved_copied_win_rate=(
            float(resolved_copied_win_rate)
            if resolved_copied_win_rate is not None
            else (resolved_copied_wins / resolved_copied_count if resolved_copied_count > 0 else None)
        ),
        resolved_copied_avg_return=(
            float(resolved_copied_avg_return)
            if resolved_copied_avg_return is not None
            else None
        ),
        resolved_copied_total_pnl_usd=float(row["resolved_copied_total_pnl_usd"] or 0.0),
        recent_window_seconds=int(row["recent_window_seconds"] or _RECENT_LOCAL_POLICY_WINDOW_SECONDS),
        recent_resolved_copied_count=recent_resolved_copied_count,
        recent_resolved_copied_wins=recent_resolved_copied_wins,
        recent_resolved_copied_win_rate=(
            float(recent_resolved_copied_win_rate)
            if recent_resolved_copied_win_rate is not None
            else (recent_resolved_copied_wins / recent_resolved_copied_count if recent_resolved_copied_count > 0 else None)
        ),
        recent_resolved_copied_avg_return=(
            float(recent_resolved_copied_avg_return)
            if recent_resolved_copied_avg_return is not None
            else None
        ),
        recent_resolved_copied_total_pnl_usd=float(row["recent_resolved_copied_total_pnl_usd"] or 0.0),
        last_resolved_at=int(row["last_resolved_at"] or 0),
        local_quality_score=(float(local_quality_score) if local_quality_score is not None else None),
        local_weight=float(local_weight or 0.0),
        local_drop_ready=bool(local_drop_ready or 0),
        local_drop_reason=str(local_drop_reason or "").strip() or None,
    )


def _wallet_policy_metrics_map(wallet_addresses: list[str]) -> dict[str, WalletPolicyMetrics]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return {}

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                wallet_address,
                total_buy_signals,
                resolved_copied_count,
                resolved_copied_wins,
                resolved_copied_win_rate,
                resolved_copied_avg_return,
                resolved_copied_total_pnl_usd,
                recent_window_seconds,
                recent_resolved_copied_count,
                recent_resolved_copied_wins,
                recent_resolved_copied_win_rate,
                recent_resolved_copied_avg_return,
                recent_resolved_copied_total_pnl_usd,
                last_resolved_at,
                local_quality_score,
                local_weight,
                local_drop_ready,
                local_drop_reason
            FROM wallet_policy_metrics
            WHERE wallet_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    metrics: dict[str, WalletPolicyMetrics] = {}
    for row in rows:
        wallet = str(row["wallet_address"] or "").strip().lower()
        if wallet:
            metrics[wallet] = _wallet_policy_metrics_from_row(row)
    return metrics


def _refresh_wallet_policy_metrics(wallet_addresses: list[str]) -> dict[str, WalletPolicyMetrics]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return {}

    placeholders = ",".join("?" for _ in wallets)
    now_ts = int(time.time())
    recent_cutoff = now_ts - _RECENT_LOCAL_POLICY_WINDOW_SECONDS
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                LOWER(trader_address) AS trader_address,
                SUM(CASE WHEN COALESCE(source_action, 'buy')='buy' THEN 1 ELSE 0 END) AS total_buy_signals,
                SUM(CASE WHEN {_RESOLVED_COPIED_BUY_SQL} THEN 1 ELSE 0 END) AS resolved_copied_count,
                SUM(CASE WHEN {_RESOLVED_COPIED_BUY_SQL} AND {_RESOLVED_PNL_SQL} > 0 THEN 1 ELSE 0 END) AS resolved_copied_wins,
                AVG(
                    CASE
                        WHEN {_RESOLVED_COPIED_BUY_SQL}
                            THEN {_RESOLVED_PNL_SQL} / NULLIF(COALESCE(actual_entry_size_usd, signal_size_usd, 0), 0)
                        ELSE NULL
                    END
                ) AS resolved_copied_avg_return,
                ROUND(
                    SUM(CASE WHEN {_RESOLVED_COPIED_BUY_SQL} THEN COALESCE({_RESOLVED_PNL_SQL}, 0) ELSE 0 END),
                    6
                ) AS resolved_copied_total_pnl_usd,
                SUM(
                    CASE
                        WHEN {_RESOLVED_COPIED_BUY_SQL}
                         AND COALESCE(exited_at, resolved_at, placed_at) >= ?
                            THEN 1
                        ELSE 0
                    END
                ) AS recent_resolved_copied_count,
                SUM(
                    CASE
                        WHEN {_RESOLVED_COPIED_BUY_SQL}
                         AND COALESCE(exited_at, resolved_at, placed_at) >= ?
                         AND {_RESOLVED_PNL_SQL} > 0
                            THEN 1
                        ELSE 0
                    END
                ) AS recent_resolved_copied_wins,
                AVG(
                    CASE
                        WHEN {_RESOLVED_COPIED_BUY_SQL}
                         AND COALESCE(exited_at, resolved_at, placed_at) >= ?
                            THEN {_RESOLVED_PNL_SQL} / NULLIF(COALESCE(actual_entry_size_usd, signal_size_usd, 0), 0)
                        ELSE NULL
                    END
                ) AS recent_resolved_copied_avg_return,
                ROUND(
                    SUM(
                        CASE
                            WHEN {_RESOLVED_COPIED_BUY_SQL}
                             AND COALESCE(exited_at, resolved_at, placed_at) >= ?
                                THEN COALESCE({_RESOLVED_PNL_SQL}, 0)
                            ELSE 0
                        END
                    ),
                    6
                ) AS recent_resolved_copied_total_pnl_usd,
                MAX(
                    CASE
                        WHEN {_RESOLVED_COPIED_BUY_SQL}
                            THEN COALESCE(exited_at, resolved_at, placed_at)
                        ELSE 0
                    END
                ) AS last_resolved_at
            FROM trade_log
            WHERE LOWER(trader_address) IN ({placeholders})
            GROUP BY LOWER(trader_address)
            """,
            (recent_cutoff, recent_cutoff, recent_cutoff, recent_cutoff, *wallets),
        ).fetchall()

        metrics_map: dict[str, WalletPolicyMetrics] = {}
        for row in rows:
            wallet = str(row["trader_address"] or "").strip().lower()
            if not wallet:
                continue
            metrics = _wallet_policy_metrics_from_row(
                {
                    **dict(row),
                    "recent_window_seconds": _RECENT_LOCAL_POLICY_WINDOW_SECONDS,
                    "local_quality_score": None,
                    "local_weight": 0.0,
                    "local_drop_ready": 0,
                    "local_drop_reason": None,
                }
            )
            local_quality_score, local_weight = _score_local_copied_performance(metrics)
            local_drop_ready, local_drop_reason = _local_drop_decision(metrics)
            metrics_map[wallet] = WalletPolicyMetrics(
                total_buy_signals=metrics.total_buy_signals,
                resolved_copied_count=metrics.resolved_copied_count,
                resolved_copied_wins=metrics.resolved_copied_wins,
                resolved_copied_win_rate=metrics.resolved_copied_win_rate,
                resolved_copied_avg_return=metrics.resolved_copied_avg_return,
                resolved_copied_total_pnl_usd=metrics.resolved_copied_total_pnl_usd,
                recent_window_seconds=metrics.recent_window_seconds,
                recent_resolved_copied_count=metrics.recent_resolved_copied_count,
                recent_resolved_copied_wins=metrics.recent_resolved_copied_wins,
                recent_resolved_copied_win_rate=metrics.recent_resolved_copied_win_rate,
                recent_resolved_copied_avg_return=metrics.recent_resolved_copied_avg_return,
                recent_resolved_copied_total_pnl_usd=metrics.recent_resolved_copied_total_pnl_usd,
                last_resolved_at=metrics.last_resolved_at,
                local_quality_score=round(local_quality_score, 4) if local_quality_score is not None else None,
                local_weight=local_weight,
                local_drop_ready=local_drop_ready,
                local_drop_reason=local_drop_reason,
            )

        for wallet in wallets:
            metrics_map.setdefault(wallet, WalletPolicyMetrics())

        conn.executemany(
            """
            INSERT INTO wallet_policy_metrics (
                wallet_address,
                total_buy_signals,
                resolved_copied_count,
                resolved_copied_wins,
                resolved_copied_win_rate,
                resolved_copied_avg_return,
                resolved_copied_total_pnl_usd,
                recent_window_seconds,
                recent_resolved_copied_count,
                recent_resolved_copied_wins,
                recent_resolved_copied_win_rate,
                recent_resolved_copied_avg_return,
                recent_resolved_copied_total_pnl_usd,
                last_resolved_at,
                local_quality_score,
                local_weight,
                local_drop_ready,
                local_drop_reason,
                updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(wallet_address) DO UPDATE SET
                total_buy_signals=excluded.total_buy_signals,
                resolved_copied_count=excluded.resolved_copied_count,
                resolved_copied_wins=excluded.resolved_copied_wins,
                resolved_copied_win_rate=excluded.resolved_copied_win_rate,
                resolved_copied_avg_return=excluded.resolved_copied_avg_return,
                resolved_copied_total_pnl_usd=excluded.resolved_copied_total_pnl_usd,
                recent_window_seconds=excluded.recent_window_seconds,
                recent_resolved_copied_count=excluded.recent_resolved_copied_count,
                recent_resolved_copied_wins=excluded.recent_resolved_copied_wins,
                recent_resolved_copied_win_rate=excluded.recent_resolved_copied_win_rate,
                recent_resolved_copied_avg_return=excluded.recent_resolved_copied_avg_return,
                recent_resolved_copied_total_pnl_usd=excluded.recent_resolved_copied_total_pnl_usd,
                last_resolved_at=excluded.last_resolved_at,
                local_quality_score=excluded.local_quality_score,
                local_weight=excluded.local_weight,
                local_drop_ready=excluded.local_drop_ready,
                local_drop_reason=excluded.local_drop_reason,
                updated_at=excluded.updated_at
            """,
            [
                (
                    wallet,
                    metrics.total_buy_signals,
                    metrics.resolved_copied_count,
                    metrics.resolved_copied_wins,
                    metrics.resolved_copied_win_rate,
                    metrics.resolved_copied_avg_return,
                    metrics.resolved_copied_total_pnl_usd,
                    metrics.recent_window_seconds,
                    metrics.recent_resolved_copied_count,
                    metrics.recent_resolved_copied_wins,
                    metrics.recent_resolved_copied_win_rate,
                    metrics.recent_resolved_copied_avg_return,
                    metrics.recent_resolved_copied_total_pnl_usd,
                    metrics.last_resolved_at,
                    metrics.local_quality_score,
                    metrics.local_weight,
                    1 if metrics.local_drop_ready else 0,
                    metrics.local_drop_reason,
                    now_ts,
                )
                for wallet, metrics in metrics_map.items()
            ],
        )
        conn.commit()
        return metrics_map
    finally:
        conn.close()


def _protected_best_wallets(wallet_addresses: list[str] | None = None) -> set[str]:
    wallets = _normalize_wallets(wallet_addresses or [])
    if not wallets:
        return set()

    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                wallet_address AS trader_address,
                resolved_copied_total_pnl_usd AS pnl
            FROM wallet_policy_metrics
            WHERE wallet_address IN ({",".join("?" for _ in wallets)})
              AND resolved_copied_count > 0
            ORDER BY pnl DESC, trader_address ASC
            LIMIT {_BEST_WALLET_DROP_PROTECTION_LIMIT}
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    protected: set[str] = set()
    for row in rows:
        wallet = str(row["trader_address"] or "").strip().lower()
        pnl = float(row["pnl"] or 0.0)
        if wallet and pnl > 0:
            protected.add(wallet)
    return protected


def _profitable_local_wallets(wallet_addresses: list[str] | None = None) -> set[str]:
    wallets = _normalize_wallets(wallet_addresses or [])
    if not wallets:
        return set()

    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                wallet_address AS trader_address,
                resolved_copied_total_pnl_usd AS pnl
            FROM wallet_policy_metrics
            WHERE wallet_address IN ({",".join("?" for _ in wallets)})
              AND resolved_copied_count > 0
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    return {
        str(row["trader_address"] or "").strip().lower()
        for row in rows
        if str(row["trader_address"] or "").strip() and float(row["pnl"] or 0.0) > 0.0
    }


def _drop_wallets(wallet_updates: list[tuple[str, str, int, int]]) -> None:
    if not wallet_updates:
        return

    now_ts = int(time.time())
    conn = get_conn()
    try:
        conn.executemany(
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
            [
                (wallet, reason, dropped_at, last_source_ts, now_ts)
                for wallet, reason, dropped_at, last_source_ts in wallet_updates
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_tracking_started(wallet_addresses: list[str]) -> None:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return

    now_ts = int(time.time())
    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        existing_rows = conn.execute(
            f"""
            SELECT
                wallet_address,
                tracking_started_at,
                reactivated_at,
                updated_at
            FROM wallet_watch_state
            WHERE wallet_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
        existing = {
            str(row["wallet_address"] or "").strip().lower(): row
            for row in existing_rows
        }

        to_insert = [
            (
                wallet,
                now_ts,
                now_ts,
            )
            for wallet in wallets
            if wallet not in existing
        ]
        to_update: list[tuple[int, int, str]] = []
        for wallet in wallets:
            row = existing.get(wallet)
            if row is None:
                continue
            tracking_started_at = int(row["tracking_started_at"] or 0)
            if tracking_started_at > 0:
                continue
            anchor = int(row["reactivated_at"] or 0) or int(row["updated_at"] or 0) or now_ts
            updated_at = int(row["updated_at"] or 0) or anchor
            to_update.append((anchor, updated_at, wallet))

        if to_insert:
            conn.executemany(
                """
                INSERT INTO wallet_watch_state (
                    wallet_address,
                    status,
                    tracking_started_at,
                    updated_at
                ) VALUES (?, 'active', ?, ?)
                ON CONFLICT(wallet_address) DO NOTHING
                """,
                to_insert,
            )
        if to_update:
            conn.executemany(
                """
                UPDATE wallet_watch_state
                SET tracking_started_at=?,
                    updated_at=?
                WHERE wallet_address=?
                  AND COALESCE(tracking_started_at, 0)=0
                """,
                to_update,
            )
        if to_insert or to_update:
            conn.commit()
    finally:
        conn.close()


def _auto_drop_underperforming_wallets(wallet_addresses: list[str], protected_wallets: set[str] | None = None) -> None:
    minimum_trades = wallet_performance_drop_min_trades()
    if minimum_trades <= 0:
        return

    max_win_rate = wallet_performance_drop_max_win_rate()
    max_avg_return = wallet_performance_drop_max_avg_return()
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return
    protected = protected_wallets or set()

    status_rows = _wallet_status_rows(wallets)
    logged_activity_map = _wallet_logged_activity_map(wallets)
    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        trader_rows = conn.execute(
            f"""
            SELECT trader_address, win_rate, n_trades, avg_return
            FROM trader_cache
            WHERE trader_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    trader_map = {
        str(row["trader_address"] or "").strip().lower(): row
        for row in trader_rows
    }
    now_ts = int(time.time())
    to_drop: list[tuple[str, str, int, int]] = []

    for wallet in wallets:
        if wallet in protected:
            continue
        status_row = status_rows.get(wallet, {})
        if status_row.get("status") == "dropped":
            continue

        row = trader_map.get(wallet)
        if row is None:
            continue

        n_trades = int(row["n_trades"] or 0)
        win_rate = float(row["win_rate"] or 0.0)
        avg_return = float(row["avg_return"] or 0.0)
        if n_trades < minimum_trades or win_rate > max_win_rate or avg_return > max_avg_return:
            continue

        reactivated_at = int(status_row.get("reactivated_at") or 0)
        last_logged_ts = int(logged_activity_map.get(wallet, 0))
        if reactivated_at > 0 and last_logged_ts <= reactivated_at:
            continue

        reason = (
            f"poor_perf {n_trades}t {win_rate * 100.0:.1f}%wr {avg_return * 100.0:.1f}%ret"
        )
        to_drop.append((wallet, reason, now_ts, last_logged_ts))

    _drop_wallets(to_drop)


def _auto_drop_local_underperforming_wallets(wallet_addresses: list[str], protected_wallets: set[str] | None = None) -> None:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return
    protected = protected_wallets or set()

    status_rows = _wallet_status_rows(wallets)
    logged_activity_map = _wallet_logged_activity_map(wallets)
    policy_metrics_map = _wallet_policy_metrics_map(wallets)
    now_ts = int(time.time())
    to_drop: list[tuple[str, str, int, int]] = []

    for wallet in wallets:
        if wallet in protected:
            continue
        status_row = status_rows.get(wallet, {})
        if status_row.get("status") == "dropped":
            continue

        metrics = policy_metrics_map.get(wallet)
        if metrics is None or not metrics.local_drop_ready:
            continue

        reactivated_at = int(status_row.get("reactivated_at") or 0)
        last_logged_ts = int(logged_activity_map.get(wallet, 0))
        if reactivated_at > 0 and last_logged_ts <= reactivated_at:
            continue

        reason = metrics.local_drop_reason or "local_perf"
        to_drop.append((wallet, reason, now_ts, last_logged_ts))

    _drop_wallets(to_drop)


def _auto_drop_uncopyable_wallets(wallet_addresses: list[str], protected_wallets: set[str] | None = None) -> None:
    minimum_buys = wallet_uncopyable_drop_min_buys()
    max_skip_rate = wallet_uncopyable_drop_max_skip_rate()
    max_resolved_copied = wallet_uncopyable_drop_max_resolved_copied()
    if minimum_buys <= 0:
        return

    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return
    protected = protected_wallets or set()

    status_rows = _wallet_status_rows(wallets)
    logged_activity_map = _wallet_logged_activity_map(wallets)
    skip_metrics = _wallet_skip_metrics_map(wallets)
    now_ts = int(time.time())
    to_drop: list[tuple[str, str, int, int]] = []

    for wallet in wallets:
        if wallet in protected:
            continue
        status_row = status_rows.get(wallet, {})
        if status_row.get("status") == "dropped":
            continue

        metrics = skip_metrics.get(wallet)
        if metrics is None:
            continue
        if metrics.total_buy_signals < minimum_buys:
            continue
        if metrics.uncopyable_skip_rate < max_skip_rate:
            continue
        if metrics.resolved_copied_count > max_resolved_copied:
            continue

        reactivated_at = int(status_row.get("reactivated_at") or 0)
        last_logged_ts = int(logged_activity_map.get(wallet, 0))
        if reactivated_at > 0 and last_logged_ts <= reactivated_at:
            continue

        reason = (
            f"uncopyable {metrics.uncopyable_skip_rate * 100.0:.0f}% "
            f"{metrics.uncopyable_skips}/{metrics.total_buy_signals} buys"
        )
        to_drop.append((wallet, reason, now_ts, last_logged_ts))

    _drop_wallets(to_drop)


def reactivate_wallet(wallet_address: str) -> bool:
    wallet = str(wallet_address or "").strip().lower()
    if not wallet:
        return False

    now_ts = int(time.time())
    conn = get_conn()
    try:
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
        conn.commit()
        return True
    finally:
        conn.close()


def _auto_drop_inactive_wallets(wallet_addresses: list[str], protected_wallets: set[str] | None = None) -> None:
    inactivity_limit = wallet_inactivity_limit_seconds()
    if not math.isfinite(inactivity_limit):
        return

    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return
    protected = protected_wallets or set()

    status_rows = _wallet_status_rows(wallets)
    cursor_map = _wallet_cursor_map(wallets)
    logged_activity_map = _wallet_logged_activity_map(wallets)
    now_ts = int(time.time())
    reason = f"inactive>{_format_duration_label(inactivity_limit)}"
    to_drop: list[tuple[str, str, int, int]] = []

    for wallet in wallets:
        if wallet in protected:
            continue
        status_row = status_rows.get(wallet, {})
        if status_row.get("status") == "dropped":
            continue

        last_logged_ts = int(logged_activity_map.get(wallet, 0))
        last_source_ts = int(cursor_map.get(wallet, 0))
        reactivated_at = int(status_row.get("reactivated_at") or 0)
        tracking_started_at = int(status_row.get("tracking_started_at") or 0)
        inactivity_anchor = max(last_logged_ts, last_source_ts, reactivated_at, tracking_started_at)
        if inactivity_anchor <= 0:
            continue
        if (now_ts - inactivity_anchor) < inactivity_limit:
            continue
        to_drop.append((wallet, reason, now_ts, max(last_logged_ts, last_source_ts)))

    _drop_wallets(to_drop)


def _slow_wallet_drop_updates(
    discovery_wallets: tuple[str, ...],
    status_rows: dict[str, dict[str, int | str | None]],
    protected_wallets: set[str] | None = None,
) -> list[tuple[str, str, int, int]]:
    max_tracking_age = wallet_slow_drop_max_tracking_age_seconds()
    if not math.isfinite(max_tracking_age) or not discovery_wallets:
        return []
    protected = protected_wallets or set()

    cursor_map = _wallet_cursor_map(list(discovery_wallets))
    logged_activity_map = _wallet_logged_activity_map(list(discovery_wallets))
    now_ts = int(time.time())
    reason = f"slow>{_format_duration_label(max_tracking_age)}"
    to_drop: list[tuple[str, str, int, int]] = []

    for wallet in discovery_wallets:
        if wallet in protected:
            continue
        status_row = status_rows.get(wallet, {})
        if status_row.get("status") == "dropped":
            continue
        tracking_started_at = int(status_row.get("tracking_started_at") or 0)
        if tracking_started_at <= 0:
            continue
        if (now_ts - tracking_started_at) < max_tracking_age:
            continue
        last_logged_ts = int(logged_activity_map.get(wallet, 0))
        last_source_ts = int(cursor_map.get(wallet, 0))
        recent_activity_anchor = max(last_logged_ts, last_source_ts)
        if recent_activity_anchor > 0 and (now_ts - recent_activity_anchor) < max_tracking_age:
            continue
        to_drop.append((wallet, reason, now_ts, recent_activity_anchor))

    return to_drop


def _load_watch_metrics(wallet_addresses: list[str]) -> list[RankedWallet]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return []

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        trader_rows = conn.execute(
            f"""
            SELECT
                trader_address,
                win_rate,
                n_trades,
                avg_return,
                realized_pnl_usd,
                volume_usd,
                avg_size_usd,
                open_positions,
                updated_at
            FROM trader_cache
            WHERE trader_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
        cursor_rows = conn.execute(
            f"""
            SELECT wallet_address, last_source_ts
            FROM wallet_cursors
            WHERE wallet_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    trader_map = {
        str(row["trader_address"] or "").strip().lower(): row
        for row in trader_rows
    }
    skip_metrics_map = _wallet_skip_metrics_map(wallets)
    policy_metrics_map = _wallet_policy_metrics_map(wallets)
    cursor_map = {
        str(row["wallet_address"] or "").strip().lower(): int(row["last_source_ts"] or 0)
        for row in cursor_rows
    }
    now_ts = int(time.time())
    ranked: list[RankedWallet] = []
    order_index = {wallet: index for index, wallet in enumerate(wallets)}
    uncopyable_penalty_min_buys = wallet_uncopyable_penalty_min_buys()
    uncopyable_penalty_weight = wallet_uncopyable_penalty_weight()

    for wallet in wallets:
        row = trader_map.get(wallet)
        skip_metrics = skip_metrics_map.get(wallet)
        win_rate = float(row["win_rate"] or 0.5) if row else 0.5
        n_trades = int(row["n_trades"] or 0) if row else 0
        avg_return = float(row["avg_return"] or 0.0) if row else 0.0
        realized_pnl_usd = float(row["realized_pnl_usd"] or 0.0) if row else 0.0
        volume_usd = float(row["volume_usd"] or 0.0) if row else 0.0
        avg_size_usd = float(row["avg_size_usd"] or 0.0) if row else 0.0
        open_positions = int(row["open_positions"] or 0) if row else 0
        cache_updated_at = int(row["updated_at"] or 0) if row else 0
        last_source_ts = int(cursor_map.get(wallet, 0))
        score = _score_wallet(
            win_rate=win_rate,
            n_trades=n_trades,
            avg_return=avg_return,
            realized_pnl_usd=realized_pnl_usd,
            volume_usd=volume_usd,
            avg_size_usd=avg_size_usd,
            open_positions=open_positions,
            last_source_ts=last_source_ts,
            cache_updated_at=cache_updated_at,
            now_ts=now_ts,
            total_buy_signals=(skip_metrics.total_buy_signals if skip_metrics else 0),
            uncopyable_skip_rate=(skip_metrics.uncopyable_skip_rate if skip_metrics else 0.0),
            uncopyable_penalty_min_buys=uncopyable_penalty_min_buys,
            uncopyable_penalty_weight=uncopyable_penalty_weight,
            local_policy=policy_metrics_map.get(wallet),
        )
        ranked.append(
            RankedWallet(
                wallet=wallet,
                follow_score=score,
                last_source_ts=last_source_ts,
                cache_updated_at=cache_updated_at,
            )
        )

    ranked.sort(
        key=lambda row: (
            -row.follow_score,
            -row.last_source_ts,
            -row.cache_updated_at,
            order_index[row.wallet],
        )
    )
    return ranked


class WatchlistManager:
    def __init__(self, wallet_addresses: list[str]):
        self.wallets = _normalize_wallets(wallet_addresses)
        self._lock = threading.Lock()
        self._loop_count = 0
        self._snapshot = self._build_snapshot()

    def _build_snapshot(self, *, run_auto_drop: bool = True) -> WatchTierSnapshot:
        _ensure_tracking_started(self.wallets)
        _refresh_wallet_policy_metrics(self.wallets)
        profitable_local_wallets = _profitable_local_wallets(self.wallets)
        inactivity_protected_wallets = _protected_best_wallets(self.wallets) | profitable_local_wallets
        quality_protected_wallets = inactivity_protected_wallets
        if run_auto_drop:
            _auto_drop_inactive_wallets(self.wallets, inactivity_protected_wallets)
            _auto_drop_local_underperforming_wallets(self.wallets, quality_protected_wallets)
            _auto_drop_underperforming_wallets(self.wallets, quality_protected_wallets)
            _auto_drop_uncopyable_wallets(self.wallets, quality_protected_wallets)
        status_rows = _wallet_status_rows(self.wallets)
        dropped_wallets = tuple(
            wallet for wallet in self.wallets if status_rows.get(wallet, {}).get("status") == "dropped"
        )
        active_wallets = [wallet for wallet in self.wallets if wallet not in dropped_wallets]
        ranked = _load_watch_metrics(active_wallets)
        hot_count = min(len(ranked), hot_wallet_count())
        remaining = max(len(ranked) - hot_count, 0)
        warm_count = min(remaining, warm_wallet_count())
        hot = tuple(row.wallet for row in ranked[:hot_count])
        warm = tuple(row.wallet for row in ranked[hot_count:hot_count + warm_count])
        discovery = tuple(row.wallet for row in ranked[hot_count + warm_count:])

        slow_drop_updates = (
            _slow_wallet_drop_updates(discovery, status_rows, quality_protected_wallets)
            if run_auto_drop
            else {}
        )
        if slow_drop_updates:
            _drop_wallets(slow_drop_updates)
            status_rows = _wallet_status_rows(self.wallets)
            dropped_wallets = tuple(
                wallet for wallet in self.wallets if status_rows.get(wallet, {}).get("status") == "dropped"
            )
            active_wallets = [wallet for wallet in self.wallets if wallet not in dropped_wallets]
            ranked = _load_watch_metrics(active_wallets)
            hot_count = min(len(ranked), hot_wallet_count())
            remaining = max(len(ranked) - hot_count, 0)
            warm_count = min(remaining, warm_wallet_count())
            hot = tuple(row.wallet for row in ranked[:hot_count])
            warm = tuple(row.wallet for row in ranked[hot_count:hot_count + warm_count])
            discovery = tuple(row.wallet for row in ranked[hot_count + warm_count:])

        return WatchTierSnapshot(
            hot=hot,
            warm=warm,
            discovery=discovery,
            dropped=dropped_wallets,
            ranked=tuple(ranked),
            refreshed_at=int(time.time()),
        )

    def refresh(self, *, run_auto_drop: bool = True) -> WatchTierSnapshot:
        snapshot = self._build_snapshot(run_auto_drop=run_auto_drop)
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def startup_wallets(self) -> list[str]:
        with self._lock:
            snapshot = self._snapshot
            wallets = list(snapshot.hot)
            wallets.extend(snapshot.warm)
        return _normalize_wallets(wallets)

    def active_wallets(self) -> list[str]:
        with self._lock:
            snapshot = self._snapshot
            wallets = list(snapshot.hot)
            wallets.extend(snapshot.warm)
            wallets.extend(snapshot.discovery)
        return _normalize_wallets(wallets)

    def wallets_for_poll(self) -> list[str]:
        batches = self.poll_batches()
        wallets: list[str] = []
        for batch in batches:
            wallets.extend(batch.wallets)
        return _normalize_wallets(wallets)

    def poll_batches(self) -> list[PollBatch]:
        with self._lock:
            self._loop_count += 1
            snapshot = self._snapshot
            loop_count = self._loop_count

            batches: list[PollBatch] = []
            if snapshot.hot:
                batches.append(
                    PollBatch(
                        wallets=tuple(_normalize_wallets(list(snapshot.hot))),
                        trade_limit=HOT_WALLET_TRADE_FETCH_LIMIT,
                    )
                )
            if snapshot.warm and (loop_count % warm_poll_interval_multiplier()) == 0:
                batches.append(
                    PollBatch(
                        wallets=tuple(_normalize_wallets(list(snapshot.warm))),
                        trade_limit=WARM_WALLET_TRADE_FETCH_LIMIT,
                    )
                )
            if snapshot.discovery and (loop_count % discovery_poll_interval_multiplier()) == 0:
                batches.append(
                    PollBatch(
                        wallets=tuple(_normalize_wallets(list(snapshot.discovery))),
                        trade_limit=DISCOVERY_WALLET_TRADE_FETCH_LIMIT,
                    )
                )
            if not batches:
                fallback = tuple(_normalize_wallets(list(snapshot.hot or snapshot.warm or snapshot.discovery)))
                if fallback:
                    batches.append(PollBatch(wallets=fallback, trade_limit=HOT_WALLET_TRADE_FETCH_LIMIT))
        return batches

    def state_fields(self) -> dict[str, int]:
        with self._lock:
            snapshot = self._snapshot
        tracked_count = len(snapshot.hot) + len(snapshot.warm) + len(snapshot.discovery)
        return {
            "tracked_wallet_count": tracked_count,
            "dropped_wallet_count": len(snapshot.dropped),
            "hot_wallet_count": len(snapshot.hot),
            "warm_wallet_count": len(snapshot.warm),
            "discovery_wallet_count": len(snapshot.discovery),
            "watch_tier_refreshed_at": snapshot.refreshed_at,
        }
