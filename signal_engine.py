from __future__ import annotations

import logging
import os

import numpy as np

from adaptive_confidence import adaptive_min_confidence_for_signal
from beliefs import adjust_heuristic_confidence
from config import model_path
from economic_model import apply_probability_calibrator, expected_return_to_confidence, inverse_return_target
from features import FEATURE_COLS, build_feature_map
from market_scorer import MarketFeatures, MarketScorer
from trade_contract import DATA_CONTRACT_VERSION, MODEL_LABEL_MODE
from trader_scorer import TraderFeatures, TraderScorer

logger = logging.getLogger(__name__)

TRADER_WEIGHT = 0.60
MARKET_WEIGHT = 0.40


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
        self._try_load_xgb()

    def _try_load_xgb(self) -> None:
        path = model_path()
        if not os.path.exists(path):
            logger.info("No XGBoost model found - using heuristic scorer")
            return
        try:
            import joblib

            artifact = joblib.load(path)
            if isinstance(artifact, dict):
                contract_version = int(artifact.get("data_contract_version") or 0)
                if (
                    contract_version < DATA_CONTRACT_VERSION
                    or artifact.get("label_mode") != MODEL_LABEL_MODE
                ):
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
            else:
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
        passed = adjusted >= min_floor

        return {
            "confidence": adjusted,
            "raw_confidence": round(combined, 4),
            "belief_prior": belief.prior_confidence,
            "belief_blend": belief.blend,
            "belief_evidence": belief.evidence,
            "min_confidence": min_floor,
            "adaptive_floor": adaptive_floor.as_dict(),
            "passed": passed,
            "reason": "passed heuristic threshold" if passed else f"heuristic conf {adjusted:.3f} < min {min_floor:.3f}",
            "veto": None,
            "mode": "heuristic",
            "trader": trader_result,
            "market": market_result,
        }

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
        edge_threshold = float(self._xgb_policy.get("edge_threshold", 0.0))
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
            "passed": passed,
            "reason": reason,
            "veto": None,
            "mode": "xgboost",
            "trader": trader_result,
            "market": market_result,
        }
