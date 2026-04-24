from __future__ import annotations

import logging
import time

from kelly_watcher.integrations.alerter import build_lines, send_alert
from kelly_watcher.config import retrain_min_new_labels
from kelly_watcher.data.db import get_conn, init_db
from kelly_watcher.engine.shadow_evidence import read_shadow_evidence_epoch
from kelly_watcher.engine.segment_policy import SEGMENT_FALLBACK, SEGMENT_IDS
from kelly_watcher.engine.trade_contract import RESOLVED_TRAINING_SAMPLE_SQL
from kelly_watcher.research.train import check_calibration, load_training_data, min_samples_required, train

logger = logging.getLogger(__name__)
_ROUTED_RETRAIN_SEGMENT_IDS: tuple[str, ...] = (*SEGMENT_IDS, SEGMENT_FALLBACK)
_ROUTED_RETRAIN_SEGMENT_SQL = ",".join("?" for _ in _ROUTED_RETRAIN_SEGMENT_IDS)


def _send_retrain_alert(message: str) -> None:
    send_alert(message, kind="retrain")


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_retrain_run(report: dict[str, object]) -> None:
    init_db()
    metrics = report.get("metrics")
    metrics_dict = metrics if isinstance(metrics, dict) else {}
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO retrain_runs (
            started_at, finished_at, trigger, status, ok, deployed,
            sample_count, min_samples, brier_score, log_loss,
            candidate_name, candidate_count, search_beats_baseline,
            search_total_pnl, val_selected_trades, val_total_pnl,
            challenger_shared_log_loss, challenger_shared_brier_score,
            incumbent_log_loss, incumbent_brier_score, message
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(report.get("started_at") or 0),
            int(report.get("finished_at") or 0),
            str(report.get("trigger") or "manual"),
            str(report.get("status") or ""),
            1 if report.get("ok") else 0,
            1 if report.get("deployed") else 0,
            int(report.get("sample_count") or 0),
            int(report.get("min_samples") or 0),
            _float_or_none(metrics_dict.get("brier_score")),
            _float_or_none(metrics_dict.get("log_loss")),
            str(metrics_dict.get("candidate_name") or "") or None,
            _int_or_none(metrics_dict.get("candidate_count")),
            (
                None
                if metrics_dict.get("search_beats_baseline") is None
                else 1 if metrics_dict.get("search_beats_baseline") else 0
            ),
            _float_or_none(metrics_dict.get("search_total_pnl")),
            _int_or_none(metrics_dict.get("val_selected_trades")),
            _float_or_none(metrics_dict.get("val_total_pnl")),
            _float_or_none(metrics_dict.get("challenger_shared_log_loss")),
            _float_or_none(metrics_dict.get("challenger_shared_brier_score")),
            _float_or_none(metrics_dict.get("incumbent_log_loss")),
            _float_or_none(metrics_dict.get("incumbent_brier_score")),
            str(report.get("message") or ""),
        ),
    )
    conn.commit()
    conn.close()


def _finalize_retrain_report(
    report: dict[str, object],
    *,
    trigger: str,
    started_at: int,
) -> dict[str, object]:
    finalized = report | {
        "trigger": trigger,
        "started_at": started_at,
        "finished_at": int(time.time()),
    }
    _record_retrain_run(finalized)
    return finalized


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


def _active_retrain_training_scope() -> tuple[int | None, bool]:
    epoch_state = read_shadow_evidence_epoch()
    epoch_started_at = max(int(epoch_state.get("shadow_evidence_epoch_started_at") or 0), 0)
    if epoch_started_at <= 0:
        return None, False
    promotion_applied_at = _latest_applied_replay_promotion_at()
    return max(epoch_started_at, promotion_applied_at), True


def _retrain_training_scope_block_reason() -> str:
    since_ts, routed_only = _active_retrain_training_scope()
    if since_ts is None or since_ts <= 0 or not routed_only:
        return (
            "current evidence window is not active yet; retrain must wait for fresh "
            "post-reset routed shadow history"
        )
    return ""


def retrain_cycle_report(signal_engine, *, trigger: str = "manual", started_at: int | None = None) -> dict[str, object]:
    started_at = int(started_at or time.time())
    sample_count = 0
    min_samples = 0

    try:
        scope_block_reason = _retrain_training_scope_block_reason()
        if scope_block_reason:
            logger.info("Auto-retrain blocked: %s", scope_block_reason)
            return _finalize_retrain_report({
                "ok": False,
                "status": "blocked_shadow_snapshot",
                "sample_count": 0,
                "min_samples": min_samples,
                "deployed": False,
                "message": f"Auto-retrain blocked: {scope_block_reason}",
            }, trigger=trigger, started_at=started_at)

        since_ts, routed_only = _active_retrain_training_scope()
        df = load_training_data(since_ts=since_ts, routed_only=routed_only)
        sample_count = len(df)
        min_samples = min_samples_required()
        if sample_count < min_samples:
            message = f"Auto-retrain skipped: {sample_count} labeled samples (need {min_samples})"
            logger.info(message)
            return _finalize_retrain_report({
                "ok": False,
                "status": "skipped_not_enough_samples",
                "sample_count": sample_count,
                "min_samples": min_samples,
                "deployed": False,
                "message": message,
            }, trigger=trigger, started_at=started_at)

        metrics = train(
            df,
            training_since_ts=since_ts,
            training_routed_only=routed_only,
        )
        if metrics.get("skipped"):
            reason = str(metrics.get("reason") or "training skipped")
            message = f"Retrain skipped: {reason}"
            logger.info(message)
            return _finalize_retrain_report({
                "ok": False,
                "status": f"skipped_{reason.replace(' ', '_')}",
                "sample_count": sample_count,
                "min_samples": min_samples,
                "deployed": False,
                "message": message,
                "metrics": metrics,
            }, trigger=trigger, started_at=started_at)
        if not metrics.get("deployed"):
            reject_reason = str(metrics.get("reject_reason") or "").strip() or None
            message = build_lines(
                "retrain rejected",
                "model failed deployment checks",
                f"reason: {reject_reason}" if reject_reason else None,
                f"path: {metrics.get('selected_prediction_path')}",
                f"calibration: {metrics.get('requested_calibration_mode')} -> {metrics.get('calibration_method')}",
                f"brier: {metrics.get('brier_score')}",
                f"log loss: {metrics.get('log_loss')}",
                f"raw brier/log loss: {metrics.get('raw_brier_score')} / {metrics.get('raw_log_loss')}",
                f"val trades: {metrics.get('val_selected_trades')}",
                f"val pnl: {metrics.get('val_total_pnl')}",
                (
                    f"shared holdout ll/brier: {metrics.get('challenger_shared_log_loss')} / "
                    f"{metrics.get('challenger_shared_brier_score')}"
                )
                if metrics.get("challenger_shared_log_loss") is not None and metrics.get("challenger_shared_brier_score") is not None
                else None,
                (
                    f"incumbent ll/brier: {metrics.get('incumbent_log_loss')} / "
                    f"{metrics.get('incumbent_brier_score')}"
                )
                if metrics.get("incumbent_log_loss") is not None and metrics.get("incumbent_brier_score") is not None
                else None,
            )
            logger.warning(message)
            _send_retrain_alert(message)
            return _finalize_retrain_report({
                "ok": False,
                "status": "completed_not_deployed",
                "sample_count": sample_count,
                "min_samples": min_samples,
                "deployed": False,
                "message": message,
                "metrics": metrics,
            }, trigger=trigger, started_at=started_at)

        signal_engine.reload_model()
        calibration = check_calibration(verbose=True)
        top_feature_lines = [f"- {name}: {score:.4f}" for name, score in metrics.get("top_features", [])]
        message = build_lines(
            "retrain accepted",
            f"deployed new model from {sample_count} samples",
            f"path: {metrics.get('selected_prediction_path')}",
            f"calibration: {metrics.get('requested_calibration_mode')} -> {metrics.get('calibration_method')}",
            f"brier: {metrics['brier_score']}",
            f"log loss: {metrics['log_loss']} (baseline {metrics['log_loss_base']})",
            f"raw brier/log loss: {metrics.get('raw_brier_score')} / {metrics.get('raw_log_loss')}",
            f"val trades: {metrics.get('val_selected_trades')}",
            f"val pnl: {metrics.get('val_total_pnl')}",
            f"edge threshold: {metrics.get('edge_threshold')}",
            f"calibration buckets: {len(calibration.get('calibration_bins', []))}",
            "top features:" if top_feature_lines else None,
            "\n".join(top_feature_lines) if top_feature_lines else None,
        )
        logger.info(message)
        _send_retrain_alert(message)
        return _finalize_retrain_report({
            "ok": True,
            "status": "deployed",
            "sample_count": sample_count,
            "min_samples": min_samples,
            "deployed": True,
            "message": message,
            "metrics": metrics,
            "calibration": calibration,
        }, trigger=trigger, started_at=started_at)
    except Exception as exc:
        message = build_lines("retrain failed", str(exc))
        logger.exception(message)
        _send_retrain_alert(message)
        _record_retrain_run(
            {
                "ok": False,
                "status": "failed",
                "sample_count": sample_count,
                "min_samples": min_samples,
                "deployed": False,
                "message": message,
                "trigger": trigger,
                "started_at": started_at,
                "finished_at": int(time.time()),
            }
        )
        raise


def retrain_cycle(signal_engine) -> bool:
    return bool(retrain_cycle_report(signal_engine).get("ok"))


def should_retrain_early(_signal_engine) -> bool:
    if _retrain_training_scope_block_reason():
        return False
    threshold = retrain_min_new_labels()
    since_ts, routed_only = _active_retrain_training_scope()
    conn = get_conn()
    row = conn.execute(
        "SELECT trained_at FROM model_history WHERE deployed=1 ORDER BY trained_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if row is None:
        return len(load_training_data(since_ts=since_ts, routed_only=routed_only)) >= min_samples_required()

    last_retrain = row["trained_at"]
    where_clauses = [f"({RESOLVED_TRAINING_SAMPLE_SQL})", "COALESCE(label_applied_at, resolved_at, placed_at) > ?"]
    params: list[object] = [last_retrain]
    if since_ts is not None and since_ts > 0:
        where_clauses.append("COALESCE(label_applied_at, resolved_at, placed_at) >= ?")
        params.append(since_ts)
    if routed_only:
        where_clauses.append(
            f"COALESCE(NULLIF(TRIM(segment_id), ''), '') IN ({_ROUTED_RETRAIN_SEGMENT_SQL})"
        )
        params.extend(_ROUTED_RETRAIN_SEGMENT_IDS)
    conn = get_conn()
    new_labeled = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM trade_log
        WHERE {" AND ".join(where_clauses)}
        """,
        tuple(params),
    ).fetchone()["n"]
    conn.close()

    if new_labeled >= threshold:
        logger.info("Early retrain triggered: %s new labeled samples (threshold %s)", new_labeled, threshold)
        return True

    return False
