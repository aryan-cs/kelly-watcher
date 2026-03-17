from __future__ import annotations

from typing import Any

DATA_CONTRACT_VERSION = 2
RESOLVED_PNL_SQL = "COALESCE(actual_pnl_usd, shadow_pnl_usd)"
PROFITABLE_TRADE_SQL = f"CASE WHEN {RESOLVED_PNL_SQL} > 0 THEN 1 ELSE 0 END"

EXECUTED_ENTRY_SQL = """
skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
"""

OPEN_EXECUTED_ENTRY_SQL = f"""
{EXECUTED_ENTRY_SQL}
AND COALESCE(remaining_entry_shares, actual_entry_shares, source_shares, 0) > 1e-9
AND COALESCE(remaining_entry_size_usd, actual_entry_size_usd, signal_size_usd, 0) > 1e-9
AND outcome IS NULL
AND exited_at IS NULL
"""

RESOLVED_EXECUTED_ENTRY_SQL = f"""
{EXECUTED_ENTRY_SQL}
AND {RESOLVED_PNL_SQL} IS NOT NULL
"""


def remaining_entry_shares_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"COALESCE({prefix}remaining_entry_shares, {prefix}actual_entry_shares, "
        f"{prefix}source_shares, 0)"
    )


def remaining_entry_size_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"COALESCE({prefix}remaining_entry_size_usd, {prefix}actual_entry_size_usd, "
        f"{prefix}signal_size_usd, 0)"
    )


def remaining_source_shares_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"COALESCE({prefix}remaining_source_shares, {prefix}source_shares, 0)"


def resolved_pnl_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"COALESCE({prefix}actual_pnl_usd, {prefix}shadow_pnl_usd)"


def profitable_trade_expr(alias: str = "") -> str:
    return f"CASE WHEN {resolved_pnl_expr(alias)} > 0 THEN 1 ELSE 0 END"


def is_fill_aware_executed_buy(row: Any) -> bool:
    if row is None:
        return False
    return (
        not bool(_value(row, "skipped"))
        and str(_value(row, "source_action") or "buy").strip().lower() == "buy"
        and _value(row, "actual_entry_price") is not None
        and _value(row, "actual_entry_shares") is not None
        and _value(row, "actual_entry_size_usd") is not None
    )


def _value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)
