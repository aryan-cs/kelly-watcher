from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from config import (
    heuristic_max_entry_price,
    heuristic_min_entry_price,
    max_bet_fraction,
    max_market_exposure_fraction,
    max_total_open_exposure_fraction,
    max_trader_exposure_fraction,
    min_bet_usd,
    min_confidence,
    model_edge_high_confidence,
    model_edge_high_threshold,
    model_edge_mid_confidence,
    model_edge_mid_threshold,
    shadow_bankroll_usd,
)
from runtime_paths import TRADING_DB_PATH

HEURISTIC_MIN_MARKET_SCORE_LOW_EDGE = 0.70
HEURISTIC_MIN_MARKET_SCORE_HIGH_EDGE = 0.60


@dataclass(frozen=True)
class ReplayPolicy:
    mode: str
    initial_bankroll_usd: float
    min_confidence: float
    min_bet_usd: float
    heuristic_min_entry_price: float
    heuristic_max_entry_price: float
    model_edge_mid_confidence: float
    model_edge_high_confidence: float
    model_edge_mid_threshold: float
    model_edge_high_threshold: float
    max_bet_fraction: float
    max_total_open_exposure_fraction: float
    max_market_exposure_fraction: float
    max_trader_exposure_fraction: float
    allow_heuristic: bool = True
    allow_xgboost: bool = True

    @classmethod
    def default(cls) -> "ReplayPolicy":
        return cls(
            mode="shadow",
            initial_bankroll_usd=float(shadow_bankroll_usd()),
            min_confidence=float(min_confidence()),
            min_bet_usd=float(min_bet_usd()),
            heuristic_min_entry_price=float(heuristic_min_entry_price()),
            heuristic_max_entry_price=float(heuristic_max_entry_price()),
            model_edge_mid_confidence=float(model_edge_mid_confidence()),
            model_edge_high_confidence=float(model_edge_high_confidence()),
            model_edge_mid_threshold=float(model_edge_mid_threshold()),
            model_edge_high_threshold=float(model_edge_high_threshold()),
            max_bet_fraction=float(max_bet_fraction()),
            max_total_open_exposure_fraction=float(max_total_open_exposure_fraction()),
            max_market_exposure_fraction=float(max_market_exposure_fraction()),
            max_trader_exposure_fraction=float(max_trader_exposure_fraction()),
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ReplayPolicy":
        base = asdict(cls.default())
        if payload:
            for key, value in payload.items():
                if key not in base or value is None:
                    continue
                base[key] = value
        return cls(
            mode=str(base["mode"] or "shadow").strip().lower() or "shadow",
            initial_bankroll_usd=max(float(base["initial_bankroll_usd"]), 0.0),
            min_confidence=_clamp(float(base["min_confidence"]), 0.0, 1.0),
            min_bet_usd=max(float(base["min_bet_usd"]), 0.0),
            heuristic_min_entry_price=_clamp(float(base["heuristic_min_entry_price"]), 0.0, 1.0),
            heuristic_max_entry_price=_clamp(float(base["heuristic_max_entry_price"]), 0.0, 1.0),
            model_edge_mid_confidence=_clamp(float(base["model_edge_mid_confidence"]), 0.0, 1.0),
            model_edge_high_confidence=_clamp(float(base["model_edge_high_confidence"]), 0.0, 1.0),
            model_edge_mid_threshold=float(base["model_edge_mid_threshold"]),
            model_edge_high_threshold=float(base["model_edge_high_threshold"]),
            max_bet_fraction=_clamp(float(base["max_bet_fraction"]), 0.0, 1.0),
            max_total_open_exposure_fraction=_clamp(float(base["max_total_open_exposure_fraction"]), 0.0, 1.0),
            max_market_exposure_fraction=_clamp(float(base["max_market_exposure_fraction"]), 0.0, 1.0),
            max_trader_exposure_fraction=_clamp(float(base["max_trader_exposure_fraction"]), 0.0, 1.0),
            allow_heuristic=bool(base["allow_heuristic"]),
            allow_xgboost=bool(base["allow_xgboost"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def version(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def run_replay(
    *,
    policy: ReplayPolicy | dict[str, Any] | None = None,
    db_path: str | Path | None = None,
    label: str = "",
    notes: str = "",
) -> dict[str, Any]:
    resolved_policy = policy if isinstance(policy, ReplayPolicy) else ReplayPolicy.from_payload(policy)
    path = Path(db_path or TRADING_DB_PATH)
    now = int(time.time())

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_replay_schema(conn)

    run_row = _simulate(conn, resolved_policy, label=label, notes=notes, started_at=now)

    conn.close()
    return run_row


def _simulate(
    conn: sqlite3.Connection,
    policy: ReplayPolicy,
    *,
    label: str,
    notes: str,
    started_at: int,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT
            id,
            trade_id,
            market_id,
            trader_address,
            COALESCE(NULLIF(signal_mode, ''), 'heuristic') AS signal_mode,
            confidence,
            price_at_signal,
            actual_entry_price,
            actual_entry_size_usd,
            signal_size_usd,
            skipped,
            skip_reason,
            placed_at,
            COALESCE(exited_at, resolved_at, market_close_ts, placed_at) AS close_ts,
            resolved_at,
            exited_at,
            counterfactual_return,
            COALESCE(actual_pnl_usd, shadow_pnl_usd) AS resolved_pnl_usd,
            decision_context_json
        FROM trade_log
        WHERE COALESCE(source_action, 'buy')='buy'
          AND real_money=?
        ORDER BY placed_at ASC, id ASC
        """,
        (1 if policy.mode == "live" else 0,),
    ).fetchall()

    open_positions: list[dict[str, Any]] = []
    realized_pnl = 0.0
    peak_equity = max(policy.initial_bankroll_usd, 0.0)
    max_drawdown_pct = 0.0
    replay_rows: list[dict[str, Any]] = []
    unresolved_count = 0

    def account_equity() -> float:
        return max(policy.initial_bankroll_usd + realized_pnl, 0.0)

    def open_exposure() -> float:
        return sum(float(position["size_usd"]) for position in open_positions)

    def free_cash() -> float:
        return max(policy.initial_bankroll_usd + realized_pnl - open_exposure(), 0.0)

    def update_drawdown() -> None:
        nonlocal peak_equity, max_drawdown_pct
        current_equity = account_equity()
        if current_equity > peak_equity:
            peak_equity = current_equity
        if peak_equity > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak_equity - current_equity) / peak_equity)

    def close_due_positions(now_ts: int) -> None:
        nonlocal realized_pnl
        remaining: list[dict[str, Any]] = []
        for position in open_positions:
            if int(position["close_ts"]) <= now_ts:
                realized_pnl += float(position["pnl_usd"])
            else:
                remaining.append(position)
        open_positions[:] = remaining
        update_drawdown()

    close_due_positions(0)
    for row in rows:
        placed_at = int(row["placed_at"] or 0)
        close_due_positions(placed_at)

        decision_context = _json_dict(row["decision_context_json"])
        signal = decision_context.get("signal") if isinstance(decision_context.get("signal"), dict) else {}
        signal_mode = str(row["signal_mode"] or signal.get("mode") or "heuristic").strip().lower()
        close_ts = int(row["close_ts"] or placed_at)
        time_to_close_seconds = max(0, close_ts - placed_at)
        time_to_close_band = _time_to_close_band(time_to_close_seconds)
        entry_price = _coalesce_float(
            row["actual_entry_price"],
            signal.get("entry_price"),
            row["price_at_signal"],
        )
        confidence = _coalesce_float(signal.get("confidence"), row["confidence"]) or 0.0
        market_score = _coalesce_float(signal.get("market", {}).get("score"))
        edge = _coalesce_float(signal.get("edge"))
        if edge is None and entry_price is not None:
            edge = confidence - entry_price
        effective_min_confidence = max(
            policy.min_confidence,
            _coalesce_float(signal.get("min_confidence")) or 0.0,
        )

        return_pct, source_status = _resolve_return_pct(row)
        if return_pct is None:
            unresolved_count += 1
            replay_rows.append(
                _replay_trade_row(
                    replay_run_id=0,
                    trade_log_id=int(row["id"]),
                    trade_id=str(row["trade_id"] or ""),
                    placed_at=placed_at,
                    market_id=str(row["market_id"] or ""),
                    trader_address=str(row["trader_address"] or "").lower(),
                    signal_mode=signal_mode,
                    decision="reject",
                    reason="unresolved_outcome",
                    source_status=source_status,
                    entry_price=entry_price,
                    time_to_close_seconds=time_to_close_seconds,
                    time_to_close_band=time_to_close_band,
                    requested_size_usd=0.0,
                    simulated_size_usd=0.0,
                    return_pct=None,
                    pnl_usd=None,
                    bankroll_after_usd=free_cash(),
                    open_exposure_after_usd=open_exposure(),
                    metadata={
                        "confidence": round(confidence, 6),
                        "effective_min_confidence": round(effective_min_confidence, 6),
                    },
                )
            )
            continue

        accepted, reason, requested_size_usd, metadata = _evaluate_trade(
            row=row,
            policy=policy,
            signal_mode=signal_mode,
            confidence=confidence,
            effective_min_confidence=effective_min_confidence,
            entry_price=entry_price,
            market_score=market_score,
            edge=edge,
            available_cash=free_cash(),
        )
        metadata["return_pct"] = round(return_pct, 6)
        if not accepted or requested_size_usd <= 0:
            replay_rows.append(
                _replay_trade_row(
                    replay_run_id=0,
                    trade_log_id=int(row["id"]),
                    trade_id=str(row["trade_id"] or ""),
                    placed_at=placed_at,
                    market_id=str(row["market_id"] or ""),
                    trader_address=str(row["trader_address"] or "").lower(),
                    signal_mode=signal_mode,
                    decision="reject",
                    reason=reason,
                    source_status=source_status,
                    entry_price=entry_price,
                    time_to_close_seconds=time_to_close_seconds,
                    time_to_close_band=time_to_close_band,
                    requested_size_usd=requested_size_usd,
                    simulated_size_usd=0.0,
                    return_pct=return_pct,
                    pnl_usd=None,
                    bankroll_after_usd=free_cash(),
                    open_exposure_after_usd=open_exposure(),
                    metadata=metadata,
                )
            )
            continue

        total_open = open_exposure()
        market_open = sum(
            float(position["size_usd"])
            for position in open_positions
            if position["market_id"] == str(row["market_id"] or "")
        )
        trader_open = sum(
            float(position["size_usd"])
            for position in open_positions
            if position["trader_address"] == str(row["trader_address"] or "").lower()
        )
        equity = account_equity()
        if policy.max_total_open_exposure_fraction > 0 and total_open + requested_size_usd > equity * policy.max_total_open_exposure_fraction + 1e-9:
            replay_rows.append(
                _replay_trade_row(
                    replay_run_id=0,
                    trade_log_id=int(row["id"]),
                    trade_id=str(row["trade_id"] or ""),
                    placed_at=placed_at,
                    market_id=str(row["market_id"] or ""),
                    trader_address=str(row["trader_address"] or "").lower(),
                    signal_mode=signal_mode,
                    decision="reject",
                    reason="total_exposure_cap",
                    source_status=source_status,
                    entry_price=entry_price,
                    time_to_close_seconds=time_to_close_seconds,
                    time_to_close_band=time_to_close_band,
                    requested_size_usd=requested_size_usd,
                    simulated_size_usd=0.0,
                    return_pct=return_pct,
                    pnl_usd=None,
                    bankroll_after_usd=free_cash(),
                    open_exposure_after_usd=open_exposure(),
                    metadata=metadata,
                )
            )
            continue
        if policy.max_market_exposure_fraction > 0 and market_open + requested_size_usd > equity * policy.max_market_exposure_fraction + 1e-9:
            replay_rows.append(
                _replay_trade_row(
                    replay_run_id=0,
                    trade_log_id=int(row["id"]),
                    trade_id=str(row["trade_id"] or ""),
                    placed_at=placed_at,
                    market_id=str(row["market_id"] or ""),
                    trader_address=str(row["trader_address"] or "").lower(),
                    signal_mode=signal_mode,
                    decision="reject",
                    reason="market_exposure_cap",
                    source_status=source_status,
                    entry_price=entry_price,
                    time_to_close_seconds=time_to_close_seconds,
                    time_to_close_band=time_to_close_band,
                    requested_size_usd=requested_size_usd,
                    simulated_size_usd=0.0,
                    return_pct=return_pct,
                    pnl_usd=None,
                    bankroll_after_usd=free_cash(),
                    open_exposure_after_usd=open_exposure(),
                    metadata=metadata,
                )
            )
            continue
        if policy.max_trader_exposure_fraction > 0 and trader_open + requested_size_usd > equity * policy.max_trader_exposure_fraction + 1e-9:
            replay_rows.append(
                _replay_trade_row(
                    replay_run_id=0,
                    trade_log_id=int(row["id"]),
                    trade_id=str(row["trade_id"] or ""),
                    placed_at=placed_at,
                    market_id=str(row["market_id"] or ""),
                    trader_address=str(row["trader_address"] or "").lower(),
                    signal_mode=signal_mode,
                    decision="reject",
                    reason="trader_exposure_cap",
                    source_status=source_status,
                    entry_price=entry_price,
                    time_to_close_seconds=time_to_close_seconds,
                    time_to_close_band=time_to_close_band,
                    requested_size_usd=requested_size_usd,
                    simulated_size_usd=0.0,
                    return_pct=return_pct,
                    pnl_usd=None,
                    bankroll_after_usd=free_cash(),
                    open_exposure_after_usd=open_exposure(),
                    metadata=metadata,
                )
            )
            continue

        pnl_usd = requested_size_usd * return_pct
        if close_ts <= placed_at:
            realized_pnl += pnl_usd
            update_drawdown()
        else:
            open_positions.append(
                {
                    "close_ts": close_ts,
                    "market_id": str(row["market_id"] or ""),
                    "trader_address": str(row["trader_address"] or "").lower(),
                    "size_usd": requested_size_usd,
                    "pnl_usd": pnl_usd,
                }
            )
        replay_rows.append(
            _replay_trade_row(
                replay_run_id=0,
                trade_log_id=int(row["id"]),
                trade_id=str(row["trade_id"] or ""),
                placed_at=placed_at,
                market_id=str(row["market_id"] or ""),
                trader_address=str(row["trader_address"] or "").lower(),
                signal_mode=signal_mode,
                decision="accept",
                reason="accepted",
                source_status=source_status,
                entry_price=entry_price,
                time_to_close_seconds=time_to_close_seconds,
                time_to_close_band=time_to_close_band,
                requested_size_usd=requested_size_usd,
                simulated_size_usd=requested_size_usd,
                return_pct=return_pct,
                pnl_usd=pnl_usd,
                bankroll_after_usd=free_cash(),
                open_exposure_after_usd=open_exposure(),
                metadata=metadata,
            )
        )

    close_due_positions(10**12)

    policy_json = json.dumps(policy.as_dict(), sort_keys=True, separators=(",", ":"))
    accepted_rows = [row for row in replay_rows if row["decision"] == "accept"]
    resolved_rows = [row for row in accepted_rows if row["pnl_usd"] is not None]
    wins = sum(1 for row in resolved_rows if float(row["pnl_usd"] or 0.0) > 0)
    final_bankroll = round(policy.initial_bankroll_usd + realized_pnl - open_exposure(), 6)
    run_id = _insert_replay_run(
        conn,
        {
            "started_at": started_at,
            "finished_at": int(time.time()),
            "label": label.strip(),
            "mode": policy.mode,
            "status": "completed",
            "policy_version": policy.version(),
            "policy_json": policy_json,
            "notes": notes.strip(),
            "initial_bankroll_usd": round(policy.initial_bankroll_usd, 6),
            "final_bankroll_usd": final_bankroll,
            "total_pnl_usd": round(final_bankroll - policy.initial_bankroll_usd, 6),
            "max_drawdown_pct": round(max_drawdown_pct, 6),
            "trade_count": len(replay_rows),
            "accepted_count": len(accepted_rows),
            "rejected_count": len(replay_rows) - len(accepted_rows),
            "unresolved_count": unresolved_count,
            "resolved_count": len(resolved_rows),
            "win_rate": round(wins / len(resolved_rows), 6) if resolved_rows else None,
        },
    )
    _insert_replay_trades(conn, run_id, replay_rows)
    segment_metric_rows = _build_segment_metric_rows(replay_rows)
    _insert_segment_metrics(conn, run_id, segment_metric_rows)
    conn.commit()

    return {
        "run_id": run_id,
        "policy_version": policy.version(),
        "initial_bankroll_usd": round(policy.initial_bankroll_usd, 6),
        "final_bankroll_usd": final_bankroll,
        "total_pnl_usd": round(final_bankroll - policy.initial_bankroll_usd, 6),
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "trade_count": len(replay_rows),
        "accepted_count": len(accepted_rows),
        "rejected_count": len(replay_rows) - len(accepted_rows),
        "unresolved_count": unresolved_count,
        "resolved_count": len(resolved_rows),
        "win_rate": round(wins / len(resolved_rows), 6) if resolved_rows else None,
        "segment_leaders": _segment_leaders(segment_metric_rows),
    }


def _evaluate_trade(
    *,
    row: sqlite3.Row,
    policy: ReplayPolicy,
    signal_mode: str,
    confidence: float,
    effective_min_confidence: float,
    entry_price: float | None,
    market_score: float | None,
    edge: float | None,
    available_cash: float,
) -> tuple[bool, str, float, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "confidence": round(confidence, 6),
        "effective_min_confidence": round(effective_min_confidence, 6),
        "market_score": round(market_score, 6) if market_score is not None else None,
        "edge": round(edge, 6) if edge is not None else None,
    }
    if available_cash <= 0:
        return False, "bankroll_depleted", 0.0, metadata
    if entry_price is None or not (0.0 < entry_price < 1.0):
        return False, "invalid_entry_price", 0.0, metadata
    if confidence < effective_min_confidence:
        return False, "confidence_below_floor", 0.0, metadata

    if signal_mode == "xgboost":
        if not policy.allow_xgboost:
            return False, "xgboost_disabled", 0.0, metadata
        edge_threshold = policy.model_edge_mid_threshold
        if confidence >= policy.model_edge_high_confidence:
            edge_threshold = policy.model_edge_high_threshold
        elif confidence < policy.model_edge_mid_confidence:
            edge_threshold = 0.0
        metadata["edge_threshold"] = round(edge_threshold, 6)
        if edge is None or edge < edge_threshold:
            return False, "model_edge_below_threshold", 0.0, metadata
        requested_size = _kelly_size(
            confidence=confidence,
            market_price=entry_price,
            bankroll_usd=available_cash,
            min_confidence=effective_min_confidence,
            min_bet=policy.min_bet_usd,
            max_fraction=policy.max_bet_fraction,
        )
        if requested_size <= 0:
            return False, "size_below_minimum", 0.0, metadata
        return True, "accepted", requested_size, metadata

    if not policy.allow_heuristic:
        return False, "heuristic_disabled", 0.0, metadata
    if not (policy.heuristic_min_entry_price <= entry_price < policy.heuristic_max_entry_price):
        return False, "heuristic_entry_band", 0.0, metadata
    min_market_score = _heuristic_min_market_score(
        entry_price=entry_price,
        min_entry_price=policy.heuristic_min_entry_price,
        max_entry_price=policy.heuristic_max_entry_price,
    )
    metadata["min_market_score"] = round(min_market_score, 6)
    if market_score is not None and market_score < min_market_score:
        return False, "heuristic_market_floor", 0.0, metadata
    requested_size = _heuristic_size(
        score=confidence,
        bankroll_usd=available_cash,
        min_confidence=effective_min_confidence,
        min_bet=policy.min_bet_usd,
        max_fraction=policy.max_bet_fraction,
        quoted_market_price=_coalesce_float(row["price_at_signal"], entry_price) or entry_price,
        effective_market_price=entry_price,
    )
    if requested_size <= 0:
        return False, "size_below_minimum", 0.0, metadata
    return True, "accepted", requested_size, metadata


def _resolve_return_pct(row: sqlite3.Row) -> tuple[float | None, str]:
    skipped = bool(row["skipped"])
    if skipped:
        counterfactual = _coalesce_float(row["counterfactual_return"])
        if counterfactual is None:
            return None, "skipped_unresolved"
        return counterfactual, "skipped_counterfactual"

    resolved_pnl = _coalesce_float(row["resolved_pnl_usd"])
    actual_entry_size = _coalesce_float(row["actual_entry_size_usd"])
    if resolved_pnl is None or actual_entry_size is None or actual_entry_size <= 0:
        return None, "executed_unresolved"
    return resolved_pnl / actual_entry_size, "executed_resolved"


def _kelly_size(
    *,
    confidence: float,
    market_price: float,
    bankroll_usd: float,
    min_confidence: float,
    min_bet: float,
    max_fraction: float,
) -> float:
    if confidence < min_confidence or bankroll_usd <= 0 or not (0.01 < market_price < 0.99):
        return 0.0
    b = (1 - market_price) / market_price
    f_star = (confidence * (b + 1) - 1) / b
    if f_star <= 0:
        return 0.0
    size = bankroll_usd * min(f_star * 0.5, max_fraction)
    return _apply_minimum_bet(size, bankroll_usd, min_bet, max_fraction)


def _heuristic_size(
    *,
    score: float,
    bankroll_usd: float,
    min_confidence: float,
    min_bet: float,
    max_fraction: float,
    quoted_market_price: float,
    effective_market_price: float,
) -> float:
    if score < min_confidence or bankroll_usd <= 0:
        return 0.0
    span = max(1.0 - min_confidence, 1e-6)
    raw_edge = min(max((score - min_confidence) / span, 0.0), 1.0)
    price_drag = max(effective_market_price - quoted_market_price, 0.0)
    raw_edge = max(raw_edge - price_drag, 0.0)
    if raw_edge <= 0:
        return 0.0
    size = bankroll_usd * max_fraction * (raw_edge ** 0.5)
    return _apply_minimum_bet(size, bankroll_usd, min_bet, max_fraction)


def _apply_minimum_bet(size: float, bankroll_usd: float, min_bet: float, max_fraction: float) -> float:
    if size <= 0:
        return 0.0
    if size >= min_bet:
        return round(size, 2)
    max_size = bankroll_usd * max_fraction
    if bankroll_usd < min_bet or max_size < min_bet:
        return 0.0
    return round(min_bet, 2)


def _heuristic_min_market_score(*, entry_price: float, min_entry_price: float, max_entry_price: float) -> float:
    band_span = max(max_entry_price - min_entry_price, 0.0)
    if band_span <= 1e-6:
        return 0.65
    band_progress = _clamp((entry_price - min_entry_price) / band_span, 0.0, 1.0)
    return float(
        np_interp(
            band_progress,
            [0.0, 1.0],
            [HEURISTIC_MIN_MARKET_SCORE_LOW_EDGE, HEURISTIC_MIN_MARKET_SCORE_HIGH_EDGE],
        )
    )


def _replay_trade_row(
    *,
    replay_run_id: int,
    trade_log_id: int,
    trade_id: str,
    placed_at: int,
    market_id: str,
    trader_address: str,
    signal_mode: str,
    decision: str,
    reason: str,
    source_status: str,
    entry_price: float | None,
    time_to_close_seconds: int,
    time_to_close_band: str,
    requested_size_usd: float,
    simulated_size_usd: float,
    return_pct: float | None,
    pnl_usd: float | None,
    bankroll_after_usd: float,
    open_exposure_after_usd: float,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    payload_metadata = dict(metadata)
    payload_metadata.setdefault("time_to_close_seconds", time_to_close_seconds)
    payload_metadata.setdefault("time_to_close_band", time_to_close_band)
    return {
        "replay_run_id": replay_run_id,
        "trade_log_id": trade_log_id,
        "trade_id": trade_id,
        "placed_at": placed_at,
        "market_id": market_id,
        "trader_address": trader_address,
        "signal_mode": signal_mode,
        "decision": decision,
        "reason": reason,
        "source_status": source_status,
        "entry_price": entry_price,
        "time_to_close_seconds": time_to_close_seconds,
        "time_to_close_band": time_to_close_band,
        "requested_size_usd": round(requested_size_usd, 6),
        "simulated_size_usd": round(simulated_size_usd, 6),
        "return_pct": round(return_pct, 6) if return_pct is not None else None,
        "pnl_usd": round(pnl_usd, 6) if pnl_usd is not None else None,
        "bankroll_after_usd": round(bankroll_after_usd, 6),
        "open_exposure_after_usd": round(open_exposure_after_usd, 6),
        "metadata_json": json.dumps(payload_metadata, sort_keys=True, separators=(",", ":"), default=str),
    }


def _insert_replay_run(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    keys = list(payload.keys())
    placeholders = ",".join(["?"] * len(keys))
    conn.execute(
        f"INSERT INTO replay_runs ({','.join(keys)}) VALUES ({placeholders})",
        [payload[key] for key in keys],
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _insert_replay_trades(conn: sqlite3.Connection, replay_run_id: int, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys = [
        "replay_run_id",
        "trade_log_id",
        "trade_id",
        "placed_at",
        "market_id",
        "trader_address",
        "signal_mode",
        "decision",
        "reason",
        "source_status",
        "entry_price",
        "requested_size_usd",
        "simulated_size_usd",
        "return_pct",
        "pnl_usd",
        "bankroll_after_usd",
        "open_exposure_after_usd",
        "metadata_json",
    ]
    placeholders = ",".join(["?"] * len(keys))
    conn.executemany(
        f"INSERT INTO replay_trades ({','.join(keys)}) VALUES ({placeholders})",
        [[replay_run_id if key == "replay_run_id" else row[key] for key in keys] for row in rows],
    )


def _build_segment_metric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        segment_values = {
            "signal_mode": str(row["signal_mode"] or ""),
            "trader_address": str(row["trader_address"] or ""),
            "entry_price_band": _entry_price_band(_coalesce_float(row["entry_price"])),
            "source_status": str(row["source_status"] or ""),
            "time_to_close_band": str(row.get("time_to_close_band") or ""),
        }
        for segment_kind, segment_value in segment_values.items():
            bucket = buckets.setdefault(
                (segment_kind, segment_value),
                {
                    "trade_count": 0.0,
                    "accepted_count": 0.0,
                    "resolved_count": 0.0,
                    "total_pnl_usd": 0.0,
                    "wins": 0.0,
                    "return_sum": 0.0,
                },
            )
            bucket["trade_count"] += 1
            if row["decision"] == "accept":
                bucket["accepted_count"] += 1
            if row["pnl_usd"] is not None:
                bucket["resolved_count"] += 1
                bucket["total_pnl_usd"] += float(row["pnl_usd"] or 0.0)
                bucket["return_sum"] += float(row["return_pct"] or 0.0)
                if float(row["pnl_usd"] or 0.0) > 0:
                    bucket["wins"] += 1

    metric_rows: list[dict[str, Any]] = []
    for (segment_kind, segment_value), values in buckets.items():
        resolved_count = int(values["resolved_count"])
        metric_rows.append(
            {
                "segment_kind": segment_kind,
                "segment_value": segment_value,
                "trade_count": int(values["trade_count"]),
                "accepted_count": int(values["accepted_count"]),
                "resolved_count": resolved_count,
                "total_pnl_usd": round(values["total_pnl_usd"], 6),
                "win_rate": round(values["wins"] / resolved_count, 6) if resolved_count else None,
                "avg_return_pct": round(values["return_sum"] / resolved_count, 6) if resolved_count else None,
            }
        )
    return metric_rows


def _insert_segment_metrics(conn: sqlite3.Connection, replay_run_id: int, rows: list[dict[str, Any]]) -> None:
    inserts = [
        (
            replay_run_id,
            row["segment_kind"],
            row["segment_value"],
            row["trade_count"],
            row["accepted_count"],
            row["resolved_count"],
            row["total_pnl_usd"],
            row["win_rate"],
            row["avg_return_pct"],
        )
        for row in rows
    ]
    if inserts:
        conn.executemany(
            """
            INSERT INTO segment_metrics (
                replay_run_id, segment_kind, segment_value, trade_count,
                accepted_count, resolved_count, total_pnl_usd, win_rate, avg_return_pct
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            inserts,
        )


def _segment_leaders(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if int(row["accepted_count"]) <= 0 or int(row["resolved_count"]) <= 0:
            continue
        grouped.setdefault(str(row["segment_kind"]), []).append(row)

    summary: dict[str, dict[str, dict[str, Any]]] = {}
    for segment_kind, segment_rows in grouped.items():
        ordered = sorted(
            segment_rows,
            key=lambda row: (
                float(row["total_pnl_usd"]),
                int(row["resolved_count"]),
                int(row["accepted_count"]),
            ),
        )
        summary[segment_kind] = {"worst": ordered[0], "best": ordered[-1]}
    return summary


def _ensure_replay_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS replay_runs (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at              INTEGER NOT NULL,
            finished_at             INTEGER NOT NULL,
            label                   TEXT NOT NULL DEFAULT '',
            mode                    TEXT NOT NULL DEFAULT 'shadow',
            status                  TEXT NOT NULL DEFAULT '',
            policy_version          TEXT NOT NULL DEFAULT '',
            policy_json             TEXT NOT NULL DEFAULT '{}',
            notes                   TEXT NOT NULL DEFAULT '',
            initial_bankroll_usd    REAL NOT NULL DEFAULT 0,
            final_bankroll_usd      REAL NOT NULL DEFAULT 0,
            total_pnl_usd           REAL NOT NULL DEFAULT 0,
            max_drawdown_pct        REAL,
            trade_count             INTEGER NOT NULL DEFAULT 0,
            accepted_count          INTEGER NOT NULL DEFAULT 0,
            rejected_count          INTEGER NOT NULL DEFAULT 0,
            unresolved_count        INTEGER NOT NULL DEFAULT 0,
            resolved_count          INTEGER NOT NULL DEFAULT 0,
            win_rate                REAL
        );

        CREATE TABLE IF NOT EXISTS replay_trades (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_run_id           INTEGER NOT NULL,
            trade_log_id            INTEGER NOT NULL,
            trade_id                TEXT NOT NULL DEFAULT '',
            placed_at               INTEGER NOT NULL DEFAULT 0,
            market_id               TEXT NOT NULL DEFAULT '',
            trader_address          TEXT NOT NULL DEFAULT '',
            signal_mode             TEXT NOT NULL DEFAULT '',
            decision                TEXT NOT NULL DEFAULT '',
            reason                  TEXT NOT NULL DEFAULT '',
            source_status           TEXT NOT NULL DEFAULT '',
            entry_price             REAL,
            requested_size_usd      REAL,
            simulated_size_usd      REAL NOT NULL DEFAULT 0,
            return_pct              REAL,
            pnl_usd                 REAL,
            bankroll_after_usd      REAL,
            open_exposure_after_usd REAL,
            metadata_json           TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS segment_metrics (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_run_id    INTEGER NOT NULL,
            segment_kind     TEXT NOT NULL,
            segment_value    TEXT NOT NULL,
            trade_count      INTEGER NOT NULL DEFAULT 0,
            accepted_count   INTEGER NOT NULL DEFAULT 0,
            resolved_count   INTEGER NOT NULL DEFAULT 0,
            total_pnl_usd    REAL NOT NULL DEFAULT 0,
            win_rate         REAL,
            avg_return_pct   REAL
        );

        CREATE INDEX IF NOT EXISTS idx_replay_runs_finished_at ON replay_runs(finished_at DESC);
        CREATE INDEX IF NOT EXISTS idx_replay_trades_run_id ON replay_trades(replay_run_id);
        CREATE INDEX IF NOT EXISTS idx_segment_metrics_run_kind ON segment_metrics(replay_run_id, segment_kind);
        """
    )


def _entry_price_band(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.45:
        return "<0.45"
    if value < 0.50:
        return "0.45-0.49"
    if value < 0.55:
        return "0.50-0.54"
    if value < 0.60:
        return "0.55-0.59"
    if value < 0.70:
        return "0.60-0.69"
    return ">=0.70"


def _time_to_close_band(seconds: int) -> str:
    if seconds <= 300:
        return "<=5m"
    if seconds <= 1800:
        return "5-30m"
    if seconds <= 7200:
        return "30m-2h"
    if seconds <= 43200:
        return "2h-12h"
    if seconds <= 86400:
        return "12h-1d"
    if seconds <= 259200:
        return "1-3d"
    return ">3d"


def _json_dict(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coalesce_float(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def np_interp(x: float, xp: list[float], fp: list[float]) -> float:
    if x <= xp[0]:
        return fp[0]
    if x >= xp[-1]:
        return fp[-1]
    span = xp[-1] - xp[0]
    if abs(span) <= 1e-9:
        return fp[-1]
    progress = (x - xp[0]) / span
    return fp[0] + progress * (fp[-1] - fp[0])
