from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

from config import max_market_horizon_label, max_market_horizon_seconds, min_execution_window_seconds, poll_interval

EXECUTION_BUFFER_SECONDS = 5.0


@dataclass
class MarketFeatures:
    best_bid: float
    best_ask: float
    mid: float
    execution_price: float
    bid_depth_usd: float
    ask_depth_usd: float
    days_to_res: float
    price_1h_ago: float | None
    volume_24h_usd: float | None
    volume_7d_avg_usd: float | None
    oi_usd: float | None
    top_holder_pct: float | None
    order_size_usd: float


def build_market_features(
    snapshot: dict,
    close_time_iso: str,
    order_size_usd: float,
    execution_price: float | None = None,
) -> MarketFeatures | None:
    if not snapshot:
        return None

    best_bid = _optional_float(snapshot.get("best_bid"), min_value=0.0) or 0.0
    best_ask = _optional_float(snapshot.get("best_ask"), min_value=0.0) or 0.0
    inferred_mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
    mid = _optional_float(snapshot.get("mid"), min_value=0.0) or inferred_mid
    effective_execution_price = float(execution_price or 0.0)
    if not (0.0 < effective_execution_price < 1.0):
        effective_execution_price = best_ask if 0.0 < best_ask < 1.0 else mid

    if not close_time_iso:
        return None
    try:
        close_dt = datetime.fromisoformat(close_time_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    days_to_res = max((close_dt - datetime.now(timezone.utc)).total_seconds() / 86400, 0.0)

    price_1h_ago = _extract_price_1h_ago(snapshot.get("price_history_1h"), mid)

    return MarketFeatures(
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        execution_price=effective_execution_price,
        bid_depth_usd=float(snapshot.get("bid_depth_usd", 0.0)),
        ask_depth_usd=float(snapshot.get("ask_depth_usd", 0.0)),
        days_to_res=days_to_res,
        price_1h_ago=price_1h_ago,
        volume_24h_usd=_optional_float(snapshot.get("volume_24h_usd"), min_value=0.0),
        volume_7d_avg_usd=_optional_float(snapshot.get("volume_7d_avg_usd"), min_value=0.0),
        oi_usd=_optional_float(snapshot.get("oi_usd"), min_value=0.0),
        top_holder_pct=_optional_float(snapshot.get("top_holder_pct"), min_value=0.0, max_value=1.0),
        order_size_usd=order_size_usd,
    )


def _optional_float(
    value: Any,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float | None:
    if value in {None, ""}:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if min_value is not None and numeric < min_value:
        return None
    if max_value is not None and numeric > max_value:
        return None
    return numeric


def _extract_price_1h_ago(history: Any, fallback: float) -> float | None:
    rows = history if isinstance(history, list) else []
    normalized: list[tuple[int, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            price = float(row.get("p") or row.get("price"))
        except (TypeError, ValueError):
            continue
        if not (0.0 < price < 1.0):
            continue
        try:
            ts = int(float(row.get("t") or row.get("timestamp") or 0))
        except (TypeError, ValueError):
            ts = 0
        normalized.append((ts, price))

    if not normalized:
        return None

    normalized.sort(key=lambda item: item[0])
    latest_ts = normalized[-1][0]
    if latest_ts <= 0:
        return normalized[0][1]

    target_ts = latest_ts - 3600
    return min(normalized, key=lambda item: abs(item[0] - target_ts))[1]


class MarketScorer:
    WEIGHTS = {
        "spread": 0.17,
        "depth": 0.15,
        "time": 0.18,
        "momentum": 0.10,
        "volume": 0.14,
        "vol_trend": 0.08,
        "oi_conc": 0.08,
        "resolution": 0.10,
    }

    @staticmethod
    def _spread(features: MarketFeatures) -> float:
        return (features.best_ask - features.best_bid) / features.mid if features.mid > 0 else 1.0

    @staticmethod
    def _min_execution_window_seconds() -> float:
        # Only veto when our polling cadence plus order submission time makes
        # the remaining window effectively impossible to trade.
        return float(max(min_execution_window_seconds(), poll_interval() + EXECUTION_BUFFER_SECONDS))

    def _veto(self, features: MarketFeatures) -> str | None:
        if features.mid <= 0 or features.mid >= 1:
            return "invalid market mid"
        if features.best_bid < 0 or features.best_ask < 0:
            return "invalid order book values"
        if features.best_ask < features.best_bid:
            return "crossed order book"
        if features.best_bid == 0 and features.best_ask == 0:
            return "missing order book"
        if features.bid_depth_usd <= 0 and features.ask_depth_usd <= 0:
            return "no visible order book depth"

        spread = self._spread(features)
        min_window_seconds = self._min_execution_window_seconds()
        if features.days_to_res * 86400 < min_window_seconds:
            return f"expires in <{int(min_window_seconds)}s"
        if features.days_to_res * 86400 > max_market_horizon_seconds():
            return f"beyond max horizon {max_market_horizon_label()}"
        return None

    def _score_spread(self, features: MarketFeatures) -> float:
        return float(np.clip(1 - self._spread(features) / 0.05, 0, 1))

    @staticmethod
    def _score_depth(features: MarketFeatures) -> float:
        depth = (features.bid_depth_usd + features.ask_depth_usd) / 2
        if depth <= 0:
            return 0.0
        return float(np.clip(1 - features.order_size_usd / depth, 0, 1))

    @staticmethod
    def _score_time(features: MarketFeatures) -> float:
        days = features.days_to_res
        min_window_days = max(min_execution_window_seconds(), poll_interval() + EXECUTION_BUFFER_SECONDS) / 86400
        if days <= min_window_days:
            return 0.0
        if days < (1 / 24):
            return float(np.interp(days, [min_window_days, 1 / 24], [0.15, 0.45]))
        if days < 0.5:
            return float(np.interp(days, [1 / 24, 0.5], [0.45, 0.8]))
        if days < 3:
            return float(np.interp(days, [0.5, 3.0], [0.8, 1.0]))
        if days <= 14:
            return 1.0
        return float(np.clip(1 - (days - 14) / 90, 0.4, 1.0))

    @staticmethod
    def _score_momentum(features: MarketFeatures) -> float | None:
        if features.price_1h_ago is None or features.price_1h_ago <= 0:
            return None
        move = abs(features.mid - features.price_1h_ago) / features.price_1h_ago
        return float(np.clip(1 - move / 0.05, 0.2, 1.0))

    @staticmethod
    def _score_volume_trend(features: MarketFeatures) -> float | None:
        avg = features.volume_7d_avg_usd
        if avg is None or avg <= 0 or features.volume_24h_usd is None:
            return None
        ratio = features.volume_24h_usd / avg
        return float(np.clip(np.interp(ratio, [0.3, 1.0, 1.5], [0.0, 0.7, 1.0]), 0, 1))

    @staticmethod
    def _score_volume(features: MarketFeatures) -> float | None:
        if features.volume_24h_usd is None:
            return None
        volume = max(features.volume_24h_usd, 0.0)
        if volume <= 0:
            return 0.0

        low = max(100.0, features.order_size_usd * 10)
        good = max(1000.0, features.order_size_usd * 100)
        if good <= low:
            good = low * 10

        return float(np.clip(np.interp(np.log10(volume), [np.log10(low), np.log10(good)], [0.1, 1.0]), 0, 1))

    @staticmethod
    def _score_oi_concentration(features: MarketFeatures) -> float | None:
        if features.top_holder_pct is None:
            return None
        return float(np.clip(1 - features.top_holder_pct / 0.8, 0, 1))

    @staticmethod
    def _score_resolution(features: MarketFeatures) -> float:
        distance_to_edge = min(features.mid, 1 - features.mid)
        return float(
            np.clip(
                np.interp(distance_to_edge, [0.01, 0.05, 0.15], [0.05, 0.5, 1.0]),
                0,
                1,
            )
        )

    def score(self, features: MarketFeatures) -> dict:
        veto = self._veto(features)
        if veto:
            return {"score": 0.0, "veto": veto, "components": {}}

        raw_components = {
            "spread": self._score_spread(features),
            "depth": self._score_depth(features),
            "time": self._score_time(features),
            "momentum": self._score_momentum(features),
            "volume": self._score_volume(features),
            "vol_trend": self._score_volume_trend(features),
            "oi_conc": self._score_oi_concentration(features),
            "resolution": self._score_resolution(features),
        }
        components = {
            key: value
            for key, value in raw_components.items()
            if value is not None
        }
        total_weight = sum(self.WEIGHTS[key] for key in components)
        score = (
            sum(self.WEIGHTS[key] * value for key, value in components.items()) / total_weight
            if total_weight > 0
            else 0.0
        )
        return {
            "score": round(score, 4),
            "veto": None,
            "components": {key: round(value, 3) for key, value in components.items()},
        }
