from __future__ import annotations

from dataclasses import dataclass

from config import (
    min_bet_usd,
    wallet_discovery_min_observed_buys,
    wallet_discovery_min_resolved_buys,
    wallet_probation_size_multiplier,
    wallet_trusted_min_resolved_copied_buys,
)
from db import get_conn
from trade_contract import OBSERVED_BUY_SQL, RESOLVED_EXECUTED_ENTRY_SQL, RESOLVED_OBSERVED_BUY_SQL, resolved_pnl_expr


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
    min_observed_buy_count: int
    min_resolved_observed_buy_count: int
    min_resolved_copied_buy_count: int

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
        if self.tier != "discovery":
            return None
        return (
            "wallet is still in discovery probation, "
            f"observed {self.observed_buy_count}/{self.min_observed_buy_count} buy opportunities "
            f"and {self.resolved_observed_buy_count}/{self.min_resolved_observed_buy_count} resolved outcomes"
        )

    @property
    def probation_note(self) -> str | None:
        if self.tier != "probation":
            return None
        return (
            "wallet is in probation, "
            f"local copied history is {self.resolved_copied_buy_count}/{self.min_resolved_copied_buy_count} "
            "resolved trades"
        )

    def as_dict(self) -> dict[str, float | int | str | None]:
        return {
            "tier": self.tier,
            "size_multiplier": self.size_multiplier,
            "observed_buy_count": self.observed_buy_count,
            "resolved_observed_buy_count": self.resolved_observed_buy_count,
            "resolved_copied_buy_count": self.resolved_copied_buy_count,
            "resolved_copied_win_rate": self.resolved_copied_win_rate,
            "resolved_copied_avg_return": self.resolved_copied_avg_return,
            "min_observed_buy_count": self.min_observed_buy_count,
            "min_resolved_observed_buy_count": self.min_resolved_observed_buy_count,
            "min_resolved_copied_buy_count": self.min_resolved_copied_buy_count,
        }


def get_wallet_trust_state(wallet_address: str) -> WalletTrustState:
    wallet_key = str(wallet_address or "").strip().lower()
    discovery_min_observed = wallet_discovery_min_observed_buys()
    discovery_min_resolved = wallet_discovery_min_resolved_buys()
    trusted_min_resolved_copied = wallet_trusted_min_resolved_copied_buys()
    probation_multiplier = wallet_probation_size_multiplier()

    if not wallet_key:
        return WalletTrustState(
            wallet_address=wallet_key,
            tier="discovery",
            size_multiplier=0.0,
            observed_buy_count=0,
            resolved_observed_buy_count=0,
            resolved_copied_buy_count=0,
            resolved_copied_win_rate=None,
            resolved_copied_avg_return=None,
            min_observed_buy_count=discovery_min_observed,
            min_resolved_observed_buy_count=discovery_min_resolved,
            min_resolved_copied_buy_count=trusted_min_resolved_copied,
        )

    conn = get_conn()
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

    if (
        observed_buy_count < discovery_min_observed
        or resolved_observed_buy_count < discovery_min_resolved
    ):
        tier = "discovery"
        size_multiplier = 0.0
    elif resolved_copied_buy_count < trusted_min_resolved_copied:
        tier = "probation"
        size_multiplier = probation_multiplier
    else:
        tier = "trusted"
        size_multiplier = 1.0

    return WalletTrustState(
        wallet_address=wallet_key,
        tier=tier,
        size_multiplier=size_multiplier,
        observed_buy_count=observed_buy_count,
        resolved_observed_buy_count=resolved_observed_buy_count,
        resolved_copied_buy_count=resolved_copied_buy_count,
        resolved_copied_win_rate=resolved_copied_win_rate,
        resolved_copied_avg_return=resolved_copied_avg_return,
        min_observed_buy_count=discovery_min_observed,
        min_resolved_observed_buy_count=discovery_min_resolved,
        min_resolved_copied_buy_count=trusted_min_resolved_copied,
    )


def apply_wallet_trust_sizing(sizing: dict, trust_state: WalletTrustState) -> dict:
    adjusted = dict(sizing)
    adjusted["wallet_trust"] = trust_state.as_dict()

    base_size = float(adjusted.get("dollar_size", 0.0) or 0.0)
    if trust_state.tier != "probation" or base_size <= 0:
        adjusted["wallet_trust_note"] = trust_state.probation_note
        adjusted["wallet_trust_multiplier"] = trust_state.size_multiplier
        adjusted["wallet_trust_effective_multiplier"] = 1.0 if base_size > 0 else 0.0
        return adjusted

    scaled_size = round(base_size * trust_state.size_multiplier, 2)
    min_bet = min_bet_usd()
    if 0.0 < scaled_size < min_bet:
        scaled_size = min(base_size, min_bet)
    scaled_size = round(min(base_size, scaled_size), 2)
    effective_multiplier = (scaled_size / base_size) if base_size > 0 else 0.0

    adjusted["dollar_size"] = scaled_size
    adjusted["kelly_f"] = round(float(adjusted.get("kelly_f", 0.0) or 0.0) * effective_multiplier, 5)
    adjusted["full_kelly_f"] = round(float(adjusted.get("full_kelly_f", 0.0) or 0.0) * effective_multiplier, 5)
    adjusted["wallet_trust_multiplier"] = trust_state.size_multiplier
    adjusted["wallet_trust_effective_multiplier"] = round(effective_multiplier, 5)
    adjusted["wallet_trust_note"] = (
        f"{trust_state.probation_note}, size scaled to {effective_multiplier * 100:.0f}%"
        if effective_multiplier < 0.999
        else f"{trust_state.probation_note}, minimum bet floor kept size unchanged"
    )
    return adjusted
