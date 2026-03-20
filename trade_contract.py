from __future__ import annotations

from typing import Any

DATA_CONTRACT_VERSION = 4
MODEL_LABEL_MODE = "expected_return_weighted_counterfactual_v1"
RESOLVED_PNL_SQL = "COALESCE(actual_pnl_usd, shadow_pnl_usd)"
REALIZED_CLOSE_TS_SQL = "COALESCE(exited_at, resolved_at, placed_at)"
PROFITABLE_TRADE_SQL = f"CASE WHEN {RESOLVED_PNL_SQL} > 0 THEN 1 ELSE 0 END"
OBSERVED_BUY_SQL = "COALESCE(source_action, 'buy')='buy'"
RESOLVED_OBSERVED_BUY_SQL = f"""
{OBSERVED_BUY_SQL}
AND {RESOLVED_PNL_SQL} IS NOT NULL
"""

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

TRAINABLE_SKIPPED_REASON_SQL = """
(
    LOWER(COALESCE(skip_reason, '')) LIKE 'signal confidence was %below the % minimum'
    OR LOWER(COALESCE(skip_reason, '')) LIKE 'confidence was %below the % minimum needed to place a trade'
    OR LOWER(COALESCE(skip_reason, '')) LIKE 'heuristic score was %below the % minimum needed to place a trade'
    OR LOWER(COALESCE(skip_reason, '')) LIKE 'model edge was %below the % threshold'
    OR LOWER(COALESCE(skip_reason, '')) = 'trade did not pass the signal checks'
    OR LOWER(COALESCE(skip_reason, '')) = 'kelly sizing found no positive edge at this price, so the trade was skipped'
)
"""

RESOLVED_TRAINABLE_SKIPPED_BUY_SQL = f"""
skipped=1
AND {OBSERVED_BUY_SQL}
AND counterfactual_return IS NOT NULL
AND {TRAINABLE_SKIPPED_REASON_SQL}
"""

RESOLVED_TRAINING_SAMPLE_SQL = f"""
(
    {RESOLVED_EXECUTED_ENTRY_SQL}
)
OR
(
    {RESOLVED_TRAINABLE_SKIPPED_BUY_SQL}
)
"""

TRAINING_LABEL_SQL = f"""
CASE
    WHEN skipped=1 THEN CASE WHEN COALESCE(counterfactual_return, 0) > 0 THEN 1 ELSE 0 END
    ELSE {PROFITABLE_TRADE_SQL}
END
"""

TRAINING_RETURN_SQL = f"""
CASE
    WHEN skipped=1 THEN counterfactual_return
    ELSE {RESOLVED_PNL_SQL} / NULLIF(COALESCE(actual_entry_size_usd, signal_size_usd), 0)
END
"""

TRAINING_OUTCOME_SQL = f"""
CASE
    WHEN {TRAINING_RETURN_SQL} > 0 THEN 1
    ELSE 0
END
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
    try:
        return row[key]
    except Exception:
        pass
    return getattr(row, key, None)
