from __future__ import annotations

import math
from typing import Any

import numpy as np

EXECUTED_SAMPLE_WEIGHT = 1.0
COUNTERFACTUAL_SAMPLE_WEIGHT = 0.25
MAX_COUNTERFACTUAL_TO_EXECUTED_WEIGHT_RATIO = 1.0
MIN_CONFIDENCE_CLIP = 1e-4
MAX_CONFIDENCE_CLIP = 1.0 - MIN_CONFIDENCE_CLIP


def sample_weight_for_trade(*, skipped: Any) -> float:
    return COUNTERFACTUAL_SAMPLE_WEIGHT if bool(skipped) else EXECUTED_SAMPLE_WEIGHT


def rebalance_training_sample_weights(skipped_flags: Any):
    skipped = np.asarray(skipped_flags, dtype=bool).reshape(-1)
    weights = np.where(skipped, COUNTERFACTUAL_SAMPLE_WEIGHT, EXECUTED_SAMPLE_WEIGHT).astype(float)
    executed_count = int((~skipped).sum())
    counterfactual_count = int(skipped.sum())
    if executed_count <= 0 or counterfactual_count <= 0:
        return weights

    executed_total = executed_count * EXECUTED_SAMPLE_WEIGHT
    counterfactual_total = counterfactual_count * COUNTERFACTUAL_SAMPLE_WEIGHT
    max_counterfactual_total = executed_total * MAX_COUNTERFACTUAL_TO_EXECUTED_WEIGHT_RATIO
    if counterfactual_total <= max_counterfactual_total:
        return weights

    scale = max_counterfactual_total / counterfactual_total
    weights[skipped] *= scale
    return weights


def transform_return_target(value: float) -> float:
    if value is None:
        return float("nan")
    numeric = float(value)
    if not math.isfinite(numeric):
        return float("nan")
    if numeric == 0.0:
        return 0.0
    return math.copysign(math.log1p(abs(numeric)), numeric)


def inverse_return_target(value: Any):
    values = np.asarray(value, dtype=float)
    restored = np.sign(values) * np.expm1(np.abs(values))
    if np.ndim(restored) == 0:
        return float(restored)
    return restored


def clip_confidence(value: Any):
    values = np.asarray(value, dtype=float)
    clipped = np.where(
        np.isfinite(values),
        np.clip(values, MIN_CONFIDENCE_CLIP, MAX_CONFIDENCE_CLIP),
        np.nan,
    )
    if np.ndim(clipped) == 0:
        return float(clipped)
    return clipped


def expected_return_to_confidence(expected_return: Any, price: Any):
    returns = np.asarray(expected_return, dtype=float)
    raw_prices = np.asarray(price, dtype=float)
    prices = np.clip(raw_prices, 0.01, 0.99)
    implied = np.where(
        np.isfinite(returns) & np.isfinite(raw_prices),
        prices * (1.0 + returns),
        np.nan,
    )
    return clip_confidence(implied)


def apply_probability_calibrator(calibrator: Any, base_confidence: Any):
    values = np.asarray(base_confidence, dtype=float).reshape(-1)
    if calibrator is None:
        calibrated = values
    elif hasattr(calibrator, "predict_proba"):
        calibrated = calibrator.predict_proba(values.reshape(-1, 1))[:, 1]
    else:
        calibrated = calibrator.predict(values)
    clipped = clip_confidence(calibrated)
    if np.ndim(np.asarray(base_confidence)) == 0:
        return float(np.asarray(clipped).reshape(-1)[0])
    return np.asarray(clipped, dtype=float)
