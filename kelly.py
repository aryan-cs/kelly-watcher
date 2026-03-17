from __future__ import annotations

from config import max_bet_fraction, min_bet_usd, min_confidence

KELLY_FRACTION = 0.5


def kelly_size(confidence: float, market_price: float, bankroll_usd: float) -> dict:
    if confidence < min_confidence():
        return _no_bet(f"conf {confidence:.3f} < min {min_confidence():.2f}")

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


def heuristic_size(score: float, bankroll_usd: float) -> dict:
    if score < min_confidence():
        return _no_bet(f"score {score:.3f} < min {min_confidence():.2f}")

    if bankroll_usd <= 0:
        return _no_bet("bankroll depleted")

    span = max(1.0 - min_confidence(), 1e-6)
    edge = min(max((score - min_confidence()) / span, 0.0), 1.0)

    # Heuristic scores are ranking signals, not calibrated probabilities.
    # Scale position size by score margin instead of running raw Kelly on them.
    fraction = max_bet_fraction() * edge
    size = round(bankroll_usd * fraction, 2)
    adjusted_size, reject_reason = _apply_minimum_bet(size, bankroll_usd)
    if reject_reason:
        return _no_bet(reject_reason)

    return {
        "dollar_size": adjusted_size,
        "kelly_f": round(fraction, 5),
        "full_kelly_f": 0.0,
        "method": "heuristic",
        "reason": "ok",
    }


def size_signal(confidence: float, market_price: float, bankroll_usd: float, mode: str) -> dict:
    if mode == "xgboost":
        return kelly_size(confidence, market_price, bankroll_usd)
    return heuristic_size(confidence, bankroll_usd)


def _no_bet(reason: str) -> dict:
    return {
        "dollar_size": 0.0,
        "kelly_f": 0.0,
        "full_kelly_f": 0.0,
        "method": "none",
        "reason": reason,
    }


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
