from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

ENTRY_PRICE_BANDS: tuple[str, ...] = (
    "<0.45",
    "0.45-0.49",
    "0.50-0.54",
    "0.55-0.59",
    "0.60-0.69",
    ">=0.70",
)

TIME_TO_CLOSE_BANDS: tuple[str, ...] = (
    "<=5m",
    "5-30m",
    "30m-2h",
    "2h-12h",
    "12h-1d",
    "1-3d",
    ">3d",
)
WATCH_TIERS: tuple[str, ...] = ("hot", "warm", "discovery")
HORIZON_BUCKETS: tuple[str, ...] = ("short", "mid", "long")
SEGMENT_FALLBACK = "fallback"
SHORT_TIME_TO_CLOSE_BANDS: tuple[str, ...] = ("<=5m", "5-30m", "30m-2h")
MID_TIME_TO_CLOSE_BANDS: tuple[str, ...] = ("2h-12h", "12h-1d")
LONG_TIME_TO_CLOSE_BANDS: tuple[str, ...] = ("1-3d", ">3d")
HORIZON_BUCKET_BY_BAND = {
    **{band: "short" for band in SHORT_TIME_TO_CLOSE_BANDS},
    **{band: "mid" for band in MID_TIME_TO_CLOSE_BANDS},
    **{band: "long" for band in LONG_TIME_TO_CLOSE_BANDS},
}
SEGMENT_IDS: tuple[str, ...] = tuple(
    f"{tier}_{horizon}"
    for tier in WATCH_TIERS
    for horizon in HORIZON_BUCKETS
)


@dataclass(frozen=True)
class SegmentRoute:
    segment_id: str
    watch_tier: str
    horizon_bucket: str
    fallback: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "watch_tier": self.watch_tier,
            "horizon_bucket": self.horizon_bucket,
            "fallback": self.fallback,
        }


def entry_price_band(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.45:
        return ENTRY_PRICE_BANDS[0]
    if value < 0.50:
        return ENTRY_PRICE_BANDS[1]
    if value < 0.55:
        return ENTRY_PRICE_BANDS[2]
    if value < 0.60:
        return ENTRY_PRICE_BANDS[3]
    if value < 0.70:
        return ENTRY_PRICE_BANDS[4]
    return ENTRY_PRICE_BANDS[5]


def time_to_close_band(seconds: int) -> str:
    if seconds <= 300:
        return TIME_TO_CLOSE_BANDS[0]
    if seconds <= 1800:
        return TIME_TO_CLOSE_BANDS[1]
    if seconds <= 7200:
        return TIME_TO_CLOSE_BANDS[2]
    if seconds <= 43200:
        return TIME_TO_CLOSE_BANDS[3]
    if seconds <= 86400:
        return TIME_TO_CLOSE_BANDS[4]
    if seconds <= 259200:
        return TIME_TO_CLOSE_BANDS[5]
    return TIME_TO_CLOSE_BANDS[6]


def normalize_watch_tier(raw: Any) -> str:
    if raw is None:
        return ""
    value = str(raw).strip().lower()
    return value if value in WATCH_TIERS else ""


def horizon_bucket_for_band(band: Any) -> str:
    value = str(band or "").strip()
    return HORIZON_BUCKET_BY_BAND.get(value, "")


def horizon_bucket_for_seconds(seconds: Any) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(value) or value < 0:
        return ""
    return horizon_bucket_for_band(time_to_close_band(int(value)))


def segment_route_for_trade(*, watch_tier: Any, time_to_close_band: Any) -> SegmentRoute:
    normalized_tier = normalize_watch_tier(watch_tier)
    normalized_horizon = horizon_bucket_for_band(time_to_close_band)
    if normalized_tier and normalized_horizon:
        return SegmentRoute(
            segment_id=f"{normalized_tier}_{normalized_horizon}",
            watch_tier=normalized_tier,
            horizon_bucket=normalized_horizon,
            fallback=False,
        )
    return SegmentRoute(
        segment_id=SEGMENT_FALLBACK,
        watch_tier=normalized_tier,
        horizon_bucket=normalized_horizon,
        fallback=True,
    )


def segment_route_for_seconds(*, watch_tier: Any, time_to_close_seconds: Any) -> SegmentRoute:
    normalized_tier = normalize_watch_tier(watch_tier)
    normalized_horizon = horizon_bucket_for_seconds(time_to_close_seconds)
    if normalized_tier and normalized_horizon:
        return SegmentRoute(
            segment_id=f"{normalized_tier}_{normalized_horizon}",
            watch_tier=normalized_tier,
            horizon_bucket=normalized_horizon,
            fallback=False,
        )
    return SegmentRoute(
        segment_id=SEGMENT_FALLBACK,
        watch_tier=normalized_tier,
        horizon_bucket=normalized_horizon,
        fallback=True,
    )


def normalize_segment_filter(
    raw: Any,
    *,
    allowed_values: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        values = [str(part).strip() for part in raw]
    else:
        values = [str(raw).strip()]
    requested = {value for value in values if value}
    if not requested:
        return ()
    unknown = sorted(requested.difference(allowed_values))
    if unknown:
        raise ValueError(f"Unknown {field_name} values: {', '.join(unknown)}")
    return tuple(value for value in allowed_values if value in requested)
