from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from config import model_path, retrain_min_samples
from db import get_conn
from economic_model import (
    apply_probability_calibrator,
    expected_return_to_confidence,
    inverse_return_target,
    sample_weight_for_trade,
    transform_return_target,
)
from features import FEATURE_COLS, LABEL_COL, OUTCOME_COL, RETURN_COL, SAMPLE_WEIGHT_COL
from trade_contract import (
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


def load_training_data():
    import numpy as np
    import pandas as pd

    conn = get_conn()
    df = pd.read_sql_query(
        f"""
        SELECT
            id,
            trade_id,
            placed_at,
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
        WHERE {RESOLVED_TRAINING_SAMPLE_SQL}
        ORDER BY placed_at ASC
        """,
        conn,
    )
    conn.close()

    if df.empty:
        df[LABEL_COL] = []
        df[OUTCOME_COL] = []
        df[RETURN_COL] = []
        df[SAMPLE_WEIGHT_COL] = []
        return df

    df[RETURN_COL] = pd.to_numeric(df[RETURN_COL], errors="coerce")
    df[OUTCOME_COL] = pd.to_numeric(df[OUTCOME_COL], errors="coerce")
    df[SAMPLE_WEIGHT_COL] = df["skipped"].map(lambda skipped: sample_weight_for_trade(skipped=skipped)).astype(float)
    df[LABEL_COL] = df[RETURN_COL].map(transform_return_target)
    df["effective_price"] = pd.to_numeric(df["effective_price"], errors="coerce")
    df["effective_size_usd"] = pd.to_numeric(df["effective_size_usd"], errors="coerce")
    df = df.replace([np.inf, -np.inf], np.nan)
    return df


def train(df=None) -> dict:
    import joblib

    if df is None:
        df = load_training_data()

    min_samples = min_samples_required()
    if len(df) < min_samples:
        logger.info("Training skipped: %s samples (need %s)", len(df), min_samples)
        return {"skipped": True, "n_samples": len(df)}

    feature_cols = _select_feature_cols(df)
    df = df.dropna(subset=feature_cols + [LABEL_COL, OUTCOME_COL, RETURN_COL, SAMPLE_WEIGHT_COL, "effective_price"])
    df = df[(df["effective_price"] > 0.01) & (df["effective_price"] < 0.99)].copy()
    if len(df) < min_samples:
        logger.info("Training skipped after filtering: %s samples (need %s)", len(df), min_samples)
        return {"skipped": True, "n_samples": len(df), "feature_cols": feature_cols}

    plan = _build_training_plan(len(df))
    if plan is None:
        logger.warning("Training skipped: not enough chronological history for search plus holdout")
        return {
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "insufficient samples for holdout search",
        }

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
        return {
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "candidate search produced no valid model",
        }

    best_candidate = max(candidate_reports, key=_candidate_rank_key)
    logger.info(
        "Selected %s candidate %s from %s options (search pnl=%.4f, beats_baseline=%s)",
        best_candidate["backend"],
        best_candidate["name"],
        len(candidate_reports),
        best_candidate["search_total_pnl"],
        best_candidate["search_beats_baseline"],
    )

    final_fit = _fit_calibrated_model(
        spec=best_candidate,
        train_df=final_train_df,
        cal_df=final_cal_df,
        feature_cols=feature_cols,
    )
    if final_fit is None:
        logger.warning("Training skipped: final fit could not satisfy class diversity requirements")
        return {
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "insufficient class diversity",
        }

    holdout_report = _evaluate_window(
        probability_calibrator=final_fit["probability_calibrator"],
        base_model=final_fit["base_model"],
        train_df=final_train_df,
        eval_df=holdout_df,
        feature_cols=feature_cols,
    )
    if holdout_report is None:
        logger.warning("Training skipped: holdout evaluation could not satisfy class diversity requirements")
        return {
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "insufficient class diversity",
        }

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
        "calibration_method": final_fit["calibration_method"],
        "prediction_mode": "expected_return",
        "log_loss": round(holdout_report["log_loss"], 4),
        "log_loss_base": round(holdout_report["log_loss_base"], 4),
        "brier_score": round(holdout_report["brier_score"], 4),
        "brier_base": round(holdout_report["brier_base"], 4),
        "beats_baseline": holdout_report["beats_baseline"],
        "val_selected_trades": holdout_report["selected_trades"],
        "val_total_pnl": round(holdout_report["total_pnl"], 4),
        "val_avg_pnl": round(holdout_report["avg_pnl"], 4),
        "val_win_rate": round(holdout_report["win_rate"], 4),
        "edge_threshold": round(holdout_report["edge_threshold"], 4),
        "top_features": top_features[:8],
        "trained_at": trained_at,
        "data_contract_version": DATA_CONTRACT_VERSION,
        "fill_aware_only": False,
        "label_mode": MODEL_LABEL_MODE,
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
    }

    deployable = (
        best_candidate["search_passed"]
        and metrics["beats_baseline"]
        and holdout_report["selected_trades"] >= MIN_VALIDATION_TRADES
        and holdout_report["total_pnl"] > 0
        and holdout_report["avg_pnl"] > 0
    )
    if not deployable:
        logger.warning("Model failed deployment checks - not deploying")
        return metrics | {"deployed": False}

    path = model_path()
    artifact = {
        "model": final_fit["base_model"],
        "probability_calibrator": final_fit["probability_calibrator"],
        "feature_cols": feature_cols,
        "model_backend": final_fit["backend"],
        "prediction_mode": "expected_return",
        "data_contract_version": DATA_CONTRACT_VERSION,
        "fill_aware_only": False,
        "label_mode": MODEL_LABEL_MODE,
        "target_transform": "signed_log1p_return",
        "sample_weight_mode": "executed_1.0_skipped_0.25",
        "policy": {
            "edge_threshold": float(holdout_report["edge_threshold"]),
            "selected_trades": int(holdout_report["selected_trades"]),
        },
        "candidate": {
            "name": best_candidate["name"],
            "backend": best_candidate["backend"],
            "search_passed": bool(best_candidate["search_passed"]),
        },
        "metrics": metrics,
    }
    joblib.dump(artifact, path)

    conn = get_conn()
    conn.execute("UPDATE model_history SET deployed=0")
    conn.execute(
        """
        INSERT INTO model_history
        (trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            trained_at,
            len(df),
            float(holdout_report["brier_score"]),
            float(holdout_report["log_loss"]),
            json.dumps(feature_cols),
            path,
            1,
        ),
    )
    conn.commit()
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
                candidates.append(
                    {
                        "name": f"xgb_return_{config['label']}_seed{seed}",
                        "backend": "xgboost",
                        "seed": seed,
                        "params": config["params"] | {"random_state": seed},
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
            candidates.append(
                {
                    "name": f"hgb_return_{config['label']}_seed{seed}",
                    "backend": "hist_gradient_boosting",
                    "seed": seed,
                    "params": config["params"] | {"random_state": seed},
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
        **aggregate,
    }


def _fit_probability_calibrator(base_confidence, outcomes, sample_weight):
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression

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
    y_train_outcome = train_df[OUTCOME_COL].astype(int).values
    y_cal_outcome = cal_df[OUTCOME_COL].astype(int).values
    if len(set(y_train_outcome)) < 2 or len(set(y_cal_outcome)) < 2:
        return None
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
    )
    return {
        "base_model": model,
        "probability_calibrator": probability_calibrator,
        "backend": backend,
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


def _evaluate_window(
    *,
    probability_calibrator,
    base_model,
    train_df,
    eval_df,
    feature_cols: list[str],
) -> dict[str, Any] | None:
    y_train = train_df[OUTCOME_COL].astype(int).values
    y_eval = eval_df[OUTCOME_COL].astype(int).values
    if len(set(y_train)) < 2 or len(set(y_eval)) < 2:
        return None

    X_eval = eval_df[feature_cols].values
    preds = _predict_model_confidence(
        model=base_model,
        prediction_mode="expected_return",
        probability_calibrator=probability_calibrator,
        X=X_eval,
        prices=eval_df["effective_price"].astype(float).values,
    )
    metrics = _score_predictions(
        preds=preds,
        outcomes=y_eval,
        prices=eval_df["effective_price"].astype(float).values,
        baseline_rate=float(y_train.mean()),
    )
    metrics["preds"] = preds
    metrics["prices"] = eval_df["effective_price"].astype(float).values
    metrics["outcomes"] = y_eval
    metrics["base_model"] = base_model
    return metrics


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


def _score_predictions(*, preds, outcomes, prices, baseline_rate: float) -> dict[str, Any]:
    import numpy as np
    from sklearn.metrics import brier_score_loss, log_loss

    preds = np.asarray(preds, dtype=float)
    baseline_pred = np.full(len(outcomes), baseline_rate, dtype=float)
    baseline_pred = apply_probability_calibrator(None, baseline_pred)
    preds = apply_probability_calibrator(None, preds)
    baseline_ll = log_loss(outcomes, baseline_pred)
    baseline_brier = brier_score_loss(outcomes, baseline_pred)
    ll = log_loss(outcomes, preds)
    brier = brier_score_loss(outcomes, preds)
    strategy = _select_decision_policy(preds=preds, prices=prices, outcomes=outcomes)
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
    pnl_per_dollar = np.where(outcomes == 1, (1 - prices) / prices, -1.0)
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
        total_pnl = float(pnl_per_dollar[mask].sum())
        avg_pnl = float(pnl_per_dollar[mask].mean())
        win_rate = float(outcomes[mask].mean()) if selected else 0.0
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
        fallback_mask = edges >= 0
        selected = int(fallback_mask.sum())
        if selected:
            best = {
                "edge_threshold": 0.0,
                "selected_trades": selected,
                "total_pnl": float(pnl_per_dollar[fallback_mask].sum()),
                "avg_pnl": float(pnl_per_dollar[fallback_mask].mean()),
                "win_rate": float(outcomes[fallback_mask].mean()),
            }
        else:
            best = {
                "edge_threshold": 0.0,
                "selected_trades": 0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "win_rate": 0.0,
            }

    return best


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
    metrics = train()
    print(json.dumps(metrics, indent=2, default=str))
