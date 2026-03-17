from __future__ import annotations

import logging

from alerter import send_alert
from db import get_conn
from train import MIN_SAMPLES, check_calibration, load_training_data, train

logger = logging.getLogger(__name__)


def retrain_cycle(signal_engine) -> bool:
    df = load_training_data()
    sample_count = len(df)
    if sample_count < MIN_SAMPLES:
        message = f"Auto-retrain skipped: {sample_count} labeled samples (need {MIN_SAMPLES})"
        logger.info(message)
        send_alert(f"[RETRAIN] {message}")
        return False

    metrics = train(df)
    if metrics.get("skipped"):
        return False
    if not metrics.get("deployed"):
        message = (
            "Retrain complete - model failed deployment checks\n"
            f"Brier: {metrics.get('brier_score')} | LL: {metrics.get('log_loss')}\n"
            f"Val trades: {metrics.get('val_selected_trades')} | Val PnL: {metrics.get('val_total_pnl')}"
        )
        logger.warning(message)
        send_alert(f"[RETRAIN] {message}")
        return False

    signal_engine.reload_model()
    calibration = check_calibration(verbose=True)
    top_features = "\n".join(f"  {name}: {score:.4f}" for name, score in metrics.get("top_features", []))
    message = (
        "[RETRAIN] New model deployed\n"
        f"Samples: {sample_count}\n"
        f"Brier: {metrics['brier_score']}\n"
        f"Log loss: {metrics['log_loss']} (baseline: {metrics['log_loss_base']})\n"
        f"Val trades: {metrics.get('val_selected_trades')}\n"
        f"Val PnL: {metrics.get('val_total_pnl')}\n"
        f"Edge threshold: {metrics.get('edge_threshold')}\n"
        f"Calibration buckets: {len(calibration.get('calibration_bins', []))}\n"
        f"Top features:\n{top_features}"
    )
    logger.info(message)
    send_alert(message)
    return True


def should_retrain_early(_signal_engine) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT trained_at FROM model_history WHERE deployed=1 ORDER BY trained_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if row is None:
        return len(load_training_data()) >= MIN_SAMPLES

    last_retrain = row["trained_at"]
    conn = get_conn()
    new_labeled = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM trade_log
        WHERE outcome IS NOT NULL AND skipped=0 AND placed_at > ?
        """,
        (last_retrain,),
    ).fetchone()["n"]
    conn.close()

    if new_labeled >= 100:
        logger.info("Early retrain triggered: %s new labeled samples", new_labeled)
        return True

    return False
