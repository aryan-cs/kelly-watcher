from __future__ import annotations

import math
import logging
import statistics
import time
from dataclasses import dataclass

from kelly_watcher.config import (
    adaptive_heuristic_entry_price_cache_seconds,
    adaptive_heuristic_entry_price_enabled,
    adaptive_heuristic_entry_price_lookback_seconds,
    adaptive_heuristic_entry_price_min_avg_return,
    adaptive_heuristic_entry_price_min_band_samples,
    adaptive_heuristic_entry_price_min_samples,
)
from kelly_watcher.data.db import get_conn
from kelly_watcher.engine.segment_policy import ENTRY_PRICE_BANDS, entry_price_band
from kelly_watcher.engine.trade_contract import NON_CHALLENGER_EXPERIMENT_ARM_SQL, RESOLVED_PNL_SQL

logger = logging.getLogger(__name__)

_snapshot_cache: tuple[float, "AdaptiveEntryPriceSnapshot"] | None = None


@dataclass(frozen=True)
class EntryPriceEvidenceRow:
    price: float
    return_value: float
    placed_at: int
    source: str


@dataclass(frozen=True)
class EntryPriceBandStats:
    band: str
    sample_count: int = 0
    weighted_avg_return: float | None = None
    weighted_win_rate: float | None = None
    score: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "band": self.band,
            "sample_count": self.sample_count,
            "weighted_avg_return": self.weighted_avg_return,
            "weighted_win_rate": self.weighted_win_rate,
            "score": self.score,
        }


@dataclass(frozen=True)
class AdaptiveEntryPriceDecision:
    enabled: bool
    source: str
    min_price: float
    max_price: float
    allowed_bands: tuple[str, ...]
    base_min_price: float
    base_max_price: float
    sample_count: int
    min_samples: int
    min_band_samples: int
    min_avg_return: float
    reasons: tuple[str, ...]
    band_stats: tuple[EntryPriceBandStats, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "source": self.source,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "allowed_bands": list(self.allowed_bands),
            "base_min_price": self.base_min_price,
            "base_max_price": self.base_max_price,
            "sample_count": self.sample_count,
            "min_samples": self.min_samples,
            "min_band_samples": self.min_band_samples,
            "min_avg_return": self.min_avg_return,
            "reasons": list(self.reasons),
            "band_stats": [stats.as_dict() for stats in self.band_stats],
        }


@dataclass(frozen=True)
class AdaptiveEntryPriceSnapshot:
    rows: tuple[EntryPriceEvidenceRow, ...]


def reset_adaptive_entry_price_cache() -> None:
    global _snapshot_cache
    _snapshot_cache = None


def adaptive_heuristic_entry_price_band(
    *,
    base_min_price: float,
    base_max_price: float,
) -> AdaptiveEntryPriceDecision:
    enabled = adaptive_heuristic_entry_price_enabled()
    min_samples = adaptive_heuristic_entry_price_min_samples()
    min_band_samples = adaptive_heuristic_entry_price_min_band_samples()
    min_avg_return = adaptive_heuristic_entry_price_min_avg_return()
    base_min = _clamp_price(base_min_price, fallback=0.0)
    base_max = _clamp_price(base_max_price, fallback=1.0)
    if base_max <= base_min:
        base_min, base_max = 0.0, 1.0

    if not enabled:
        return _fallback_decision(
            enabled=False,
            source="disabled",
            base_min_price=base_min,
            base_max_price=base_max,
            min_samples=min_samples,
            min_band_samples=min_band_samples,
            min_avg_return=min_avg_return,
            reasons=("adaptive heuristic entry-price bands are disabled",),
        )

    snapshot = _load_snapshot()
    return derive_adaptive_entry_price_band(
        rows=snapshot.rows,
        base_min_price=base_min,
        base_max_price=base_max,
        min_samples=min_samples,
        min_band_samples=min_band_samples,
        min_avg_return=min_avg_return,
        now_ts=int(time.time()),
        lookback_seconds=adaptive_heuristic_entry_price_lookback_seconds(),
    )


def derive_adaptive_entry_price_band(
    *,
    rows: tuple[EntryPriceEvidenceRow, ...],
    base_min_price: float,
    base_max_price: float,
    min_samples: int,
    min_band_samples: int,
    min_avg_return: float,
    now_ts: int,
    lookback_seconds: float,
) -> AdaptiveEntryPriceDecision:
    base_min = _clamp_price(base_min_price, fallback=0.0)
    base_max = _clamp_price(base_max_price, fallback=1.0)
    if base_max <= base_min:
        base_min, base_max = 0.0, 1.0
    min_samples = max(int(min_samples), 1)
    min_band_samples = max(int(min_band_samples), 1)
    min_avg_return = float(min_avg_return)
    if not math.isfinite(min_avg_return):
        min_avg_return = 0.0
    lookback = max(float(lookback_seconds or 0.0), 3600.0)
    clean_rows = tuple(row for row in rows if _valid_row(row))
    if len(clean_rows) < min_samples:
        return _fallback_decision(
            enabled=True,
            source="fallback",
            base_min_price=base_min,
            base_max_price=base_max,
            min_samples=min_samples,
            min_band_samples=min_band_samples,
            min_avg_return=min_avg_return,
            sample_count=len(clean_rows),
            reasons=("not enough resolved price-band evidence",),
            band_stats=_band_stats(clean_rows, now_ts=now_ts, lookback_seconds=lookback),
        )

    stats = _band_stats(clean_rows, now_ts=now_ts, lookback_seconds=lookback)
    selected = tuple(
        band_stat.band
        for band_stat in stats
        if band_stat.sample_count >= min_band_samples
        and band_stat.weighted_avg_return is not None
        and band_stat.score is not None
        and band_stat.weighted_avg_return >= min_avg_return
        and band_stat.score >= min_avg_return
    )
    if not selected:
        return _fallback_decision(
            enabled=True,
            source="fallback",
            base_min_price=base_min,
            base_max_price=base_max,
            min_samples=min_samples,
            min_band_samples=min_band_samples,
            min_avg_return=min_avg_return,
            sample_count=len(clean_rows),
            reasons=("no price band cleared the adaptive return threshold",),
            band_stats=stats,
        )

    min_price, max_price = _price_range_for_bands(selected)
    return AdaptiveEntryPriceDecision(
        enabled=True,
        source="adaptive",
        min_price=round(min_price, 4),
        max_price=round(max_price, 4),
        allowed_bands=selected,
        base_min_price=round(base_min, 4),
        base_max_price=round(base_max, 4),
        sample_count=len(clean_rows),
        min_samples=min_samples,
        min_band_samples=min_band_samples,
        min_avg_return=round(min_avg_return, 4),
        reasons=("resolved shadow evidence selected profitable entry-price bands",),
        band_stats=stats,
    )


def _load_snapshot() -> AdaptiveEntryPriceSnapshot:
    global _snapshot_cache
    now = time.time()
    ttl = max(adaptive_heuristic_entry_price_cache_seconds(), 1.0)
    if _snapshot_cache and (now - _snapshot_cache[0]) < ttl:
        return _snapshot_cache[1]

    lookback = adaptive_heuristic_entry_price_lookback_seconds()
    cutoff = int(now - lookback) if lookback > 0 else 0
    try:
        conn = get_conn()
    except Exception:
        logger.warning("Adaptive heuristic entry-price evidence unavailable; using fallback band.", exc_info=True)
        snapshot = AdaptiveEntryPriceSnapshot(rows=())
        _snapshot_cache = (now, snapshot)
        return snapshot
    try:
        try:
            rows = conn.execute(
                f"""
                SELECT
                    price_at_signal,
                    actual_entry_price,
                    actual_entry_size_usd,
                    signal_size_usd,
                    skipped,
                    skip_reason,
                    counterfactual_return,
                    {RESOLVED_PNL_SQL} AS resolved_pnl,
                    placed_at
                FROM trade_log
                WHERE COALESCE(source_action, 'buy')='buy'
                  AND {NON_CHALLENGER_EXPERIMENT_ARM_SQL}
                  AND LOWER(COALESCE(signal_mode, 'heuristic')) IN ('heuristic', 'heuristic_bootstrap')
                  AND placed_at >= ?
                  AND (
                      (
                          skipped=0
                          AND COALESCE(actual_entry_size_usd, signal_size_usd, 0) > 0
                          AND {RESOLVED_PNL_SQL} IS NOT NULL
                      )
                      OR (
                          skipped=1
                          AND counterfactual_return IS NOT NULL
                          AND (
                              LOWER(COALESCE(skip_reason, '')) LIKE 'heuristic entry price % outside band %'
                              OR LOWER(COALESCE(skip_reason, '')) LIKE 'heuristic entry band % outside adaptive allowlist %'
                          )
                      )
                  )
                ORDER BY placed_at DESC
                LIMIT 1000
                """,
                (cutoff,),
            ).fetchall()
        except Exception:
            logger.warning("Adaptive heuristic entry-price query failed; using fallback band.", exc_info=True)
            rows = []
    finally:
        conn.close()

    evidence: list[EntryPriceEvidenceRow] = []
    for row in rows:
        skipped = bool(int(row["skipped"] or 0))
        price = _finite_float(row["actual_entry_price"]) if not skipped else None
        if price is None:
            price = _finite_float(row["price_at_signal"])
        if price is None or not (0.0 < price < 1.0):
            continue
        if skipped:
            return_value = _finite_float(row["counterfactual_return"])
            source = "counterfactual_outside_band"
        else:
            size = _finite_float(row["actual_entry_size_usd"]) or _finite_float(row["signal_size_usd"])
            pnl = _finite_float(row["resolved_pnl"])
            if size is None or size <= 0 or pnl is None:
                continue
            return_value = pnl / size
            source = "executed"
        placed_at = int(_finite_float(row["placed_at"]) or 0)
        if return_value is None or not math.isfinite(return_value) or placed_at <= 0:
            continue
        evidence.append(
            EntryPriceEvidenceRow(
                price=float(price),
                return_value=float(return_value),
                placed_at=placed_at,
                source=source,
            )
        )

    snapshot = AdaptiveEntryPriceSnapshot(rows=tuple(evidence))
    _snapshot_cache = (now, snapshot)
    return snapshot


def _band_stats(
    rows: tuple[EntryPriceEvidenceRow, ...],
    *,
    now_ts: int,
    lookback_seconds: float,
) -> tuple[EntryPriceBandStats, ...]:
    by_band: dict[str, list[EntryPriceEvidenceRow]] = {band: [] for band in ENTRY_PRICE_BANDS}
    for row in rows:
        by_band.setdefault(entry_price_band(row.price), []).append(row)

    result: list[EntryPriceBandStats] = []
    half_life = max(float(lookback_seconds) / 2.0, 3600.0)
    for band in ENTRY_PRICE_BANDS:
        band_rows = by_band.get(band, [])
        if not band_rows:
            result.append(EntryPriceBandStats(band=band))
            continue
        weights = [_recency_weight(row.placed_at, now_ts=now_ts, half_life_seconds=half_life) for row in band_rows]
        returns = [row.return_value for row in band_rows]
        weight_sum = sum(weights)
        if weight_sum <= 0:
            result.append(EntryPriceBandStats(band=band, sample_count=len(band_rows)))
            continue
        avg_return = sum(value * weight for value, weight in zip(returns, weights)) / weight_sum
        win_rate = sum((1.0 if row.return_value > 0 else 0.0) * weight for row, weight in zip(band_rows, weights)) / weight_sum
        if len(returns) >= 2:
            stdev = statistics.pstdev(returns)
        else:
            stdev = abs(avg_return)
        uncertainty = stdev / math.sqrt(max(len(returns), 1))
        score = avg_return - (0.25 * uncertainty)
        result.append(
            EntryPriceBandStats(
                band=band,
                sample_count=len(band_rows),
                weighted_avg_return=round(float(avg_return), 4),
                weighted_win_rate=round(float(win_rate), 4),
                score=round(float(score), 4),
            )
        )
    return tuple(result)


def _recency_weight(placed_at: int, *, now_ts: int, half_life_seconds: float) -> float:
    age = max(float(now_ts - int(placed_at or 0)), 0.0)
    return float(0.5 ** (age / max(float(half_life_seconds), 1.0)))


def _price_range_for_bands(bands: tuple[str, ...]) -> tuple[float, float]:
    ranges = [_band_range(band) for band in bands]
    return min(item[0] for item in ranges), max(item[1] for item in ranges)


def _band_range(band: str) -> tuple[float, float]:
    if band == "<0.45":
        return 0.01, 0.45
    if band == "0.45-0.49":
        return 0.45, 0.50
    if band == "0.50-0.54":
        return 0.50, 0.55
    if band == "0.55-0.59":
        return 0.55, 0.60
    if band == "0.60-0.69":
        return 0.60, 0.70
    if band == ">=0.70":
        return 0.70, 1.00
    return 0.01, 1.00


def _fallback_decision(
    *,
    enabled: bool,
    source: str,
    base_min_price: float,
    base_max_price: float,
    min_samples: int,
    min_band_samples: int,
    min_avg_return: float,
    sample_count: int = 0,
    reasons: tuple[str, ...] = (),
    band_stats: tuple[EntryPriceBandStats, ...] = (),
) -> AdaptiveEntryPriceDecision:
    return AdaptiveEntryPriceDecision(
        enabled=enabled,
        source=source,
        min_price=round(base_min_price, 4),
        max_price=round(base_max_price, 4),
        allowed_bands=(),
        base_min_price=round(base_min_price, 4),
        base_max_price=round(base_max_price, 4),
        sample_count=sample_count,
        min_samples=max(int(min_samples), 1),
        min_band_samples=max(int(min_band_samples), 1),
        min_avg_return=round(float(min_avg_return), 4),
        reasons=reasons,
        band_stats=band_stats,
    )


def _valid_row(row: EntryPriceEvidenceRow) -> bool:
    return (
        math.isfinite(row.price)
        and 0.0 < row.price < 1.0
        and math.isfinite(row.return_value)
        and int(row.placed_at or 0) > 0
    )


def _finite_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _clamp_price(value: object, *, fallback: float) -> float:
    numeric = _finite_float(value)
    if numeric is None:
        return fallback
    return max(0.0, min(1.0, numeric))
