from __future__ import annotations

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
