from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from db import get_conn
from market_scorer import MarketFeatures
from trade_contract import is_fill_aware_executed_buy, resolved_pnl_expr
from trader_scorer import TraderFeatures

logger = logging.getLogger(__name__)

PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0
BELIEF_CACHE_TTL_SECONDS = 60.0
MAX_BELIEF_BLEND = 0.30
COUNTERFACTUAL_SKIP_WEIGHT = 0.35

FEATURE_WEIGHTS = {
    "confidence": 1.0,
    "trader_win_rate": 0.9,
    "conviction_ratio": 0.5,
    "consistency": 0.5,
    "price": 0.8,
    "days_to_res": 0.8,
    "spread_pct": 0.8,
    "momentum_1h": 0.5,
    "volume_trend": 0.6,
    "oi_usd": 0.4,
    "depth_ratio": 0.7,
}

_belief_cache: dict[tuple[str, str], tuple[float, float]] | None = None
_belief_cache_loaded_at = 0.0


@dataclass
class BeliefAdjustment:
    adjusted_confidence: float
    prior_confidence: float
    blend: float
    matched_buckets: int
    evidence: int


def sync_belief_priors() -> int:
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT
            id,
            skipped,
            skip_reason,
            signal_mode,
            market_veto,
            source_action,
            outcome,
            {resolved_pnl_expr()} AS resolved_pnl_usd,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            confidence,
            COALESCE(actual_entry_size_usd, signal_size_usd) AS effective_size_usd,
            f_trader_win_rate,
            f_conviction_ratio,
            f_consistency,
            f_days_to_res,
            f_price,
            f_spread_pct,
            f_momentum_1h,
            f_volume_trend,
            f_oi_usd,
            f_bid_depth_usd,
            f_ask_depth_usd
        FROM trade_log
        WHERE COALESCE(source_action, 'buy')='buy'
          AND (
              {resolved_pnl_expr()} IS NOT NULL
              OR outcome IS NOT NULL
          )
          AND id NOT IN (SELECT trade_log_id FROM belief_updates)
        ORDER BY id
        """
    ).fetchall()

    if not rows:
        conn.close()
        return 0

    now = int(time.time())
    applied = 0
    counterfactual = 0
    for row in rows:
        label_and_weight = _belief_label_and_weight(row)
        if label_and_weight is not None:
            outcome, weight = label_and_weight
            wins = weight if outcome == 1 else 0.0
            losses = weight if outcome == 0 else 0.0
            buckets = _feature_buckets_from_row(row)

            _apply_bucket_update(conn, "__global__", "all", wins, losses, now)
            for feature_name, bucket in buckets.items():
                _apply_bucket_update(conn, feature_name, bucket, wins, losses, now)
            applied += 1
            if weight < 0.999:
                counterfactual += 1

        conn.execute(
            "INSERT OR IGNORE INTO belief_updates (trade_log_id, applied_at) VALUES (?, ?)",
            (row["id"], now),
        )

    conn.commit()
    conn.close()
    invalidate_belief_cache()
    logger.info(
        "Applied belief updates for %s resolved trades (%s counterfactual)",
        applied,
        counterfactual,
    )
    return applied


def adjust_heuristic_confidence(
    base_confidence: float,
    trader_features: TraderFeatures,
    market_features: MarketFeatures,
) -> BeliefAdjustment:
    prior_map = _load_belief_map()
    global_entry = prior_map.get(("__global__", "all"))
    global_posterior, global_evidence = _posterior_and_evidence(global_entry)

    buckets = _feature_buckets_from_live_signal(base_confidence, trader_features, market_features)
    weighted_sum = 0.0
    total_weight = 0.0
    matched = 0
    evidence_sum = 0

    for feature_name, bucket in buckets.items():
        entry = prior_map.get((feature_name, bucket))
        posterior, evidence = _posterior_and_evidence(entry)
        if evidence <= 0:
            continue

        matched += 1
        evidence_sum += evidence
        weight = FEATURE_WEIGHTS.get(feature_name, 0.5) * min(1.0, evidence / 20.0)
        weighted_sum += posterior * weight
        total_weight += weight

    if matched == 0 and global_evidence <= 0:
        return BeliefAdjustment(
            adjusted_confidence=round(base_confidence, 4),
            prior_confidence=0.5,
            blend=0.0,
            matched_buckets=0,
            evidence=0,
        )

    if global_evidence > 0:
        global_weight = 0.4 * min(1.0, global_evidence / 50.0)
        weighted_sum += global_posterior * global_weight
        total_weight += global_weight
        evidence_sum += global_evidence

    prior_confidence = weighted_sum / total_weight if total_weight > 0 else global_posterior
    coverage = min(1.0, total_weight / max(sum(FEATURE_WEIGHTS.values()), 1e-6))
    blend = MAX_BELIEF_BLEND * coverage
    adjusted = (1 - blend) * base_confidence + blend * prior_confidence

    return BeliefAdjustment(
        adjusted_confidence=round(max(0.0, min(1.0, adjusted)), 4),
        prior_confidence=round(prior_confidence, 4),
        blend=round(blend, 4),
        matched_buckets=matched,
        evidence=evidence_sum,
    )


def invalidate_belief_cache() -> None:
    global _belief_cache, _belief_cache_loaded_at
    _belief_cache = None
    _belief_cache_loaded_at = 0.0


def _load_belief_map() -> dict[tuple[str, str], tuple[float, float]]:
    global _belief_cache, _belief_cache_loaded_at
    if _belief_cache is not None and (time.time() - _belief_cache_loaded_at) < BELIEF_CACHE_TTL_SECONDS:
        return _belief_cache

    conn = get_conn()
    rows = conn.execute(
        "SELECT feature_name, bucket, wins, losses FROM belief_priors"
    ).fetchall()
    conn.close()

    _belief_cache = {
        (str(row["feature_name"]), str(row["bucket"])): (float(row["wins"]), float(row["losses"]))
        for row in rows
    }
    _belief_cache_loaded_at = time.time()
    return _belief_cache


def _apply_bucket_update(
    conn,
    feature_name: str,
    bucket: str,
    wins: float,
    losses: float,
    now: int,
) -> None:
    conn.execute(
        """
        INSERT INTO belief_priors (feature_name, bucket, wins, losses, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(feature_name, bucket) DO UPDATE SET
            wins = belief_priors.wins + excluded.wins,
            losses = belief_priors.losses + excluded.losses,
            updated_at = excluded.updated_at
        """,
        (feature_name, bucket, wins, losses, now),
    )


def _posterior_and_evidence(entry: tuple[float, float] | None) -> tuple[float, int]:
    wins, losses = entry or (0.0, 0.0)
    evidence = int(wins + losses)
    posterior = (wins + PRIOR_ALPHA) / (wins + losses + PRIOR_ALPHA + PRIOR_BETA)
    return posterior, evidence


def _belief_label_and_weight(row) -> tuple[int, float] | None:
    if is_fill_aware_executed_buy(row):
        resolved_pnl = row["resolved_pnl_usd"]
        if resolved_pnl is None:
            return None
        return (1 if float(resolved_pnl) > 0 else 0, 1.0)

    if _is_counterfactual_signal_reject(row):
        outcome = row["outcome"]
        if outcome is None:
            return None
        return (1 if int(outcome) == 1 else 0, COUNTERFACTUAL_SKIP_WEIGHT)

    return None


def _is_counterfactual_signal_reject(row) -> bool:
    if not bool(row["skipped"]):
        return False
    if str(row["signal_mode"] or "").strip().lower() != "heuristic":
        return False
    if row["market_veto"] is not None:
        return False

    reason = str(row["skip_reason"] or "").strip().lower()
    if not reason:
        return False

    return (
        ("confidence was" in reason and "below the" in reason and "minimum" in reason)
        or ("heuristic score was" in reason and "below the" in reason and "minimum" in reason)
    )


def _feature_buckets_from_row(row) -> dict[str, str]:
    avg_depth = _average_depth(row["f_bid_depth_usd"], row["f_ask_depth_usd"])
    depth_ratio = _depth_ratio(row["effective_size_usd"], avg_depth)
    return {
        "confidence": _bucket_confidence(row["confidence"]),
        "trader_win_rate": _bucket_trader_win_rate(row["f_trader_win_rate"]),
        "conviction_ratio": _bucket_conviction(row["f_conviction_ratio"]),
        "consistency": _bucket_consistency(row["f_consistency"]),
        "price": _bucket_price(row["f_price"]),
        "days_to_res": _bucket_days_to_res(row["f_days_to_res"]),
        "spread_pct": _bucket_spread(row["f_spread_pct"]),
        "momentum_1h": _bucket_momentum(row["f_momentum_1h"]),
        "volume_trend": _bucket_volume_trend(row["f_volume_trend"]),
        "oi_usd": _bucket_oi_usd(row["f_oi_usd"]),
        "depth_ratio": _bucket_depth_ratio(depth_ratio),
    }


def _feature_buckets_from_live_signal(
    base_confidence: float,
    trader_features: TraderFeatures,
    market_features: MarketFeatures,
) -> dict[str, str]:
    avg_depth = _average_depth(market_features.bid_depth_usd, market_features.ask_depth_usd)
    depth_ratio = _depth_ratio(market_features.order_size_usd, avg_depth)
    return {
        "confidence": _bucket_confidence(base_confidence),
        "trader_win_rate": _bucket_trader_win_rate(trader_features.win_rate),
        "conviction_ratio": _bucket_conviction(trader_features.conviction_ratio),
        "consistency": _bucket_consistency(trader_features.consistency),
        "price": _bucket_price(market_features.execution_price if market_features.execution_price > 0 else market_features.mid),
        "days_to_res": _bucket_days_to_res(market_features.days_to_res),
        "spread_pct": _bucket_spread(
            (market_features.best_ask - market_features.best_bid) / market_features.mid
            if market_features.mid > 0
            else None
        ),
        "momentum_1h": _bucket_momentum(
            abs(market_features.mid - market_features.price_1h_ago) / market_features.price_1h_ago
            if market_features.price_1h_ago is not None and market_features.price_1h_ago > 0
            else None
        ),
        "volume_trend": _bucket_volume_trend(
            market_features.volume_24h_usd / market_features.volume_7d_avg_usd
            if (
                market_features.volume_24h_usd is not None
                and market_features.volume_7d_avg_usd is not None
                and market_features.volume_7d_avg_usd > 0
            )
            else None
        ),
        "oi_usd": _bucket_oi_usd(market_features.oi_usd),
        "depth_ratio": _bucket_depth_ratio(depth_ratio),
    }


def _average_depth(bid_depth: float | None, ask_depth: float | None) -> float | None:
    bid = float(bid_depth or 0.0)
    ask = float(ask_depth or 0.0)
    avg = (bid + ask) / 2
    return avg if avg > 0 else None


def _depth_ratio(size_usd: float | None, avg_depth: float | None) -> float | None:
    if size_usd is None or avg_depth is None or avg_depth <= 0:
        return None
    return float(size_usd) / avg_depth


def _bucket_confidence(value: float | None) -> str:
    return _bucket_numeric(value, [0.55, 0.60, 0.65, 0.70, 0.75, 0.80], "conf")


def _bucket_trader_win_rate(value: float | None) -> str:
    return _bucket_numeric(value, [0.45, 0.55, 0.65, 0.75, 0.85], "trader_wr")


def _bucket_conviction(value: float | None) -> str:
    return _bucket_numeric(value, [0.75, 1.0, 1.25, 1.5, 2.0], "conv")


def _bucket_consistency(value: float | None) -> str:
    return _bucket_numeric(value, [-0.5, 0.0, 0.25, 0.5, 1.0], "cons")


def _bucket_price(value: float | None) -> str:
    return _bucket_numeric(value, [0.10, 0.25, 0.40, 0.60, 0.75, 0.90], "price")


def _bucket_days_to_res(value: float | None) -> str:
    return _bucket_numeric(value, [1 / 24, 0.25, 1.0, 3.0, 7.0], "dtr")


def _bucket_spread(value: float | None) -> str:
    return _bucket_numeric(value, [0.01, 0.02, 0.04, 0.07, 0.10], "spread")


def _bucket_momentum(value: float | None) -> str:
    return _bucket_numeric(value, [0.01, 0.03, 0.05, 0.10], "mom")


def _bucket_volume_trend(value: float | None) -> str:
    return _bucket_numeric(value, [0.50, 0.80, 1.00, 1.30, 2.00], "voltrend")


def _bucket_oi_usd(value: float | None) -> str:
    return _bucket_numeric(value, [1_000, 10_000, 100_000, 1_000_000], "oi")


def _bucket_depth_ratio(value: float | None) -> str:
    return _bucket_numeric(value, [0.05, 0.10, 0.20, 0.40, 0.80, 1.20], "depth")


def _bucket_numeric(value: float | None, cutoffs: list[float], prefix: str) -> str:
    if value is None:
        return f"{prefix}:unknown"

    numeric = float(value)
    for cutoff in cutoffs:
        if numeric < cutoff:
            return f"{prefix}:<{_fmt(cutoff)}"
    return f"{prefix}:>={_fmt(cutoffs[-1])}"


def _fmt(value: float) -> str:
    if value >= 1000:
        return f"{int(value)}"
    return f"{value:.3f}".rstrip("0").rstrip(".")
