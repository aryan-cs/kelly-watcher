from __future__ import annotations

import logging
import os
import time

import numpy as np

from adaptive_confidence import adaptive_min_confidence_for_signal
from beliefs import adjust_heuristic_confidence
from config import (
    heuristic_max_entry_price,
    heuristic_min_entry_price,
    model_edge_high_confidence,
    model_edge_high_threshold,
    model_edge_mid_confidence,
    model_edge_mid_threshold,
    model_path,
)
from economic_model import apply_probability_calibrator, expected_return_to_confidence, inverse_return_target
from features import FEATURE_COLS, build_feature_map
from market_scorer import MarketFeatures, MarketScorer
from trade_contract import DATA_CONTRACT_VERSION, MODEL_LABEL_MODE
from trader_scorer import TraderFeatures, TraderScorer

logger = logging.getLogger(__name__)

TRADER_WEIGHT = 0.60
MARKET_WEIGHT = 0.40
HEURISTIC_MIN_MARKET_SCORE_LOW_EDGE = 0.70
HEURISTIC_MIN_MARKET_SCORE_HIGH_EDGE = 0.60


class SignalEngine:
    def __init__(self):
        self.trader_scorer = TraderScorer()
        self.market_scorer = MarketScorer()
        self._xgb = None
        self._xgb_cols = FEATURE_COLS
        self._xgb_policy = {"edge_threshold": 0.0}
        self._xgb_probability_calibrator = None
        self._xgb_prediction_mode = "probability"
        self._model_backend = "heuristic"
        self._artifact_backend = None
        self._artifact_contract_version = None
        self._artifact_label_mode = None
        self._artifact_prediction_mode = None
        self._artifact_exists = False
        self._artifact_path = ""
        self._fallback_reason = "missing_artifact"
        self._load_error = ""
        self._loaded_at = 0
        self._try_load_xgb()

    def _try_load_xgb(self) -> None:
        path = model_path()
        self._artifact_path = path
        self._artifact_exists = os.path.exists(path)
        self._artifact_backend = None
        self._artifact_contract_version = None
        self._artifact_label_mode = None
        self._artifact_prediction_mode = None
        self._fallback_reason = ""
        self._load_error = ""
        self._loaded_at = 0
        if not os.path.exists(path):
            self._fallback_reason = "missing_artifact"
            logger.info("No XGBoost model found - using heuristic scorer")
            return
        try:
            import joblib

            artifact = joblib.load(path)
            if isinstance(artifact, dict):
                contract_version = int(artifact.get("data_contract_version") or 0)
                self._artifact_contract_version = contract_version
                self._artifact_label_mode = str(artifact.get("label_mode") or "") or None
                self._artifact_prediction_mode = str(artifact.get("prediction_mode") or "probability")
                self._artifact_backend = str(artifact.get("model_backend") or "ml")
                if contract_version < DATA_CONTRACT_VERSION:
                    self._fallback_reason = "contract_mismatch"
                    logger.warning(
                        "Ignoring legacy model at %s because it was not trained under the current training-label contract",
                        path,
                    )
                    return
                if artifact.get("label_mode") != MODEL_LABEL_MODE:
                    self._fallback_reason = "label_mode_mismatch"
                    logger.warning(
                        "Ignoring legacy model at %s because it was not trained under the current training-label contract",
                        path,
                    )
                    return
                self._xgb = artifact.get("model")
                self._xgb_probability_calibrator = artifact.get("probability_calibrator")
                self._xgb_prediction_mode = str(artifact.get("prediction_mode") or "probability")
                self._xgb_cols = artifact.get("feature_cols", FEATURE_COLS)
                self._xgb_policy = artifact.get("policy", {"edge_threshold": 0.0})
                self._model_backend = artifact.get("model_backend", "ml")
                self._loaded_at = int(time.time())
            else:
                self._fallback_reason = "legacy_artifact_type"
                logger.warning("Ignoring legacy tuple model artifact at %s", path)
                return
            logger.info("%s model loaded from %s", self._model_backend, path)
        except Exception as exc:
            self._xgb = None
            self._xgb_cols = FEATURE_COLS
            self._xgb_policy = {"edge_threshold": 0.0}
            self._xgb_probability_calibrator = None
            self._xgb_prediction_mode = "probability"
            self._model_backend = "heuristic"
            self._fallback_reason = "load_failed"
            self._load_error = str(exc)
            logger.warning("Failed to load trained model: %s - using heuristic scorer", exc)

    def reload_model(self) -> None:
        self._xgb = None
        self._xgb_cols = FEATURE_COLS
        self._xgb_policy = {"edge_threshold": 0.0}
        self._xgb_probability_calibrator = None
        self._xgb_prediction_mode = "probability"
        self._model_backend = "heuristic"
        self._try_load_xgb()

    def sizing_mode(self) -> str:
        return "xgboost" if self._xgb is not None else "heuristic"

    def runtime_info(self) -> dict:
        artifact_backend = self._artifact_backend
        if artifact_backend is None and self._artifact_exists:
            artifact_backend = "unknown"
        return {
            "loaded_scorer": self.sizing_mode(),
            "loaded_model_backend": self._model_backend,
            "model_artifact_exists": bool(self._artifact_exists),
            "model_artifact_path": self._artifact_path,
            "model_artifact_backend": artifact_backend,
            "model_artifact_contract": self._artifact_contract_version,
            "runtime_contract": DATA_CONTRACT_VERSION,
            "model_artifact_label_mode": self._artifact_label_mode,
            "runtime_label_mode": MODEL_LABEL_MODE,
            "model_runtime_compatible": bool(self._xgb is not None),
            "model_fallback_reason": self._fallback_reason,
            "model_load_error": self._load_error,
            "model_prediction_mode": self._artifact_prediction_mode or self._xgb_prediction_mode,
            "model_loaded_at": self._loaded_at,
        }

    def evaluate(
        self,
        trader_features: TraderFeatures,
        market_features: MarketFeatures,
        order_size_usd: float = 10.0,
        trader_address: str | None = None,
    ) -> dict:
        market_result = self.market_scorer.score(market_features)
        if market_result["veto"]:
            return {
                "confidence": 0.0,
                "passed": False,
                "veto": market_result["veto"],
                "mode": "veto",
                "trader": {},
                "market": market_result,
            }

        if self._xgb is not None:
            return self._evaluate_xgb(trader_features, market_features, order_size_usd)

        return self._evaluate_heuristic(
            trader_features,
            market_features,
            market_result,
            trader_address=trader_address,
        )

    def _evaluate_heuristic(
        self,
        trader_features: TraderFeatures,
        market_features: MarketFeatures,
        market_result: dict,
        *,
        trader_address: str | None = None,
    ) -> dict:
        trader_result = self.trader_scorer.score(trader_features)
        trader_score = trader_result["score"]
        market_score = market_result["score"]

        if trader_score <= 0 or market_score <= 0:
            combined = 0.0
        else:
            combined = float(
                np.exp(TRADER_WEIGHT * np.log(trader_score) + MARKET_WEIGHT * np.log(market_score))
            )
        belief = adjust_heuristic_confidence(combined, trader_features, market_features)
        adjusted = belief.adjusted_confidence
        adaptive_floor = adaptive_min_confidence_for_signal(
            days_to_res=market_features.days_to_res,
            trader_address=trader_address,
        )
        min_floor = adaptive_floor.floor
        execution_price = (
            market_features.execution_price
            if 0.0 < market_features.execution_price < 1.0
            else market_features.mid
        )
        min_entry_price = heuristic_min_entry_price()
        max_entry_price = heuristic_max_entry_price()
        min_market_score, band_progress = self._heuristic_min_market_score(
            execution_price,
            min_entry_price,
            max_entry_price,
        )
        passed_confidence = adjusted >= min_floor
        passed_entry_price = execution_price >= min_entry_price and execution_price < max_entry_price
        passed_market_score = market_score >= min_market_score
        passed = passed_confidence and passed_entry_price and passed_market_score
        if not passed_entry_price:
            reason = (
                f"heuristic entry price {execution_price:.3f} outside band "
                f"{min_entry_price:.3f}-{max_entry_price:.3f}"
            )
        elif not passed_market_score:
            reason = f"heuristic market score {market_score:.3f} < min {min_market_score:.3f}"
        elif not passed_confidence:
            reason = f"heuristic conf {adjusted:.3f} < min {min_floor:.3f}"
        else:
            reason = "passed heuristic threshold"

        return {
            "confidence": adjusted,
            "raw_confidence": round(combined, 4),
            "belief_prior": belief.prior_confidence,
            "belief_blend": belief.blend,
            "belief_evidence": belief.evidence,
            "min_confidence": min_floor,
            "entry_price": round(execution_price, 4),
            "min_entry_price": round(min_entry_price, 4),
            "max_entry_price": round(max_entry_price, 4),
            "min_market_score": round(min_market_score, 4),
            "band_progress": round(band_progress, 4),
            "adaptive_floor": adaptive_floor.as_dict(),
            "passed": passed,
            "reason": reason,
            "veto": None,
            "mode": "heuristic",
            "trader": trader_result,
            "market": market_result,
        }

    @staticmethod
    def _heuristic_min_market_score(
        execution_price: float,
        min_entry_price: float,
        max_entry_price: float,
    ) -> tuple[float, float]:
        band_span = max(max_entry_price - min_entry_price, 0.0)
        if band_span <= 1e-6:
            return 0.65, 1.0

        band_progress = float(np.clip((execution_price - min_entry_price) / band_span, 0.0, 1.0))
        required_market_score = float(
            np.interp(
                band_progress,
                [0.0, 1.0],
                [HEURISTIC_MIN_MARKET_SCORE_LOW_EDGE, HEURISTIC_MIN_MARKET_SCORE_HIGH_EDGE],
            )
        )
        return required_market_score, band_progress

    def _evaluate_xgb(
        self,
        trader_features: TraderFeatures,
        market_features: MarketFeatures,
        _order_size_usd: float,
    ) -> dict:
        trader_result = self.trader_scorer.score(trader_features)
        market_result = self.market_scorer.score(market_features)
        feature_map = build_feature_map(trader_features, market_features)

        ordered = np.array(
            [
                [
                    np.nan
                    if feature_map.get(column) is None
                    else float(feature_map.get(column))
                    for column in self._xgb_cols
                ]
            ],
            dtype=float,
        )
        execution_price = market_features.execution_price if market_features.execution_price > 0 else market_features.mid
        if self._xgb_prediction_mode == "expected_return":
            expected_return = float(inverse_return_target(self._xgb.predict(ordered)[0]))
            base_confidence = float(expected_return_to_confidence(expected_return, execution_price))
            confidence = float(
                apply_probability_calibrator(
                    self._xgb_probability_calibrator,
                    base_confidence,
                )
            )
        else:
            expected_return = None
            base_confidence = None
            confidence = float(self._xgb.predict_proba(ordered)[0, 1])
        edge = confidence - execution_price
        base_edge_threshold = float(self._xgb_policy.get("edge_threshold", 0.0))
        edge_threshold = base_edge_threshold
        if confidence >= model_edge_high_confidence():
            edge_threshold = model_edge_high_threshold()
        elif confidence >= model_edge_mid_confidence():
            edge_threshold = model_edge_mid_threshold()
        passed = edge >= edge_threshold
        reason = (
            "passed model edge threshold"
            if passed
            else f"model edge {edge:.3f} < threshold {edge_threshold:.3f}"
        )
        return {
            "confidence": round(confidence, 4),
            "raw_confidence": round(base_confidence if base_confidence is not None else confidence, 4),
            "expected_return": round(expected_return, 4) if expected_return is not None else None,
            "edge": round(edge, 4),
            "edge_threshold": round(edge_threshold, 4),
            "base_edge_threshold": round(base_edge_threshold, 4),
            "passed": passed,
            "reason": reason,
            "veto": None,
            "mode": "xgboost",
            "trader": trader_result,
            "market": market_result,
        }
