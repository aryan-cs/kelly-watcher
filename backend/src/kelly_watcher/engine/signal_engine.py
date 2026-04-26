from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import time

import numpy as np

from kelly_watcher.engine.adaptive_confidence import adaptive_min_confidence_for_signal
from kelly_watcher.engine.beliefs import adjust_heuristic_confidence
from kelly_watcher.config import (
    allow_heuristic,
    allow_xgboost,
    allowed_entry_price_bands,
    allowed_time_to_close_bands,
    entry_price_band_label,
    heuristic_max_entry_price,
    heuristic_allowed_entry_price_bands,
    heuristic_min_entry_price,
    heuristic_min_time_to_close_seconds,
    model_edge_high_confidence,
    model_edge_high_threshold,
    model_edge_mid_confidence,
    model_edge_mid_threshold,
    model_min_time_to_close_seconds,
    model_path,
    time_to_close_band_label,
    use_real_money,
    xgboost_allowed_entry_price_bands,
)
from kelly_watcher.engine.economic_model import apply_probability_calibrator, expected_return_to_confidence, inverse_return_target
from kelly_watcher.engine.features import FEATURE_COLS, build_feature_map
from kelly_watcher.engine.market_scorer import MarketFeatures, MarketScorer
from kelly_watcher.engine.segment_policy import (
    SEGMENT_FALLBACK,
    SegmentRoute,
    segment_route_for_trade,
)
from kelly_watcher.engine.shadow_evidence import read_shadow_evidence_epoch
from kelly_watcher.engine.trade_contract import DATA_CONTRACT_VERSION, DEFAULT_EXPERIMENT_ARM, MODEL_LABEL_MODE
from kelly_watcher.engine.trader_scorer import TraderFeatures, TraderScorer

logger = logging.getLogger(__name__)

TRADER_WEIGHT = 0.60
MARKET_WEIGHT = 0.40
HEURISTIC_MIN_MARKET_SCORE_LOW_EDGE = 0.70
HEURISTIC_MIN_MARKET_SCORE_HIGH_EDGE = 0.60
SEGMENT_POLICY_ID = "shadow-runtime-segment-policy-v1"
SEGMENT_POLICY_BUNDLE_VERSION = 1
_ARTIFACT_BOOL_TRUE = {"1", "true", "t", "yes", "y", "on"}
_ARTIFACT_BOOL_FALSE = {"0", "false", "f", "no", "n", "off", ""}


def _artifact_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _ARTIFACT_BOOL_TRUE:
            return True
        if normalized in _ARTIFACT_BOOL_FALSE:
            return False
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(numeric):
        return False
    return bool(numeric)


@dataclass(frozen=True)
class SegmentRuntimePolicy:
    segment_id: str
    policy_id: str
    policy_bundle_version: int
    watch_tier: str
    horizon_bucket: str
    fallback: bool
    allowed_entry_price_bands: tuple[str, ...]
    allowed_time_to_close_bands: tuple[str, ...]
    heuristic_allowed_entry_price_bands: tuple[str, ...]
    heuristic_min_entry_price: float
    heuristic_max_entry_price: float
    heuristic_min_time_to_close_seconds: float
    model_allowed_entry_price_bands: tuple[str, ...]
    model_min_time_to_close_seconds: float
    model_edge_mid_confidence: float
    model_edge_high_confidence: float
    model_edge_mid_threshold: float
    model_edge_high_threshold: float

    def as_dict(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "policy_id": self.policy_id,
            "policy_bundle_version": self.policy_bundle_version,
            "watch_tier": self.watch_tier,
            "horizon_bucket": self.horizon_bucket,
            "fallback": self.fallback,
            "allowed_entry_price_bands": list(self.allowed_entry_price_bands),
            "allowed_time_to_close_bands": list(self.allowed_time_to_close_bands),
            "heuristic_allowed_entry_price_bands": list(self.heuristic_allowed_entry_price_bands),
            "heuristic_min_entry_price": round(self.heuristic_min_entry_price, 4),
            "heuristic_max_entry_price": round(self.heuristic_max_entry_price, 4),
            "heuristic_min_time_to_close_seconds": round(self.heuristic_min_time_to_close_seconds, 3),
            "model_allowed_entry_price_bands": list(self.model_allowed_entry_price_bands),
            "model_min_time_to_close_seconds": round(self.model_min_time_to_close_seconds, 3),
            "model_edge_mid_confidence": round(self.model_edge_mid_confidence, 4),
            "model_edge_high_confidence": round(self.model_edge_high_confidence, 4),
            "model_edge_mid_threshold": round(self.model_edge_mid_threshold, 4),
            "model_edge_high_threshold": round(self.model_edge_high_threshold, 4),
        }


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
        self._artifact_training_scope = "unknown"
        self._artifact_training_since_ts = 0
        self._artifact_training_routed_only = False
        self._artifact_training_provenance_trusted = False
        self._artifact_training_block_reason = ""
        self._artifact_exists = False
        self._artifact_path = ""
        self._fallback_reason = "missing_artifact"
        self._load_error = ""
        self._loaded_at = 0
        self._segment_policy_id = SEGMENT_POLICY_ID
        self._segment_policy_bundle_version = SEGMENT_POLICY_BUNDLE_VERSION
        self._try_load_xgb()

    def _try_load_xgb(self) -> None:
        path = model_path()
        self._artifact_path = path
        self._artifact_exists = os.path.exists(path)
        self._artifact_backend = None
        self._artifact_contract_version = None
        self._artifact_label_mode = None
        self._artifact_prediction_mode = None
        self._artifact_training_scope = "unknown"
        self._artifact_training_since_ts = 0
        self._artifact_training_routed_only = False
        self._artifact_training_provenance_trusted = False
        self._artifact_training_block_reason = ""
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
                artifact_metrics = artifact.get("metrics") if isinstance(artifact.get("metrics"), dict) else {}
                self._artifact_training_scope = str(
                    artifact.get("training_scope")
                    or artifact_metrics.get("training_scope")
                    or "unknown"
                ).strip().lower() or "unknown"
                self._artifact_training_since_ts = max(
                    int(
                        artifact.get("training_since_ts")
                        or artifact_metrics.get("training_since_ts")
                        or 0
                    ),
                    0,
                )
                self._artifact_training_routed_only = _artifact_bool(
                    artifact.get("training_routed_only")
                    if "training_routed_only" in artifact
                    else artifact_metrics.get("training_routed_only")
                )
                self._artifact_training_provenance_trusted = _artifact_bool(
                    artifact.get("training_provenance_trusted")
                    if "training_provenance_trusted" in artifact
                    else artifact_metrics.get("training_provenance_trusted")
                )
                self._artifact_training_block_reason = str(
                    artifact.get("training_provenance_block_reason")
                    or artifact_metrics.get("training_provenance_block_reason")
                    or ""
                ).strip()
                if not self._artifact_training_provenance_trusted and not self._artifact_training_block_reason:
                    self._artifact_training_block_reason = (
                        "artifact missing post-epoch routed training provenance"
                    )
                active_epoch_started_at = max(
                    int(
                        read_shadow_evidence_epoch().get("shadow_evidence_epoch_started_at")
                        or 0
                    ),
                    0,
                )
                if active_epoch_started_at > 0:
                    if self._artifact_training_scope != "current_evidence_window":
                        self._artifact_training_provenance_trusted = False
                        self._artifact_training_block_reason = (
                            "artifact training scope does not match the active shadow evidence window"
                        )
                    elif not self._artifact_training_routed_only:
                        self._artifact_training_provenance_trusted = False
                        self._artifact_training_block_reason = (
                            "artifact was not trained on routed-only post-epoch samples"
                        )
                    elif self._artifact_training_since_ts < active_epoch_started_at:
                        self._artifact_training_provenance_trusted = False
                        self._artifact_training_block_reason = (
                            "artifact predates the active shadow evidence epoch "
                            f"({self._artifact_training_since_ts} < {active_epoch_started_at})"
                        )
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
                if not self._artifact_training_provenance_trusted:
                    self._fallback_reason = "training_provenance_untrusted"
                    logger.warning(
                        "Ignoring model at %s because training provenance is untrusted: %s",
                        path,
                        self._artifact_training_block_reason
                        or "artifact missing post-epoch routed training provenance",
                    )
                    return
                self._xgb = artifact.get("model")
                self._xgb_probability_calibrator = artifact.get("probability_calibrator")
                self._xgb_prediction_mode = str(artifact.get("prediction_mode") or "probability")
                self._xgb_cols = artifact.get("feature_cols", FEATURE_COLS)
                self._xgb_policy = _sanitize_model_policy(artifact.get("policy"))
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
        if self._xgb is not None and allow_xgboost():
            return "xgboost"
        if allow_heuristic():
            return "heuristic"
        if self._shadow_bootstrap_heuristic_enabled():
            return "heuristic_bootstrap"
        return "disabled"

    def _shadow_bootstrap_heuristic_enabled(self) -> bool:
        return bool(self._xgb is None and allow_xgboost() and not use_real_money())

    def _heuristic_runtime_enabled(self) -> bool:
        return bool(allow_heuristic() or self._shadow_bootstrap_heuristic_enabled())

    def runtime_info(self) -> dict:
        artifact_backend = self._artifact_backend
        if artifact_backend is None and self._artifact_exists:
            artifact_backend = "unknown"
        return {
            "loaded_scorer": self.sizing_mode(),
            "loaded_model_backend": self._model_backend,
            "heuristic_enabled": bool(allow_heuristic()),
            "heuristic_bootstrap_enabled": bool(self._shadow_bootstrap_heuristic_enabled()),
            "xgboost_enabled": bool(allow_xgboost()),
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
            "model_training_scope": self._artifact_training_scope,
            "model_training_since_ts": self._artifact_training_since_ts,
            "model_training_routed_only": bool(self._artifact_training_routed_only),
            "model_training_provenance_trusted": bool(self._artifact_training_provenance_trusted),
            "model_training_block_reason": self._artifact_training_block_reason,
            "segment_policy_id": self._segment_policy_id,
            "segment_policy_bundle_version": self._segment_policy_bundle_version,
        }

    def _segment_runtime_policy(self, route: SegmentRoute) -> SegmentRuntimePolicy:
        return SegmentRuntimePolicy(
            segment_id=route.segment_id,
            policy_id=self._segment_policy_id,
            policy_bundle_version=self._segment_policy_bundle_version,
            watch_tier=route.watch_tier or SEGMENT_FALLBACK,
            horizon_bucket=route.horizon_bucket or SEGMENT_FALLBACK,
            fallback=bool(route.fallback),
            allowed_entry_price_bands=allowed_entry_price_bands(),
            allowed_time_to_close_bands=allowed_time_to_close_bands(),
            heuristic_allowed_entry_price_bands=heuristic_allowed_entry_price_bands(),
            heuristic_min_entry_price=heuristic_min_entry_price(),
            heuristic_max_entry_price=heuristic_max_entry_price(),
            heuristic_min_time_to_close_seconds=float(heuristic_min_time_to_close_seconds()),
            model_allowed_entry_price_bands=xgboost_allowed_entry_price_bands(),
            model_min_time_to_close_seconds=float(model_min_time_to_close_seconds()),
            model_edge_mid_confidence=model_edge_mid_confidence(),
            model_edge_high_confidence=model_edge_high_confidence(),
            model_edge_mid_threshold=model_edge_mid_threshold(),
            model_edge_high_threshold=model_edge_high_threshold(),
        )

    @staticmethod
    def _segment_context(
        market_features: MarketFeatures,
        *,
        watch_tier: str | None,
    ) -> dict[str, object]:
        time_to_close_seconds = max(float(getattr(market_features, "days_to_res", 0.0) or 0.0) * 86400.0, 0.0)
        time_to_close_band = time_to_close_band_label(int(time_to_close_seconds))
        route = segment_route_for_trade(
            watch_tier=watch_tier,
            time_to_close_band=time_to_close_band,
        )
        return {
            "route": route,
            "segment_id": route.segment_id,
            "watch_tier": route.watch_tier or SEGMENT_FALLBACK,
            "horizon_bucket": route.horizon_bucket or SEGMENT_FALLBACK,
            "segment_fallback": bool(route.fallback),
            "time_to_close_seconds": round(time_to_close_seconds, 3),
            "time_to_close_band": time_to_close_band,
        }

    def _segment_payload(self, segment_policy: SegmentRuntimePolicy, segment_context: dict[str, object]) -> dict[str, object]:
        segment_metadata = {
            "segment_id": segment_policy.segment_id,
            "watch_tier": str(segment_context.get("watch_tier") or segment_policy.watch_tier),
            "horizon_bucket": str(segment_context.get("horizon_bucket") or segment_policy.horizon_bucket),
            "segment_fallback": bool(segment_context.get("segment_fallback") or segment_policy.fallback),
            "time_to_close_seconds": segment_context.get("time_to_close_seconds"),
            "time_to_close_band": segment_context.get("time_to_close_band"),
            "policy_id": segment_policy.policy_id,
            "policy_bundle_version": segment_policy.policy_bundle_version,
            "segment_policy": segment_policy.as_dict(),
        }
        return {
            "segment_id": segment_policy.segment_id,
            "policy_id": segment_policy.policy_id,
            "policy_bundle_version": segment_policy.policy_bundle_version,
            "watch_tier": segment_policy.watch_tier,
            "horizon_bucket": segment_policy.horizon_bucket,
            "experiment_arm": DEFAULT_EXPERIMENT_ARM,
            "segment": {
                **segment_metadata,
            },
        }

    def evaluate(
        self,
        trader_features: TraderFeatures,
        market_features: MarketFeatures,
        order_size_usd: float = 10.0,
        trader_address: str | None = None,
        watch_tier: str | None = None,
    ) -> dict:
        market_result = self.market_scorer.score(market_features)
        segment_context = self._segment_context(market_features, watch_tier=watch_tier)
        segment_route = segment_context["route"]
        segment_policy = self._segment_runtime_policy(segment_route)
        if market_result["veto"]:
            return {
                "confidence": 0.0,
                "passed": False,
                "veto": market_result["veto"],
                "mode": "veto",
                "trader": {},
                "market": market_result,
                **self._segment_payload(segment_policy, segment_context),
            }

        if self._xgb is not None and allow_xgboost():
            return self._evaluate_xgb(
                trader_features,
                market_features,
                order_size_usd,
                segment_policy=segment_policy,
                segment_context=segment_context,
            )

        if self._heuristic_runtime_enabled():
            return self._evaluate_heuristic(
                trader_features,
                market_features,
                market_result,
                segment_policy=segment_policy,
                segment_context=segment_context,
                trader_address=trader_address,
            )

        disabled_reason = "all scorers disabled by config"
        if self._xgb is not None and not allow_xgboost():
            disabled_reason = "xgboost disabled by config and heuristic disabled"
        elif self._xgb is None and self._fallback_reason:
            disabled_reason = f"model unavailable ({self._fallback_reason}) and heuristic disabled"
        elif self._xgb is None:
            disabled_reason = "heuristic disabled and no compatible model loaded"
        return {
            "confidence": 0.0,
            "passed": False,
            "veto": None,
            "reason": disabled_reason,
            "mode": "disabled",
            "trader": {},
            "market": market_result,
            **self._segment_payload(segment_policy, segment_context),
        }

    def _evaluate_heuristic(
        self,
        trader_features: TraderFeatures,
        market_features: MarketFeatures,
        market_result: dict,
        *,
        segment_policy: SegmentRuntimePolicy | None = None,
        segment_context: dict[str, object] | None = None,
        trader_address: str | None = None,
    ) -> dict:
        if segment_policy is None or segment_context is None:
            segment_context = self._segment_context(market_features, watch_tier=None)
            segment_policy = self._segment_runtime_policy(segment_context["route"])
        heuristic_mode = (
            "heuristic_bootstrap"
            if self._shadow_bootstrap_heuristic_enabled() and not allow_heuristic()
            else "heuristic"
        )
        if not self._heuristic_runtime_enabled():
            return {
                "confidence": 0.0,
                "passed": False,
                "reason": "heuristic disabled by config",
                "veto": None,
                "mode": heuristic_mode,
                "trader": {},
                "market": market_result,
                **self._segment_payload(segment_policy, segment_context),
            }
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
        time_to_close_seconds = float(segment_context["time_to_close_seconds"])
        time_to_close_band = str(segment_context["time_to_close_band"])
        min_time_to_close_seconds = segment_policy.heuristic_min_time_to_close_seconds
        min_entry_price = segment_policy.heuristic_min_entry_price
        max_entry_price = segment_policy.heuristic_max_entry_price
        entry_price_band = entry_price_band_label(execution_price)
        global_allowed_entry_price_bands = segment_policy.allowed_entry_price_bands
        global_allowed_time_to_close_bands = segment_policy.allowed_time_to_close_bands
        mode_allowed_entry_price_bands = segment_policy.heuristic_allowed_entry_price_bands
        min_market_score, band_progress = self._heuristic_min_market_score(
            execution_price,
            min_entry_price,
            max_entry_price,
        )
        passed_confidence = adjusted >= min_floor
        passed_global_band_filter = not global_allowed_entry_price_bands or entry_price_band in global_allowed_entry_price_bands
        passed_global_horizon_filter = (
            not global_allowed_time_to_close_bands or time_to_close_band in global_allowed_time_to_close_bands
        )
        passed_band_filter = not mode_allowed_entry_price_bands or entry_price_band in mode_allowed_entry_price_bands
        passed_entry_price = execution_price >= min_entry_price and execution_price < max_entry_price
        passed_market_score = market_score >= min_market_score
        passed_horizon = time_to_close_seconds >= min_time_to_close_seconds
        passed = (
            passed_confidence
            and passed_global_band_filter
            and passed_global_horizon_filter
            and passed_band_filter
            and passed_entry_price
            and passed_market_score
            and passed_horizon
        )
        if not passed_global_horizon_filter:
            reason = (
                f"time to close band {time_to_close_band} outside allowlist "
                f"{','.join(global_allowed_time_to_close_bands)}"
            )
        elif not passed_horizon:
            reason = (
                f"heuristic time to close {time_to_close_seconds:.0f}s "
                f"< min {min_time_to_close_seconds:.0f}s"
            )
        elif not passed_global_band_filter:
            reason = (
                f"entry band {entry_price_band} outside global allowlist "
                f"{','.join(global_allowed_entry_price_bands)}"
            )
        elif not passed_band_filter:
            reason = (
                f"heuristic entry band {entry_price_band} outside allowlist "
                f"{','.join(mode_allowed_entry_price_bands)}"
            )
        elif not passed_entry_price:
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
            "entry_price_band": entry_price_band,
            "global_allowed_entry_price_bands": list(global_allowed_entry_price_bands),
            "min_entry_price": round(min_entry_price, 4),
            "max_entry_price": round(max_entry_price, 4),
            "allowed_entry_price_bands": list(mode_allowed_entry_price_bands),
            "min_market_score": round(min_market_score, 4),
            "band_progress": round(band_progress, 4),
            "time_to_close_seconds": round(time_to_close_seconds, 3),
            "time_to_close_band": time_to_close_band,
            "allowed_time_to_close_bands": list(global_allowed_time_to_close_bands),
            "min_time_to_close_seconds": round(min_time_to_close_seconds, 3),
            "adaptive_floor": adaptive_floor.as_dict(),
            "passed": passed,
            "reason": reason,
            "veto": None,
            "mode": heuristic_mode,
            "trader": trader_result,
            "market": market_result,
            **self._segment_payload(segment_policy, segment_context),
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
        *,
        segment_policy: SegmentRuntimePolicy | None = None,
        segment_context: dict[str, object] | None = None,
    ) -> dict:
        if segment_policy is None or segment_context is None:
            segment_context = self._segment_context(market_features, watch_tier=None)
            segment_policy = self._segment_runtime_policy(segment_context["route"])
        if not allow_xgboost():
            return {
                "confidence": 0.0,
                "raw_confidence": 0.0,
                "expected_return": None,
                "edge": 0.0,
                "entry_price_band": None,
                "global_allowed_entry_price_bands": [],
                "allowed_entry_price_bands": [],
                "edge_threshold": 0.0,
                "base_edge_threshold": 0.0,
                "time_to_close_seconds": 0.0,
                "time_to_close_band": None,
                "allowed_time_to_close_bands": [],
                "min_time_to_close_seconds": 0.0,
                "passed": False,
                "reason": "xgboost disabled by config",
                "veto": None,
                "mode": "xgboost",
                "trader": {},
                "market": {},
                **self._segment_payload(segment_policy, segment_context),
            }
        trader_result = self.trader_scorer.score(trader_features)
        market_result = self.market_scorer.score(market_features)
        feature_map = build_feature_map(trader_features, market_features)

        ordered = np.array(
            [
                [
                    _model_feature_value(feature_map.get(column))
                    for column in self._xgb_cols
                ]
            ],
            dtype=float,
        )
        execution_price = market_features.execution_price if market_features.execution_price > 0 else market_features.mid
        time_to_close_seconds = float(segment_context["time_to_close_seconds"])
        time_to_close_band = str(segment_context["time_to_close_band"])
        min_time_to_close_seconds = segment_policy.model_min_time_to_close_seconds
        entry_price_band = entry_price_band_label(execution_price)
        global_allowed_entry_price_bands = segment_policy.allowed_entry_price_bands
        global_allowed_time_to_close_bands = segment_policy.allowed_time_to_close_bands
        mode_allowed_entry_price_bands = segment_policy.model_allowed_entry_price_bands
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
        invalid_prediction_reason = None
        if expected_return is not None and not np.isfinite(expected_return):
            invalid_prediction_reason = "model expected return was non-finite"
        elif base_confidence is not None and not np.isfinite(base_confidence):
            invalid_prediction_reason = "model raw confidence was non-finite"
        elif not np.isfinite(confidence):
            invalid_prediction_reason = "model confidence was non-finite"
        elif not (0.0 <= confidence <= 1.0):
            invalid_prediction_reason = f"model confidence {confidence:.3f} outside [0, 1]"
        if invalid_prediction_reason:
            safe_raw_confidence = base_confidence if base_confidence is not None and np.isfinite(base_confidence) else 0.0
            safe_expected_return = expected_return if expected_return is not None and np.isfinite(expected_return) else None
            return {
                "confidence": 0.0,
                "raw_confidence": round(safe_raw_confidence, 4),
                "expected_return": round(safe_expected_return, 4) if safe_expected_return is not None else None,
                "edge": 0.0,
                "entry_price_band": entry_price_band,
                "global_allowed_entry_price_bands": list(global_allowed_entry_price_bands),
                "allowed_entry_price_bands": list(mode_allowed_entry_price_bands),
                "edge_threshold": 0.0,
                "base_edge_threshold": round(float(self._xgb_policy.get("edge_threshold", 0.0)), 4),
                "time_to_close_seconds": round(time_to_close_seconds, 3),
                "time_to_close_band": time_to_close_band,
                "allowed_time_to_close_bands": list(global_allowed_time_to_close_bands),
                "min_time_to_close_seconds": round(min_time_to_close_seconds, 3),
                "passed": False,
                "reason": invalid_prediction_reason,
                "veto": None,
                "mode": "xgboost",
                "trader": trader_result,
                "market": market_result,
                **self._segment_payload(segment_policy, segment_context),
            }
        edge = confidence - execution_price
        base_edge_threshold = float(self._xgb_policy.get("edge_threshold", 0.0))
        edge_threshold = base_edge_threshold
        if confidence >= segment_policy.model_edge_high_confidence:
            edge_threshold = segment_policy.model_edge_high_threshold
        elif confidence >= segment_policy.model_edge_mid_confidence:
            edge_threshold = segment_policy.model_edge_mid_threshold
        passed_global_band_filter = not global_allowed_entry_price_bands or entry_price_band in global_allowed_entry_price_bands
        passed_global_horizon_filter = (
            not global_allowed_time_to_close_bands or time_to_close_band in global_allowed_time_to_close_bands
        )
        passed_horizon = time_to_close_seconds >= min_time_to_close_seconds
        passed_band_filter = not mode_allowed_entry_price_bands or entry_price_band in mode_allowed_entry_price_bands
        passed = passed_global_horizon_filter and passed_horizon and passed_global_band_filter and passed_band_filter and edge >= edge_threshold
        if not passed_global_horizon_filter:
            reason = (
                f"time to close band {time_to_close_band} outside allowlist "
                f"{','.join(global_allowed_time_to_close_bands)}"
            )
        elif not passed_horizon:
            reason = f"model time to close {time_to_close_seconds:.0f}s < min {min_time_to_close_seconds:.0f}s"
        elif not passed_global_band_filter:
            reason = (
                f"entry band {entry_price_band} outside global allowlist "
                f"{','.join(global_allowed_entry_price_bands)}"
            )
        elif not passed_band_filter:
            reason = (
                f"model entry band {entry_price_band} outside allowlist "
                f"{','.join(mode_allowed_entry_price_bands)}"
            )
        elif passed:
            reason = "passed model edge threshold"
        else:
            reason = f"model edge {edge:.3f} < threshold {edge_threshold:.3f}"
        return {
            "confidence": round(confidence, 4),
            "raw_confidence": round(base_confidence if base_confidence is not None else confidence, 4),
            "expected_return": round(expected_return, 4) if expected_return is not None else None,
            "edge": round(edge, 4),
            "entry_price_band": entry_price_band,
            "global_allowed_entry_price_bands": list(global_allowed_entry_price_bands),
            "allowed_entry_price_bands": list(mode_allowed_entry_price_bands),
            "edge_threshold": round(edge_threshold, 4),
            "base_edge_threshold": round(base_edge_threshold, 4),
            "time_to_close_seconds": round(time_to_close_seconds, 3),
            "time_to_close_band": time_to_close_band,
            "allowed_time_to_close_bands": list(global_allowed_time_to_close_bands),
            "min_time_to_close_seconds": round(min_time_to_close_seconds, 3),
            "passed": passed,
            "reason": reason,
            "veto": None,
            "mode": "xgboost",
            "trader": trader_result,
            "market": market_result,
            **self._segment_payload(segment_policy, segment_context),
        }


def _model_feature_value(value: object) -> float:
    if value is None:
        return float("nan")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(numeric):
        return float("nan")
    return numeric


def _sanitize_model_policy(policy: object) -> dict[str, float]:
    payload = policy if isinstance(policy, dict) else {}
    return {
        "edge_threshold": _nonnegative_model_float(
            payload.get("edge_threshold"),
            default=0.0,
        )
    }


def _nonnegative_model_float(value: object, *, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(numeric):
        return default
    return max(numeric, 0.0)
