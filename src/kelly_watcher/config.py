from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from kelly_watcher.research.replay_search_contract import validate_replay_search_score_weight_payload

from dotenv import dotenv_values

from kelly_watcher.data.db import get_runtime_setting
from kelly_watcher.env_profile import (
    ENV_ONLY_KEYS,
    LEGACY_ENV_PATH,
    active_env_path,
    init_env_profile,
)
from kelly_watcher.runtime_paths import MODEL_ARTIFACT_PATH, REPO_ROOT
from kelly_watcher.engine.segment_policy import (
    ENTRY_PRICE_BANDS,
    TIME_TO_CLOSE_BANDS,
    entry_price_band as _entry_price_band_label,
    normalize_segment_filter,
    time_to_close_band as _time_to_close_band_label,
)

ENV_PROFILE = "default"
ENV_PATH = active_env_path()
init_env_profile(override=False)
MIN_POLL_INTERVAL_SECONDS = 1.0
_DURATION_UNITS = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}
ENTRY_PRICE_BAND_CHOICES = (
    "<0.45",
    "0.45-0.49",
    "0.50-0.54",
    "0.55-0.59",
    "0.60-0.69",
    ">=0.70",
)
TIME_TO_CLOSE_BAND_CHOICES = TIME_TO_CLOSE_BANDS


class ConfigError(ValueError):
    pass


def _source_env_path() -> Path:
    if ENV_PATH.exists():
        return ENV_PATH
    repo_env_path = REPO_ROOT / ".env"
    if repo_env_path.exists():
        return repo_env_path
    if LEGACY_ENV_PATH.exists():
        return LEGACY_ENV_PATH
    return ENV_PATH


def _is_env_only_key(name: str) -> bool:
    return str(name or "").strip().upper() in ENV_ONLY_KEYS


def _get_runtime_setting_value(name: str) -> str | None:
    if _is_env_only_key(name):
        return None
    try:
        return get_runtime_setting(name)
    except Exception:
        return None


def _get(name: str, default: str = "") -> str:
    runtime_value = _get_runtime_setting_value(name)
    if runtime_value is not None:
        return str(runtime_value).strip()
    return os.getenv(name, default).strip()


def _get_bool(name: str, default: str = "false") -> bool:
    return _get(name, default).lower() in {"1", "true", "yes", "on"}


def _get_env_file_bool(name: str, default: str = "false") -> bool:
    raw = _get_env_file_value(name)
    if raw is None:
        raw = _get(name, default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: str) -> float:
    raw = _get(name, default)
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be numeric, got {raw!r}") from exc


def _get_bounded_float(
    name: str,
    default: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = _get_float(name, default)
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}, got {value}")
    return value


def _get_int(name: str, default: str) -> int:
    raw = _get(name, default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _get_bounded_int(
    name: str,
    default: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = _get_int(name, default)
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}, got {value}")
    return value


def _get_env_file_value(name: str) -> str | None:
    runtime_value = _get_runtime_setting_value(name)
    if runtime_value is not None:
        return str(runtime_value).strip()

    source_path = _source_env_path()
    if not source_path.exists():
        return None

    try:
        value = dotenv_values(source_path).get(name)
    except Exception:
        return None

    if value is None:
        return None
    return str(value).strip()


def _get_env_file_bounded_float(
    name: str,
    default: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = _get_env_file_value(name) or _get(name, default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be numeric, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}, got {value}")
    return value


def _get_env_file_csv_choices(name: str, *, default: str = "", allowed: tuple[str, ...]) -> tuple[str, ...]:
    raw = _get_env_file_value(name)
    if raw is None:
        raw = _get(name, default)
    values = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    invalid = [value for value in values if value not in allowed]
    if invalid:
        raise ConfigError(
            f"{name} has invalid values {invalid!r}; allowed values are {list(allowed)!r}"
        )
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _get_env_file_json_object(name: str, *, default: str = "{}") -> dict[str, Any]:
    raw = _get_env_file_value(name)
    if raw is None:
        raw = _get(name, default)
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} must be a JSON object, got invalid JSON") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return payload


def _load_json_object_file(path_text: str) -> dict[str, Any]:
    text = str(path_text or "").strip()
    if not text:
        return {}
    path = Path(text)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} does not exist") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} must contain a JSON object, got invalid JSON") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return payload


def use_real_money() -> bool:
    return _get_bool("USE_REAL_MONEY", "false")


def max_bet_fraction() -> float:
    return _get_float("MAX_BET_FRACTION", "0.05")


def min_confidence() -> float:
    return _get_float("MIN_CONFIDENCE", "0.55")


def min_bet_usd() -> float:
    return _get_float("MIN_BET_USD", "1.00")


def entry_fixed_cost_usd() -> float:
    return _get_bounded_float("ENTRY_FIXED_COST_USD", "0.00", minimum=0.0)


def exit_fixed_cost_usd() -> float:
    return _get_bounded_float("EXIT_FIXED_COST_USD", "0.00", minimum=0.0)


def approval_fixed_cost_usd() -> float:
    return _get_bounded_float("APPROVAL_FIXED_COST_USD", "0.00", minimum=0.0)


def settlement_fixed_cost_usd() -> float:
    return _get_bounded_float("SETTLEMENT_FIXED_COST_USD", "0.00", minimum=0.0)


def include_expected_exit_fee_in_sizing() -> bool:
    return _get_bool("INCLUDE_EXPECTED_EXIT_FEE_IN_SIZING", "true")


def expected_close_fixed_cost_usd() -> float:
    raw = _get_env_file_value("EXPECTED_CLOSE_FIXED_COST_USD") or _get("EXPECTED_CLOSE_FIXED_COST_USD", "")
    if raw:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ConfigError(f"EXPECTED_CLOSE_FIXED_COST_USD must be numeric, got {raw!r}") from exc
        if value < 0:
            raise ConfigError(f"EXPECTED_CLOSE_FIXED_COST_USD must be >= 0, got {value}")
        return value
    return max(exit_fixed_cost_usd(), settlement_fixed_cost_usd())


def heuristic_min_entry_price() -> float:
    return _get_env_file_bounded_float(
        "HEURISTIC_MIN_ENTRY_PRICE",
        "0.65",
        minimum=0.0,
        maximum=0.99,
    )


def heuristic_max_entry_price() -> float:
    return _get_env_file_bounded_float(
        "HEURISTIC_MAX_ENTRY_PRICE",
        "0.75",
        minimum=0.0,
        maximum=1.0,
    )


def heuristic_allowed_entry_price_bands() -> tuple[str, ...]:
    return _get_env_file_csv_choices(
        "HEURISTIC_ALLOWED_ENTRY_PRICE_BANDS",
        default="",
        allowed=ENTRY_PRICE_BAND_CHOICES,
    )


def allow_heuristic() -> bool:
    return _get_env_file_bool("ALLOW_HEURISTIC", "true")


def xgboost_allowed_entry_price_bands() -> tuple[str, ...]:
    return _get_env_file_csv_choices(
        "XGBOOST_ALLOWED_ENTRY_PRICE_BANDS",
        default="",
        allowed=ENTRY_PRICE_BAND_CHOICES,
    )


def allow_xgboost() -> bool:
    return _get_env_file_bool("ALLOW_XGBOOST", "true")


def allowed_entry_price_bands() -> tuple[str, ...]:
    raw = _get_env_file_value("ALLOWED_ENTRY_PRICE_BANDS") or _get("ALLOWED_ENTRY_PRICE_BANDS", "")
    return normalize_segment_filter(
        raw,
        allowed_values=ENTRY_PRICE_BANDS,
        field_name="ALLOWED_ENTRY_PRICE_BANDS",
    )


def allowed_time_to_close_bands() -> tuple[str, ...]:
    raw = _get_env_file_value("ALLOWED_TIME_TO_CLOSE_BANDS") or _get("ALLOWED_TIME_TO_CLOSE_BANDS", "")
    return normalize_segment_filter(
        raw,
        allowed_values=TIME_TO_CLOSE_BANDS,
        field_name="ALLOWED_TIME_TO_CLOSE_BANDS",
    )


def heuristic_min_time_to_close_seconds() -> float:
    return _get_duration_seconds(
        "HEURISTIC_MIN_TIME_TO_CLOSE",
        "0s",
        minimum_seconds=0.0,
        allow_unlimited=False,
    )


def model_min_time_to_close_seconds() -> float:
    return _get_duration_seconds(
        "MODEL_MIN_TIME_TO_CLOSE",
        "0s",
        minimum_seconds=0.0,
        allow_unlimited=False,
    )


def model_edge_mid_confidence() -> float:
    return _get_env_file_bounded_float(
        "MODEL_EDGE_MID_CONFIDENCE",
        "0.55",
        minimum=0.0,
        maximum=1.0,
    )


def model_edge_high_confidence() -> float:
    return _get_env_file_bounded_float(
        "MODEL_EDGE_HIGH_CONFIDENCE",
        "0.65",
        minimum=0.0,
        maximum=1.0,
    )


def model_edge_mid_threshold() -> float:
    return _get_env_file_bounded_float(
        "MODEL_EDGE_MID_THRESHOLD",
        "0.0125",
        minimum=0.0,
        maximum=1.0,
    )


def model_edge_high_threshold() -> float:
    return _get_env_file_bounded_float(
        "MODEL_EDGE_HIGH_THRESHOLD",
        "0.0",
        minimum=0.0,
        maximum=1.0,
    )


def poll_interval() -> float:
    raw = _get_env_file_value("POLL_INTERVAL_SECONDS") or _get("POLL_INTERVAL_SECONDS", "45")
    try:
        return max(MIN_POLL_INTERVAL_SECONDS, float(raw))
    except ValueError:
        return 45.0


def _get_duration_seconds(
    name: str,
    default: str,
    *,
    minimum_seconds: float | None = None,
    allow_unlimited: bool = True,
) -> float:
    raw = _get_env_file_value(name) or _get(name, default)
    value = (raw or "").strip().lower()
    if not value:
        value = default.lower()

    if allow_unlimited and value in {"unlimited", "infinite", "inf", "none"}:
        return float("inf")

    try:
        seconds = max(float(value), 0.0)
    except ValueError:
        unit = value[-1:] if value else ""
        number = value[:-1]
        if unit not in _DURATION_UNITS or not number:
            raise ConfigError(f"{name} must look like 1h, 24h, 7d, or unlimited, got {raw!r}")
        try:
            seconds = max(float(number) * _DURATION_UNITS[unit], 0.0)
        except ValueError as exc:
            raise ConfigError(f"{name} must look like 1h, 24h, 7d, or unlimited, got {raw!r}") from exc

    if minimum_seconds is not None and seconds < minimum_seconds:
        raise ConfigError(f"{name} must be >= {minimum_seconds} seconds, got {seconds}")
    return seconds


def hot_wallet_count() -> int:
    raw = _get_env_file_value("HOT_WALLET_COUNT") or _get("HOT_WALLET_COUNT", "12")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"HOT_WALLET_COUNT must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ConfigError(f"HOT_WALLET_COUNT must be >= 1, got {value}")
    return value


def warm_wallet_count() -> int:
    raw = _get_env_file_value("WARM_WALLET_COUNT") or _get("WARM_WALLET_COUNT", "24")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"WARM_WALLET_COUNT must be an integer, got {raw!r}") from exc
    if value < 0:
        raise ConfigError(f"WARM_WALLET_COUNT must be >= 0, got {value}")
    return value


def warm_poll_interval_multiplier() -> int:
    raw = _get_env_file_value("WARM_POLL_INTERVAL_MULTIPLIER") or _get("WARM_POLL_INTERVAL_MULTIPLIER", "5")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"WARM_POLL_INTERVAL_MULTIPLIER must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ConfigError(f"WARM_POLL_INTERVAL_MULTIPLIER must be >= 1, got {value}")
    return value


def discovery_poll_interval_multiplier() -> int:
    raw = _get_env_file_value("DISCOVERY_POLL_INTERVAL_MULTIPLIER") or _get("DISCOVERY_POLL_INTERVAL_MULTIPLIER", "20")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"DISCOVERY_POLL_INTERVAL_MULTIPLIER must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ConfigError(f"DISCOVERY_POLL_INTERVAL_MULTIPLIER must be >= 1, got {value}")
    return value


def wallet_discovery_enabled() -> bool:
    return _get_env_file_bool("WALLET_DISCOVERY_ENABLED", "true")


def wallet_discovery_scan_interval_seconds() -> float:
    return _get_duration_seconds(
        "WALLET_DISCOVERY_SCAN_INTERVAL",
        "6h",
        minimum_seconds=300.0,
        allow_unlimited=False,
    )


def wallet_discovery_leaderboard_pages() -> int:
    return _get_bounded_int("WALLET_DISCOVERY_LEADERBOARD_PAGES", "2", minimum=1, maximum=10)


def wallet_discovery_leaderboard_per_page() -> int:
    return _get_bounded_int("WALLET_DISCOVERY_LEADERBOARD_PER_PAGE", "25", minimum=1, maximum=100)


def wallet_discovery_analyze_limit() -> int:
    return _get_bounded_int("WALLET_DISCOVERY_ANALYZE_LIMIT", "40", minimum=1, maximum=250)


def wallet_discovery_candidate_limit() -> int:
    return _get_bounded_int("WALLET_DISCOVERY_CANDIDATE_LIMIT", "20", minimum=1, maximum=250)


def wallet_inactivity_limit_seconds() -> float:
    return _get_duration_seconds(
        "WALLET_INACTIVITY_LIMIT",
        "unlimited",
        minimum_seconds=60.0,
        allow_unlimited=True,
    )


def wallet_slow_drop_max_tracking_age_seconds() -> float:
    return _get_duration_seconds(
        "WALLET_SLOW_DROP_MAX_TRACKING_AGE",
        "unlimited",
        minimum_seconds=60.0,
        allow_unlimited=True,
    )


def wallet_performance_drop_min_trades() -> int:
    return _get_bounded_int("WALLET_PERFORMANCE_DROP_MIN_TRADES", "40", minimum=0)


def wallet_performance_drop_max_win_rate() -> float:
    return _get_bounded_float("WALLET_PERFORMANCE_DROP_MAX_WIN_RATE", "0.40", minimum=0.0, maximum=1.0)


def wallet_performance_drop_max_avg_return() -> float:
    return _get_bounded_float("WALLET_PERFORMANCE_DROP_MAX_AVG_RETURN", "-0.03", minimum=-1.0, maximum=1.0)


def wallet_uncopyable_penalty_min_buys() -> int:
    return _get_bounded_int("WALLET_UNCOPYABLE_PENALTY_MIN_BUYS", "12", minimum=0)


def wallet_uncopyable_penalty_weight() -> float:
    return _get_bounded_float("WALLET_UNCOPYABLE_PENALTY_WEIGHT", "0.25", minimum=0.0, maximum=1.0)


def wallet_uncopyable_drop_min_buys() -> int:
    return _get_bounded_int("WALLET_UNCOPYABLE_DROP_MIN_BUYS", "24", minimum=0)


def wallet_uncopyable_drop_max_skip_rate() -> float:
    return _get_bounded_float("WALLET_UNCOPYABLE_DROP_MAX_SKIP_RATE", "0.75", minimum=0.0, maximum=1.0)


def wallet_uncopyable_drop_max_resolved_copied() -> int:
    return _get_bounded_int("WALLET_UNCOPYABLE_DROP_MAX_RESOLVED_COPIED", "3", minimum=0)


def wallet_discovery_min_observed_buys() -> int:
    return _get_bounded_int("WALLET_DISCOVERY_MIN_OBSERVED_BUYS", "8", minimum=0)


def wallet_cold_start_min_observed_buys() -> int:
    return _get_bounded_int("WALLET_COLD_START_MIN_OBSERVED_BUYS", "3", minimum=0)


def wallet_discovery_min_resolved_buys() -> int:
    return _get_bounded_int("WALLET_DISCOVERY_MIN_RESOLVED_BUYS", "3", minimum=0)


def wallet_discovery_size_multiplier() -> float:
    return _get_bounded_float("WALLET_DISCOVERY_SIZE_MULTIPLIER", "0.20", minimum=0.01, maximum=1.0)


def wallet_trusted_min_resolved_copied_buys() -> int:
    return _get_bounded_int("WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS", "15", minimum=0)


def wallet_probation_size_multiplier() -> float:
    return _get_bounded_float("WALLET_PROBATION_SIZE_MULTIPLIER", "0.50", minimum=0.01, maximum=1.0)


def wallet_local_performance_penalty_min_resolved_copied_buys() -> int:
    return _get_bounded_int(
        "WALLET_LOCAL_PERFORMANCE_PENALTY_MIN_RESOLVED_COPIED_BUYS",
        "15",
        minimum=0,
    )


def wallet_local_performance_penalty_max_avg_return() -> float:
    return _get_bounded_float(
        "WALLET_LOCAL_PERFORMANCE_PENALTY_MAX_AVG_RETURN",
        "-0.10",
        minimum=-1.0,
        maximum=1.0,
    )


def wallet_local_performance_penalty_size_multiplier() -> float:
    return _get_bounded_float(
        "WALLET_LOCAL_PERFORMANCE_PENALTY_SIZE_MULTIPLIER",
        "0.25",
        minimum=0.0,
        maximum=1.0,
    )


def wallet_local_drop_min_resolved_copied_buys() -> int:
    return _get_bounded_int(
        "WALLET_LOCAL_DROP_MIN_RESOLVED_COPIED_BUYS",
        "12",
        minimum=0,
    )


def wallet_local_drop_max_avg_return() -> float:
    return _get_bounded_float(
        "WALLET_LOCAL_DROP_MAX_AVG_RETURN",
        "-0.08",
        minimum=-1.0,
        maximum=1.0,
    )


def wallet_local_drop_max_total_pnl_usd() -> float:
    return _get_bounded_float(
        "WALLET_LOCAL_DROP_MAX_TOTAL_PNL_USD",
        "0.0",
        minimum=-1_000_000.0,
        maximum=1_000_000.0,
    )


def wallet_quality_size_min_multiplier() -> float:
    return _get_bounded_float("WALLET_QUALITY_SIZE_MIN_MULTIPLIER", "0.75", minimum=0.10, maximum=1.0)


def wallet_quality_size_max_multiplier() -> float:
    return _get_bounded_float("WALLET_QUALITY_SIZE_MAX_MULTIPLIER", "1.25", minimum=1.0, maximum=3.0)


def _parse_duration(raw: str, default_seconds: float) -> float:
    value = (raw or "").strip().lower()
    if not value:
        return default_seconds

    if value in {"unlimited", "infinite", "inf", "none"}:
        return float("inf")

    try:
        return max(float(value), 0.0)
    except ValueError:
        pass

    unit = value[-1]
    number = value[:-1]
    if unit not in _DURATION_UNITS or not number:
        return default_seconds

    try:
        return max(float(number) * _DURATION_UNITS[unit], 0.0)
    except ValueError:
        return default_seconds


def max_market_horizon_seconds() -> float:
    raw = _get_env_file_value("MAX_MARKET_HORIZON") or _get("MAX_MARKET_HORIZON", "6h")
    seconds = _parse_duration(raw, 6 * 3600.0)
    return seconds if seconds == float("inf") else max(60.0, seconds)


def max_market_horizon_label() -> str:
    raw = _get_env_file_value("MAX_MARKET_HORIZON") or _get("MAX_MARKET_HORIZON", "6h")
    value = (raw or "").strip().lower()
    return "unlimited" if value in {"unlimited", "infinite", "inf", "none"} else (value or "6h")


def max_source_trade_age_seconds() -> int:
    raw = _get_env_file_value("MAX_SOURCE_TRADE_AGE") or _get("MAX_SOURCE_TRADE_AGE", "10m")
    seconds = _parse_duration(raw, 10 * 60.0)
    if seconds == float("inf"):
        return 10 * 60
    return max(int(seconds), 30)


def max_source_latency_seconds() -> float:
    raw = _get_env_file_value("MAX_SOURCE_LATENCY") or _get("MAX_SOURCE_LATENCY", "5s")
    seconds = _parse_duration(raw, 5.0)
    if seconds == float("inf"):
        return 5.0
    return max(float(seconds), 0.5)


def max_feed_staleness_seconds() -> int:
    raw = _get_env_file_value("MAX_FEED_STALENESS") or _get("MAX_FEED_STALENESS", "3m")
    seconds = _parse_duration(raw, 3 * 60.0)
    if seconds == float("inf"):
        return 3 * 60
    return max(int(seconds), 30)


def max_orderbook_staleness_seconds() -> int:
    raw = _get_env_file_value("MAX_ORDERBOOK_STALENESS") or _get("MAX_ORDERBOOK_STALENESS", "3s")
    seconds = _parse_duration(raw, 3.0)
    if seconds == float("inf"):
        return 3
    return max(int(seconds), 1)


def min_execution_window_seconds() -> int:
    raw = _get_env_file_value("MIN_EXECUTION_WINDOW") or _get("MIN_EXECUTION_WINDOW", "45s")
    seconds = _parse_duration(raw, 45.0)
    if seconds == float("inf"):
        return 45
    return max(int(seconds), 10)


def private_key() -> str:
    return _get("POLYGON_PRIVATE_KEY")


def wallet_address() -> str:
    return _get("POLYGON_WALLET_ADDRESS").lower()


def model_path() -> str:
    return _get("MODEL_PATH", str(MODEL_ARTIFACT_PATH.relative_to(REPO_ROOT)))


def log_level() -> str:
    return _get("LOG_LEVEL", "INFO").upper()


def shadow_bankroll_usd() -> float:
    return _get_float("SHADOW_BANKROLL_USD", "3000")


def trade_log_archive_enabled() -> bool:
    return _get_env_file_bool("TRADE_LOG_ARCHIVE_ENABLED", "true")


def trade_log_archive_min_age_days() -> int:
    return _get_bounded_int("TRADE_LOG_ARCHIVE_MIN_AGE_DAYS", "30", minimum=1)


def trade_log_archive_batch_rows() -> int:
    return _get_bounded_int("TRADE_LOG_ARCHIVE_BATCH_ROWS", "10000", minimum=100)


def trade_log_archive_vacuum_enabled() -> bool:
    return _get_env_file_bool("TRADE_LOG_ARCHIVE_VACUUM", "true")


def max_live_drawdown_pct() -> float:
    return _get_bounded_float("MAX_LIVE_DRAWDOWN_PCT", "0.15", minimum=0.0, maximum=1.0)


def max_daily_loss_pct() -> float:
    return _get_env_file_bounded_float("MAX_DAILY_LOSS_PCT", "0.08", minimum=0.0, maximum=1.0)


def stop_loss_enabled() -> bool:
    return _get_env_file_bool("STOP_LOSS_ENABLED", "true")


def stop_loss_max_loss_pct() -> float:
    return _get_env_file_bounded_float("STOP_LOSS_MAX_LOSS_PCT", "0.15", minimum=0.0, maximum=1.0)


def stop_loss_min_hold_seconds() -> int:
    seconds = _get_duration_seconds(
        "STOP_LOSS_MIN_HOLD",
        "20m",
        minimum_seconds=0.0,
        allow_unlimited=False,
    )
    return int(seconds)


def max_total_open_exposure_fraction() -> float:
    return _get_bounded_float("MAX_TOTAL_OPEN_EXPOSURE_FRACTION", "0.60", minimum=0.0, maximum=1.0)


def exposure_override_total_cap_fraction() -> float:
    return _get_bounded_float("EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION", "0.30", minimum=0.0, maximum=1.0)


def duplicate_side_override_min_skips() -> int:
    return _get_bounded_int("DUPLICATE_SIDE_OVERRIDE_MIN_SKIPS", "20", minimum=0)


def duplicate_side_override_min_avg_return() -> float:
    return _get_bounded_float("DUPLICATE_SIDE_OVERRIDE_MIN_AVG_RETURN", "0.05", minimum=-1.0, maximum=1.0)


def exposure_override_min_skips() -> int:
    return _get_bounded_int("EXPOSURE_OVERRIDE_MIN_SKIPS", "20", minimum=0)


def exposure_override_min_avg_return() -> float:
    return _get_bounded_float("EXPOSURE_OVERRIDE_MIN_AVG_RETURN", "0.03", minimum=-1.0, maximum=1.0)


def max_market_exposure_fraction() -> float:
    return _get_bounded_float("MAX_MARKET_EXPOSURE_FRACTION", "0.20", minimum=0.0, maximum=1.0)


def max_trader_exposure_fraction() -> float:
    return _get_bounded_float("MAX_TRADER_EXPOSURE_FRACTION", "0.30", minimum=0.0, maximum=1.0)


def max_live_health_failures() -> int:
    return _get_bounded_int("MAX_LIVE_HEALTH_FAILURES", "3", minimum=1)


def live_require_shadow_history() -> bool:
    return _get_bool("LIVE_REQUIRE_SHADOW_HISTORY", "true")


def live_min_shadow_resolved() -> int:
    return _get_bounded_int("LIVE_MIN_SHADOW_RESOLVED", "50", minimum=0)


def replay_search_base_policy_file() -> str:
    raw = _get_env_file_value("REPLAY_SEARCH_BASE_POLICY_FILE") or _get("REPLAY_SEARCH_BASE_POLICY_FILE", "replay_search_specs/base_policy.json")
    return str(raw or "").strip()


def replay_search_grid_file() -> str:
    raw = _get_env_file_value("REPLAY_SEARCH_GRID_FILE") or _get("REPLAY_SEARCH_GRID_FILE", "replay_search_specs/grid.json")
    return str(raw or "").strip()


def replay_search_constraints_file() -> str:
    raw = _get_env_file_value("REPLAY_SEARCH_CONSTRAINTS_FILE") or _get("REPLAY_SEARCH_CONSTRAINTS_FILE", "replay_search_specs/constraints.json")
    return str(raw or "").strip()


def replay_search_score_weights_file() -> str:
    raw = _get_env_file_value("REPLAY_SEARCH_SCORE_WEIGHTS_FILE") or _get("REPLAY_SEARCH_SCORE_WEIGHTS_FILE", "replay_search_specs/score_weights.json")
    return str(raw or "").strip()


def _normalize_dashboard_web_url(raw: str | None) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = text.rstrip("/")
    if text.lower().endswith("/api"):
        text = text[:-4].rstrip("/")
    return text


def _dashboard_web_url_is_local(raw: str | None) -> bool:
    text = str(raw or "").strip()
    if not text:
        return True
    if "://" not in text:
        text = f"http://{text}"
    try:
        host = str(urlsplit(text).hostname or "").strip().lower()
    except Exception:
        return True
    return host in {"", "127.0.0.1", "0.0.0.0", "localhost", "::1", "::"}


def dashboard_web_url() -> str:
    raw = _get_env_file_value("DASHBOARD_WEB_URL")
    if raw is None:
        raw = _get("DASHBOARD_WEB_URL")
    normalized = _normalize_dashboard_web_url(raw)
    if normalized:
        return normalized

    for name in ("VITE_KELLY_API_BASE_URL", "KELLY_API_BASE_URL"):
        raw = _get_env_file_value(name)
        if raw is None:
            raw = _get(name)
        normalized = _normalize_dashboard_web_url(raw)
        if normalized and not _dashboard_web_url_is_local(normalized):
            return normalized
    return ""


def dashboard_api_port() -> int:
    raw = _get_env_file_value("DASHBOARD_API_PORT") or _get("DASHBOARD_API_PORT", "8765")
    try:
        return max(int(raw), 1)
    except ValueError:
        return 8765


def telegram_bot_token() -> str:
    return _get("TELEGRAM_BOT_TOKEN")


def telegram_chat_id() -> str:
    return _get("TELEGRAM_CHAT_ID")


def retrain_base_cadence() -> str:
    raw = (_get_env_file_value("RETRAIN_BASE_CADENCE") or _get("RETRAIN_BASE_CADENCE", "daily")).lower()
    return raw if raw in {"daily", "weekly"} else "daily"


def retrain_hour_local() -> int:
    raw = _get_env_file_value("RETRAIN_HOUR_LOCAL") or _get("RETRAIN_HOUR_LOCAL", "3")
    try:
        return min(max(int(raw), 0), 23)
    except ValueError:
        return 3


def retrain_early_check_seconds() -> int:
    raw = _get_env_file_value("RETRAIN_EARLY_CHECK_INTERVAL") or _get("RETRAIN_EARLY_CHECK_INTERVAL", "24h")
    seconds = _parse_duration(raw, 24 * 3600.0)
    if seconds == float("inf"):
        return 24 * 3600
    return max(int(seconds), 3600)


def retrain_min_new_labels() -> int:
    raw = _get_env_file_value("RETRAIN_MIN_NEW_LABELS") or _get("RETRAIN_MIN_NEW_LABELS", "100")
    try:
        return max(int(raw), 1)
    except ValueError:
        return 100


def retrain_min_samples() -> int:
    raw = _get_env_file_value("RETRAIN_MIN_SAMPLES") or _get("RETRAIN_MIN_SAMPLES", "200")
    try:
        return max(int(raw), 1)
    except ValueError:
        return 200


def replay_search_base_cadence() -> str:
    raw = (_get_env_file_value("REPLAY_SEARCH_BASE_CADENCE") or _get("REPLAY_SEARCH_BASE_CADENCE", "off")).lower()
    return raw if raw in {"off", "daily", "weekly"} else "off"


def replay_search_hour_local() -> int:
    raw = (
        _get_env_file_value("REPLAY_SEARCH_SCHEDULE_HOUR_LOCAL")
        or _get_env_file_value("REPLAY_SEARCH_HOUR_LOCAL")
        or _get("REPLAY_SEARCH_SCHEDULE_HOUR_LOCAL", "")
        or _get("REPLAY_SEARCH_HOUR_LOCAL", "5")
    )
    try:
        return min(max(int(raw), 0), 23)
    except ValueError:
        return 5


def replay_search_label_prefix() -> str:
    return str(_get_env_file_value("REPLAY_SEARCH_LABEL_PREFIX") or _get("REPLAY_SEARCH_LABEL_PREFIX", "scheduled")).strip() or "scheduled"


def replay_search_notes() -> str:
    return str(_get_env_file_value("REPLAY_SEARCH_NOTES") or _get("REPLAY_SEARCH_NOTES", "")).strip()


def replay_search_base_policy() -> dict[str, Any]:
    payload = _load_json_object_file(replay_search_base_policy_file())
    payload.update(_get_env_file_json_object("REPLAY_SEARCH_BASE_POLICY_JSON"))
    return payload


def replay_search_grid() -> dict[str, Any]:
    payload = _load_json_object_file(replay_search_grid_file())
    payload.update(_get_env_file_json_object("REPLAY_SEARCH_GRID_JSON"))
    return payload


def replay_search_constraints() -> dict[str, Any]:
    payload = _load_json_object_file(replay_search_constraints_file())
    payload.update(_get_env_file_json_object("REPLAY_SEARCH_CONSTRAINTS_JSON"))
    return payload


def replay_search_score_weights() -> dict[str, Any]:
    payload = _load_json_object_file(replay_search_score_weights_file())
    payload.update(_get_env_file_json_object("REPLAY_SEARCH_SCORE_WEIGHTS_JSON"))
    return validate_replay_search_score_weight_payload(payload, error_cls=ConfigError)


def replay_search_top() -> int:
    raw = _get_env_file_value("REPLAY_SEARCH_TOP") or _get("REPLAY_SEARCH_TOP", "10")
    try:
        return max(int(raw), 1)
    except ValueError:
        return 10


def replay_search_max_combos() -> int:
    raw = _get_env_file_value("REPLAY_SEARCH_MAX_COMBOS") or _get("REPLAY_SEARCH_MAX_COMBOS", "256")
    try:
        return max(int(raw), 1)
    except ValueError:
        return 256


def replay_search_window_days() -> int:
    raw = _get_env_file_value("REPLAY_SEARCH_WINDOW_DAYS") or _get("REPLAY_SEARCH_WINDOW_DAYS", "14")
    try:
        return max(int(raw), 0)
    except ValueError:
        return 14


def replay_search_window_count() -> int:
    raw = _get_env_file_value("REPLAY_SEARCH_WINDOW_COUNT") or _get("REPLAY_SEARCH_WINDOW_COUNT", "6")
    try:
        return max(int(raw), 1)
    except ValueError:
        return 6


def replay_auto_promote() -> bool:
    if _get_env_file_value("REPLAY_AUTO_PROMOTE_ENABLED") is not None or _get("REPLAY_AUTO_PROMOTE_ENABLED"):
        return _get_env_file_bool("REPLAY_AUTO_PROMOTE_ENABLED", "true")
    return _get_env_file_bool("REPLAY_AUTO_PROMOTE", "true")


def replay_auto_promote_min_score_delta() -> float:
    return _get_env_file_bounded_float("REPLAY_AUTO_PROMOTE_MIN_SCORE_DELTA", "0")


def replay_auto_promote_min_pnl_delta_usd() -> float:
    return _get_env_file_bounded_float("REPLAY_AUTO_PROMOTE_MIN_PNL_DELTA_USD", "0")


def live_min_shadow_resolved_since_promotion() -> int:
    raw = _get_env_file_value("LIVE_MIN_SHADOW_RESOLVED_SINCE_PROMOTION") or _get("LIVE_MIN_SHADOW_RESOLVED_SINCE_PROMOTION", "0")
    try:
        return max(int(raw), 0)
    except ValueError:
        return 0


def entry_price_band_label(value: float | None) -> str:
    return _entry_price_band_label(value)


def time_to_close_band_label(seconds: int) -> str:
    return _time_to_close_band_label(seconds)


def watched_wallets() -> list[str]:
    # Legacy bootstrap helper. Steady-state wallet membership now lives in SQLite.
    raw = _get("WATCHED_WALLETS")
    if not raw:
        return []
    seen: set[str] = set()
    wallets: list[str] = []
    for value in raw.split(","):
        wallet = value.strip().lower()
        if wallet and wallet not in seen:
            seen.add(wallet)
            wallets.append(wallet)
    return wallets
