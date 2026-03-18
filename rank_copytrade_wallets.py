from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import httpx

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT_SECONDS = 15.0
RETRY_BASE_DELAY_SECONDS = 0.5
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class LeaderboardEntry:
    address: str
    username: str
    rank: int | None
    pnl_usd: float
    volume_usd: float
    verified: bool


@dataclass
class PerformanceMetrics:
    closed_positions: int
    wins: int
    ties: int
    shrunk_win_rate: float
    realized_pnl_usd: float
    total_bought_usd: float
    roi: float
    avg_return: float
    consistency: float
    avg_position_size_usd: float
    account_age_days: int


@dataclass
class TradeTimingMetrics:
    last_trade_ts: int
    recent_trade_count: int
    recent_buy_count: int
    recent_buy_volume_usd: float
    avg_recent_buy_size_usd: float | None
    large_buy_count: int
    large_buy_ratio: float | None
    conviction_buy_count: int
    conviction_buy_ratio: float | None
    lead_sample_count: int
    median_buy_lead_seconds: float | None
    p25_buy_lead_seconds: float | None
    late_buy_ratio: float | None


@dataclass
class RankedWallet:
    address: str
    username: str
    style: str
    follow_score: float
    accepted: bool
    reject_reason: str
    leaderboard_rank: int | None
    leaderboard_pnl_usd: float
    leaderboard_volume_usd: float
    closed_positions: int
    win_rate: float
    roi: float
    realized_pnl_usd: float
    recent_trades: int
    recent_buys: int
    avg_recent_buy_size_usd: float | None
    large_buy_ratio: float | None
    conviction_buy_ratio: float | None
    copyability_score: float
    last_trade_age_hours: float | None
    median_buy_lead_hours: float | None
    p25_buy_lead_hours: float | None
    late_buy_ratio: float | None


def _request_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    failure_log: str,
    attempts: int = 4,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.get(url, params=params)
            if response.status_code in RETRY_STATUSES and attempt < attempts - 1:
                delay = RETRY_BASE_DELAY_SECONDS * (attempt + 1)
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                delay = RETRY_BASE_DELAY_SECONDS * (attempt + 1)
                time.sleep(delay)
                continue
    raise RuntimeError(f"{failure_log}: {last_error}")


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_timestamp(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts // 1000 if ts > 10_000_000_000 else ts
    text = str(value).strip()
    if not text:
        return 0
    try:
        ts = int(float(text))
        return ts // 1000 if ts > 10_000_000_000 else ts
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text.ljust(width)
    if width <= 3:
        return text[:width]
    return f"{text[:width - 3]}..."


def _fit_right(text: str, width: int) -> str:
    return _fit(text, width).rjust(width)


def _short_wallet(address: str) -> str:
    if len(address) <= 14:
        return address
    return f"{address[:8]}...{address[-4:]}"


def _format_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.{digits}f}%"


def _format_usd(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}${value:,.0f}"


def _format_usd_plain(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${value:,.0f}"


def _format_hours(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 24:
        return f"{value / 24:.1f}d"
    return f"{value:.1f}h"


def _time_period_to_legacy_window(value: str) -> str:
    mapping = {
        "DAY": "1d",
        "WEEK": "1w",
        "MONTH": "1m",
        "ALL": "all",
    }
    return mapping[value]


def _trade_size_usd(row: dict[str, Any]) -> float:
    size_usd = _safe_float(row.get("sizeUsd") or row.get("usdc_size") or row.get("amount"))
    if size_usd > 0:
        return size_usd

    shares = _safe_float(row.get("size") or row.get("shares"))
    price = _trade_price(row)
    if shares > 0 and price > 0:
        return shares * price
    return 0.0


def _trade_price(row: dict[str, Any]) -> float:
    price = _safe_float(row.get("price") or row.get("outcomePrice"))
    return price if 0.0 < price < 1.0 else 0.0


def fetch_leaderboard(
    client: httpx.Client,
    *,
    category: str,
    time_period: str,
    order_by: str,
    per_page: int,
    pages: int,
) -> list[LeaderboardEntry]:
    rows: list[LeaderboardEntry] = []
    seen: set[str] = set()

    for page in range(pages):
        offset = page * per_page
        payload: Any
        try:
            payload = _request_json(
                client,
                f"{DATA_API}/v1/leaderboard",
                params={
                    "category": category,
                    "timePeriod": time_period,
                    "orderBy": order_by,
                    "limit": per_page,
                    "offset": offset,
                },
                failure_log="Leaderboard fetch failed",
            )
        except RuntimeError:
            payload = _request_json(
                client,
                f"{DATA_API}/leaderboard",
                params={
                    "window": _time_period_to_legacy_window(time_period),
                    "limit": per_page,
                },
                failure_log="Legacy leaderboard fetch failed",
            )

        batch = payload if isinstance(payload, list) else payload.get("users", [])
        if not batch:
            break

        for item in batch:
            if not isinstance(item, dict):
                continue
            address = str(item.get("proxyWallet") or item.get("address") or "").strip().lower()
            if not address or address in seen:
                continue
            seen.add(address)
            rank = _safe_int(item.get("rank"))
            rows.append(
                LeaderboardEntry(
                    address=address,
                    username=str(item.get("userName") or item.get("username") or "").strip() or "-",
                    rank=rank if rank > 0 else None,
                    pnl_usd=_safe_float(item.get("pnl") or item.get("profit")),
                    volume_usd=_safe_float(item.get("vol") or item.get("volume")),
                    verified=bool(item.get("verifiedBadge") or item.get("verified")),
                )
            )

        if len(batch) < per_page:
            break

    return rows


def fetch_recent_trades(client: httpx.Client, wallet: str, *, limit: int) -> list[dict[str, Any]]:
    payload = _request_json(
        client,
        f"{DATA_API}/trades",
        params={"user": wallet, "limit": limit},
        failure_log=f"Trade fetch failed for {wallet[:10]}",
    )
    rows = payload if isinstance(payload, list) else payload.get("trades", [])
    return [row for row in rows if isinstance(row, dict)]


def fetch_closed_positions(
    client: httpx.Client,
    wallet: str,
    *,
    page_limit: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for page in range(max_pages):
        payload = _request_json(
            client,
            f"{DATA_API}/closed-positions",
            params={
                "user": wallet,
                "limit": page_limit,
                "offset": page * page_limit,
            },
            failure_log=f"Closed positions fetch failed for {wallet[:10]}",
        )
        batch = payload if isinstance(payload, list) else payload.get("positions", [])
        if not batch:
            break

        added = 0
        for item in batch:
            if not isinstance(item, dict):
                continue
            key = "|".join(
                [
                    str(item.get("conditionId") or "").strip().lower(),
                    str(item.get("outcome") or "").strip().lower(),
                    str(item.get("timestamp") or "").strip(),
                    str(item.get("realizedPnl") or "").strip(),
                ]
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append(item)
            added += 1

        if len(batch) < page_limit or added == 0:
            break

    return rows


def fetch_market_close_ts(
    client: httpx.Client,
    condition_id: str,
    cache: dict[str, int],
) -> int:
    normalized = str(condition_id or "").strip().lower()
    if not normalized:
        return 0
    if normalized in cache:
        return cache[normalized]

    payload = _request_json(
        client,
        f"{GAMMA_API}/markets",
        params={"condition_ids": normalized},
        failure_log=f"Market metadata fetch failed for {normalized[:12]}",
    )
    markets = payload if isinstance(payload, list) else payload.get("markets", [])
    close_ts = 0
    for market in markets:
        if not isinstance(market, dict):
            continue
        market_condition = str(market.get("conditionId") or "").strip().lower()
        if market_condition and market_condition != normalized:
            continue
        close_ts = _normalize_timestamp(
            market.get("endDate") or market.get("closeTime") or market.get("closedTime")
        )
        if close_ts > 0:
            break
    cache[normalized] = close_ts
    return close_ts


def compute_performance_metrics(closed_positions: list[dict[str, Any]], *, now_ts: int) -> PerformanceMetrics:
    wins = 0
    ties = 0
    realized_pnl_total = 0.0
    total_bought_total = 0.0
    returns: list[float] = []
    sizes: list[float] = []
    timestamps: list[int] = []

    for row in closed_positions:
        realized_pnl = _safe_float(row.get("realizedPnl"))
        total_bought = max(_safe_float(row.get("totalBought")), 0.0)
        timestamp = _safe_int(row.get("timestamp"))
        if realized_pnl > 0:
            wins += 1
        elif abs(realized_pnl) < 1e-9:
            ties += 1
        realized_pnl_total += realized_pnl
        total_bought_total += total_bought
        if total_bought > 0:
            sizes.append(total_bought)
            returns.append(max(-1.0, min(3.0, realized_pnl / total_bought)))
        if timestamp > 0:
            timestamps.append(timestamp)

    n_positions = len(closed_positions)
    prior = 20.0
    shrunk_win_rate = (wins + 0.5 * ties + 0.5 * prior) / (n_positions + prior) if n_positions >= 0 else 0.5
    avg_return = statistics.fmean(returns) if returns else 0.0
    std_dev = statistics.pstdev(returns) if len(returns) > 1 else abs(avg_return)
    consistency = (avg_return / (std_dev + 1e-6)) if returns else 0.0
    avg_size = statistics.fmean(sizes) if sizes else 0.0
    roi = (realized_pnl_total / total_bought_total) if total_bought_total > 0 else 0.0
    age_days = int((now_ts - min(timestamps)) / 86400) if timestamps else 0

    return PerformanceMetrics(
        closed_positions=n_positions,
        wins=wins,
        ties=ties,
        shrunk_win_rate=shrunk_win_rate,
        realized_pnl_usd=realized_pnl_total,
        total_bought_usd=total_bought_total,
        roi=roi,
        avg_return=avg_return,
        consistency=consistency,
        avg_position_size_usd=avg_size,
        account_age_days=age_days,
    )


def compute_trade_timing_metrics(
    client: httpx.Client,
    trades: list[dict[str, Any]],
    *,
    now_ts: int,
    activity_window_days: int,
    late_buy_threshold_seconds: int,
    large_buy_threshold_usd: float,
    high_conviction_price: float,
    buy_sample_limit: int,
    market_close_cache: dict[str, int],
) -> TradeTimingMetrics:
    trade_timestamps = [_safe_int(row.get("timestamp")) for row in trades]
    last_trade_ts = max((ts for ts in trade_timestamps if ts > 0), default=0)
    recent_cutoff = now_ts - (activity_window_days * 86400)
    recent_trade_count = sum(1 for ts in trade_timestamps if ts >= recent_cutoff)

    buys = [
        row for row in trades
        if str(row.get("side") or row.get("tradeSide") or "").strip().upper() == "BUY"
    ]
    recent_buys = [row for row in buys if _safe_int(row.get("timestamp")) >= recent_cutoff]
    recent_buy_count = len(recent_buys)
    recent_buy_sizes = [_trade_size_usd(row) for row in recent_buys]
    recent_buy_sizes = [size for size in recent_buy_sizes if size > 0]
    recent_buy_volume_usd = float(sum(recent_buy_sizes))
    avg_recent_buy_size_usd = statistics.fmean(recent_buy_sizes) if recent_buy_sizes else None
    large_buy_count = sum(1 for size in recent_buy_sizes if size >= large_buy_threshold_usd)
    large_buy_ratio = (large_buy_count / recent_buy_count) if recent_buy_count > 0 else None
    conviction_buy_count = sum(1 for row in recent_buys if _trade_price(row) >= high_conviction_price)
    conviction_buy_ratio = (conviction_buy_count / recent_buy_count) if recent_buy_count > 0 else None

    lead_seconds: list[float] = []
    for row in buys[:buy_sample_limit]:
        trade_ts = _safe_int(row.get("timestamp"))
        condition_id = str(row.get("conditionId") or row.get("condition_id") or "").strip()
        if trade_ts <= 0 or not condition_id:
            continue
        try:
            close_ts = fetch_market_close_ts(client, condition_id, market_close_cache)
        except Exception:
            continue
        if close_ts <= trade_ts:
            continue
        lead_seconds.append(float(close_ts - trade_ts))

    median_lead = statistics.median(lead_seconds) if lead_seconds else None
    p25_lead = _quantile(lead_seconds, 0.25)
    late_ratio = (
        sum(1 for value in lead_seconds if value < late_buy_threshold_seconds) / len(lead_seconds)
        if lead_seconds
        else None
    )

    return TradeTimingMetrics(
        last_trade_ts=last_trade_ts,
        recent_trade_count=recent_trade_count,
        recent_buy_count=recent_buy_count,
        recent_buy_volume_usd=recent_buy_volume_usd,
        avg_recent_buy_size_usd=avg_recent_buy_size_usd,
        large_buy_count=large_buy_count,
        large_buy_ratio=large_buy_ratio,
        conviction_buy_count=conviction_buy_count,
        conviction_buy_ratio=conviction_buy_ratio,
        lead_sample_count=len(lead_seconds),
        median_buy_lead_seconds=median_lead,
        p25_buy_lead_seconds=p25_lead,
        late_buy_ratio=late_ratio,
    )


def describe_style(timing: TradeTimingMetrics) -> str:
    median = timing.median_buy_lead_seconds or 0.0
    if median >= 24 * 3600:
        horizon = "swing"
    elif median >= 6 * 3600:
        horizon = "medium-horizon"
    elif median >= 1800:
        horizon = "short-horizon"
    elif median >= 600:
        horizon = "fast-copyable"
    else:
        horizon = "last-minute"
    if timing.recent_buy_count >= 15 or timing.recent_trade_count >= 24:
        activity = "hyperactive"
    elif timing.recent_buy_count >= 8 or timing.recent_trade_count >= 14:
        activity = "active"
    elif timing.recent_buy_count >= 4 or timing.recent_trade_count >= 8:
        activity = "steady"
    else:
        activity = "selective"
    return f"{activity} {horizon}"


def _score_win_rate(win_rate: float, closed_positions: int) -> float:
    evidence_weight = closed_positions / (closed_positions + 25.0) if closed_positions > 0 else 0.0
    shrunk = 0.5 + (win_rate - 0.5) * evidence_weight
    return _clip(shrunk)


def _score_roi(roi: float) -> float:
    if roi <= -0.25:
        return 0.0
    if roi >= 0.50:
        return 1.0
    return _clip((roi + 0.25) / 0.75)


def _score_consistency(consistency: float) -> float:
    return _clip((consistency + 0.5) / 2.0)


def _score_sample(count: int) -> float:
    return _clip(math.log1p(max(count, 0)) / math.log1p(200))


def _score_recency(now_ts: int, last_trade_ts: int, max_days_since_last_trade: int) -> float:
    if last_trade_ts <= 0:
        return 0.0
    age_seconds = max(0, now_ts - last_trade_ts)
    max_age_seconds = max_days_since_last_trade * 86400
    if age_seconds >= max_age_seconds:
        return 0.0
    return _clip(1.0 - (age_seconds / max_age_seconds))


def _score_activity(recent_trades: int, recent_buys: int, activity_window_days: int) -> float:
    window_days = max(activity_window_days, 1)
    trades_per_day = recent_trades / window_days
    buys_per_day = recent_buys / window_days
    trade_flow_score = _clip(trades_per_day / 6.0)
    buy_flow_score = _clip(buys_per_day / 3.0)
    return (0.45 * trade_flow_score) + (0.55 * buy_flow_score)


def _score_lead_time(seconds: float | None) -> float:
    if seconds is None or seconds <= 0:
        return 0.0
    return _clip(math.log1p(seconds) / math.log1p(3 * 86400))


def _score_avg_buy_size(avg_buy_size_usd: float | None, large_buy_threshold_usd: float) -> float:
    if avg_buy_size_usd is None or avg_buy_size_usd <= 0:
        return 0.0
    scale = max(large_buy_threshold_usd * 8.0, 1.0)
    return _clip(math.log1p(avg_buy_size_usd) / math.log1p(scale))


def _score_ratio(value: float | None) -> float:
    if value is None:
        return 0.0
    return _clip(value)


def _score_leaderboard_signal(pnl_usd: float, volume_usd: float) -> float:
    pnl_score = _clip(math.log1p(max(pnl_usd, 0.0)) / math.log1p(1_000_000))
    volume_score = _clip(math.log1p(max(volume_usd, 0.0)) / math.log1p(10_000_000))
    return (0.65 * pnl_score) + (0.35 * volume_score)


def build_ranked_wallet(
    entry: LeaderboardEntry,
    performance: PerformanceMetrics,
    timing: TradeTimingMetrics,
    *,
    now_ts: int,
    activity_window_days: int,
    min_closed_positions: int,
    min_recent_trades: int,
    min_recent_buys: int,
    min_lead_samples: int,
    min_median_lead_seconds: int,
    max_late_buy_ratio: float,
    max_days_since_last_trade: int,
    min_avg_buy_size_usd: float,
    min_large_buy_count: int,
    min_conviction_buy_ratio: float,
    large_buy_threshold_usd: float,
) -> RankedWallet:
    reasons: list[str] = []
    if performance.closed_positions < min_closed_positions:
        reasons.append(f"closed<{min_closed_positions}")
    if timing.recent_trade_count < min_recent_trades:
        reasons.append(f"recent_trades<{min_recent_trades}")
    if timing.recent_buy_count < min_recent_buys:
        reasons.append(f"recent_buys<{min_recent_buys}")
    if timing.lead_sample_count < min_lead_samples:
        reasons.append(f"lead_samples<{min_lead_samples}")
    if timing.last_trade_ts <= 0 or (now_ts - timing.last_trade_ts) > (max_days_since_last_trade * 86400):
        reasons.append(f"stale>{max_days_since_last_trade}d")
    if timing.median_buy_lead_seconds is None or timing.median_buy_lead_seconds < min_median_lead_seconds:
        reasons.append("median_lead_too_short")
    if timing.late_buy_ratio is None or timing.late_buy_ratio > max_late_buy_ratio:
        reasons.append(f"late_buy_ratio>{max_late_buy_ratio:.0%}")
    if timing.avg_recent_buy_size_usd is None or timing.avg_recent_buy_size_usd < min_avg_buy_size_usd:
        reasons.append(f"avg_buy_size<${min_avg_buy_size_usd:.0f}")
    if timing.large_buy_count < min_large_buy_count:
        reasons.append(f"large_buys<{min_large_buy_count}")
    if timing.conviction_buy_ratio is None or timing.conviction_buy_ratio < min_conviction_buy_ratio:
        reasons.append(f"conviction_ratio<{min_conviction_buy_ratio:.0%}")

    success_score = (
        0.45 * _score_win_rate(performance.shrunk_win_rate, performance.closed_positions)
        + 0.25 * _score_roi(performance.roi)
        + 0.15 * _score_consistency(performance.consistency)
        + 0.15 * _score_sample(performance.closed_positions)
    )
    activity_score = (
        0.70 * _score_activity(timing.recent_trade_count, timing.recent_buy_count, activity_window_days)
        + 0.30 * _score_recency(now_ts, timing.last_trade_ts, max_days_since_last_trade)
    )
    timing_score = (
        0.45 * _score_lead_time(timing.median_buy_lead_seconds)
        + 0.20 * _score_lead_time(timing.p25_buy_lead_seconds)
        + 0.35 * (1.0 - min(1.0, timing.late_buy_ratio if timing.late_buy_ratio is not None else 1.0))
    )
    copyability_score = (
        0.40 * _score_avg_buy_size(timing.avg_recent_buy_size_usd, large_buy_threshold_usd)
        + 0.25 * _score_ratio(timing.large_buy_ratio)
        + 0.25 * _score_ratio(timing.conviction_buy_ratio)
        + 0.10 * (1.0 - min(1.0, timing.late_buy_ratio if timing.late_buy_ratio is not None else 1.0))
    )
    follow_score = (
        0.30 * success_score
        + 0.20 * activity_score
        + 0.25 * timing_score
        + 0.20 * copyability_score
        + 0.05 * _score_leaderboard_signal(entry.pnl_usd, entry.volume_usd)
    )

    last_trade_age_hours = ((now_ts - timing.last_trade_ts) / 3600) if timing.last_trade_ts > 0 else None
    return RankedWallet(
        address=entry.address,
        username=entry.username,
        style=describe_style(timing),
        follow_score=round(follow_score, 4),
        accepted=not reasons,
        reject_reason=", ".join(reasons),
        leaderboard_rank=entry.rank,
        leaderboard_pnl_usd=entry.pnl_usd,
        leaderboard_volume_usd=entry.volume_usd,
        closed_positions=performance.closed_positions,
        win_rate=performance.shrunk_win_rate,
        roi=performance.roi,
        realized_pnl_usd=performance.realized_pnl_usd,
        recent_trades=timing.recent_trade_count,
        recent_buys=timing.recent_buy_count,
        avg_recent_buy_size_usd=timing.avg_recent_buy_size_usd,
        large_buy_ratio=timing.large_buy_ratio,
        conviction_buy_ratio=timing.conviction_buy_ratio,
        copyability_score=round(copyability_score, 4),
        last_trade_age_hours=last_trade_age_hours,
        median_buy_lead_hours=(timing.median_buy_lead_seconds / 3600) if timing.median_buy_lead_seconds is not None else None,
        p25_buy_lead_hours=(timing.p25_buy_lead_seconds / 3600) if timing.p25_buy_lead_seconds is not None else None,
        late_buy_ratio=timing.late_buy_ratio,
    )


def analyze_wallet(
    entry: LeaderboardEntry,
    *,
    trade_limit: int,
    closed_page_limit: int,
    closed_pages: int,
    activity_window_days: int,
    late_buy_threshold_seconds: int,
    buy_sample_limit: int,
    min_closed_positions: int,
    min_recent_trades: int,
    min_recent_buys: int,
    min_lead_samples: int,
    min_median_lead_seconds: int,
    max_late_buy_ratio: float,
    max_days_since_last_trade: int,
    min_avg_buy_size_usd: float,
    min_large_buy_count: int,
    min_conviction_buy_ratio: float,
    large_buy_threshold_usd: float,
    high_conviction_price: float,
    market_close_cache: dict[str, int],
) -> RankedWallet:
    now_ts = int(time.time())
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
        trades = fetch_recent_trades(client, entry.address, limit=trade_limit)
        closed_positions = fetch_closed_positions(
            client,
            entry.address,
            page_limit=closed_page_limit,
            max_pages=closed_pages,
        )
        performance = compute_performance_metrics(closed_positions, now_ts=now_ts)
        timing = compute_trade_timing_metrics(
            client,
            trades,
            now_ts=now_ts,
            activity_window_days=activity_window_days,
            late_buy_threshold_seconds=late_buy_threshold_seconds,
            large_buy_threshold_usd=large_buy_threshold_usd,
            high_conviction_price=high_conviction_price,
            buy_sample_limit=buy_sample_limit,
            market_close_cache=market_close_cache,
        )
    return build_ranked_wallet(
        entry,
        performance,
        timing,
        now_ts=now_ts,
        activity_window_days=activity_window_days,
        min_closed_positions=min_closed_positions,
        min_recent_trades=min_recent_trades,
        min_recent_buys=min_recent_buys,
        min_lead_samples=min_lead_samples,
        min_median_lead_seconds=min_median_lead_seconds,
        max_late_buy_ratio=max_late_buy_ratio,
        max_days_since_last_trade=max_days_since_last_trade,
        min_avg_buy_size_usd=min_avg_buy_size_usd,
        min_large_buy_count=min_large_buy_count,
        min_conviction_buy_ratio=min_conviction_buy_ratio,
        large_buy_threshold_usd=large_buy_threshold_usd,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank Polymarket leaderboard wallets by copy-tradability."
    )
    parser.add_argument("--category", default="OVERALL")
    parser.add_argument("--time-period", choices=["DAY", "WEEK", "MONTH", "ALL"], default="WEEK")
    parser.add_argument("--order-by", choices=["PNL", "VOL"], default="VOL")
    parser.add_argument("--leaderboard-pages", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=25)
    parser.add_argument("--trade-limit", type=int, default=40)
    parser.add_argument("--closed-page-limit", type=int, default=50)
    parser.add_argument("--closed-pages", type=int, default=4)
    parser.add_argument("--activity-window-days", type=int, default=3)
    parser.add_argument("--late-buy-threshold-minutes", type=int, default=20)
    parser.add_argument("--buy-sample-limit", type=int, default=12)
    parser.add_argument("--min-closed-positions", type=int, default=10)
    parser.add_argument("--min-recent-trades", type=int, default=10)
    parser.add_argument("--min-recent-buys", type=int, default=3)
    parser.add_argument("--min-lead-samples", type=int, default=2)
    parser.add_argument("--min-median-lead-hours", type=float, default=2.0)
    parser.add_argument("--max-late-buy-ratio", type=float, default=0.25)
    parser.add_argument("--max-days-since-last-trade", type=int, default=2)
    parser.add_argument("--large-buy-usd", type=float, default=150.0)
    parser.add_argument("--min-large-buys", type=int, default=2)
    parser.add_argument("--high-conviction-price", type=float, default=0.80)
    parser.add_argument("--min-conviction-buy-ratio", type=float, default=0.30)
    parser.add_argument("--min-avg-buy-size-usd", type=float, default=100.0)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--show-rejected", action="store_true")
    parser.add_argument("--wallets-only", action="store_true")
    parser.add_argument("--json-out")
    return parser.parse_args(argv)


def print_ranked_wallets(rows: list[RankedWallet], *, wallets_only: bool) -> None:
    if wallets_only:
        for row in rows:
            print(row.address)
        return

    headers = [
        ("#", 3),
        ("score", 7),
        ("wallet", 16),
        ("user", 18),
        ("style", 20),
        ("closed", 7),
        ("wr", 7),
        ("roi", 7),
        ("buys", 5),
        ("avg$", 8),
        ("conv", 7),
        ("copy", 7),
        ("lead", 7),
        ("late", 7),
        ("pnl", 10),
    ]
    print(" ".join(_fit(name, width) for name, width in headers))
    for index, row in enumerate(rows, start=1):
        values = [
            (str(index), 3),
            (f"{row.follow_score:.3f}", 7),
            (_short_wallet(row.address), 16),
            (row.username, 18),
            (row.style, 20),
            (str(row.closed_positions), 7),
            (_format_pct(row.win_rate), 7),
            (_format_pct(row.roi), 7),
            (str(row.recent_buys), 5),
            (_format_usd_plain(row.avg_recent_buy_size_usd), 8),
            (_format_pct(row.conviction_buy_ratio), 7),
            (f"{row.copyability_score:.3f}", 7),
            (_format_hours(row.median_buy_lead_hours), 7),
            (_format_pct(row.late_buy_ratio), 7),
            (_format_usd(row.realized_pnl_usd), 10),
        ]
        print(" ".join(_fit(value, width) for value, width in values))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    market_close_cache: dict[str, int] = {}

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
        leaderboard = fetch_leaderboard(
            client,
            category=args.category,
            time_period=args.time_period,
            order_by=args.order_by,
            per_page=args.per_page,
            pages=args.leaderboard_pages,
        )

    if not leaderboard:
        print("No leaderboard wallets returned.")
        return 1

    ranked: list[RankedWallet] = []
    for entry in leaderboard:
        try:
            ranked.append(
                analyze_wallet(
                    entry,
                    trade_limit=args.trade_limit,
                    closed_page_limit=args.closed_page_limit,
                    closed_pages=args.closed_pages,
                    activity_window_days=args.activity_window_days,
                    late_buy_threshold_seconds=args.late_buy_threshold_minutes * 60,
                    buy_sample_limit=args.buy_sample_limit,
                    min_closed_positions=args.min_closed_positions,
                    min_recent_trades=args.min_recent_trades,
                    min_recent_buys=args.min_recent_buys,
                    min_lead_samples=args.min_lead_samples,
                    min_median_lead_seconds=int(args.min_median_lead_hours * 3600),
                    max_late_buy_ratio=args.max_late_buy_ratio,
                    max_days_since_last_trade=args.max_days_since_last_trade,
                    min_avg_buy_size_usd=args.min_avg_buy_size_usd,
                    min_large_buy_count=args.min_large_buys,
                    min_conviction_buy_ratio=args.min_conviction_buy_ratio,
                    large_buy_threshold_usd=args.large_buy_usd,
                    high_conviction_price=args.high_conviction_price,
                    market_close_cache=market_close_cache,
                )
            )
        except Exception as exc:
            ranked.append(
                RankedWallet(
                    address=entry.address,
                    username=entry.username,
                    style="error",
                    follow_score=0.0,
                    accepted=False,
                    reject_reason=f"analysis_failed: {exc}",
                    leaderboard_rank=entry.rank,
                    leaderboard_pnl_usd=entry.pnl_usd,
                    leaderboard_volume_usd=entry.volume_usd,
                    closed_positions=0,
                    win_rate=0.0,
                    roi=0.0,
                    realized_pnl_usd=0.0,
                    recent_trades=0,
                    recent_buys=0,
                    avg_recent_buy_size_usd=None,
                    large_buy_ratio=None,
                    conviction_buy_ratio=None,
                    copyability_score=0.0,
                    last_trade_age_hours=None,
                    median_buy_lead_hours=None,
                    p25_buy_lead_hours=None,
                    late_buy_ratio=None,
                )
            )

    accepted = sorted(
        [row for row in ranked if row.accepted],
        key=lambda row: row.follow_score,
        reverse=True,
    )
    rejected = sorted(
        [row for row in ranked if not row.accepted],
        key=lambda row: row.follow_score,
        reverse=True,
    )
    selected = accepted[: max(0, args.top)]

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "accepted": [asdict(row) for row in accepted],
                    "rejected": [asdict(row) for row in rejected],
                    "selected_wallets": [row.address for row in selected],
                },
                handle,
                indent=2,
            )

    print_ranked_wallets(selected, wallets_only=args.wallets_only)

    if not args.wallets_only:
        print("")
        print(f"Accepted: {len(accepted)} / {len(ranked)}")
        print(f"WATCHED_WALLETS={','.join(row.address for row in selected)}")
        if args.show_rejected and rejected:
            print("")
            print("Rejected:")
            for row in rejected[:20]:
                print(f"- {row.address} ({row.username}): {row.reject_reason}")

    return 0 if selected else 1


if __name__ == "__main__":
    raise SystemExit(main())
