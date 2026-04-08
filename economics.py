from __future__ import annotations

from dataclasses import dataclass

FEE_USD_PRECISION = 5
SHARE_PRECISION = 6
USD_PRECISION = 6
MIN_FEE_USD = 10 ** (-FEE_USD_PRECISION)


@dataclass(frozen=True)
class EntryEconomics:
    fee_rate_bps: int
    gross_price: float
    gross_shares: float
    gross_spent_usd: float
    entry_fee_usd: float
    entry_fee_shares: float
    fixed_cost_usd: float
    total_cost_usd: float
    net_shares: float
    effective_entry_price: float
    expected_exit_fee_usd: float
    expected_close_fixed_cost_usd: float
    sizing_total_cost_usd: float
    sizing_effective_price: float


@dataclass(frozen=True)
class ExitEconomics:
    fee_rate_bps: int
    gross_price: float
    gross_shares: float
    gross_notional_usd: float
    exit_fee_usd: float
    fixed_cost_usd: float
    net_proceeds_usd: float
    effective_exit_price: float


def taker_fee_usd(shares: float, price: float, fee_rate_bps: int) -> float:
    if shares <= 0 or not (0.0 < price < 1.0) or fee_rate_bps <= 0:
        return 0.0

    fee_rate = float(fee_rate_bps) / 10_000.0
    fee = shares * fee_rate * price * (1.0 - price)
    rounded = round(max(fee, 0.0), FEE_USD_PRECISION)
    return rounded if rounded >= MIN_FEE_USD else 0.0


def build_entry_economics(
    *,
    gross_price: float,
    gross_shares: float,
    gross_spent_usd: float,
    fee_rate_bps: int,
    fixed_cost_usd: float,
    include_expected_exit_fee_in_sizing: bool,
    expected_close_fixed_cost_usd: float,
) -> EntryEconomics:
    rounded_spent = round(max(gross_spent_usd, 0.0), USD_PRECISION)
    rounded_fixed = round(max(fixed_cost_usd, 0.0), USD_PRECISION)
    rounded_close_fixed = round(max(expected_close_fixed_cost_usd, 0.0), USD_PRECISION)
    safe_fee_bps = max(int(fee_rate_bps or 0), 0)

    if gross_shares <= 0 or rounded_spent <= 0 or not (0.0 < gross_price < 1.0):
        total_cost = round(rounded_spent + rounded_fixed, USD_PRECISION)
        sizing_total_cost = round(total_cost + rounded_close_fixed, USD_PRECISION)
        return EntryEconomics(
            fee_rate_bps=safe_fee_bps,
            gross_price=max(float(gross_price or 0.0), 0.0),
            gross_shares=0.0,
            gross_spent_usd=rounded_spent,
            entry_fee_usd=0.0,
            entry_fee_shares=0.0,
            fixed_cost_usd=rounded_fixed,
            total_cost_usd=total_cost,
            net_shares=0.0,
            effective_entry_price=0.0,
            expected_exit_fee_usd=0.0,
            expected_close_fixed_cost_usd=rounded_close_fixed,
            sizing_total_cost_usd=sizing_total_cost,
            sizing_effective_price=0.0,
        )

    rounded_shares = round(max(gross_shares, 0.0), SHARE_PRECISION)
    entry_fee_usd = taker_fee_usd(rounded_shares, gross_price, safe_fee_bps)
    entry_fee_shares = round(entry_fee_usd / gross_price, SHARE_PRECISION) if gross_price > 0 else 0.0
    net_shares = round(max(rounded_shares - entry_fee_shares, 0.0), SHARE_PRECISION)
    total_cost_usd = round(rounded_spent + rounded_fixed, USD_PRECISION)
    effective_entry_price = round(total_cost_usd / net_shares, USD_PRECISION) if net_shares > 0 else 0.0
    expected_exit_fee_usd = (
        taker_fee_usd(net_shares, gross_price, safe_fee_bps)
        if include_expected_exit_fee_in_sizing and net_shares > 0
        else 0.0
    )
    sizing_total_cost_usd = round(total_cost_usd + expected_exit_fee_usd + rounded_close_fixed, USD_PRECISION)
    sizing_effective_price = round(sizing_total_cost_usd / net_shares, USD_PRECISION) if net_shares > 0 else 0.0

    return EntryEconomics(
        fee_rate_bps=safe_fee_bps,
        gross_price=round(gross_price, USD_PRECISION),
        gross_shares=rounded_shares,
        gross_spent_usd=rounded_spent,
        entry_fee_usd=entry_fee_usd,
        entry_fee_shares=entry_fee_shares,
        fixed_cost_usd=rounded_fixed,
        total_cost_usd=total_cost_usd,
        net_shares=net_shares,
        effective_entry_price=effective_entry_price,
        expected_exit_fee_usd=expected_exit_fee_usd,
        expected_close_fixed_cost_usd=rounded_close_fixed,
        sizing_total_cost_usd=sizing_total_cost_usd,
        sizing_effective_price=sizing_effective_price,
    )


def build_exit_economics(
    *,
    gross_price: float,
    gross_shares: float,
    gross_notional_usd: float,
    fee_rate_bps: int,
    fixed_cost_usd: float,
) -> ExitEconomics:
    safe_fee_bps = max(int(fee_rate_bps or 0), 0)
    rounded_shares = round(max(gross_shares, 0.0), SHARE_PRECISION)
    rounded_notional = round(max(gross_notional_usd, 0.0), USD_PRECISION)
    rounded_fixed = round(max(fixed_cost_usd, 0.0), USD_PRECISION)
    exit_fee_usd = taker_fee_usd(rounded_shares, gross_price, safe_fee_bps)
    net_proceeds_usd = round(max(rounded_notional - exit_fee_usd - rounded_fixed, 0.0), USD_PRECISION)
    effective_exit_price = round(net_proceeds_usd / rounded_shares, USD_PRECISION) if rounded_shares > 0 else 0.0

    return ExitEconomics(
        fee_rate_bps=safe_fee_bps,
        gross_price=round(max(gross_price, 0.0), USD_PRECISION),
        gross_shares=rounded_shares,
        gross_notional_usd=rounded_notional,
        exit_fee_usd=exit_fee_usd,
        fixed_cost_usd=rounded_fixed,
        net_proceeds_usd=net_proceeds_usd,
        effective_exit_price=effective_exit_price,
    )
