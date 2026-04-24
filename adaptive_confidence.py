from __future__ import annotations

import statistics
import time
from dataclasses import dataclass

from config import min_confidence
from db import get_conn
from trade_contract import NON_CHALLENGER_EXPERIMENT_ARM_SQL

CACHE_TTL_SECONDS = 60.0
SHORT_WINDOW_SECONDS = 15 * 60
INTRAHOUR_WINDOW_SECONDS = 60 * 60
LOWER_STEP = 0.005
MAX_LOWERING = 0.015
MAX_RAISE = 0.01

_snapshot_cache: tuple[float, "AdaptiveFloorSnapshot"] | None = None


@dataclass(frozen=True)
class CounterfactualRow:
    confidence: float
    won: bool
    counterfactual_return: float


@dataclass(frozen=True)
class BucketStats:
    resolved_executed_count: int = 0
    resolved_executed_avg_return: float | None = None
    low_conf_samples: tuple[CounterfactualRow, ...] = ()


@dataclass(frozen=True)
class LocalCopyStats:
    resolved_copied_count: int = 0
    copied_avg_return: float | None = None
    copied_win_rate: float | None = None


@dataclass(frozen=True)
class AdaptiveFloorDecision:
    floor: float
    base_floor: float
    bucket: str
    bucket_low_conf_samples: int
    bucket_executed_samples: int
    local_resolved_copied_count: int
    local_copied_avg_return: float | None
    adjustment: float
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, float | int | str | None | list[str]]:
        return {
            "floor": self.floor,
            "base_floor": self.base_floor,
            "bucket": self.bucket,
            "bucket_low_conf_samples": self.bucket_low_conf_samples,
            "bucket_executed_samples": self.bucket_executed_samples,
            "local_resolved_copied_count": self.local_resolved_copied_count,
            "local_copied_avg_return": self.local_copied_avg_return,
            "adjustment": self.adjustment,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class AdaptiveFloorSnapshot:
    bucket_stats: dict[str, BucketStats]
    local_copy_stats: dict[str, LocalCopyStats]


def reset_adaptive_floor_cache() -> None:
    global _snapshot_cache
    _snapshot_cache = None


def adaptive_min_confidence_for_signal(
    *,
    days_to_res: float | None,
    trader_address: str | None = None,
) -> AdaptiveFloorDecision:
    base_floor = round(float(min_confidence()), 4)
    bucket = _bucket_from_days_to_res(days_to_res)
    snapshot = _load_snapshot()
    bucket_stats = snapshot.bucket_stats.get(bucket, BucketStats())
    local_stats = snapshot.local_copy_stats.get(str(trader_address or "").strip().lower()) if trader_address else None
    return derive_adaptive_floor(
        base_floor=base_floor,
        bucket=bucket,
        bucket_stats=bucket_stats,
        local_stats=local_stats,
    )


def derive_adaptive_floor(
    *,
    base_floor: float,
    bucket: str,
    bucket_stats: BucketStats,
    local_stats: LocalCopyStats | None = None,
) -> AdaptiveFloorDecision:
    min_floor = max(0.0, round(base_floor - MAX_LOWERING, 4))
    max_floor = min(1.0, round(base_floor + MAX_RAISE, 4))
    floor = base_floor
    reasons: list[str] = []

    if bucket == "under_15m":
        executed_avg = bucket_stats.resolved_executed_avg_return
        if (
            bucket_stats.resolved_executed_count >= 5
            and executed_avg is not None
            and executed_avg < -0.05
        ):
            floor = round(base_floor + (0.01 if executed_avg <= -0.15 else 0.005), 4)
            reasons.append("sub-15m executed returns are weak")
    else:
        suggested_floor = _suggest_bucket_floor(base_floor, bucket, bucket_stats)
        if suggested_floor < floor:
            floor = suggested_floor
            reasons.append("resolved low-confidence misses support a lower floor")

        executed_avg = bucket_stats.resolved_executed_avg_return
        if (
            bucket_stats.resolved_executed_count >= 5
            and executed_avg is not None
        ):
            if executed_avg <= -0.10:
                floor = max(floor, base_floor)
                reasons.append("executed returns in this horizon bucket are still negative")
            elif executed_avg < -0.05:
                floor = max(floor, round(base_floor - 0.005, 4))
                reasons.append("live bucket returns are soft, limiting the floor reduction")

    if local_stats and local_stats.resolved_copied_count >= 3 and local_stats.copied_avg_return is not None:
        local_avg = local_stats.copied_avg_return
        if local_avg < -0.10:
            floor = max(floor, round(base_floor + 0.01, 4))
            reasons.append("local copied returns for this wallet are materially negative")
        elif local_avg < 0:
            floor = max(floor, round(base_floor + 0.005, 4))
            reasons.append("local copied returns for this wallet are slightly negative")
        elif (
            bucket != "under_15m"
            and local_stats.resolved_copied_count >= 5
            and local_avg >= 0.05
        ):
            floor = min(floor, round(base_floor - 0.005, 4))
            reasons.append("local copied returns for this wallet are strong")

    clamped_floor = round(min(max(floor, min_floor), max_floor), 4)
    return AdaptiveFloorDecision(
        floor=clamped_floor,
        base_floor=base_floor,
        bucket=bucket,
        bucket_low_conf_samples=len(bucket_stats.low_conf_samples),
        bucket_executed_samples=bucket_stats.resolved_executed_count,
        local_resolved_copied_count=(local_stats.resolved_copied_count if local_stats else 0),
        local_copied_avg_return=(local_stats.copied_avg_return if local_stats else None),
        adjustment=round(clamped_floor - base_floor, 4),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _suggest_bucket_floor(base_floor: float, bucket: str, bucket_stats: BucketStats) -> float:
    if bucket not in {"15m_1h", "1h_6h"}:
        return base_floor

    candidates = [
        round(base_floor - LOWER_STEP, 4),
        round(base_floor - (2 * LOWER_STEP), 4),
        round(base_floor - (3 * LOWER_STEP), 4),
    ]
    chosen = base_floor
    for candidate in candidates:
        stats = _counterfactual_stats(bucket_stats.low_conf_samples, candidate)
        if stats is None:
            continue
        n_samples, win_rate, avg_return = stats
        if bucket == "15m_1h":
            if n_samples >= 8 and win_rate >= 0.45 and avg_return >= 0.08:
                chosen = candidate
        else:
            if n_samples >= 8 and win_rate >= 0.50 and avg_return >= 0.08:
                chosen = candidate
    return chosen


def _counterfactual_stats(
    rows: tuple[CounterfactualRow, ...],
    threshold: float,
) -> tuple[int, float, float] | None:
    qualifying = [row for row in rows if row.confidence >= threshold]
    if not qualifying:
        return None
    win_rate = sum(1 for row in qualifying if row.won) / len(qualifying)
    avg_return = statistics.fmean(row.counterfactual_return for row in qualifying)
    return len(qualifying), float(win_rate), float(avg_return)


def _load_snapshot() -> AdaptiveFloorSnapshot:
    global _snapshot_cache
    now = time.time()
    if _snapshot_cache and (now - _snapshot_cache[0]) < CACHE_TTL_SECONDS:
        return _snapshot_cache[1]

    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                trader_address,
                skipped,
                skip_reason,
                confidence,
                outcome,
                counterfactual_return,
                actual_entry_size_usd,
                actual_pnl_usd,
                shadow_pnl_usd,
                source_ts,
                market_close_ts
            FROM trade_log
            WHERE COALESCE(source_action, 'buy')='buy'
              AND {NON_CHALLENGER_EXPERIMENT_ARM_SQL}
              AND (
                  (skipped=1 AND outcome IS NOT NULL)
                  OR (
                      skipped=0
                      AND actual_entry_size_usd IS NOT NULL
                      AND COALESCE(actual_pnl_usd, shadow_pnl_usd) IS NOT NULL
                  )
              )
            """
        ).fetchall()
    finally:
        conn.close()

    bucket_counterfactuals: dict[str, list[CounterfactualRow]] = {}
    bucket_executed_returns: dict[str, list[float]] = {}
    local_returns: dict[str, list[float]] = {}
    local_wins: dict[str, int] = {}

    for row in rows:
        lead_seconds = _lead_seconds(row["source_ts"], row["market_close_ts"])
        bucket = _bucket_from_seconds(lead_seconds)
        pnl = _resolved_pnl(row["actual_pnl_usd"], row["shadow_pnl_usd"])

        if bool(row["skipped"]):
            if not _is_low_conf_skip_reason(row["skip_reason"]):
                continue
            confidence = float(row["confidence"] or 0.0)
            outcome = bool(int(row["outcome"] or 0))
            counterfactual = float(row["counterfactual_return"] or 0.0)
            bucket_counterfactuals.setdefault(bucket, []).append(
                CounterfactualRow(
                    confidence=confidence,
                    won=outcome,
                    counterfactual_return=counterfactual,
                )
            )
            continue

        size = float(row["actual_entry_size_usd"] or 0.0)
        if size <= 0 or pnl is None:
            continue
        ret = float(pnl) / size
        bucket_executed_returns.setdefault(bucket, []).append(ret)
        wallet = str(row["trader_address"] or "").strip().lower()
        if wallet:
            local_returns.setdefault(wallet, []).append(ret)
            if ret > 0:
                local_wins[wallet] = local_wins.get(wallet, 0) + 1

    bucket_stats = {
        bucket: BucketStats(
            resolved_executed_count=len(bucket_executed_returns.get(bucket, [])),
            resolved_executed_avg_return=(
                round(statistics.fmean(bucket_executed_returns[bucket]), 4)
                if bucket_executed_returns.get(bucket)
                else None
            ),
            low_conf_samples=tuple(bucket_counterfactuals.get(bucket, [])),
        )
        for bucket in {"under_15m", "15m_1h", "1h_6h", "over_6h"}
    }
    local_copy_stats = {
        wallet: LocalCopyStats(
            resolved_copied_count=len(returns),
            copied_avg_return=round(statistics.fmean(returns), 4) if returns else None,
            copied_win_rate=round(local_wins.get(wallet, 0) / len(returns), 4) if returns else None,
        )
        for wallet, returns in local_returns.items()
    }

    snapshot = AdaptiveFloorSnapshot(bucket_stats=bucket_stats, local_copy_stats=local_copy_stats)
    _snapshot_cache = (now, snapshot)
    return snapshot


def _bucket_from_days_to_res(days_to_res: float | None) -> str:
    seconds = float(days_to_res or 0.0) * 86400.0
    return _bucket_from_seconds(seconds)


def _bucket_from_seconds(seconds: float | None) -> str:
    value = float(seconds or 0.0)
    if value < SHORT_WINDOW_SECONDS:
        return "under_15m"
    if value < INTRAHOUR_WINDOW_SECONDS:
        return "15m_1h"
    if value <= 6 * 3600:
        return "1h_6h"
    return "over_6h"


def _lead_seconds(source_ts: object, close_ts: object) -> float:
    try:
        source_value = int(float(source_ts or 0))
        close_value = int(float(close_ts or 0))
    except (TypeError, ValueError):
        return 0.0
    if source_value <= 0 or close_value <= 0:
        return 0.0
    return max(float(close_value - source_value), 0.0)


def _resolved_pnl(actual_pnl: object, shadow_pnl: object) -> float | None:
    value = actual_pnl if actual_pnl is not None else shadow_pnl
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_low_conf_skip_reason(reason: object) -> bool:
    text = str(reason or "").strip().lower()
    if not text:
        return False
    return (
        "confidence was" in text
        and "below the" in text
        and "minimum" in text
    )
