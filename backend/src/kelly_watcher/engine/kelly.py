from __future__ import annotations

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
    threshold = _effective_min_confidence(min_confidence_override)
    if score < threshold:
        return _no_bet(f"score {score:.3f} < min {threshold:.3f}")

    if bankroll_usd <= 0:
        return _no_bet("bankroll depleted")

    span = max(1.0 - threshold, 1e-6)
    raw_edge = min(max((score - threshold) / span, 0.0), 1.0)
    price_drag = 0.0
    if (
        quoted_market_price is not None
        and effective_market_price is not None
        and quoted_market_price > 0
        and effective_market_price > 0
    ):
        price_drag = max(float(effective_market_price) - float(quoted_market_price), 0.0)
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
        float(effective_market_price)
        if effective_market_price is not None
        else float(quoted_market_price)
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
    return max(0.0, min(threshold, 1.0))


def _apply_minimum_bet(size: float, bankroll_usd: float) -> tuple[float, str | None]:
    min_bet = min_bet_usd()
    max_size = round(bankroll_usd * max_bet_fraction(), 2)

    if size <= 0:
        return 0.0, f"size ${size:.2f} <= 0"

    if size >= min_bet:
        return size, None

    if bankroll_usd < min_bet:
        return 0.0, f"available bankroll ${bankroll_usd:.2f} < min ${min_bet:.2f}"

    if max_size < min_bet:
        return 0.0, f"max size ${max_size:.2f} < min ${min_bet:.2f}"

    return round(min_bet, 2), None
