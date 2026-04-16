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
    wallet_uncopyable_drop_max_skip_rate,
)
from kelly_watcher.data.db import get_trade_log_read_conn, load_wallet_promotion_state
from kelly_watcher.engine.trade_contract import (
    NON_CHALLENGER_EXPERIMENT_ARM_SQL,
    OBSERVED_BUY_SQL,
    RESOLVED_EXECUTED_ENTRY_SQL,
    RESOLVED_OBSERVED_BUY_SQL,
    resolved_pnl_expr,
)

OVERRIDE_CACHE_TTL_SECONDS = 60.0
_override_cache: tuple[float, dict[str, "WalletSkipOverrideStats"]] | None = None
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
    post_promotion_baseline_at: int = 0
    post_promotion_source: str | None = None
    post_promotion_reason: str | None = None
    post_promotion_total_buy_signals: int = 0
    post_promotion_uncopyable_skips: int = 0
    post_promotion_uncopyable_skip_rate: float = 0.0
    post_promotion_resolved_copied_buy_count: int = 0
    post_promotion_resolved_copied_win_rate: float | None = None
    post_promotion_resolved_copied_avg_return: float | None = None
    post_promotion_evidence_ready: bool = False
    post_promotion_evidence_note: str | None = None

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
        elif self.tier == "promotion_probation":
            notes.append(
                self.post_promotion_evidence_note
                or (
                    "wallet is in post-promotion probation, "
                    f"resolved copied history since promotion is "
                    f"{self.post_promotion_resolved_copied_buy_count}/{max(self.min_resolved_observed_buy_count, 1)} trades"
                )
            )
        elif self.post_promotion_evidence_note:
            notes.append(self.post_promotion_evidence_note)
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
            "post_promotion_baseline_at": self.post_promotion_baseline_at,
            "post_promotion_source": self.post_promotion_source,
            "post_promotion_reason": self.post_promotion_reason,
            "post_promotion_total_buy_signals": self.post_promotion_total_buy_signals,
            "post_promotion_uncopyable_skips": self.post_promotion_uncopyable_skips,
            "post_promotion_uncopyable_skip_rate": self.post_promotion_uncopyable_skip_rate,
            "post_promotion_resolved_copied_buy_count": self.post_promotion_resolved_copied_buy_count,
            "post_promotion_resolved_copied_win_rate": self.post_promotion_resolved_copied_win_rate,
            "post_promotion_resolved_copied_avg_return": self.post_promotion_resolved_copied_avg_return,
            "post_promotion_evidence_ready": self.post_promotion_evidence_ready,
            "post_promotion_evidence_note": self.post_promotion_evidence_note,
        }


@dataclass(frozen=True)
class WalletSkipOverrideStats:
    duplicate_skip_count: int = 0
    duplicate_avg_return: float | None = None
    exposure_skip_count: int = 0
    exposure_avg_return: float | None = None


def _post_promotion_evidence_min_resolved_copied_buys() -> int:
    return max(wallet_discovery_min_resolved_buys(), 1)


def _post_promotion_evidence_min_buy_signals() -> int:
    return max(wallet_discovery_min_observed_buys(), _post_promotion_evidence_min_resolved_copied_buys() * 2, 1)


def _post_promotion_evidence_max_uncopyable_skip_rate() -> float:
    return min(max(wallet_uncopyable_drop_max_skip_rate(), 0.0), 0.5)


def _post_promotion_evidence_state(
    *,
    total_buy_signals: int,
    uncopyable_skip_rate: float,
    resolved_copied_buy_count: int,
) -> tuple[bool, str]:
    minimum_resolved = _post_promotion_evidence_min_resolved_copied_buys()
    minimum_buy_signals = _post_promotion_evidence_min_buy_signals()
    max_skip_rate = _post_promotion_evidence_max_uncopyable_skip_rate()
    reasons: list[str] = []
    if resolved_copied_buy_count < minimum_resolved:
        reasons.append(f"{resolved_copied_buy_count}/{minimum_resolved} resolved copied trades")
    if total_buy_signals < minimum_buy_signals:
        reasons.append(f"{total_buy_signals}/{minimum_buy_signals} buy signals")
    if total_buy_signals > 0 and uncopyable_skip_rate > max_skip_rate:
        reasons.append(
            f"{uncopyable_skip_rate * 100.0:.0f}%>{max_skip_rate * 100.0:.0f}% uncopyable skip rate"
        )
    if reasons:
        return False, "awaiting post-promotion evidence " + ", ".join(reasons)
    return (
        True,
        "post-promotion evidence ready "
        f"{resolved_copied_buy_count}/{minimum_resolved} resolved copied trades, "
        f"{total_buy_signals}/{minimum_buy_signals} buy signals, "
        f"{uncopyable_skip_rate * 100.0:.0f}% uncopyable skip rate",
    )


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
            post_promotion_baseline_at=0,
            post_promotion_source=None,
            post_promotion_reason=None,
            post_promotion_total_buy_signals=0,
            post_promotion_uncopyable_skips=0,
            post_promotion_uncopyable_skip_rate=0.0,
            post_promotion_resolved_copied_buy_count=0,
            post_promotion_resolved_copied_win_rate=None,
            post_promotion_resolved_copied_avg_return=None,
            post_promotion_evidence_ready=True,
            post_promotion_evidence_note=None,
        )

    conn = get_trade_log_read_conn()
    try:
        promotion_state = load_wallet_promotion_state([wallet_key]).get(wallet_key, {})
        post_promotion_baseline_at = int(promotion_state.get("baseline_at") or 0)
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

        post_promotion_row = None
        if post_promotion_baseline_at > 0:
            post_promotion_row = conn.execute(
                f"""
                SELECT
                    SUM(
                        CASE
                            WHEN {OBSERVED_BUY_SQL}
                             AND COALESCE(placed_at, 0) >= ?
                                THEN 1
                            ELSE 0
                        END
                    ) AS post_promotion_total_buy_signals,
                    SUM(
                        CASE
                            WHEN {OBSERVED_BUY_SQL}
                             AND COALESCE(placed_at, 0) >= ?
                             AND {_UNCOPYABLE_SKIP_SQL}
                                THEN 1
                            ELSE 0
                        END
                    ) AS post_promotion_uncopyable_skips,
                    SUM(
                        CASE
                            WHEN {RESOLVED_EXECUTED_ENTRY_SQL}
                             AND COALESCE(placed_at, 0) >= ?
                                THEN 1
                            ELSE 0
                        END
                    ) AS post_promotion_resolved_copied_buy_count,
                    SUM(
                        CASE
                            WHEN {RESOLVED_EXECUTED_ENTRY_SQL}
                             AND COALESCE(placed_at, 0) >= ?
                             AND {resolved_pnl_expr()} > 0
                                THEN 1
                            ELSE 0
                        END
                    ) AS post_promotion_resolved_copied_wins,
                    AVG(
                        CASE
                            WHEN {RESOLVED_EXECUTED_ENTRY_SQL}
                             AND COALESCE(placed_at, 0) >= ?
                                THEN {resolved_pnl_expr()} / NULLIF(COALESCE(actual_entry_size_usd, signal_size_usd, 0), 0)
                            ELSE NULL
                        END
                    ) AS post_promotion_resolved_copied_avg_return
                FROM trade_log
                WHERE trader_address=?
                """,
                (
                    post_promotion_baseline_at,
                    post_promotion_baseline_at,
                    post_promotion_baseline_at,
                    post_promotion_baseline_at,
                    post_promotion_baseline_at,
                    wallet_key,
                ),
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
    post_promotion_total_buy_signals = int((post_promotion_row["post_promotion_total_buy_signals"] or 0) if post_promotion_row else 0)
    post_promotion_resolved_copied_buy_count = int(
        (post_promotion_row["post_promotion_resolved_copied_buy_count"] or 0)
        if post_promotion_row
        else 0
    )
    post_promotion_uncopyable_skips = int(
        (post_promotion_row["post_promotion_uncopyable_skips"] or 0)
        if post_promotion_row
        else 0
    )
    post_promotion_uncopyable_skip_rate = (
        post_promotion_uncopyable_skips / post_promotion_total_buy_signals
        if post_promotion_total_buy_signals > 0
        else 0.0
    )
    post_promotion_resolved_copied_wins = int(
        (post_promotion_row["post_promotion_resolved_copied_wins"] or 0)
        if post_promotion_row
        else 0
    )
    post_promotion_resolved_copied_avg_return = (
        float(post_promotion_row["post_promotion_resolved_copied_avg_return"])
        if post_promotion_row and post_promotion_row["post_promotion_resolved_copied_avg_return"] is not None
        else None
    )
    post_promotion_resolved_copied_win_rate = (
        post_promotion_resolved_copied_wins / post_promotion_resolved_copied_buy_count
        if post_promotion_resolved_copied_buy_count > 0
        else None
    )
    post_promotion_evidence_ready = True
    post_promotion_evidence_note = None
    if post_promotion_baseline_at > 0:
        post_promotion_evidence_ready, post_promotion_evidence_note = _post_promotion_evidence_state(
            total_buy_signals=post_promotion_total_buy_signals,
            uncopyable_skip_rate=post_promotion_uncopyable_skip_rate,
            resolved_copied_buy_count=post_promotion_resolved_copied_buy_count,
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

    if post_promotion_baseline_at > 0 and not post_promotion_evidence_ready:
        tier = "promotion_probation"
        size_multiplier = min(
            size_multiplier,
            discovery_multiplier
            if (
                post_promotion_resolved_copied_buy_count <= 0
                or post_promotion_total_buy_signals < _post_promotion_evidence_min_buy_signals()
                or post_promotion_uncopyable_skip_rate > _post_promotion_evidence_max_uncopyable_skip_rate()
            )
            else probation_multiplier,
        )

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
        post_promotion_baseline_at=post_promotion_baseline_at,
        post_promotion_source=str(promotion_state.get("promotion_source") or "").strip() or None,
        post_promotion_reason=str(promotion_state.get("promotion_reason") or "").strip() or None,
        post_promotion_total_buy_signals=post_promotion_total_buy_signals,
        post_promotion_uncopyable_skips=post_promotion_uncopyable_skips,
        post_promotion_uncopyable_skip_rate=post_promotion_uncopyable_skip_rate,
        post_promotion_resolved_copied_buy_count=post_promotion_resolved_copied_buy_count,
        post_promotion_resolved_copied_win_rate=post_promotion_resolved_copied_win_rate,
        post_promotion_resolved_copied_avg_return=post_promotion_resolved_copied_avg_return,
        post_promotion_evidence_ready=post_promotion_evidence_ready,
        post_promotion_evidence_note=post_promotion_evidence_note,
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
