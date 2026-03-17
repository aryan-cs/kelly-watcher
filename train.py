from __future__ import annotations

import json
import logging
import time
from typing import Any

from config import model_path
from db import get_conn
from features import FEATURE_COLS, LABEL_COL
from trade_contract import DATA_CONTRACT_VERSION, PROFITABLE_TRADE_SQL, RESOLVED_EXECUTED_ENTRY_SQL

logger = logging.getLogger(__name__)

MIN_SAMPLES = 200
MIN_VALIDATION_TRADES = 20
MIN_FEATURE_COVERAGE = 0.75


def load_training_data():
    import pandas as pd

    conn = get_conn()
    df = pd.read_sql_query(
        f"""
        SELECT
            id,
            trade_id,
            placed_at,
            source_action,
            price_at_signal,
            signal_size_usd,
            COALESCE(actual_entry_price, price_at_signal) AS effective_price,
            COALESCE(actual_entry_size_usd, signal_size_usd) AS effective_size_usd,
            counterfactual_return,
            {PROFITABLE_TRADE_SQL} AS {LABEL_COL},
            {", ".join(FEATURE_COLS)},
        FROM trade_log
        WHERE {RESOLVED_EXECUTED_ENTRY_SQL}
        ORDER BY placed_at ASC
        """,
        conn,
    )
    conn.close()
    return df


def train(df=None) -> dict:
    import joblib
    import numpy as np
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.frozen import FrozenEstimator
    from sklearn.metrics import brier_score_loss, log_loss

    if df is None:
        df = load_training_data()

    if len(df) < MIN_SAMPLES:
        logger.info("Training skipped: %s samples (need %s)", len(df), MIN_SAMPLES)
        return {"skipped": True, "n_samples": len(df)}

    feature_cols = _select_feature_cols(df)
    df = df.dropna(subset=feature_cols + [LABEL_COL, "effective_price"])
    if len(df) < MIN_SAMPLES:
        logger.info("Training skipped after filtering: %s samples (need %s)", len(df), MIN_SAMPLES)
        return {"skipped": True, "n_samples": len(df), "feature_cols": feature_cols}

    train_end = int(len(df) * 0.7)
    cal_end = int(len(df) * 0.85)
    train_end = min(max(train_end, 1), len(df) - 2)
    cal_end = min(max(cal_end, train_end + 1), len(df) - 1)

    train_df = df.iloc[:train_end].copy()
    cal_df = df.iloc[train_end:cal_end].copy()
    val_df = df.iloc[cal_end:].copy()

    y_train = train_df[LABEL_COL].astype(int).values
    y_cal = cal_df[LABEL_COL].astype(int).values
    y_val = val_df[LABEL_COL].astype(int).values
    if len(set(y_train)) < 2 or len(set(y_cal)) < 2 or len(set(y_val)) < 2:
        logger.warning("Training skipped: need both outcome classes in train, calibration, and validation")
        return {
            "skipped": True,
            "n_samples": len(df),
            "feature_cols": feature_cols,
            "reason": "insufficient class diversity",
        }

    X_train = train_df[feature_cols].values
    X_cal = cal_df[feature_cols].values
    X_val = val_df[feature_cols].values

    model, model_backend = _build_classifier()
    if model_backend == "xgboost":
        model.fit(X_train, y_train, eval_set=[(X_cal, y_cal)], verbose=False)
    else:
        model.fit(X_train, y_train)

    calibration_method = "sigmoid" if len(X_cal) < 400 else "isotonic"
    calibrated = CalibratedClassifierCV(FrozenEstimator(model), method=calibration_method)
    calibrated.fit(X_cal, y_cal)

    preds = calibrated.predict_proba(X_val)[:, 1]
    base_rate = float(train_df[LABEL_COL].mean())
    baseline_pred = np.full(len(y_val), base_rate, dtype=float)
    baseline_ll = log_loss(y_val, baseline_pred)
    baseline_brier = brier_score_loss(y_val, baseline_pred)
    ll = log_loss(y_val, preds)
    brier = brier_score_loss(y_val, preds)

    strategy = _select_decision_policy(
        preds=preds,
        prices=val_df["effective_price"].astype(float).values,
        outcomes=y_val,
    )

    importances = _feature_ranking(model, feature_cols, train_df)
    top_features = sorted(importances.items(), key=lambda item: -item[1])
    trained_at = int(time.time())
    metrics = {
        "n_samples": len(df),
        "n_train": len(train_df),
        "n_cal": len(cal_df),
        "n_val": len(val_df),
        "feature_cols": feature_cols,
        "feature_count": len(feature_cols),
        "model_backend": model_backend,
        "calibration_method": calibration_method,
        "log_loss": round(ll, 4),
        "log_loss_base": round(baseline_ll, 4),
        "brier_score": round(brier, 4),
        "brier_base": round(baseline_brier, 4),
        "beats_baseline": ll < baseline_ll and brier < baseline_brier,
        "val_selected_trades": strategy["selected_trades"],
        "val_total_pnl": round(strategy["total_pnl"], 4),
        "val_avg_pnl": round(strategy["avg_pnl"], 4),
        "val_win_rate": round(strategy["win_rate"], 4),
        "edge_threshold": round(strategy["edge_threshold"], 4),
        "top_features": top_features[:8],
        "trained_at": trained_at,
        "data_contract_version": DATA_CONTRACT_VERSION,
        "fill_aware_only": True,
        "label_mode": "economic_pnl_positive",
    }

    deployable = (
        metrics["beats_baseline"]
        and strategy["selected_trades"] >= MIN_VALIDATION_TRADES
        and strategy["total_pnl"] > 0
        and strategy["avg_pnl"] > 0
    )
    if not deployable:
        logger.warning("Model failed deployment checks - not deploying")
        return metrics | {"deployed": False}

    path = model_path()
    artifact = {
        "model": calibrated,
        "feature_cols": feature_cols,
        "model_backend": model_backend,
        "data_contract_version": DATA_CONTRACT_VERSION,
        "fill_aware_only": True,
        "policy": {
            "edge_threshold": float(strategy["edge_threshold"]),
            "selected_trades": int(strategy["selected_trades"]),
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
            brier,
            ll,
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
    else:
        model, cols = artifact

    df = df.dropna(subset=cols + [LABEL_COL])
    X = df[cols].values
    y = df[LABEL_COL].values
    pred = model.predict_proba(X)[:, 1]

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


def _build_classifier():
    try:
        import xgboost as xgb

        return xgb.XGBClassifier(
            n_estimators=400,
            max_depth=4,
            learning_rate=0.03,
            subsample=0.85,
            colsample_bytree=0.8,
            min_child_weight=10,
            gamma=0.05,
            reg_alpha=0.1,
            reg_lambda=1.0,
            scale_pos_weight=1.0,
            eval_metric="logloss",
            early_stopping_rounds=30,
            random_state=42,
            verbosity=0,
        ), "xgboost"
    except Exception as exc:
        logger.warning("XGBoost unavailable (%s) - falling back to HistGradientBoosting", exc)
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            max_depth=4,
            learning_rate=0.04,
            max_iter=250,
            min_samples_leaf=12,
            l2_regularization=0.1,
            random_state=42,
        ), "hist_gradient_boosting"


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
