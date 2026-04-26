from __future__ import annotations

import math
from typing import Any

from kelly_watcher.engine.market_scorer import MarketFeatures
from kelly_watcher.engine.trader_scorer import TraderFeatures

FEATURE_COLS = [
    "f_trader_win_rate",
    "f_trader_n_trades",
    "f_conviction_ratio",
    "f_trader_volume_usd",
    "f_trader_avg_size_usd",
    "f_account_age_days",
    "f_consistency",
    "f_trader_diversity",
    "f_days_to_res",
    "f_price",
    "f_spread_pct",
    "f_momentum_1h",
    "f_volume_24h_usd",
    "f_volume_7d_avg_usd",
    "f_volume_trend",
    "f_oi_usd",
    "f_top_holder_pct",
    "f_bid_depth_usd",
    "f_ask_depth_usd",
]

LABEL_COL = "label"
OUTCOME_COL = "outcome_label"
RETURN_COL = "economic_return"
SAMPLE_WEIGHT_COL = "sample_weight"


def build_feature_map(
    trader_features: TraderFeatures,
    market_features: MarketFeatures,
) -> dict[str, float | None]:
    best_bid = _finite_float(getattr(market_features, "best_bid", None))
    best_ask = _finite_float(getattr(market_features, "best_ask", None))
    mid = _finite_float(getattr(market_features, "mid", None))
    execution_price = _finite_float(getattr(market_features, "execution_price", None))
    price_1h_ago = _finite_float(getattr(market_features, "price_1h_ago", None))
    volume_24h_usd = _finite_float(getattr(market_features, "volume_24h_usd", None))
    volume_7d_avg_usd = _finite_float(getattr(market_features, "volume_7d_avg_usd", None))

    spread = (
        (best_ask - best_bid) / mid
        if best_ask is not None and best_bid is not None and mid is not None and mid > 0
        else None
    )
    momentum = (
        abs(mid - price_1h_ago) / price_1h_ago
        if mid is not None and price_1h_ago is not None and price_1h_ago > 0
        else None
    )
    volume_trend = (
        volume_24h_usd / volume_7d_avg_usd
        if volume_24h_usd is not None and volume_7d_avg_usd is not None and volume_7d_avg_usd > 0
        else None
    )
    price = execution_price if execution_price is not None and execution_price > 0 else mid

    return {
        "f_trader_win_rate": _finite_float(getattr(trader_features, "win_rate", None)),
        "f_trader_n_trades": _finite_float(getattr(trader_features, "n_trades", None)),
        "f_conviction_ratio": _finite_float(getattr(trader_features, "conviction_ratio", None)),
        "f_trader_volume_usd": _finite_float(getattr(trader_features, "volume_usd", None)),
        "f_trader_avg_size_usd": _finite_float(getattr(trader_features, "avg_size_usd", None)),
        "f_account_age_days": _finite_float(getattr(trader_features, "account_age_d", None)),
        "f_consistency": _finite_float(getattr(trader_features, "consistency", None)),
        "f_trader_diversity": _finite_float(getattr(trader_features, "diversity", None)),
        "f_days_to_res": _finite_float(getattr(market_features, "days_to_res", None)),
        "f_price": price,
        "f_spread_pct": spread,
        "f_momentum_1h": momentum,
        "f_volume_24h_usd": volume_24h_usd,
        "f_volume_7d_avg_usd": volume_7d_avg_usd,
        "f_volume_trend": volume_trend,
        "f_oi_usd": _finite_float(getattr(market_features, "oi_usd", None)),
        "f_top_holder_pct": _finite_float(getattr(market_features, "top_holder_pct", None)),
        "f_bid_depth_usd": _finite_float(getattr(market_features, "bid_depth_usd", None)),
        "f_ask_depth_usd": _finite_float(getattr(market_features, "ask_depth_usd", None)),
    }


def _finite_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None
