from __future__ import annotations

import time
from dataclasses import dataclass

from kelly_watcher.config import (
    duplicate_side_override_min_avg_return,
    duplicate_side_override_min_skips,
    exposure_override_min_avg_return,
    exposure_override_min_skips,
    exposure_override_total_cap_fraction,
    min_bet_usd,
    wallet_cold_start_min_observed_buys,
    wallet_discovery_min_observed_buys,
    wallet_discovery_min_resolved_buys,
    wallet_discovery_size_multiplier,
    wallet_local_performance_penalty_max_avg_return,
    wallet_local_performance_penalty_min_resolved_copied_buys,
    wallet_local_performance_penalty_size_multiplier,
    wallet_quality_size_max_multiplier,
    wallet_quality_size_min_multiplier,
    wallet_probation_size_multiplier,
    wallet_trusted_min_resolved_copied_buys,
)
from kelly_watcher.data.db import get_conn, get_trade_log_read_conn
from kelly_watcher.engine.trade_contract import (
    NON_CHALLENGER_EXPERIMENT_ARM_SQL,
    OBSERVED_BUY_SQL,
    RESOLVED_EXECUTED_ENTRY_SQL,
    RESOLVED_OBSERVED_BUY_SQL,
    resolved_pnl_expr,
)

OVERRIDE_CACHE_TTL_SECONDS = 60.0
_override_cache: tuple[float, dict[str, "WalletSkipOverrideStats"]] | None = None


@dataclass(frozen=True)
class WalletTrustState:
    wallet_address: str
    tier: str
    size_multiplier: float
    observed_buy_count: int
    resolved_observed_buy_count: int
    resolved_copied_buy_count: int
    resolved_copied_win_rate: float | None
    resolved_copied_avg_return: float | None
    min_cold_start_observed_buy_count: int
    min_observed_buy_count: int
    min_resolved_observed_buy_count: int
    min_resolved_copied_buy_count: int
    local_performance_penalty_multiplier: float | None = None
    local_performance_penalty_reason: str | None = None

    @property
    def cold_start_ready(self) -> bool:
        return self.observed_buy_count >= self.min_cold_start_observed_buy_count

    @property
    def discovery_ready(self) -> bool:
        return (
            self.observed_buy_count >= self.min_observed_buy_count
            and self.resolved_observed_buy_count >= self.min_resolved_observed_buy_count
        )

    @property
    def trusted_ready(self) -> bool:
        return self.resolved_copied_buy_count >= self.min_resolved_copied_buy_count

    @property
    def skip_reason(self) -> str | None:
        if self.tier != "cold_start":
            return None
        return (
            "wallet is still in cold start, "
            f"observed {self.observed_buy_count}/{self.min_cold_start_observed_buy_count} buy opportunities"
        )

    @property
    def tier_note(self) -> str | None:
        notes: list[str] = []
        if self.tier == "discovery":
            notes.append(
                "wallet is in discovery, "
                f"observed {self.observed_buy_count}/{self.min_observed_buy_count} buy opportunities "
                f"and {self.resolved_observed_buy_count}/{self.min_resolved_observed_buy_count} resolved outcomes"
            )
        elif self.tier == "probation":
            notes.append(
                "wallet is in probation, "
                f"local copied history is {self.resolved_copied_buy_count}/{self.min_resolved_copied_buy_count} "
                "resolved trades"
            )
        if self.local_performance_penalty_reason:
            notes.append(self.local_performance_penalty_reason)
        return ", ".join(notes) if notes else None

    def as_dict(self) -> dict[str, float | int | str | None]:
        return {
            "tier": self.tier,
            "size_multiplier": self.size_multiplier,
            "observed_buy_count": self.observed_buy_count,
            "resolved_observed_buy_count": self.resolved_observed_buy_count,
            "resolved_copied_buy_count": self.resolved_copied_buy_count,
            "resolved_copied_win_rate": self.resolved_copied_win_rate,
            "resolved_copied_avg_return": self.resolved_copied_avg_return,
            "min_cold_start_observed_buy_count": self.min_cold_start_observed_buy_count,
            "min_observed_buy_count": self.min_observed_buy_count,
            "min_resolved_observed_buy_count": self.min_resolved_observed_buy_count,
            "min_resolved_copied_buy_count": self.min_resolved_copied_buy_count,
            "local_performance_penalty_multiplier": self.local_performance_penalty_multiplier,
            "local_performance_penalty_reason": self.local_performance_penalty_reason,
        }


@dataclass(frozen=True)
class WalletSkipOverrideStats:
    duplicate_skip_count: int = 0
    duplicate_avg_return: float | None = None
    exposure_skip_count: int = 0
    exposure_avg_return: float | None = None


def get_wallet_trust_state(wallet_address: str) -> WalletTrustState:
    wallet_key = str(wallet_address or "").strip().lower()
    cold_start_min_observed = wallet_cold_start_min_observed_buys()
    discovery_min_observed = wallet_discovery_min_observed_buys()
    discovery_min_resolved = wallet_discovery_min_resolved_buys()
    discovery_multiplier = wallet_discovery_size_multiplier()
    trusted_min_resolved_copied = wallet_trusted_min_resolved_copied_buys()
    probation_multiplier = wallet_probation_size_multiplier()

    if not wallet_key:
        return WalletTrustState(
            wallet_address=wallet_key,
            tier="cold_start",
            size_multiplier=0.0,
            observed_buy_count=0,
            resolved_observed_buy_count=0,
            resolved_copied_buy_count=0,
            resolved_copied_win_rate=None,
            resolved_copied_avg_return=None,
            min_cold_start_observed_buy_count=cold_start_min_observed,
            min_observed_buy_count=discovery_min_observed,
            min_resolved_observed_buy_count=discovery_min_resolved,
            min_resolved_copied_buy_count=trusted_min_resolved_copied,
            local_performance_penalty_multiplier=None,
            local_performance_penalty_reason=None,
        )

    conn = get_trade_log_read_conn()
    try:
        row = conn.execute(
            f"""
            SELECT
                SUM(CASE WHEN {OBSERVED_BUY_SQL} THEN 1 ELSE 0 END) AS observed_buy_count,
                SUM(CASE WHEN {RESOLVED_OBSERVED_BUY_SQL} THEN 1 ELSE 0 END) AS resolved_observed_buy_count,
                SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} THEN 1 ELSE 0 END) AS resolved_copied_buy_count,
                SUM(
                    CASE
                        WHEN {RESOLVED_EXECUTED_ENTRY_SQL} AND {resolved_pnl_expr()} > 0
                            THEN 1
                        ELSE 0
                    END
                ) AS resolved_copied_wins,
                AVG(
                    CASE
                        WHEN {RESOLVED_EXECUTED_ENTRY_SQL}
                            THEN {resolved_pnl_expr()} / NULLIF(COALESCE(actual_entry_size_usd, signal_size_usd, 0), 0)
                        ELSE NULL
                    END
                ) AS resolved_copied_avg_return
            FROM trade_log
            WHERE trader_address=?
            """,
            (wallet_key,),
        ).fetchone()
    finally:
        conn.close()

    observed_buy_count = int((row["observed_buy_count"] or 0) if row else 0)
    resolved_observed_buy_count = int((row["resolved_observed_buy_count"] or 0) if row else 0)
    resolved_copied_buy_count = int((row["resolved_copied_buy_count"] or 0) if row else 0)
    resolved_copied_wins = int((row["resolved_copied_wins"] or 0) if row else 0)
    resolved_copied_avg_return = (
        float(row["resolved_copied_avg_return"])
        if row and row["resolved_copied_avg_return"] is not None
        else None
    )
    resolved_copied_win_rate = (
        resolved_copied_wins / resolved_copied_buy_count
        if resolved_copied_buy_count > 0
        else None
    )

    if observed_buy_count < cold_start_min_observed:
        tier = "cold_start"
        size_multiplier = 0.0
    elif (
        observed_buy_count < discovery_min_observed
        or resolved_observed_buy_count < discovery_min_resolved
    ):
        tier = "discovery"
        size_multiplier = discovery_multiplier
    elif resolved_copied_buy_count < trusted_min_resolved_copied:
        tier = "probation"
        size_multiplier = probation_multiplier
    else:
        tier = "trusted"
        size_multiplier = 1.0

    local_penalty_multiplier, local_penalty_reason = _local_performance_penalty(
        resolved_copied_buy_count=resolved_copied_buy_count,
        resolved_copied_avg_return=resolved_copied_avg_return,
    )
    if local_penalty_multiplier is not None:
        size_multiplier = min(size_multiplier, local_penalty_multiplier)

    return WalletTrustState(
        wallet_address=wallet_key,
        tier=tier,
        size_multiplier=size_multiplier,
        observed_buy_count=observed_buy_count,
        resolved_observed_buy_count=resolved_observed_buy_count,
        resolved_copied_buy_count=resolved_copied_buy_count,
        resolved_copied_win_rate=resolved_copied_win_rate,
        resolved_copied_avg_return=resolved_copied_avg_return,
        min_cold_start_observed_buy_count=cold_start_min_observed,
        min_observed_buy_count=discovery_min_observed,
        min_resolved_observed_buy_count=discovery_min_resolved,
        min_resolved_copied_buy_count=trusted_min_resolved_copied,
        local_performance_penalty_multiplier=local_penalty_multiplier,
        local_performance_penalty_reason=local_penalty_reason,
    )


def _local_performance_penalty(
    *,
    resolved_copied_buy_count: int,
    resolved_copied_avg_return: float | None,
) -> tuple[float | None, str | None]:
    minimum_trades = wallet_local_performance_penalty_min_resolved_copied_buys()
    max_avg_return = wallet_local_performance_penalty_max_avg_return()
    penalty_multiplier = wallet_local_performance_penalty_size_multiplier()
    if (
        minimum_trades <= 0
        or penalty_multiplier >= 1.0
        or resolved_copied_buy_count < minimum_trades
        or resolved_copied_avg_return is None
        or resolved_copied_avg_return > max_avg_return
    ):
        return None, None

    clamped_multiplier = max(0.0, min(float(penalty_multiplier), 1.0))
    reason = (
        f"local copied avg return {resolved_copied_avg_return * 100.0:.1f}% "
        f"over {resolved_copied_buy_count} resolved trades, limiting size to {clamped_multiplier * 100.0:.0f}%"
    )
    return clamped_multiplier, reason


def reset_wallet_skip_override_cache() -> None:
    global _override_cache
    _override_cache = None


def allow_duplicate_side_override(wallet_address: str) -> bool:
    wallet_key = str(wallet_address or "").strip().lower()
    if not wallet_key:
        return False
    stats = _load_wallet_skip_override_stats().get(wallet_key, WalletSkipOverrideStats())
    avg_return = float(stats.duplicate_avg_return or 0.0)
    return (
        stats.duplicate_skip_count >= duplicate_side_override_min_skips()
        and avg_return >= duplicate_side_override_min_avg_return()
    )


def total_open_exposure_cap_fraction_for_wallet(wallet_address: str, base_fraction: float) -> float:
    wallet_key = str(wallet_address or "").strip().lower()
    if not wallet_key:
        return base_fraction
    stats = _load_wallet_skip_override_stats().get(wallet_key, WalletSkipOverrideStats())
    avg_return = float(stats.exposure_avg_return or 0.0)
    if (
        stats.exposure_skip_count >= exposure_override_min_skips()
        and avg_return >= exposure_override_min_avg_return()
    ):
        return max(base_fraction, exposure_override_total_cap_fraction())
    return base_fraction


def _load_wallet_skip_override_stats() -> dict[str, WalletSkipOverrideStats]:
    global _override_cache
    now = time.time()
    if _override_cache and (now - _override_cache[0]) < OVERRIDE_CACHE_TTL_SECONDS:
        return _override_cache[1]

    conn = get_trade_log_read_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT trader_address, skip_reason, counterfactual_return
            FROM trade_log
            WHERE skipped=1
              AND COALESCE(source_action, 'buy')='buy'
              AND {NON_CHALLENGER_EXPERIMENT_ARM_SQL}
              AND counterfactual_return IS NOT NULL
            """
        ).fetchall()
    finally:
        conn.close()

    aggregates: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        wallet_key = str(row["trader_address"] or "").strip().lower()
        if not wallet_key:
            continue
        reason = str(row["skip_reason"] or "").strip().lower()
        ret = float(row["counterfactual_return"] or 0.0)
        bucket = None
        if reason.startswith("we already had this side of the market open"):
            bucket = "duplicate"
        elif reason.startswith("total open exposure would be"):
            bucket = "exposure"
        if bucket is None:
            continue
        wallet_stats = aggregates.setdefault(wallet_key, {"duplicate": [], "exposure": []})
        wallet_stats[bucket].append(ret)

    snapshot: dict[str, WalletSkipOverrideStats] = {}
    for wallet_key, wallet_stats in aggregates.items():
        duplicate_returns = wallet_stats["duplicate"]
        exposure_returns = wallet_stats["exposure"]
        snapshot[wallet_key] = WalletSkipOverrideStats(
            duplicate_skip_count=len(duplicate_returns),
            duplicate_avg_return=(
                sum(duplicate_returns) / len(duplicate_returns) if duplicate_returns else None
            ),
            exposure_skip_count=len(exposure_returns),
            exposure_avg_return=(
                sum(exposure_returns) / len(exposure_returns) if exposure_returns else None
            ),
        )

    _override_cache = (now, snapshot)
    return snapshot


def wallet_quality_multiplier(quality_score: float | None) -> float:
    minimum = wallet_quality_size_min_multiplier()
    maximum = wallet_quality_size_max_multiplier()
    if quality_score is None:
        return 1.0
    clipped_score = max(0.0, min(float(quality_score), 1.0))
    return minimum + (clipped_score * (maximum - minimum))


def apply_wallet_trust_sizing(
    sizing: dict,
    trust_state: WalletTrustState,
    *,
    quality_score: float | None = None,
    max_size_usd: float | None = None,
) -> dict:
    adjusted = dict(sizing)
    adjusted["wallet_trust"] = trust_state.as_dict()
    adjusted["wallet_quality_score"] = (
        round(float(quality_score), 4) if quality_score is not None else None
    )

    base_size = float(adjusted.get("dollar_size", 0.0) or 0.0)
    quality_multiplier = wallet_quality_multiplier(quality_score) if base_size > 0 else 1.0
    adjusted["wallet_quality_multiplier"] = round(quality_multiplier, 5)
    adjusted["wallet_quality_effective_multiplier"] = (
        round(quality_multiplier, 5) if base_size > 0 and trust_state.size_multiplier > 0 else 0.0
    )

    if base_size <= 0 or trust_state.size_multiplier <= 0:
        adjusted["wallet_trust_note"] = trust_state.tier_note
        adjusted["wallet_trust_multiplier"] = trust_state.size_multiplier
        adjusted["wallet_trust_effective_multiplier"] = (
            0.0 if trust_state.size_multiplier <= 0 else 1.0
        )
        return adjusted

    combined_multiplier = trust_state.size_multiplier * quality_multiplier
    scaled_size = round(base_size * combined_multiplier, 2)
    if max_size_usd is not None and max_size_usd > 0:
        scaled_size = round(min(scaled_size, max_size_usd), 2)
    min_bet = min_bet_usd()
    if 0.0 < scaled_size < min_bet:
        scaled_size = min_bet if max_size_usd is None or max_size_usd >= min_bet else max_size_usd
    scaled_size = round(max(scaled_size, 0.0), 2)
    effective_multiplier = (scaled_size / base_size) if base_size > 0 else 0.0

    adjusted["dollar_size"] = scaled_size
    adjusted["kelly_f"] = round(float(adjusted.get("kelly_f", 0.0) or 0.0) * effective_multiplier, 5)
    adjusted["full_kelly_f"] = round(float(adjusted.get("full_kelly_f", 0.0) or 0.0) * effective_multiplier, 5)
    adjusted["wallet_trust_multiplier"] = trust_state.size_multiplier
    adjusted["wallet_trust_effective_multiplier"] = round(effective_multiplier, 5)
    trust_prefix = trust_state.tier_note
    quality_note = (
        f"wallet quality {adjusted['wallet_quality_score']:.2f} -> {quality_multiplier * 100:.0f}%"
        if quality_score is not None
        else "wallet quality neutral"
    )
    scaling_note = (
        f"size scaled to {effective_multiplier * 100:.0f}%"
        if abs(effective_multiplier - 1.0) > 1e-9
        else "size unchanged after wallet adjustments"
    )
    note_parts = [part for part in (trust_prefix, quality_note, scaling_note) if part]
    adjusted["wallet_trust_note"] = ", ".join(note_parts)
    return adjusted
