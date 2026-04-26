from __future__ import annotations

import math

from kelly_watcher.config import max_bet_fraction, min_bet_usd, min_confidence

KELLY_FRACTION = 0.5
HEURISTIC_EDGE_EXPONENT = 0.5


def kelly_size(
    confidence: float,
    market_price: float,
    bankroll_usd: float,
    *,
    min_confidence_override: float | None = None,
) -> dict:
    try:
        confidence = float(confidence)
        market_price = float(market_price)
        bankroll_usd = float(bankroll_usd)
    except (TypeError, ValueError):
        return _no_bet("invalid numeric input")
    if not math.isfinite(confidence):
        return _no_bet("non-finite confidence")
    if not math.isfinite(market_price):
        return _no_bet("non-finite market price")
    if not math.isfinite(bankroll_usd):
        return _no_bet("non-finite bankroll")
    confidence = max(0.0, min(confidence, 1.0))

    threshold = _effective_min_confidence(min_confidence_override)
    if confidence < threshold:
        return _no_bet(f"conf {confidence:.3f} < min {threshold:.3f}")

    if bankroll_usd <= 0:
        return _no_bet("bankroll depleted")

    if not (0.01 < market_price < 0.99):
        return _no_bet(f"invalid price {market_price:.3f}")

    b = (1 - market_price) / market_price
    f_star = (confidence * (b + 1) - 1) / b
    if f_star <= 0:
        return _no_bet("negative Kelly - no edge at this price/confidence")

    f_scaled = f_star * KELLY_FRACTION
    f_capped = min(f_scaled, max_bet_fraction())
    size = round(bankroll_usd * f_capped, 2)
    adjusted_size, reject_reason = _apply_minimum_bet(size, bankroll_usd)
    if reject_reason:
        return _no_bet(reject_reason)

    return {
        "dollar_size": adjusted_size,
        "kelly_f": round(f_capped, 5),
        "full_kelly_f": round(f_star, 5),
        "method": "kelly",
        "reason": "ok",
    }


def heuristic_size(
    score: float,
    bankroll_usd: float,
    *,
    quoted_market_price: float | None = None,
    effective_market_price: float | None = None,
    min_confidence_override: float | None = None,
) -> dict:
    try:
        score = float(score)
        bankroll_usd = float(bankroll_usd)
    except (TypeError, ValueError):
        return _no_bet("invalid numeric input")
    if not math.isfinite(score):
        return _no_bet("non-finite score")
    if not math.isfinite(bankroll_usd):
        return _no_bet("non-finite bankroll")
    score = max(0.0, min(score, 1.0))

    threshold = _effective_min_confidence(min_confidence_override)
    if score < threshold:
        return _no_bet(f"score {score:.3f} < min {threshold:.3f}")

    if bankroll_usd <= 0:
        return _no_bet("bankroll depleted")

    quoted_price, quoted_reject = _optional_probability_price(quoted_market_price, "quoted market price")
    if quoted_reject:
        return _no_bet(quoted_reject)
    effective_price, effective_reject = _optional_probability_price(effective_market_price, "effective market price")
    if effective_reject:
        return _no_bet(effective_reject)

    span = max(1.0 - threshold, 1e-6)
    raw_edge = min(max((score - threshold) / span, 0.0), 1.0)
    price_drag = 0.0
    if quoted_price is not None and effective_price is not None:
        price_drag = max(effective_price - quoted_price, 0.0)
        raw_edge = max(raw_edge - price_drag, 0.0)
        if raw_edge <= 0:
            return _no_bet(
                f"heuristic edge {score - threshold:.3f} <= execution drag {price_drag:.3f}"
            )

    # Heuristic scores are ranking signals, not calibrated probabilities.
    # Expand small but valid score margins so the ranking signal does not
    # collapse most trades back onto the minimum bet floor.
    edge = raw_edge**HEURISTIC_EDGE_EXPONENT
    fraction = max_bet_fraction() * edge
    size = round(bankroll_usd * fraction, 2)
    adjusted_size, reject_reason = _apply_minimum_bet(size, bankroll_usd)
    if reject_reason:
        return _no_bet(reject_reason)

    return {
        "dollar_size": adjusted_size,
        "kelly_f": round(fraction, 5),
        "full_kelly_f": 0.0,
        "heuristic_raw_edge": round(raw_edge, 5),
        "heuristic_size_edge": round(edge, 5),
        "execution_price_drag": round(price_drag, 5),
        "method": "heuristic",
        "reason": "ok",
    }


def size_signal(
    confidence: float,
    quoted_market_price: float,
    bankroll_usd: float,
    mode: str,
    *,
    effective_market_price: float | None = None,
    min_confidence_override: float | None = None,
) -> dict:
    market_price = (
        effective_market_price
        if effective_market_price is not None
        else quoted_market_price
    )
    if mode == "xgboost":
        return kelly_size(
            confidence,
            market_price,
            bankroll_usd,
            min_confidence_override=min_confidence_override,
        )
    return heuristic_size(
        confidence,
        bankroll_usd,
        quoted_market_price=quoted_market_price,
        effective_market_price=market_price,
        min_confidence_override=min_confidence_override,
    )


def _no_bet(reason: str) -> dict:
    return {
        "dollar_size": 0.0,
        "kelly_f": 0.0,
        "full_kelly_f": 0.0,
        "method": "none",
        "reason": reason,
    }


def _effective_min_confidence(min_confidence_override: float | None) -> float:
    threshold = min_confidence() if min_confidence_override is None else float(min_confidence_override)
    if not math.isfinite(threshold):
        threshold = 1.0
    return max(0.0, min(threshold, 1.0))


def _optional_probability_price(value: float | None, label: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None, f"invalid {label}"
    if not math.isfinite(numeric):
        return None, f"non-finite {label}"
    if not (0.0 < numeric < 1.0):
        return None, f"invalid {label} {numeric:.3f}"
    return numeric, None


def _apply_minimum_bet(size: float, bankroll_usd: float) -> tuple[float, str | None]:
    min_bet = min_bet_usd()
    max_size = round(bankroll_usd * max_bet_fraction(), 2)
    if not math.isfinite(size):
        return 0.0, "non-finite size"
    if not math.isfinite(min_bet) or min_bet <= 0:
        return 0.0, "invalid minimum bet"
    if not math.isfinite(max_size) or max_size <= 0:
        return 0.0, "invalid max size"

    if size <= 0:
        return 0.0, f"size ${size:.2f} <= 0"

    if size >= min_bet:
        return size, None

    if bankroll_usd < min_bet:
        return 0.0, f"available bankroll ${bankroll_usd:.2f} < min ${min_bet:.2f}"

    if max_size < min_bet:
        return 0.0, f"max size ${max_size:.2f} < min ${min_bet:.2f}"

    return 0.0, f"computed size ${size:.2f} < min ${min_bet:.2f}"
