from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

load_dotenv()
ENV_PATH = Path(__file__).resolve().with_name(".env")
_DURATION_UNITS = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_bool(name: str, default: str = "false") -> bool:
    return _get(name, default).lower() in {"1", "true", "yes", "on"}


def _get_env_file_value(name: str) -> str | None:
    if not ENV_PATH.exists():
        return None

    try:
        value = dotenv_values(ENV_PATH).get(name)
    except Exception:
        return None

    if value is None:
        return None
    return str(value).strip()


def use_real_money() -> bool:
    return _get_bool("USE_REAL_MONEY", "false")


def max_bet_fraction() -> float:
    return float(_get("MAX_BET_FRACTION", "0.05"))


def min_confidence() -> float:
    return float(_get("MIN_CONFIDENCE", "0.60"))


def min_bet_usd() -> float:
    return float(_get("MIN_BET_USD", "1.00"))


def poll_interval() -> float:
    raw = _get_env_file_value("POLL_INTERVAL_SECONDS") or _get("POLL_INTERVAL_SECONDS", "45")
    try:
        return max(0.05, float(raw))
    except ValueError:
        return 45.0


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
    raw = _get_env_file_value("MAX_MARKET_HORIZON") or _get("MAX_MARKET_HORIZON", "365d")
    seconds = _parse_duration(raw, 365 * 86400.0)
    return seconds if seconds == float("inf") else max(60.0, seconds)


def max_market_horizon_label() -> str:
    raw = _get_env_file_value("MAX_MARKET_HORIZON") or _get("MAX_MARKET_HORIZON", "365d")
    value = (raw or "").strip().lower()
    return "unlimited" if value in {"unlimited", "infinite", "inf", "none"} else (value or "365d")


def private_key() -> str:
    return _get("POLYGON_PRIVATE_KEY")


def wallet_address() -> str:
    return _get("POLYGON_WALLET_ADDRESS").lower()


def model_path() -> str:
    return _get("MODEL_PATH", "model.joblib")


def shadow_bankroll_usd() -> float:
    return float(_get("SHADOW_BANKROLL_USD", "1000"))


def max_live_drawdown_pct() -> float:
    raw = _get("MAX_LIVE_DRAWDOWN_PCT", "0.15")
    try:
        return min(max(float(raw), 0.0), 1.0)
    except ValueError:
        return 0.15


def live_require_shadow_history() -> bool:
    return _get_bool("LIVE_REQUIRE_SHADOW_HISTORY", "true")


def live_min_shadow_resolved() -> int:
    raw = _get("LIVE_MIN_SHADOW_RESOLVED", "50")
    try:
        return max(int(raw), 0)
    except ValueError:
        return 50


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


@lru_cache(maxsize=1)
def watched_wallets() -> list[str]:
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
