from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any

from kelly_watcher.config import (
    adaptive_loss_guard_enabled,
    adaptive_loss_guard_lookback_seconds,
    adaptive_loss_guard_max_segment_avg_return,
    adaptive_loss_guard_max_segment_pnl_usd,
    adaptive_loss_guard_max_segment_win_rate,
    adaptive_loss_guard_min_segment_resolved,
    adaptive_loss_guard_min_total_resolved,
    adaptive_loss_guard_refresh_seconds,
    entry_price_band_label,
    time_to_close_band_label,
)
from kelly_watcher.data.db import get_conn
from kelly_watcher.engine.trade_contract import NON_CHALLENGER_EXPERIMENT_ARM_SQL, resolved_pnl_expr


_CRYPTO_RE = re.compile(r"\b(bitcoin|btc|ethereum|ether|eth|solana|crypto)\b", re.IGNORECASE)
_ESPORTS_RE = re.compile(
    r"\b(counter[- ]strike|cs2|valorant|dota|league of legends|lol:|esports?|map \d|game \d)\b",
    re.IGNORECASE,
)
_SPORTS_RE = re.compile(
    r"\b(spread:|nba|nfl|mlb|nhl|ufc|soccer|football|basketball|baseball|hockey|vs\.| vs )\b",
    re.IGNORECASE,
)
_POLITICS_NEWS_RE = re.compile(
    r"\b(election|president|trump|biden|congress|senate|house|tariff|war|iran|ukraine|russia)\b",
    re.IGNORECASE,
)
_WEATHER_RE = re.compile(r"\b(weather|temperature|rain|snow|hurricane|storm|wind)\b", re.IGNORECASE)

_CACHE: tuple[float, dict[tuple[str, str], "SegmentStats"], int] | None = None


@dataclass(frozen=True)
class SegmentStats:
    resolved: int
    pnl_usd: float
    size_usd: float
    wins: int

    @property
    def avg_return(self) -> float:
        if self.size_usd <= 0:
            return 0.0
        return self.pnl_usd / self.size_usd

    @property
    def win_rate(self) -> float:
        if self.resolved <= 0:
            return 0.0
        return self.wins / self.resolved


def reset_adaptive_loss_guard_cache() -> None:
    global _CACHE
    _CACHE = None


def classify_market_family(question: Any) -> str:
    text = str(question or "").strip()
    if not text:
        return "unknown"
    if _CRYPTO_RE.search(text):
        return "crypto"
    if _ESPORTS_RE.search(text):
        return "esports"
    if _WEATHER_RE.search(text):
        return "weather"
    if _POLITICS_NEWS_RE.search(text):
        return "politics_news"
    if _SPORTS_RE.search(text):
        return "sports"
    return "other"


def adaptive_loss_guard_reason(*, event: Any, signal: dict[str, Any]) -> str | None:
    if not adaptive_loss_guard_enabled():
        return None

    snapshot, total_resolved = _segment_snapshot()
    if total_resolved < adaptive_loss_guard_min_total_resolved():
        return None

    candidates = _event_segments(event, signal)
    if not candidates:
        return None

    blocked: list[tuple[tuple[str, str], SegmentStats]] = []
    min_resolved = adaptive_loss_guard_min_segment_resolved()
    max_pnl = adaptive_loss_guard_max_segment_pnl_usd()
    max_avg_return = adaptive_loss_guard_max_segment_avg_return()
    max_win_rate = adaptive_loss_guard_max_segment_win_rate()
    for key in candidates:
        stats = snapshot.get(key)
        if stats is None or stats.resolved < min_resolved:
            continue
        losing_return = stats.avg_return <= max_avg_return
        losing_win_rate = stats.win_rate <= max_win_rate
        losing_pnl = stats.pnl_usd <= max_pnl
        if losing_pnl and (losing_return or losing_win_rate):
            blocked.append((key, stats))

    if not blocked:
        return None

    key, stats = min(blocked, key=lambda item: (item[1].avg_return, item[1].pnl_usd))
    dimension, value = key
    return (
        f"adaptive loss guard blocked {dimension}={value}: "
        f"{stats.resolved} resolved, pnl ${stats.pnl_usd:.2f}, "
        f"avg return {stats.avg_return * 100:.1f}%, win {stats.win_rate * 100:.1f}%"
    )


def _segment_snapshot() -> tuple[dict[tuple[str, str], SegmentStats], int]:
    global _CACHE
    now = time.time()
    if _CACHE is not None and (now - _CACHE[0]) <= adaptive_loss_guard_refresh_seconds():
        return _CACHE[1], _CACHE[2]

    rows = _resolved_shadow_rows()
    buckets: dict[tuple[str, str], list[tuple[float, float, int]]] = {}
    for row in rows:
        pnl = _finite_float(row["pnl_usd"])
        size = _finite_float(row["size_usd"])
        if pnl is None or size is None or size <= 0:
            continue
        win = 1 if pnl > 0 else 0
        for key in _row_segments(row):
            buckets.setdefault(key, []).append((pnl, size, win))

    snapshot: dict[tuple[str, str], SegmentStats] = {}
    for key, values in buckets.items():
        snapshot[key] = SegmentStats(
            resolved=len(values),
            pnl_usd=sum(value[0] for value in values),
            size_usd=sum(value[1] for value in values),
            wins=sum(value[2] for value in values),
        )

    total_resolved = len(rows)
    _CACHE = (now, snapshot, total_resolved)
    return snapshot, total_resolved


def _resolved_shadow_rows() -> list[Any]:
    pnl_sql = resolved_pnl_expr()
    conn = get_conn()
    try:
        anchor_row = conn.execute("SELECT MAX(source_ts) AS max_source_ts FROM trade_log").fetchone()
        anchor_ts = int(anchor_row["max_source_ts"] or time.time()) if anchor_row is not None else int(time.time())
        lookback = adaptive_loss_guard_lookback_seconds()
        since_ts = int(anchor_ts - lookback)
        return list(
            conn.execute(
                f"""
                SELECT
                    question,
                    side,
                    source_ts,
                    market_close_ts,
                    price_at_signal,
                    actual_entry_price,
                    signal_mode,
                    {pnl_sql} AS pnl_usd,
                    COALESCE(actual_entry_size_usd, signal_size_usd, 0) AS size_usd
                FROM trade_log
                WHERE skipped=0
                  AND COALESCE(real_money, 0)=0
                  AND COALESCE(source_action, 'buy')='buy'
                  AND outcome IS NOT NULL
                  AND {pnl_sql} IS NOT NULL
                  AND COALESCE(actual_entry_size_usd, signal_size_usd, 0) > 0
                  AND {NON_CHALLENGER_EXPERIMENT_ARM_SQL}
                  AND source_ts >= ?
                """,
                (since_ts,),
            ).fetchall()
        )
    finally:
        conn.close()


def _row_segments(row: Any) -> list[tuple[str, str]]:
    price = _finite_float(row["actual_entry_price"])
    if price is None:
        price = _finite_float(row["price_at_signal"])
    source_ts = _finite_float(row["source_ts"])
    close_ts = _finite_float(row["market_close_ts"])
    time_to_close_seconds = None
    if source_ts is not None and close_ts is not None:
        time_to_close_seconds = max(0, int(close_ts - source_ts))

    return _clean_segments(
        (
            ("market_family", classify_market_family(row["question"])),
            ("entry_price_band", entry_price_band_label(price) if price is not None else "unknown"),
            (
                "time_to_close_band",
                time_to_close_band_label(time_to_close_seconds) if time_to_close_seconds is not None else "unknown",
            ),
            ("source_side", _normalize_side(row["side"])),
        )
    )


def _event_segments(event: Any, signal: dict[str, Any]) -> list[tuple[str, str]]:
    price = _first_finite(
        signal.get("entry_price"),
        signal.get("execution_price"),
        getattr(event, "price", None),
    )
    time_to_close_seconds = _first_finite(
        signal.get("time_to_close_seconds"),
        _event_time_to_close_seconds(event),
    )
    return _clean_segments(
        (
            ("market_family", classify_market_family(getattr(event, "question", ""))),
            ("entry_price_band", entry_price_band_label(price) if price is not None else "unknown"),
            (
                "time_to_close_band",
                time_to_close_band_label(int(time_to_close_seconds)) if time_to_close_seconds is not None else "unknown",
            ),
            ("source_side", _normalize_side(getattr(event, "side", None))),
        )
    )


def _clean_segments(segments: tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
    cleaned: list[tuple[str, str]] = []
    for dimension, value in segments:
        normalized = str(value or "").strip().lower()
        if not normalized or normalized == "unknown":
            continue
        cleaned.append((dimension, normalized))
    return cleaned


def _normalize_side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if text in {"yes", "y"}:
        return "yes"
    if text in {"no", "n"}:
        return "no"
    if text in {"up", "above", "higher"}:
        return "up"
    if text in {"down", "below", "lower"}:
        return "down"
    return text[:32]


def _event_time_to_close_seconds(event: Any) -> float | None:
    close_ts = _first_finite(
        getattr(event, "market_close_ts", None),
        getattr(event, "close_ts", None),
    )
    source_ts = _first_finite(
        getattr(event, "timestamp", None),
        getattr(event, "source_ts", None),
    )
    if close_ts is None or source_ts is None:
        return None
    return max(0.0, close_ts - source_ts)


def _first_finite(*values: Any) -> float | None:
    for value in values:
        parsed = _finite_float(value)
        if parsed is not None:
            return parsed
    return None


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed
