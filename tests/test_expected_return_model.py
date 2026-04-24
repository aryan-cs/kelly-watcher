from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import joblib
import numpy as np

import kelly_watcher.engine.signal_engine as signal_engine
from kelly_watcher.engine.economic_model import expected_return_to_confidence, inverse_return_target, transform_return_target
from kelly_watcher.engine.features import FEATURE_COLS
from kelly_watcher.engine.trade_contract import DATA_CONTRACT_VERSION, MODEL_LABEL_MODE


class ConstantReturnModel:
    def __init__(self, target_value: float):
        self.target_value = float(target_value)

    def predict(self, X):
        return np.full(len(X), self.target_value, dtype=float)


class IdentityCalibrator:
    def predict(self, values):
        return np.asarray(values, dtype=float)


class ExpectedReturnModelTest(unittest.TestCase):
    @staticmethod
    def _trusted_model_artifact(
        expected_return: float,
        *,
        edge_threshold: float = 0.01,
        contract_version: int = DATA_CONTRACT_VERSION,
    ) -> dict:
        return {
            "model": ConstantReturnModel(transform_return_target(expected_return)),
            "probability_calibrator": IdentityCalibrator(),
            "feature_cols": FEATURE_COLS[:3],
            "model_backend": "hist_gradient_boosting",
            "prediction_mode": "expected_return",
            "data_contract_version": contract_version,
            "label_mode": MODEL_LABEL_MODE,
            "training_scope": "current_evidence_window",
            "training_since_ts": 1_700_000_400,
            "training_routed_only": True,
            "training_provenance_trusted": True,
            "training_provenance_block_reason": "",
            "policy": {"edge_threshold": edge_threshold},
        }

    def test_return_target_transform_round_trips(self) -> None:
        values = [-1.0, -0.25, 0.0, 0.4, 1.75]
        restored = [inverse_return_target(transform_return_target(value)) for value in values]
        for original, recovered in zip(values, restored):
            self.assertAlmostEqual(original, recovered, places=6)

    def test_signal_engine_scores_expected_return_artifact(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = self._trusted_model_artifact(0.35)
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            market_features = SimpleNamespace(execution_price=0.4, mid=0.4)
            expected_confidence = float(expected_return_to_confidence(0.35, 0.4))
            with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch.object(
                engine.market_scorer,
                "score",
                return_value={"score": 0.7, "veto": None},
            ), patch(
                "kelly_watcher.engine.signal_engine.build_feature_map",
                return_value={column: 0.5 for column in FEATURE_COLS[:3]},
            ):
                result = engine._evaluate_xgb(SimpleNamespace(), market_features, 10.0)

            self.assertAlmostEqual(result["confidence"], round(expected_confidence, 4), places=4)
            self.assertAlmostEqual(result["raw_confidence"], round(expected_confidence, 4), places=4)
            self.assertAlmostEqual(result["expected_return"], 0.35, places=4)
            self.assertTrue(result["passed"])
            self.assertAlmostEqual(result["edge_threshold"], 0.01, places=4)

    def test_signal_engine_tags_segment_metadata_for_runtime_scoring(self) -> None:
        with patch(
            "kelly_watcher.engine.signal_engine.model_path",
            return_value="/tmp/kelly-watcher-missing-model.joblib",
        ):
            engine = signal_engine.SignalEngine()

        market_features = SimpleNamespace(execution_price=0.42, mid=0.42, days_to_res=0.5)
        adaptive_floor = SimpleNamespace(floor=0.1, as_dict=lambda: {"floor": 0.1})
        belief = SimpleNamespace(
            adjusted_confidence=0.83,
            prior_confidence=0.72,
            blend=0.5,
            evidence=3,
        )
        with patch.object(engine.trader_scorer, "score", return_value={"score": 0.88}), patch.object(
            engine.market_scorer,
            "score",
            return_value={"score": 0.79, "veto": None},
        ), patch(
            "kelly_watcher.engine.signal_engine.adaptive_min_confidence_for_signal",
            return_value=adaptive_floor,
        ), patch(
            "kelly_watcher.engine.signal_engine.adjust_heuristic_confidence",
            return_value=belief,
        ):
            result = engine.evaluate(SimpleNamespace(), market_features, 10.0, watch_tier="warm")

        self.assertEqual(result["segment_id"], "warm_mid")
        self.assertEqual(result["segment"]["watch_tier"], "warm")
        self.assertEqual(result["segment"]["horizon_bucket"], "mid")
        self.assertEqual(result["segment"]["segment_policy"]["segment_id"], "warm_mid")
        self.assertEqual(result["segment"]["policy_bundle_version"], 1)

    def test_signal_engine_relaxes_edge_threshold_for_high_confidence_predictions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = self._trusted_model_artifact(0.35, edge_threshold=0.02)
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            market_features = SimpleNamespace(execution_price=0.4, mid=0.4)
            with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch.object(
                engine.market_scorer,
                "score",
                return_value={"score": 0.7, "veto": None},
            ), patch(
                "kelly_watcher.engine.signal_engine.build_feature_map",
                return_value={column: 0.5 for column in FEATURE_COLS[:3]},
            ), patch(
                "kelly_watcher.engine.signal_engine.expected_return_to_confidence",
                return_value=0.8,
            ):
                result = engine._evaluate_xgb(SimpleNamespace(), market_features, 10.0)

            self.assertAlmostEqual(result["base_edge_threshold"], 0.02, places=6)
            self.assertAlmostEqual(result["edge_threshold"], 0.0, places=6)
            self.assertTrue(result["passed"])

    def test_signal_engine_blocks_heuristic_entries_below_min_entry_price(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            "kelly_watcher.engine.signal_engine.model_path",
            return_value="/tmp/kelly-watcher-missing-model.joblib",
        ):
            engine = signal_engine.SignalEngine()

        market_features = SimpleNamespace(execution_price=0.42, mid=0.42, days_to_res=0.5)
        belief = SimpleNamespace(
            adjusted_confidence=0.7,
            prior_confidence=0.5,
            blend=0.0,
            evidence=0,
        )
        adaptive_floor = SimpleNamespace(floor=0.55, as_dict=lambda: {"floor": 0.55})

        with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch(
            "kelly_watcher.engine.signal_engine.adjust_heuristic_confidence",
            return_value=belief,
        ), patch(
            "kelly_watcher.engine.signal_engine.adaptive_min_confidence_for_signal",
            return_value=adaptive_floor,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_entry_price",
            return_value=0.45,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_max_entry_price",
            return_value=0.5,
        ):
            result = engine._evaluate_heuristic(
                SimpleNamespace(),
                market_features,
                {"score": 0.7, "veto": None},
            )

        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "heuristic entry price 0.420 outside band 0.450-0.500")

    def test_signal_engine_blocks_heuristic_entries_outside_allowlisted_band(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            "kelly_watcher.engine.signal_engine.model_path",
            return_value="/tmp/kelly-watcher-missing-model.joblib",
        ):
            engine = signal_engine.SignalEngine()

        market_features = SimpleNamespace(execution_price=0.66, mid=0.66, days_to_res=0.5)
        belief = SimpleNamespace(
            adjusted_confidence=0.7,
            prior_confidence=0.5,
            blend=0.0,
            evidence=0,
        )
        adaptive_floor = SimpleNamespace(floor=0.55, as_dict=lambda: {"floor": 0.55})

        with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch(
            "kelly_watcher.engine.signal_engine.adjust_heuristic_confidence",
            return_value=belief,
        ), patch(
            "kelly_watcher.engine.signal_engine.adaptive_min_confidence_for_signal",
            return_value=adaptive_floor,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_entry_price",
            return_value=0.60,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_max_entry_price",
            return_value=0.75,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_allowed_entry_price_bands",
            return_value=(">=0.70",),
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_time_to_close_seconds",
            return_value=0,
        ):
            result = engine._evaluate_heuristic(
                SimpleNamespace(),
                market_features,
                {"score": 0.8, "veto": None},
            )

        self.assertFalse(result["passed"])
        self.assertEqual(result["entry_price_band"], "0.60-0.69")
        self.assertEqual(result["reason"], "heuristic entry band 0.60-0.69 outside allowlist >=0.70")

    def test_signal_engine_blocks_heuristic_entries_below_min_time_to_close(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            "kelly_watcher.engine.signal_engine.model_path",
            return_value="/tmp/kelly-watcher-missing-model.joblib",
        ):
            engine = signal_engine.SignalEngine()

        market_features = SimpleNamespace(execution_price=0.70, mid=0.70, days_to_res=0.01)
        belief = SimpleNamespace(
            adjusted_confidence=0.72,
            prior_confidence=0.5,
            blend=0.0,
            evidence=0,
        )
        adaptive_floor = SimpleNamespace(floor=0.55, as_dict=lambda: {"floor": 0.55})

        with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch(
            "kelly_watcher.engine.signal_engine.adjust_heuristic_confidence",
            return_value=belief,
        ), patch(
            "kelly_watcher.engine.signal_engine.adaptive_min_confidence_for_signal",
            return_value=adaptive_floor,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_entry_price",
            return_value=0.65,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_max_entry_price",
            return_value=0.75,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_allowed_entry_price_bands",
            return_value=(),
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_time_to_close_seconds",
            return_value=3600,
        ):
            result = engine._evaluate_heuristic(
                SimpleNamespace(),
                market_features,
                {"score": 0.8, "veto": None},
            )

        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "heuristic time to close 864s < min 3600s")

    def test_signal_engine_blocks_heuristic_entries_with_weak_market_score_near_lower_band(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            "kelly_watcher.engine.signal_engine.model_path",
            return_value="/tmp/kelly-watcher-missing-model.joblib",
        ):
            engine = signal_engine.SignalEngine()

        market_features = SimpleNamespace(execution_price=0.66, mid=0.66, days_to_res=0.5)
        belief = SimpleNamespace(
            adjusted_confidence=0.7,
            prior_confidence=0.5,
            blend=0.0,
            evidence=0,
        )
        adaptive_floor = SimpleNamespace(floor=0.55, as_dict=lambda: {"floor": 0.55})

        with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch(
            "kelly_watcher.engine.signal_engine.adjust_heuristic_confidence",
            return_value=belief,
        ), patch(
            "kelly_watcher.engine.signal_engine.adaptive_min_confidence_for_signal",
            return_value=adaptive_floor,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_entry_price",
            return_value=0.65,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_max_entry_price",
            return_value=0.75,
        ):
            result = engine._evaluate_heuristic(
                SimpleNamespace(),
                market_features,
                {"score": 0.64, "veto": None},
            )

        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "heuristic market score 0.640 < min 0.690")
        self.assertAlmostEqual(result["min_market_score"], 0.69, places=4)

    def test_signal_engine_relaxes_heuristic_market_floor_near_upper_band(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            "kelly_watcher.engine.signal_engine.model_path",
            return_value="/tmp/kelly-watcher-missing-model.joblib",
        ):
            engine = signal_engine.SignalEngine()

        market_features = SimpleNamespace(execution_price=0.74, mid=0.74, days_to_res=0.5)
        belief = SimpleNamespace(
            adjusted_confidence=0.7,
            prior_confidence=0.5,
            blend=0.0,
            evidence=0,
        )
        adaptive_floor = SimpleNamespace(floor=0.55, as_dict=lambda: {"floor": 0.55})

        with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch(
            "kelly_watcher.engine.signal_engine.adjust_heuristic_confidence",
            return_value=belief,
        ), patch(
            "kelly_watcher.engine.signal_engine.adaptive_min_confidence_for_signal",
            return_value=adaptive_floor,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_entry_price",
            return_value=0.65,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_max_entry_price",
            return_value=0.75,
        ):
            result = engine._evaluate_heuristic(
                SimpleNamespace(),
                market_features,
                {"score": 0.64, "veto": None},
            )

        self.assertTrue(result["passed"])
        self.assertEqual(result["reason"], "passed heuristic threshold")
        self.assertAlmostEqual(result["min_market_score"], 0.61, places=4)

    def test_signal_engine_blocks_heuristic_entries_outside_global_band_allowlist(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            "kelly_watcher.engine.signal_engine.model_path",
            return_value="/tmp/kelly-watcher-missing-model.joblib",
        ):
            engine = signal_engine.SignalEngine()

        market_features = SimpleNamespace(execution_price=0.66, mid=0.66, days_to_res=0.5)
        belief = SimpleNamespace(
            adjusted_confidence=0.7,
            prior_confidence=0.5,
            blend=0.0,
            evidence=0,
        )
        adaptive_floor = SimpleNamespace(floor=0.55, as_dict=lambda: {"floor": 0.55})

        with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch(
            "kelly_watcher.engine.signal_engine.adjust_heuristic_confidence",
            return_value=belief,
        ), patch(
            "kelly_watcher.engine.signal_engine.adaptive_min_confidence_for_signal",
            return_value=adaptive_floor,
        ), patch(
            "kelly_watcher.engine.signal_engine.allowed_entry_price_bands",
            return_value=(">=0.70",),
        ), patch(
            "kelly_watcher.engine.signal_engine.allowed_time_to_close_bands",
            return_value=(),
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_allowed_entry_price_bands",
            return_value=(),
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_entry_price",
            return_value=0.65,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_max_entry_price",
            return_value=0.75,
        ), patch(
            "kelly_watcher.engine.signal_engine.heuristic_min_time_to_close_seconds",
            return_value=0.0,
        ):
            result = engine._evaluate_heuristic(
                SimpleNamespace(),
                market_features,
                {"score": 0.8, "veto": None},
            )

        self.assertFalse(result["passed"])
        self.assertEqual(result["entry_price_band"], "0.60-0.69")
        self.assertEqual(result["reason"], "entry band 0.60-0.69 outside global allowlist >=0.70")

    def test_signal_engine_blocks_heuristic_when_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch(
            "kelly_watcher.engine.signal_engine.model_path",
            return_value="/tmp/kelly-watcher-missing-model.joblib",
        ):
            engine = signal_engine.SignalEngine()

        with patch("kelly_watcher.engine.signal_engine.allow_heuristic", return_value=False):
            result = engine._evaluate_heuristic(
                SimpleNamespace(),
                SimpleNamespace(execution_price=0.70, mid=0.70, days_to_res=0.5),
                {"score": 0.8, "veto": None},
            )

        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "heuristic disabled by config")
        self.assertEqual(result["mode"], "heuristic")

    def test_signal_engine_blocks_model_entries_outside_global_horizon_allowlist(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = self._trusted_model_artifact(0.35)
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            market_features = SimpleNamespace(execution_price=0.4, mid=0.4, days_to_res=0.5)
            with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch.object(
                engine.market_scorer,
                "score",
                return_value={"score": 0.7, "veto": None},
            ), patch(
                "kelly_watcher.engine.signal_engine.build_feature_map",
                return_value={column: 0.5 for column in FEATURE_COLS[:3]},
            ), patch(
                "kelly_watcher.engine.signal_engine.allowed_entry_price_bands",
                return_value=(),
            ), patch(
                "kelly_watcher.engine.signal_engine.allowed_time_to_close_bands",
                return_value=(">3d",),
            ), patch(
                "kelly_watcher.engine.signal_engine.xgboost_allowed_entry_price_bands",
                return_value=(),
            ), patch(
                "kelly_watcher.engine.signal_engine.model_min_time_to_close_seconds",
                return_value=0.0,
            ):
                result = engine._evaluate_xgb(SimpleNamespace(), market_features, 10.0)

        self.assertFalse(result["passed"])
        self.assertEqual(result["time_to_close_band"], "2h-12h")
        self.assertEqual(result["reason"], "time to close band 2h-12h outside allowlist >3d")

    def test_signal_engine_runtime_info_reports_contract_mismatch(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = {
                "model": ConstantReturnModel(transform_return_target(0.20)),
                "probability_calibrator": IdentityCalibrator(),
                "feature_cols": FEATURE_COLS[:3],
                "model_backend": "hist_gradient_boosting",
                "prediction_mode": "expected_return",
                "data_contract_version": DATA_CONTRACT_VERSION - 1,
                "label_mode": MODEL_LABEL_MODE,
                "policy": {"edge_threshold": 0.01},
            }
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            runtime = engine.runtime_info()
            self.assertEqual(runtime["loaded_scorer"], "heuristic")
            self.assertTrue(runtime["model_artifact_exists"])
            self.assertEqual(runtime["model_artifact_backend"], "hist_gradient_boosting")
            self.assertEqual(runtime["model_artifact_contract"], DATA_CONTRACT_VERSION - 1)
            self.assertEqual(runtime["runtime_contract"], DATA_CONTRACT_VERSION)
            self.assertFalse(runtime["model_runtime_compatible"])
            self.assertEqual(runtime["model_fallback_reason"], "contract_mismatch")
            self.assertEqual(runtime["model_training_scope"], "unknown")
            self.assertFalse(runtime["model_training_provenance_trusted"])
            self.assertIn("missing post-epoch routed training provenance", runtime["model_training_block_reason"])

    def test_signal_engine_runtime_info_reports_untrusted_training_provenance(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = {
                "model": ConstantReturnModel(transform_return_target(0.20)),
                "probability_calibrator": IdentityCalibrator(),
                "feature_cols": FEATURE_COLS[:3],
                "model_backend": "hist_gradient_boosting",
                "prediction_mode": "expected_return",
                "data_contract_version": DATA_CONTRACT_VERSION,
                "label_mode": MODEL_LABEL_MODE,
                "policy": {"edge_threshold": 0.01},
            }
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            runtime = engine.runtime_info()
            self.assertEqual(runtime["loaded_scorer"], "heuristic")
            self.assertEqual(runtime["loaded_model_backend"], "heuristic")
            self.assertTrue(runtime["model_artifact_exists"])
            self.assertFalse(runtime["model_runtime_compatible"])
            self.assertEqual(runtime["model_fallback_reason"], "training_provenance_untrusted")
            self.assertEqual(int(runtime["model_loaded_at"] or 0), 0)
            self.assertEqual(runtime["model_training_scope"], "unknown")
            self.assertFalse(runtime["model_training_provenance_trusted"])
            self.assertIn("missing post-epoch routed training provenance", runtime["model_training_block_reason"])

    def test_signal_engine_runtime_info_reports_loaded_model(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = self._trusted_model_artifact(0.20)
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            runtime = engine.runtime_info()
            self.assertEqual(runtime["loaded_scorer"], "xgboost")
            self.assertEqual(runtime["loaded_model_backend"], "hist_gradient_boosting")
            self.assertTrue(runtime["model_artifact_exists"])
            self.assertTrue(runtime["model_runtime_compatible"])
            self.assertEqual(runtime["model_fallback_reason"], "")
            self.assertGreater(int(runtime["model_loaded_at"] or 0), 0)
            self.assertEqual(runtime["model_training_scope"], "current_evidence_window")
            self.assertTrue(runtime["model_training_provenance_trusted"])
            self.assertEqual(runtime["model_training_block_reason"], "")

    def test_signal_engine_runtime_info_rejects_model_that_predates_active_epoch(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = self._trusted_model_artifact(0.20)
            artifact["training_since_ts"] = 1_700_000_100
            joblib.dump(artifact, model_file)

            with patch(
                "kelly_watcher.engine.signal_engine.read_shadow_evidence_epoch",
                return_value={"shadow_evidence_epoch_started_at": 1_700_000_400},
            ), patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            runtime = engine.runtime_info()
            self.assertEqual(runtime["loaded_scorer"], "heuristic")
            self.assertEqual(runtime["loaded_model_backend"], "heuristic")
            self.assertFalse(runtime["model_runtime_compatible"])
            self.assertEqual(runtime["model_fallback_reason"], "training_provenance_untrusted")
            self.assertFalse(runtime["model_training_provenance_trusted"])
            self.assertIn("predates the active shadow evidence epoch", runtime["model_training_block_reason"])
            self.assertIn("1700000100", runtime["model_training_block_reason"])
            self.assertIn("1700000400", runtime["model_training_block_reason"])

    def test_signal_engine_runtime_info_reports_trusted_training_provenance(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = {
                "model": ConstantReturnModel(transform_return_target(0.20)),
                "probability_calibrator": IdentityCalibrator(),
                "feature_cols": FEATURE_COLS[:3],
                "model_backend": "hist_gradient_boosting",
                "prediction_mode": "expected_return",
                "data_contract_version": DATA_CONTRACT_VERSION,
                "label_mode": MODEL_LABEL_MODE,
                "training_scope": "current_evidence_window",
                "training_since_ts": 1_700_000_400,
                "training_routed_only": True,
                "training_provenance_trusted": True,
                "training_provenance_block_reason": "",
                "policy": {"edge_threshold": 0.01},
            }
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            runtime = engine.runtime_info()
            self.assertEqual(runtime["model_training_scope"], "current_evidence_window")
            self.assertEqual(int(runtime["model_training_since_ts"]), 1_700_000_400)
            self.assertTrue(runtime["model_training_routed_only"])
            self.assertTrue(runtime["model_training_provenance_trusted"])
            self.assertEqual(runtime["model_training_block_reason"], "")

    def test_signal_engine_blocks_model_entries_outside_allowlisted_band(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = self._trusted_model_artifact(0.35)
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            market_features = SimpleNamespace(execution_price=0.58, mid=0.58, days_to_res=0.5)
            with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch.object(
                engine.market_scorer,
                "score",
                return_value={"score": 0.7, "veto": None},
            ), patch(
                "kelly_watcher.engine.signal_engine.build_feature_map",
                return_value={column: 0.5 for column in FEATURE_COLS[:3]},
            ), patch(
                "kelly_watcher.engine.signal_engine.expected_return_to_confidence",
                return_value=0.8,
            ), patch(
                "kelly_watcher.engine.signal_engine.xgboost_allowed_entry_price_bands",
                return_value=(">=0.70",),
            ), patch(
                "kelly_watcher.engine.signal_engine.model_min_time_to_close_seconds",
                return_value=0,
            ):
                result = engine._evaluate_xgb(SimpleNamespace(), market_features, 10.0)

            self.assertFalse(result["passed"])
            self.assertEqual(result["entry_price_band"], "0.55-0.59")
            self.assertEqual(result["reason"], "model entry band 0.55-0.59 outside allowlist >=0.70")

    def test_signal_engine_blocks_model_entries_below_min_time_to_close(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = self._trusted_model_artifact(0.35)
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            market_features = SimpleNamespace(execution_price=0.58, mid=0.58, days_to_res=0.01)
            with patch.object(engine.trader_scorer, "score", return_value={"score": 0.8}), patch.object(
                engine.market_scorer,
                "score",
                return_value={"score": 0.7, "veto": None},
            ), patch(
                "kelly_watcher.engine.signal_engine.build_feature_map",
                return_value={column: 0.5 for column in FEATURE_COLS[:3]},
            ), patch(
                "kelly_watcher.engine.signal_engine.expected_return_to_confidence",
                return_value=0.8,
            ), patch(
                "kelly_watcher.engine.signal_engine.xgboost_allowed_entry_price_bands",
                return_value=(),
            ), patch(
                "kelly_watcher.engine.signal_engine.model_min_time_to_close_seconds",
                return_value=3600,
            ):
                result = engine._evaluate_xgb(SimpleNamespace(), market_features, 10.0)

            self.assertFalse(result["passed"])
            self.assertEqual(result["reason"], "model time to close 864s < min 3600s")

    def test_signal_engine_blocks_model_when_disabled(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = self._trusted_model_artifact(0.35)
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            with patch("kelly_watcher.engine.signal_engine.allow_xgboost", return_value=False):
                result = engine._evaluate_xgb(
                    SimpleNamespace(),
                    SimpleNamespace(execution_price=0.58, mid=0.58, days_to_res=0.5),
                    10.0,
                )

        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "xgboost disabled by config")
        self.assertEqual(result["mode"], "xgboost")

    def test_signal_engine_evaluate_falls_back_to_heuristic_when_model_disabled(self) -> None:
        with TemporaryDirectory() as tmpdir:
            model_file = Path(tmpdir) / "model.joblib"
            artifact = self._trusted_model_artifact(0.35)
            joblib.dump(artifact, model_file)

            with patch("kelly_watcher.engine.signal_engine.model_path", return_value=str(model_file)):
                engine = signal_engine.SignalEngine()

            market_features = SimpleNamespace(execution_price=0.58, mid=0.58, days_to_res=0.5)
            expected = {"passed": True, "mode": "heuristic", "reason": "fallback"}
            with patch.object(engine.market_scorer, "score", return_value={"score": 0.7, "veto": None}), patch.object(
                engine,
                "_evaluate_heuristic",
                return_value=expected,
            ) as heuristic_eval, patch(
                "kelly_watcher.engine.signal_engine.allow_xgboost",
                return_value=False,
            ), patch(
                "kelly_watcher.engine.signal_engine.allow_heuristic",
                return_value=True,
            ):
                result = engine.evaluate(SimpleNamespace(), market_features, 10.0)

        self.assertIs(result, expected)
        heuristic_eval.assert_called_once()


if __name__ == "__main__":
    unittest.main()
