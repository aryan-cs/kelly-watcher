from __future__ import annotations

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
    spread = (
        (market_features.best_ask - market_features.best_bid) / market_features.mid
        if market_features.mid > 0
        else 1.0
    )
    momentum = (
        abs(market_features.mid - market_features.price_1h_ago) / market_features.price_1h_ago
        if market_features.price_1h_ago is not None and market_features.price_1h_ago > 0
        else None
    )
    volume_trend = (
        market_features.volume_24h_usd / market_features.volume_7d_avg_usd
        if (
            market_features.volume_24h_usd is not None
            and market_features.volume_7d_avg_usd is not None
            and market_features.volume_7d_avg_usd > 0
        )
        else None
    )

    return {
        "f_trader_win_rate": trader_features.win_rate,
        "f_trader_n_trades": trader_features.n_trades,
        "f_conviction_ratio": trader_features.conviction_ratio,
        "f_trader_volume_usd": trader_features.volume_usd,
        "f_trader_avg_size_usd": trader_features.avg_size_usd,
        "f_account_age_days": trader_features.account_age_d,
        "f_consistency": trader_features.consistency,
        "f_trader_diversity": trader_features.diversity,
        "f_days_to_res": market_features.days_to_res,
        "f_price": market_features.execution_price if market_features.execution_price > 0 else market_features.mid,
        "f_spread_pct": spread,
        "f_momentum_1h": momentum,
        "f_volume_24h_usd": market_features.volume_24h_usd,
        "f_volume_7d_avg_usd": market_features.volume_7d_avg_usd,
        "f_volume_trend": volume_trend,
        "f_oi_usd": market_features.oi_usd,
        "f_top_holder_pct": market_features.top_holder_pct,
        "f_bid_depth_usd": market_features.bid_depth_usd,
        "f_ask_depth_usd": market_features.ask_depth_usd,
    }
