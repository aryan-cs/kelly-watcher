from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from kelly_watcher.config import model_path, retrain_min_samples
from kelly_watcher.data.db import get_conn
from kelly_watcher.engine.economic_model import (
    apply_probability_calibrator,
    expected_return_to_confidence,
    inverse_return_target,
    transform_return_target,
)
from kelly_watcher.engine.features import FEATURE_COLS, LABEL_COL, OUTCOME_COL, RETURN_COL, SAMPLE_WEIGHT_COL
from kelly_watcher.engine.segment_policy import SEGMENT_FALLBACK, SEGMENT_IDS
from kelly_watcher.engine.shadow_evidence import read_shadow_evidence_epoch
from kelly_watcher.engine.trade_contract import (
    DATA_CONTRACT_VERSION,
    MODEL_LABEL_MODE,
    RESOLVED_TRAINING_SAMPLE_SQL,
    TRAINING_OUTCOME_SQL,
    TRAINING_RETURN_SQL,
)

logger = logging.getLogger(__name__)

MIN_VALIDATION_TRADES = 20
MIN_FEATURE_COVERAGE = 0.75
MIN_FINAL_HOLDOUT_SAMPLES = 20
MIN_FINAL_CALIBRATION_SAMPLES = 15
MIN_SEARCH_CALIBRATION_SAMPLES = 12
MIN_SEARCH_EVAL_SAMPLES = 12
MIN_SEARCH_TRAIN_SAMPLES = 60
_ROUTED_TRAINING_SEGMENT_IDS: tuple[str, ...] = (*SEGMENT_IDS, SEGMENT_FALLBACK)
_ROUTED_TRAINING_SEGMENT_SQL = ",".join("?" for _ in _ROUTED_TRAINING_SEGMENT_IDS)


@dataclass(frozen=True)
class TrainingWindow:
    name: str
    train_end: int
    cal_end: int
    eval_end: int


@dataclass(frozen=True)
class TrainingPlan:
    search_windows: tuple[TrainingWindow, ...]
    final_train_end: int
    final_cal_end: int
    holdout_end: int


def min_samples_required() -> int:
    return retrain_min_samples()


def _latest_applied_replay_promotion_at() -> int:
    conn = get_conn()
    try:
        try:
            row = conn.execute(
                """
                SELECT applied_at
                FROM replay_promotions
                WHERE status='applied'
                  AND applied_at > 0
                ORDER BY applied_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        except Exception as exc:
            if "no such table" in str(exc).lower():
                return 0
            raise
        return max(int(row["applied_at"] or 0), 0) if row is not None else 0
    finally:
        conn.close()


def _standalone_training_scope() -> tuple[int, bool]:
    epoch_state = read_shadow_evidence_epoch()
    epoch_started_at = max(int(epoch_state.get("shadow_evidence_epoch_started_at") or 0), 0)
    if epoch_started_at <= 0:
        return 0, False
    return max(epoch_started_at, _latest_applied_replay_promotion_at()), True


def _training_provenance_payload(
    *,
    since_ts: int | None,
    routed_only: bool,
) -> dict[str, Any]:
    effective_since_ts = max(int(since_ts or 0), 0)
    routed_only = bool(routed_only)
    if effective_since_ts > 0 and routed_only:
        scope = "current_evidence_window"
        trusted = True
        block_reason = ""
    elif effective_since_ts > 0:
        scope = "since_ts"
        trusted = False
        block_reason = "artifact training scope included non-routed shadow history"
    elif routed_only:
        scope = "routed_all_history"
        trusted = False
        block_reason = "artifact was trained on routed-only rows without an active evidence epoch"
    else:
        scope = "all_history"
        trusted = False
        block_reason = "artifact was not trained on post-epoch routed shadow history"
    return {
        "training_scope": scope,
        "training_since_ts": effective_since_ts,
        "training_routed_only": routed_only,
        "training_provenance_trusted": trusted,
        "training_provenance_block_reason": block_reason,
    }


def _with_training_provenance(metrics: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    payload = dict(metrics)
    payload.update(provenance)
    return payload


def load_training_data(
    *,
    since_ts: int | None = None,
    routed_only: bool = False,
):
    import numpy as np
    import pandas as pd

    conn = get_conn()
    label_ts_expr = "COALESCE(label_applied_at, resolved_at, placed_at)"
    where_clauses = [f"({RESOLVED_TRAINING_SAMPLE_SQL})"]
    params: list[object] = []
    effective_since_ts = max(int(since_ts or 0), 0)
    if effective_since_ts > 0:
        where_clauses.append(f"{label_ts_expr} >= ?")
        params.append(effective_since_ts)
    if routed_only:
        where_clauses.append(
            f"COALESCE(NULLIF(TRIM(segment_id), ''), '') IN ({_ROUTED_TRAINING_SEGMENT_SQL})"
        )
        params.extend(_ROUTED_TRAINING_SEGMENT_IDS)

    try:
        df = pd.read_sql_query(
            f"""
            SELECT
                id,
                trade_id,
                placed_at,
                {label_ts_expr} AS label_ts,
                source_action,
                skipped,
                skip_reason,
                price_at_signal,
                signal_size_usd,
                COALESCE(actual_entry_price, price_at_signal) AS effective_price,
                COALESCE(actual_entry_size_usd, signal_size_usd) AS effective_size_usd,
                counterfactual_return,
                {TRAINING_RETURN_SQL} AS {RETURN_COL},
                {TRAINING_OUTCOME_SQL} AS {OUTCOME_COL},
                {", ".join(FEATURE_COLS)}
            FROM trade_log
            WHERE {" AND ".join(where_clauses)}
            ORDER BY label_ts ASC, placed_at ASC, id ASC
            """,
            conn,
            params=params,
        )
    finally:
        conn.close()

    if df.empty:
        df[LABEL_COL] = []
        df[OUTCOME_COL] = []
        df[RETURN_COL] = []
        df[SAMPLE_WEIGHT_COL] = []
        return df

    df[RETURN_COL] = pd.to_numeric(df[RETURN_COL], errors="coerce")
    df[OUTCOME_COL] = pd.to_numeric(df[OUTCOME_COL], errors="coerce")
    df[SAMPLE_WEIGHT_COL] = 1.0
    df[LABEL_COL] = df[RETURN_COL].map(transform_return_target)
    df["label_ts"] = pd.to_numeric(df["label_ts"], errors="coerce")
    df["effective_price"] = pd.to_numeric(df["effective_price"], errors="coerce")
    df["effective_size_usd"] = pd.to_numeric(df["effective_size_usd"], errors="coerce")
    df = df.replace([np.inf, -np.inf], np.nan)
    return df


def train(
    df=None,
    *,
    training_since_ts: int | None = None,
    training_routed_only: bool = False,
) -> dict:
    provenance = _training_provenance_payload(
        since_ts=training_since_ts,
        routed_only=training_routed_only,
    )
    if df is None:
        since_ts, epoch_ready = _standalone_training_scope()
        if not epoch_ready:
            return _with_training_provenance({
                "skipped": True,
                "n_samples": 0,
                "reason": (
                    "current evidence window is not active yet; "
                    "kelly_watcher.research.train.py must wait for fresh post-reset routed shadow history"
                ),
            }, provenance)
        training_since_ts = since_ts
        training_routed_only = True
        provenance = _training_provenance_payload(
            since_ts=training_since_ts,
            routed_only=training_routed_only,
        )
        df = load_training_data(since_ts=since_ts, routed_only=True)

    min_samples = min_samples_required()
    if len(df) < min_samples:
        logger.info("Training skipped: %s samples (need %s)", len(df), min_samples)
        return _with_training_provenance({"skipped": True, "n_samples": len(df)}, provenance)

    feature_cols = _select_feature_cols(df)
    df = df.dropna(subset=feature_cols + [LABEL_COL, OUTCOME_COL, RETURN_COL, SAMPLE_WEIGHT_COL, "effective_price"])
    df = df[(df["effective_price"] > 0.01) & (df["effective_price"] < 0.99)].copy()
    df = df.reset_index(drop=True)
    if not df["label_ts"].is_monotonic_increasing:
        raise ValueError("training label_ts must be sorted before chronological windowing")
    if len(df) < min_samples:
        logger.info("Training skipped after filtering: %s samples (need %s)", len(df), min_samples)
        return _with_training_provenance(
            {"skipped": True, "n_samples": len(df), "feature_cols": feature_cols},
            provenance,
        )

    plan = _build_training_plan(len(df))
    if plan is None:
        logger.warning("Training skipped: not enough chronological history for search plus holdout")
        return _with_training_provenance({
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "insufficient samples for holdout search",
        }, provenance)

    final_train_df = df.iloc[: plan.final_train_end].copy()
    final_cal_df = df.iloc[plan.final_train_end : plan.final_cal_end].copy()
    holdout_df = df.iloc[plan.final_cal_end : plan.holdout_end].copy()

    candidate_reports: list[dict[str, Any]] = []
    for spec in _candidate_specs():
        report = _evaluate_candidate_spec(df, feature_cols, plan.search_windows, spec)
        if report is not None:
            candidate_reports.append(report)

    if not candidate_reports:
        logger.warning("Training skipped: candidate search produced no valid model")
        return _with_training_provenance({
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "candidate search produced no valid model",
        }, provenance)

    best_candidate = max(candidate_reports, key=_candidate_rank_key)
    logger.info(
        "Selected %s candidate %s from %s options (search pnl=%.4f, beats_baseline=%s)",
        best_candidate["backend"],
        best_candidate["name"],
        len(candidate_reports),
        best_candidate["search_total_pnl"],
        best_candidate["search_beats_baseline"],
    )
    selected_edge_threshold = float(best_candidate["search_edge_threshold"])

    final_fit = _fit_calibrated_model(
        spec=best_candidate,
        train_df=final_train_df,
        cal_df=final_cal_df,
        feature_cols=feature_cols,
    )
    if final_fit is None:
        logger.warning("Training skipped: final fit could not satisfy class diversity requirements")
        return _with_training_provenance({
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "insufficient class diversity",
        }, provenance)

    calibrated_holdout_report = _evaluate_window(
        probability_calibrator=final_fit["probability_calibrator"],
        base_model=final_fit["base_model"],
        train_df=final_train_df,
        eval_df=holdout_df,
        feature_cols=feature_cols,
        fixed_edge_threshold=selected_edge_threshold,
    )
    if calibrated_holdout_report is None:
        logger.warning("Training skipped: holdout evaluation could not satisfy class diversity requirements")
        return _with_training_provenance({
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "insufficient class diversity",
        }, provenance)

    raw_holdout_report = _evaluate_window(
        probability_calibrator=None,
        base_model=final_fit["base_model"],
        train_df=final_train_df,
        eval_df=holdout_df,
        feature_cols=feature_cols,
        fixed_edge_threshold=selected_edge_threshold,
    )
    if raw_holdout_report is None:
        logger.warning("Training skipped: raw holdout evaluation could not satisfy class diversity requirements")
        return _with_training_provenance({
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "insufficient class diversity",
        }, provenance)

    (
        holdout_report,
        selected_prediction_path,
        selected_probability_calibrator,
        selected_calibration_method,
    ) = _select_prediction_path(
        calibrated_report=calibrated_holdout_report,
        raw_report=raw_holdout_report,
        probability_calibrator=final_fit["probability_calibrator"],
        calibration_method=final_fit["calibration_method"],
    )

    importances = _feature_ranking(final_fit["base_model"], feature_cols, final_train_df)
    top_features = sorted(importances.items(), key=lambda item: -item[1])
    trained_at = int(time.time())
    metrics = {
        "n_samples": len(df),
        "n_train": len(final_train_df),
        "n_cal": len(final_cal_df),
        "n_val": len(holdout_df),
        "feature_cols": feature_cols,
        "feature_count": len(feature_cols),
        "model_backend": final_fit["backend"],
        "requested_calibration_mode": final_fit["requested_calibration_mode"],
        "trained_calibration_method": final_fit["calibration_method"],
        "calibration_method": selected_calibration_method,
        "selected_prediction_path": selected_prediction_path,
        "prediction_mode": "expected_return",
        "log_loss": round(holdout_report["log_loss"], 4),
        "log_loss_base": round(holdout_report["log_loss_base"], 4),
        "brier_score": round(holdout_report["brier_score"], 4),
        "brier_base": round(holdout_report["brier_base"], 4),
        "raw_log_loss": round(raw_holdout_report["log_loss"], 4),
        "raw_brier_score": round(raw_holdout_report["brier_score"], 4),
        "raw_selected_trades": int(raw_holdout_report["selected_trades"]),
        "raw_total_pnl": round(raw_holdout_report["total_pnl"], 4),
        "calibrated_log_loss": round(calibrated_holdout_report["log_loss"], 4),
        "calibrated_brier_score": round(calibrated_holdout_report["brier_score"], 4),
        "calibrated_selected_trades": int(calibrated_holdout_report["selected_trades"]),
        "calibrated_total_pnl": round(calibrated_holdout_report["total_pnl"], 4),
        "beats_baseline": holdout_report["beats_baseline"],
        "val_selected_trades": holdout_report["selected_trades"],
        "val_total_pnl": round(holdout_report["total_pnl"], 4),
        "val_avg_pnl": round(holdout_report["avg_pnl"], 4),
        "val_win_rate": round(holdout_report["win_rate"], 4),
        "edge_threshold": round(holdout_report["edge_threshold"], 4),
        "top_features": top_features[:8],
        "trained_at": trained_at,
        "data_contract_version": DATA_CONTRACT_VERSION,
        "fill_aware_only": True,
        "label_mode": MODEL_LABEL_MODE,
        **provenance,
        "candidate_count": len(candidate_reports),
        "candidate_name": best_candidate["name"],
        "search_log_loss": round(best_candidate["search_log_loss"], 4),
        "search_log_loss_base": round(best_candidate["search_log_loss_base"], 4),
        "search_brier_score": round(best_candidate["search_brier_score"], 4),
        "search_brier_base": round(best_candidate["search_brier_base"], 4),
        "search_beats_baseline": best_candidate["search_beats_baseline"],
        "search_selected_trades": best_candidate["search_selected_trades"],
        "search_total_pnl": round(best_candidate["search_total_pnl"], 4),
        "search_avg_pnl": round(best_candidate["search_avg_pnl"], 4),
        "search_win_rate": round(best_candidate["search_win_rate"], 4),
        "search_edge_threshold": round(best_candidate["search_edge_threshold"], 4),
        "decision_threshold_source": "search_cv",
        "dataset_cohorts": _cohort_summaries(df),
        "train_cohorts": _cohort_summaries(final_train_df),
        "calibration_cohorts": _cohort_summaries(final_cal_df),
        "val_cohorts": _cohort_summaries(
            holdout_df,
            preds=holdout_report["preds"],
            baseline_rate=float(final_train_df[OUTCOME_COL].mean()),
        ),
    }
    incumbent_artifact = _load_model_artifact()
    incumbent_gate = _compare_against_incumbent(
        incumbent_artifact=incumbent_artifact,
        final_train_df=final_train_df,
        holdout_df=holdout_df,
        challenger_model=final_fit["base_model"],
        challenger_feature_cols=feature_cols,
        challenger_probability_calibrator=selected_probability_calibrator,
        challenger_prediction_mode="expected_return",
    )
    metrics.update(incumbent_gate)
    incumbent_runtime_compatible = _artifact_runtime_compatible(incumbent_artifact)
    metrics["incumbent_runtime_compatible"] = incumbent_runtime_compatible
    deployable, promotion_mode = _should_deploy_candidate(
        best_candidate=best_candidate,
        holdout_report=holdout_report,
        beats_baseline=bool(metrics["beats_baseline"]),
        incumbent_present=bool(metrics.get("incumbent_present")),
        incumbent_runtime_compatible=incumbent_runtime_compatible,
        beats_incumbent=bool(metrics.get("beats_incumbent", True)),
    )
    metrics["promotion_mode"] = promotion_mode
    if not deployable:
        metrics.setdefault(
            "reject_reason",
            _deployment_reject_reason(
                best_candidate=best_candidate,
                holdout_report=holdout_report,
                beats_baseline=bool(metrics["beats_baseline"]),
                beats_incumbent=bool(metrics.get("beats_incumbent", True)),
            ),
        )
        logger.warning("Model failed deployment checks - not deploying")
        return metrics | {"deployed": False}
    if promotion_mode != "standard":
        logger.warning(
            "Deploying challenger via %s path because incumbent artifact is runtime-incompatible",
            promotion_mode,
        )

    path = model_path()
    artifact = {
        "model": final_fit["base_model"],
        "probability_calibrator": selected_probability_calibrator,
        "feature_cols": feature_cols,
        "model_backend": final_fit["backend"],
        "prediction_mode": "expected_return",
        "data_contract_version": DATA_CONTRACT_VERSION,
        "fill_aware_only": True,
        "label_mode": MODEL_LABEL_MODE,
        "target_transform": "signed_log1p_return",
        "sample_weight_mode": "executed_only_1.0",
        **provenance,
        "policy": {
            "edge_threshold": float(holdout_report["edge_threshold"]),
            "selected_trades": int(holdout_report["selected_trades"]),
        },
        "candidate": {
            "name": best_candidate["name"],
            "backend": best_candidate["backend"],
            "search_passed": bool(best_candidate["search_passed"]),
            "requested_calibration_mode": best_candidate["calibration_mode"],
        },
        "metrics": metrics,
    }
    _dump_model_artifact_atomic(artifact, path)

    conn = get_conn()
    try:
        conn.execute("UPDATE model_history SET deployed=0")
        conn.execute(
            """
            INSERT INTO model_history
            (
                trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed,
                training_scope, training_since_ts, training_routed_only, training_provenance_trusted
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trained_at,
                len(df),
                float(holdout_report["brier_score"]),
                float(holdout_report["log_loss"]),
                json.dumps(feature_cols),
                path,
                1,
                str(provenance["training_scope"]),
                int(provenance["training_since_ts"]),
                1 if provenance["training_routed_only"] else 0,
                1 if provenance["training_provenance_trusted"] else 0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Model saved to %s", path)
    return metrics | {"deployed": True}


def check_calibration(verbose: bool = True) -> dict:
    import joblib
    import numpy as np

    df = load_training_data()
    if len(df) < 50:
        return {"error": "not enough data"}

    path = model_path()
    if not path:
        return {"error": "model path missing"}

    artifact = joblib.load(path)
    if isinstance(artifact, dict):
        model = artifact["model"]
        cols = artifact["feature_cols"]
        prediction_mode = str(artifact.get("prediction_mode") or "probability")
        probability_calibrator = artifact.get("probability_calibrator")
    else:
        return {"error": "legacy model artifact"}

    df = df.dropna(subset=cols + [OUTCOME_COL, "effective_price"])
    X = df[cols].values
    prices = df["effective_price"].astype(float).values
    y = df[OUTCOME_COL].astype(int).values
    pred = _predict_model_confidence(
        model=model,
        prediction_mode=prediction_mode,
        probability_calibrator=probability_calibrator,
        X=X,
        prices=prices,
    )

    bins = np.linspace(0, 1, 11)
    results = []
    for index in range(len(bins) - 1):
        mask = (pred >= bins[index]) & (pred < bins[index + 1])
        if mask.sum() < 5:
            continue
        bucket = {
            "pred_range": f"{bins[index]:.1f}-{bins[index + 1]:.1f}",
            "mean_pred": round(pred[mask].mean(), 3),
            "actual_wr": round(y[mask].mean(), 3),
            "n": int(mask.sum()),
            "gap": round(abs(pred[mask].mean() - y[mask].mean()), 3),
        }
        results.append(bucket)
        if verbose:
            logger.info(
                "%s pred=%.3f actual=%.3f n=%s",
                bucket["pred_range"],
                bucket["mean_pred"],
                bucket["actual_wr"],
                bucket["n"],
            )

    return {"calibration_bins": results}


def _build_training_plan(n_samples: int) -> TrainingPlan | None:
    holdout_size = max(MIN_FINAL_HOLDOUT_SAMPLES, int(round(n_samples * 0.15)))
    final_cal_size = max(MIN_FINAL_CALIBRATION_SAMPLES, int(round(n_samples * 0.10)))
    final_train_end = n_samples - holdout_size - final_cal_size
    if final_train_end < MIN_SEARCH_TRAIN_SAMPLES:
        return None

    search_cal_size = max(MIN_SEARCH_CALIBRATION_SAMPLES, int(round(final_train_end * 0.12)))
    search_eval_size = max(MIN_SEARCH_EVAL_SAMPLES, int(round(final_train_end * 0.12)))
    first_train_end = final_train_end - (2 * search_cal_size) - (2 * search_eval_size)
    if first_train_end < MIN_SEARCH_TRAIN_SAMPLES:
        return None

    fold1_cal_end = first_train_end + search_cal_size
    fold1_eval_end = fold1_cal_end + search_eval_size
    fold2_train_end = fold1_eval_end
    fold2_cal_end = fold2_train_end + search_cal_size
    fold2_eval_end = fold2_cal_end + search_eval_size
    if fold2_eval_end != final_train_end:
        return None

    return TrainingPlan(
        search_windows=(
            TrainingWindow(name="search_fold_1", train_end=first_train_end, cal_end=fold1_cal_end, eval_end=fold1_eval_end),
            TrainingWindow(name="search_fold_2", train_end=fold2_train_end, cal_end=fold2_cal_end, eval_end=fold2_eval_end),
        ),
        final_train_end=final_train_end,
        final_cal_end=final_train_end + final_cal_size,
        holdout_end=n_samples,
    )


def _select_feature_cols(df) -> list[str]:
    selected: list[str] = []
    for column in FEATURE_COLS:
        if column not in df.columns:
            continue
        series = df[column]
        if float(series.notna().mean()) < MIN_FEATURE_COVERAGE:
            continue
        if series.dropna().nunique() < 2:
            continue
        selected.append(column)

    return selected or FEATURE_COLS


@lru_cache(maxsize=1)
def _xgboost_available() -> bool:
    try:
        import xgboost  # noqa: F401

        return True
    except Exception:
        return False


def _candidate_specs() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    calibration_modes = ("identity", "auto")
    if _xgboost_available():
        xgb_configs = (
            {
                "label": "balanced",
                "params": {
                    "n_estimators": 350,
                    "max_depth": 3,
                    "learning_rate": 0.03,
                    "subsample": 0.9,
                    "colsample_bytree": 0.85,
                    "min_child_weight": 8,
                    "gamma": 0.0,
                    "reg_alpha": 0.05,
                    "reg_lambda": 1.0,
                },
            },
            {
                "label": "deeper",
                "params": {
                    "n_estimators": 450,
                    "max_depth": 4,
                    "learning_rate": 0.025,
                    "subsample": 0.85,
                    "colsample_bytree": 0.8,
                    "min_child_weight": 10,
                    "gamma": 0.05,
                    "reg_alpha": 0.1,
                    "reg_lambda": 1.0,
                },
            },
            {
                "label": "conservative",
                "params": {
                    "n_estimators": 300,
                    "max_depth": 3,
                    "learning_rate": 0.02,
                    "subsample": 0.95,
                    "colsample_bytree": 0.9,
                    "min_child_weight": 12,
                    "gamma": 0.1,
                    "reg_alpha": 0.15,
                    "reg_lambda": 1.25,
                },
            },
            {
                "label": "wide",
                "params": {
                    "n_estimators": 400,
                    "max_depth": 5,
                    "learning_rate": 0.03,
                    "subsample": 0.8,
                    "colsample_bytree": 0.75,
                    "min_child_weight": 12,
                    "gamma": 0.05,
                    "reg_alpha": 0.1,
                    "reg_lambda": 1.2,
                },
            },
        )
        for config in xgb_configs:
            for seed in (11, 42, 89):
                for calibration_mode in calibration_modes:
                    candidates.append(
                        {
                            "name": f"xgb_return_{config['label']}_seed{seed}_{calibration_mode}",
                            "backend": "xgboost",
                            "seed": seed,
                            "params": config["params"] | {"random_state": seed},
                            "calibration_mode": calibration_mode,
                        }
                    )

    hist_configs = (
        {
            "label": "stable",
            "params": {
                "max_depth": 4,
                "learning_rate": 0.04,
                "max_iter": 250,
                "min_samples_leaf": 12,
                "l2_regularization": 0.1,
            },
        },
        {
            "label": "regularized",
            "params": {
                "max_depth": 3,
                "learning_rate": 0.03,
                "max_iter": 300,
                "min_samples_leaf": 16,
                "l2_regularization": 0.2,
            },
        },
    )
    for config in hist_configs:
        for seed in (11, 42):
            for calibration_mode in calibration_modes:
                candidates.append(
                    {
                        "name": f"hgb_return_{config['label']}_seed{seed}_{calibration_mode}",
                        "backend": "hist_gradient_boosting",
                        "seed": seed,
                        "params": config["params"] | {"random_state": seed},
                        "calibration_mode": calibration_mode,
                    }
                )

    return candidates


def _build_regressor(spec: dict[str, Any]):
    backend = str(spec.get("backend") or "hist_gradient_boosting")
    params = dict(spec.get("params") or {})
    if backend == "xgboost":
        import xgboost as xgb

        return xgb.XGBRegressor(
            objective="reg:squarederror",
            eval_metric="rmse",
            early_stopping_rounds=30,
            verbosity=0,
            n_jobs=1,
            **params,
        ), "xgboost"

    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(**params), "hist_gradient_boosting"


def _feature_ranking(model, feature_cols: list[str], train_df) -> dict[str, float]:
    if hasattr(model, "feature_importances_"):
        return dict(zip(feature_cols, model.feature_importances_))

    rankings: dict[str, float] = {}
    target = train_df[LABEL_COL].astype(float)
    for column in feature_cols:
        series = train_df[column].astype(float)
        if series.nunique() < 2:
            rankings[column] = 0.0
            continue
        rankings[column] = float(abs(series.corr(target)))
    return rankings


def _evaluate_candidate_spec(
    df,
    feature_cols: list[str],
    windows: tuple[TrainingWindow, ...],
    spec: dict[str, Any],
) -> dict[str, Any] | None:
    if not df["label_ts"].is_monotonic_increasing:
        raise ValueError("training label_ts must be sorted before candidate windowing")
    fold_reports: list[dict[str, Any]] = []
    for window in windows:
        train_df = df.iloc[: window.train_end].copy()
        cal_df = df.iloc[window.train_end : window.cal_end].copy()
        eval_df = df.iloc[window.cal_end : window.eval_end].copy()
        fit_result = _fit_calibrated_model(
            spec=spec,
            train_df=train_df,
            cal_df=cal_df,
            feature_cols=feature_cols,
        )
        if fit_result is None:
            return None
        report = _evaluate_window(
            probability_calibrator=fit_result["probability_calibrator"],
            base_model=fit_result["base_model"],
            train_df=train_df,
            eval_df=eval_df,
            feature_cols=feature_cols,
        )
        if report is None:
            return None
        fold_reports.append(report)

    aggregate = _aggregate_search_reports(fold_reports)
    return {
        "name": str(spec["name"]),
        "backend": str(spec["backend"]),
        "seed": int(spec["seed"]),
        "params": dict(spec["params"]),
        "calibration_mode": str(spec.get("calibration_mode") or "auto"),
        **aggregate,
    }


def _fit_probability_calibrator(base_confidence, outcomes, sample_weight, *, requested_mode: str = "auto"):
    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression

    requested_mode = str(requested_mode or "auto").strip().lower()
    allowed_modes = {"identity", "auto", "sigmoid", "isotonic"}
    if requested_mode not in allowed_modes:
        raise ValueError(f"unsupported calibration mode {requested_mode!r}")
    if requested_mode == "identity":
        return None, "identity"

    base_confidence = np.asarray(base_confidence, dtype=float)
    outcomes = np.asarray(outcomes, dtype=int)
    sample_weight = np.asarray(sample_weight, dtype=float)
    if (
        len(base_confidence) == 0
        or len(base_confidence) != len(outcomes)
        or len(base_confidence) != len(sample_weight)
    ):
        raise ValueError("calibration inputs must be non-empty and aligned")
    if (
        not np.all(np.isfinite(base_confidence))
        or not np.all(np.isfinite(sample_weight))
        or np.unique(outcomes).size < 2
        or float(np.std(base_confidence)) <= 1e-9
    ):
        return None, "identity"

    method = requested_mode
    if method == "auto":
        method = "sigmoid" if len(base_confidence) < 400 else "isotonic"
    try:
        if method == "sigmoid":
            calibrator = LogisticRegression(random_state=0, solver="lbfgs")
            calibrator.fit(base_confidence.reshape(-1, 1), outcomes, sample_weight=sample_weight)
            return calibrator, method

        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(base_confidence, outcomes, sample_weight=sample_weight)
        return calibrator, method
    except Exception as exc:
        logger.warning("Probability calibration fell back to identity mapping: %s", exc)
        return None, "identity"


def _fit_calibrated_model(
    *,
    spec: dict[str, Any],
    train_df,
    cal_df,
    feature_cols: list[str],
) -> dict[str, Any] | None:
    import numpy as np

    y_train_target = train_df[LABEL_COL].astype(float).values
    y_cal_target = cal_df[LABEL_COL].astype(float).values
    y_cal_outcome = cal_df[OUTCOME_COL].astype(int).values
    if np.nanstd(y_train_target) <= 1e-9:
        return None

    X_train = train_df[feature_cols].values
    X_cal = cal_df[feature_cols].values
    train_weights = train_df[SAMPLE_WEIGHT_COL].astype(float).values
    cal_weights = cal_df[SAMPLE_WEIGHT_COL].astype(float).values
    model, backend = _build_regressor(spec)
    if backend == "xgboost":
        model.fit(X_train, y_train_target, sample_weight=train_weights, eval_set=[(X_cal, y_cal_target)], verbose=False)
    else:
        model.fit(X_train, y_train_target, sample_weight=train_weights)

    base_confidence = _predict_base_confidence(
        model=model,
        X=X_cal,
        prices=cal_df["effective_price"].astype(float).values,
    )
    probability_calibrator, calibration_method = _fit_probability_calibrator(
        base_confidence=np.asarray(base_confidence, dtype=float),
        outcomes=y_cal_outcome,
        sample_weight=cal_weights,
        requested_mode=str(spec.get("calibration_mode") or "auto"),
    )
    return {
        "base_model": model,
        "probability_calibrator": probability_calibrator,
        "backend": backend,
        "requested_calibration_mode": str(spec.get("calibration_mode") or "auto"),
        "calibration_method": calibration_method,
    }


def _predict_base_confidence(*, model, X, prices):
    predicted_target = model.predict(X)
    expected_return = inverse_return_target(predicted_target)
    return expected_return_to_confidence(expected_return, prices)


def _predict_model_confidence(*, model, prediction_mode: str, probability_calibrator, X, prices):
    if prediction_mode == "expected_return":
        base_confidence = _predict_base_confidence(model=model, X=X, prices=prices)
        return apply_probability_calibrator(probability_calibrator, base_confidence)
    return model.predict_proba(X)[:, 1]


def _load_model_artifact(path: str | None = None) -> dict[str, Any] | None:
    import joblib

    selected_path = str(path or model_path() or "").strip()
    if not selected_path:
        return None
    try:
        artifact = joblib.load(selected_path)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Could not load incumbent model artifact from %s: %s", selected_path, exc)
        return None

    if not isinstance(artifact, dict):
        logger.warning("Could not compare against incumbent model artifact from %s: legacy format", selected_path)
        return None
    if artifact.get("model") is None:
        logger.warning("Could not compare against incumbent model artifact from %s: missing model", selected_path)
        return None

    raw_feature_cols = artifact.get("feature_cols")
    feature_cols = [str(col) for col in raw_feature_cols] if isinstance(raw_feature_cols, (list, tuple)) else list(FEATURE_COLS)
    return {
        "model": artifact["model"],
        "probability_calibrator": artifact.get("probability_calibrator"),
        "prediction_mode": str(artifact.get("prediction_mode") or "probability"),
        "feature_cols": feature_cols or list(FEATURE_COLS),
        "data_contract_version": int(artifact.get("data_contract_version") or 0),
        "label_mode": str(artifact.get("label_mode") or ""),
        "path": selected_path,
    }


def _artifact_runtime_compatible(artifact: dict[str, Any] | None) -> bool:
    if artifact is None:
        return True
    return (
        int(artifact.get("data_contract_version") or 0) == DATA_CONTRACT_VERSION
        and str(artifact.get("label_mode") or "") == MODEL_LABEL_MODE
    )


def _should_deploy_candidate(
    *,
    best_candidate: dict[str, Any],
    holdout_report: dict[str, Any],
    beats_baseline: bool,
    incumbent_present: bool,
    incumbent_runtime_compatible: bool,
    beats_incumbent: bool,
) -> tuple[bool, str]:
    meets_search_gate = bool(best_candidate["search_passed"]) and beats_baseline
    meets_holdout_gate = (
        int(holdout_report["selected_trades"]) >= MIN_VALIDATION_TRADES
        and float(holdout_report["total_pnl"]) > 0
        and float(holdout_report["avg_pnl"]) > 0
    )
    standard = (
        meets_search_gate
        and meets_holdout_gate
        and bool(beats_incumbent)
    )
    if standard:
        return True, "standard"

    recovery_standard_gate = (
        incumbent_present
        and not incumbent_runtime_compatible
        and meets_search_gate
        and meets_holdout_gate
    )
    if recovery_standard_gate:
        return True, "recovery_incompatible_incumbent_standard_gate"

    recovery_shared_holdout = (
        incumbent_present
        and not incumbent_runtime_compatible
        and meets_holdout_gate
        and bool(beats_incumbent)
    )
    if recovery_shared_holdout:
        return True, "recovery_incompatible_incumbent_shared_holdout"

    return False, "rejected"


def _deployment_reject_reason(
    *,
    best_candidate: dict[str, Any],
    holdout_report: dict[str, Any],
    beats_baseline: bool,
    beats_incumbent: bool,
) -> str:
    failures: list[str] = []
    if not bool(best_candidate["search_passed"]):
        failures.append("search candidate failed deployment policy")
    if not beats_baseline:
        failures.append("final holdout did not beat baseline on both ll and brier")
    if int(holdout_report["selected_trades"]) < MIN_VALIDATION_TRADES:
        failures.append(
            f"final holdout selected only {int(holdout_report['selected_trades'])} trades "
            f"(need {MIN_VALIDATION_TRADES})"
        )
    if float(holdout_report["total_pnl"]) <= 0:
        failures.append(f"final holdout pnl {float(holdout_report['total_pnl']):.4f} <= 0")
    if float(holdout_report["avg_pnl"]) <= 0:
        failures.append(f"final holdout avg pnl {float(holdout_report['avg_pnl']):.4f} <= 0")
    if not beats_incumbent:
        failures.append("challenger did not beat incumbent on shared final holdout")
    return "; ".join(failures) if failures else "model failed deployment checks"


def _shared_eval_df(eval_df, feature_cols: list[str]):
    missing = [column for column in feature_cols if column not in eval_df.columns]
    if missing:
        return None
    required_cols = list(dict.fromkeys([*feature_cols, OUTCOME_COL, "effective_price"]))
    shared = eval_df.dropna(subset=required_cols).copy()
    if shared.empty:
        return None
    return shared


def _evaluate_prediction_report(
    *,
    model,
    prediction_mode: str,
    probability_calibrator,
    eval_df,
    feature_cols: list[str],
    baseline_rate: float,
    fixed_edge_threshold: float | None = None,
) -> dict[str, Any] | None:
    y_eval = eval_df[OUTCOME_COL].astype(int).values

    prices = eval_df["effective_price"].astype(float).values
    X_eval = eval_df[feature_cols].values
    preds = _predict_model_confidence(
        model=model,
        prediction_mode=prediction_mode,
        probability_calibrator=probability_calibrator,
        X=X_eval,
        prices=prices,
    )
    metrics = _score_predictions(
        preds=preds,
        outcomes=y_eval,
        prices=prices,
        baseline_rate=baseline_rate,
        fixed_edge_threshold=fixed_edge_threshold,
    )
    metrics["preds"] = preds
    metrics["prices"] = prices
    metrics["outcomes"] = y_eval
    return metrics


def _evaluate_window(
    *,
    probability_calibrator,
    base_model,
    train_df,
    eval_df,
    feature_cols: list[str],
    fixed_edge_threshold: float | None = None,
) -> dict[str, Any] | None:
    return _evaluate_prediction_report(
        model=base_model,
        prediction_mode="expected_return",
        probability_calibrator=probability_calibrator,
        eval_df=eval_df,
        feature_cols=feature_cols,
        baseline_rate=float(train_df[OUTCOME_COL].astype(int).mean()),
        fixed_edge_threshold=fixed_edge_threshold,
    )


def _aggregate_search_reports(fold_reports: list[dict[str, Any]]) -> dict[str, Any]:
    import numpy as np

    total_eval = sum(int(report["n_eval"]) for report in fold_reports)
    weighted_ll = sum(float(report["log_loss"]) * int(report["n_eval"]) for report in fold_reports) / total_eval
    weighted_ll_base = (
        sum(float(report["log_loss_base"]) * int(report["n_eval"]) for report in fold_reports) / total_eval
    )
    weighted_brier = sum(float(report["brier_score"]) * int(report["n_eval"]) for report in fold_reports) / total_eval
    weighted_brier_base = (
        sum(float(report["brier_base"]) * int(report["n_eval"]) for report in fold_reports) / total_eval
    )
    combined_preds = np.concatenate([report["preds"] for report in fold_reports])
    combined_prices = np.concatenate([report["prices"] for report in fold_reports])
    combined_outcomes = np.concatenate([report["outcomes"] for report in fold_reports])
    strategy = _select_decision_policy(
        preds=combined_preds,
        prices=combined_prices,
        outcomes=combined_outcomes,
    )
    min_selected = _min_required_search_trades(total_eval)
    beats_baseline = weighted_ll < weighted_ll_base and weighted_brier < weighted_brier_base
    return {
        "search_log_loss": weighted_ll,
        "search_log_loss_base": weighted_ll_base,
        "search_brier_score": weighted_brier,
        "search_brier_base": weighted_brier_base,
        "search_beats_baseline": beats_baseline,
        "search_selected_trades": strategy["selected_trades"],
        "search_total_pnl": float(strategy["total_pnl"]),
        "search_avg_pnl": float(strategy["avg_pnl"]),
        "search_win_rate": float(strategy["win_rate"]),
        "search_edge_threshold": float(strategy["edge_threshold"]),
        "search_passed": (
            beats_baseline
            and strategy["selected_trades"] >= min_selected
            and strategy["total_pnl"] > 0
            and strategy["avg_pnl"] > 0
        ),
    }


def _score_predictions(
    *,
    preds,
    outcomes,
    prices,
    baseline_rate: float,
    fixed_edge_threshold: float | None = None,
) -> dict[str, Any]:
    import numpy as np
    from sklearn.metrics import brier_score_loss, log_loss

    preds = np.asarray(preds, dtype=float)
    if not np.all(np.isfinite(preds)):
        raise ValueError("model predictions must be finite before scoring")
    baseline_rate = float(baseline_rate)
    if not np.isfinite(baseline_rate):
        raise ValueError("baseline rate must be finite before scoring")
    baseline_pred = np.full(len(outcomes), baseline_rate, dtype=float)
    baseline_pred = np.clip(baseline_pred, 1e-4, 1.0 - 1e-4)
    preds = np.clip(preds, 1e-4, 1.0 - 1e-4)
    baseline_ll = log_loss(outcomes, baseline_pred, labels=[0, 1])
    baseline_brier = brier_score_loss(outcomes, baseline_pred)
    ll = log_loss(outcomes, preds, labels=[0, 1])
    brier = brier_score_loss(outcomes, preds)
    if fixed_edge_threshold is None:
        strategy = _select_decision_policy(preds=preds, prices=prices, outcomes=outcomes)
    else:
        strategy = _decision_metrics_for_threshold(
            preds=preds,
            prices=prices,
            outcomes=outcomes,
            edge_threshold=fixed_edge_threshold,
        )
    return {
        "n_eval": len(outcomes),
        "log_loss": float(ll),
        "log_loss_base": float(baseline_ll),
        "brier_score": float(brier),
        "brier_base": float(baseline_brier),
        "beats_baseline": ll < baseline_ll and brier < baseline_brier,
        "selected_trades": int(strategy["selected_trades"]),
        "total_pnl": float(strategy["total_pnl"]),
        "avg_pnl": float(strategy["avg_pnl"]),
        "win_rate": float(strategy["win_rate"]),
        "edge_threshold": float(strategy["edge_threshold"]),
    }


def _report_dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["log_loss"] > right["log_loss"] or left["brier_score"] > right["brier_score"]:
        return False
    return left["log_loss"] < right["log_loss"] or left["brier_score"] < right["brier_score"]


def _compare_against_incumbent(
    *,
    incumbent_artifact: dict[str, Any] | None,
    final_train_df,
    holdout_df,
    challenger_model,
    challenger_feature_cols: list[str],
    challenger_probability_calibrator,
    challenger_prediction_mode: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "incumbent_present": bool(incumbent_artifact),
        "beats_incumbent": True,
    }
    if incumbent_artifact is None:
        return result

    incumbent_feature_cols = [str(column) for column in incumbent_artifact.get("feature_cols") or FEATURE_COLS]
    shared_holdout_df = _shared_eval_df(holdout_df, incumbent_feature_cols)
    shared_rows = len(shared_holdout_df) if shared_holdout_df is not None else 0
    result["shared_holdout_rows"] = shared_rows
    if shared_holdout_df is None or shared_rows < MIN_FINAL_HOLDOUT_SAMPLES:
        result["beats_incumbent"] = False
        result["reject_reason"] = "incumbent comparison had too few shared final-holdout rows"
        return result

    baseline_rate = float(final_train_df[OUTCOME_COL].mean())
    challenger_report = _evaluate_prediction_report(
        model=challenger_model,
        prediction_mode=challenger_prediction_mode,
        probability_calibrator=challenger_probability_calibrator,
        eval_df=shared_holdout_df,
        feature_cols=challenger_feature_cols,
        baseline_rate=baseline_rate,
    )
    incumbent_report = _evaluate_prediction_report(
        model=incumbent_artifact["model"],
        prediction_mode=str(incumbent_artifact.get("prediction_mode") or "probability"),
        probability_calibrator=incumbent_artifact.get("probability_calibrator"),
        eval_df=shared_holdout_df,
        feature_cols=incumbent_feature_cols,
        baseline_rate=baseline_rate,
    )
    if challenger_report is None or incumbent_report is None:
        result["beats_incumbent"] = False
        result["reject_reason"] = "incumbent comparison could not score both models on the shared final holdout"
        return result

    result.update(
        {
            "shared_holdout_rows": int(challenger_report["n_eval"]),
            "challenger_shared_log_loss": round(float(challenger_report["log_loss"]), 4),
            "challenger_shared_brier_score": round(float(challenger_report["brier_score"]), 4),
            "challenger_shared_total_pnl": round(float(challenger_report["total_pnl"]), 4),
            "challenger_shared_selected_trades": int(challenger_report["selected_trades"]),
            "incumbent_log_loss": round(float(incumbent_report["log_loss"]), 4),
            "incumbent_brier_score": round(float(incumbent_report["brier_score"]), 4),
            "incumbent_total_pnl": round(float(incumbent_report["total_pnl"]), 4),
            "incumbent_selected_trades": int(incumbent_report["selected_trades"]),
        }
    )

    beats_incumbent = _report_dominates(challenger_report, incumbent_report)
    result["beats_incumbent"] = beats_incumbent
    if beats_incumbent:
        return result

    result["reject_reason"] = (
        "challenger did not beat the deployed model on the shared final holdout "
        f"(ll {float(challenger_report['log_loss']):.4f} vs {float(incumbent_report['log_loss']):.4f}, "
        f"brier {float(challenger_report['brier_score']):.4f} vs {float(incumbent_report['brier_score']):.4f})"
    )
    return result


def _select_prediction_path(
    *,
    calibrated_report: dict[str, Any],
    raw_report: dict[str, Any],
    probability_calibrator,
    calibration_method: str,
) -> tuple[dict[str, Any], str, Any, str]:
    if probability_calibrator is None:
        return raw_report, "raw", None, "identity"
    if _report_dominates(raw_report, calibrated_report):
        logger.info(
            "Raw prediction path outperformed calibrated path on final holdout (raw ll=%.4f brier=%.4f; calibrated ll=%.4f brier=%.4f)",
            raw_report["log_loss"],
            raw_report["brier_score"],
            calibrated_report["log_loss"],
            calibrated_report["brier_score"],
        )
        return raw_report, "raw", None, "identity"
    return calibrated_report, "calibrated", probability_calibrator, calibration_method


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_parent(path: Path) -> None:
    parent = path.parent if str(path.parent) else Path(".")
    try:
        directory_fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _dump_model_artifact_atomic(artifact: dict[str, Any], path: str | Path) -> None:
    import joblib

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        joblib.dump(artifact, tmp_path, compress=3)
        _fsync_file(tmp_path)
        tmp_path.replace(target)
        _fsync_parent(target)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _cohort_summaries(df, *, preds=None, baseline_rate: float | None = None) -> dict[str, dict[str, Any]]:
    import numpy as np

    skipped = df["skipped"].astype(bool).to_numpy()
    outcomes = df[OUTCOME_COL].astype(float).to_numpy()
    returns = df[RETURN_COL].astype(float).to_numpy()
    prices = df["effective_price"].astype(float).to_numpy()
    weights = df[SAMPLE_WEIGHT_COL].astype(float).to_numpy() if SAMPLE_WEIGHT_COL in df.columns else None
    pred_values = np.asarray(preds, dtype=float) if preds is not None else None

    def summarize(mask: np.ndarray) -> dict[str, Any]:
        count = int(mask.sum())
        if count == 0:
            return {"n": 0}
        summary: dict[str, Any] = {
            "n": count,
            "weight_total": round(float(weights[mask].sum()), 4) if weights is not None else None,
            "win_rate": round(float(outcomes[mask].mean()), 4),
            "avg_return": round(float(returns[mask].mean()), 4),
            "avg_price": round(float(prices[mask].mean()), 4),
        }
        if pred_values is not None:
            summary["avg_pred"] = round(float(pred_values[mask].mean()), 4)
        if pred_values is not None and baseline_rate is not None and count >= 5 and np.unique(outcomes[mask]).size >= 2:
            report = _score_predictions(
                preds=pred_values[mask],
                outcomes=outcomes[mask].astype(int),
                prices=prices[mask],
                baseline_rate=float(baseline_rate),
            )
            summary.update(
                {
                    "log_loss": round(float(report["log_loss"]), 4),
                    "brier_score": round(float(report["brier_score"]), 4),
                    "selected_trades": int(report["selected_trades"]),
                    "total_pnl": round(float(report["total_pnl"]), 4),
                }
            )
        return summary

    return {
        "executed": summarize(~skipped),
        "counterfactual": summarize(skipped),
    }


def _candidate_rank_key(report: dict[str, Any]) -> tuple:
    return (
        int(bool(report["search_passed"])),
        int(bool(report["search_beats_baseline"])),
        round(float(report["search_total_pnl"]), 6),
        round(float(report["search_avg_pnl"]), 6),
        -round(float(report["search_log_loss"]), 6),
        -round(float(report["search_brier_score"]), 6),
        int(report["search_selected_trades"]),
    )


def _min_required_search_trades(n_eval: int) -> int:
    return min(MIN_VALIDATION_TRADES, max(5, n_eval // 10))


def _select_decision_policy(preds, prices, outcomes) -> dict[str, Any]:
    import numpy as np

    prices = np.clip(prices.astype(float), 0.01, 0.99)
    edges = preds - prices

    candidates = {0.0}
    candidates.update(float(value) for value in np.round(np.linspace(0.0, 0.15, 16), 4))
    positive_edges = edges[edges > 0]
    if len(positive_edges):
        for value in np.quantile(positive_edges, [0.25, 0.5, 0.75]):
            candidates.add(float(np.round(value, 4)))

    min_trades = min(MIN_VALIDATION_TRADES, max(5, len(outcomes) // 10))
    best = {
        "edge_threshold": 0.0,
        "selected_trades": 0,
        "total_pnl": float("-inf"),
        "avg_pnl": float("-inf"),
        "win_rate": 0.0,
    }

    for threshold in sorted(candidates):
        mask = edges >= threshold
        selected = int(mask.sum())
        if selected < min_trades:
            continue
        metrics = _decision_metrics_for_threshold(
            preds=preds,
            prices=prices,
            outcomes=outcomes,
            edge_threshold=threshold,
        )
        total_pnl = float(metrics["total_pnl"])
        avg_pnl = float(metrics["avg_pnl"])
        win_rate = float(metrics["win_rate"])
        score = (total_pnl, avg_pnl, selected)
        best_score = (best["total_pnl"], best["avg_pnl"], best["selected_trades"])
        if score > best_score:
            best = {
                "edge_threshold": float(threshold),
                "selected_trades": selected,
                "total_pnl": total_pnl,
                "avg_pnl": avg_pnl,
                "win_rate": win_rate,
            }

    if best["selected_trades"] == 0:
        fallback = _decision_metrics_for_threshold(
            preds=preds,
            prices=prices,
            outcomes=outcomes,
            edge_threshold=0.0,
        )
        selected = int(fallback["selected_trades"])
        if selected:
            best = fallback
        else:
            best = {
                "edge_threshold": 0.0,
                "selected_trades": 0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "win_rate": 0.0,
            }

    return best


def _decision_metrics_for_threshold(*, preds, prices, outcomes, edge_threshold: float) -> dict[str, Any]:
    import numpy as np

    threshold = float(edge_threshold)
    if not np.isfinite(threshold):
        raise ValueError("edge threshold must be finite")
    prices = np.clip(np.asarray(prices, dtype=float), 0.01, 0.99)
    preds = np.asarray(preds, dtype=float)
    outcomes = np.asarray(outcomes, dtype=int)
    pnl_per_dollar = np.where(outcomes == 1, (1 - prices) / prices, -1.0)
    mask = (preds - prices) >= threshold
    selected = int(mask.sum())
    if selected <= 0:
        return {
            "edge_threshold": threshold,
            "selected_trades": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
        }
    return {
        "edge_threshold": threshold,
        "selected_trades": selected,
        "total_pnl": float(pnl_per_dollar[mask].sum()),
        "avg_pnl": float(pnl_per_dollar[mask].mean()),
        "win_rate": float(outcomes[mask].mean()),
    }


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
    metrics = train()
    print(json.dumps(metrics, indent=2, default=str))
