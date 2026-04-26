from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kelly_watcher.config import (
    ENTRY_PRICE_BAND_CHOICES,
    allowed_entry_price_bands,
    allowed_time_to_close_bands,
    entry_price_band_label,
    heuristic_max_entry_price,
    heuristic_allowed_entry_price_bands,
    heuristic_min_entry_price,
    heuristic_min_time_to_close_seconds,
    max_bet_fraction,
    max_daily_loss_pct,
    max_live_drawdown_pct,
    max_market_exposure_fraction,
    max_total_open_exposure_fraction,
    max_trader_exposure_fraction,
    min_bet_usd,
    min_confidence,
    model_edge_high_confidence,
    model_edge_high_threshold,
    model_edge_mid_confidence,
    model_edge_mid_threshold,
    model_min_time_to_close_seconds,
    shadow_bankroll_usd,
    xgboost_allowed_entry_price_bands,
)
from kelly_watcher.runtime_paths import TRADING_DB_PATH
from kelly_watcher.engine.trade_contract import NON_CHALLENGER_EXPERIMENT_ARM_SQL, resolved_pnl_expr

HEURISTIC_MIN_MARKET_SCORE_LOW_EDGE = 0.70
HEURISTIC_MIN_MARKET_SCORE_HIGH_EDGE = 0.60

REPLAY_POLICY_CONFIG_KEY_MAP: dict[str, str] = {
    "initial_bankroll_usd": "SHADOW_BANKROLL_USD",
    "min_confidence": "MIN_CONFIDENCE",
    "min_bet_usd": "MIN_BET_USD",
    "allowed_entry_price_bands": "ALLOWED_ENTRY_PRICE_BANDS",
    "allowed_time_to_close_bands": "ALLOWED_TIME_TO_CLOSE_BANDS",
    "allow_heuristic": "ALLOW_HEURISTIC",
    "allow_xgboost": "ALLOW_XGBOOST",
    "heuristic_min_entry_price": "HEURISTIC_MIN_ENTRY_PRICE",
    "heuristic_max_entry_price": "HEURISTIC_MAX_ENTRY_PRICE",
    "heuristic_allowed_entry_price_bands": "HEURISTIC_ALLOWED_ENTRY_PRICE_BANDS",
    "heuristic_min_time_to_close_seconds": "HEURISTIC_MIN_TIME_TO_CLOSE",
    "model_edge_mid_confidence": "MODEL_EDGE_MID_CONFIDENCE",
    "model_edge_high_confidence": "MODEL_EDGE_HIGH_CONFIDENCE",
    "model_edge_mid_threshold": "MODEL_EDGE_MID_THRESHOLD",
    "model_edge_high_threshold": "MODEL_EDGE_HIGH_THRESHOLD",
    "xgboost_allowed_entry_price_bands": "XGBOOST_ALLOWED_ENTRY_PRICE_BANDS",
    "model_min_time_to_close_seconds": "MODEL_MIN_TIME_TO_CLOSE",
    "max_bet_fraction": "MAX_BET_FRACTION",
    "max_total_open_exposure_fraction": "MAX_TOTAL_OPEN_EXPOSURE_FRACTION",
    "max_market_exposure_fraction": "MAX_MARKET_EXPOSURE_FRACTION",
    "max_trader_exposure_fraction": "MAX_TRADER_EXPOSURE_FRACTION",
    "max_daily_loss_pct": "MAX_DAILY_LOSS_PCT",
    "max_live_drawdown_pct": "MAX_LIVE_DRAWDOWN_PCT",
}

ENTRY_PRICE_BANDS: tuple[str, ...] = (
    "<0.45",
    "0.45-0.49",
    "0.50-0.54",
    "0.55-0.59",
    "0.60-0.69",
    ">=0.70",
)

TIME_TO_CLOSE_BANDS: tuple[str, ...] = (
    "<=5m",
    "5-30m",
    "30m-2h",
    "2h-12h",
    "12h-1d",
    "1-3d",
    ">3d",
)


@dataclass(frozen=True)
class ReplayPolicy:
    mode: str
    initial_bankroll_usd: float
    min_confidence: float
    min_bet_usd: float
    heuristic_min_entry_price: float
    heuristic_max_entry_price: float
    heuristic_allowed_entry_price_bands: tuple[str, ...]
    heuristic_min_time_to_close_seconds: int
    model_edge_mid_confidence: float
    model_edge_high_confidence: float
    edge_threshold: float
    model_edge_mid_threshold: float
    model_edge_high_threshold: float
    xgboost_allowed_entry_price_bands: tuple[str, ...]
    model_min_time_to_close_seconds: int
    max_bet_fraction: float
    max_total_open_exposure_fraction: float
    max_market_exposure_fraction: float
    max_trader_exposure_fraction: float
    max_daily_loss_pct: float
    max_live_drawdown_pct: float
    allowed_entry_price_bands: tuple[str, ...] = ()
    allowed_time_to_close_bands: tuple[str, ...] = ()
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
            heuristic_allowed_entry_price_bands=tuple(heuristic_allowed_entry_price_bands()),
            heuristic_min_time_to_close_seconds=int(heuristic_min_time_to_close_seconds()),
            model_edge_mid_confidence=float(model_edge_mid_confidence()),
            model_edge_high_confidence=float(model_edge_high_confidence()),
            edge_threshold=0.0,
            model_edge_mid_threshold=float(model_edge_mid_threshold()),
            model_edge_high_threshold=float(model_edge_high_threshold()),
            xgboost_allowed_entry_price_bands=tuple(xgboost_allowed_entry_price_bands()),
            model_min_time_to_close_seconds=int(model_min_time_to_close_seconds()),
            max_bet_fraction=float(max_bet_fraction()),
            max_total_open_exposure_fraction=float(max_total_open_exposure_fraction()),
            max_market_exposure_fraction=float(max_market_exposure_fraction()),
            max_trader_exposure_fraction=float(max_trader_exposure_fraction()),
            max_daily_loss_pct=float(max_daily_loss_pct()),
            max_live_drawdown_pct=float(max_live_drawdown_pct()),
            allowed_entry_price_bands=tuple(allowed_entry_price_bands()),
            allowed_time_to_close_bands=tuple(allowed_time_to_close_bands()),
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ReplayPolicy":
        base = asdict(cls.default())
        if payload:
            for key, value in payload.items():
                if key not in base or value is None:
                    continue
                base[key] = value
        finite = _finite_float
        return cls(
            mode=str(base["mode"] or "shadow").strip().lower() or "shadow",
            initial_bankroll_usd=max(finite(base["initial_bankroll_usd"], "initial_bankroll_usd"), 0.0),
            min_confidence=_clamp(finite(base["min_confidence"], "min_confidence"), 0.0, 1.0),
            min_bet_usd=max(finite(base["min_bet_usd"], "min_bet_usd"), 0.0),
            heuristic_min_entry_price=_clamp(
                finite(base["heuristic_min_entry_price"], "heuristic_min_entry_price"), 0.0, 1.0
            ),
            heuristic_max_entry_price=_clamp(
                finite(base["heuristic_max_entry_price"], "heuristic_max_entry_price"), 0.0, 1.0
            ),
            heuristic_allowed_entry_price_bands=_normalize_segment_filter(
                base["heuristic_allowed_entry_price_bands"],
                allowed_values=ENTRY_PRICE_BAND_CHOICES,
                field_name="heuristic_allowed_entry_price_bands",
            ),
            heuristic_min_time_to_close_seconds=_coerce_nonnegative_seconds(
                base["heuristic_min_time_to_close_seconds"],
                field_name="heuristic_min_time_to_close_seconds",
            ),
            model_edge_mid_confidence=_clamp(
                finite(base["model_edge_mid_confidence"], "model_edge_mid_confidence"), 0.0, 1.0
            ),
            model_edge_high_confidence=_clamp(
                finite(base["model_edge_high_confidence"], "model_edge_high_confidence"), 0.0, 1.0
            ),
            edge_threshold=_clamp(finite(base["edge_threshold"], "edge_threshold"), 0.0, 1.0),
            model_edge_mid_threshold=_clamp(
                finite(base["model_edge_mid_threshold"], "model_edge_mid_threshold"), 0.0, 1.0
            ),
            model_edge_high_threshold=_clamp(
                finite(base["model_edge_high_threshold"], "model_edge_high_threshold"), 0.0, 1.0
            ),
            xgboost_allowed_entry_price_bands=_normalize_segment_filter(
                base["xgboost_allowed_entry_price_bands"],
                allowed_values=ENTRY_PRICE_BAND_CHOICES,
                field_name="xgboost_allowed_entry_price_bands",
            ),
            model_min_time_to_close_seconds=_coerce_nonnegative_seconds(
                base["model_min_time_to_close_seconds"],
                field_name="model_min_time_to_close_seconds",
            ),
            max_bet_fraction=_clamp(finite(base["max_bet_fraction"], "max_bet_fraction"), 0.0, 1.0),
            max_total_open_exposure_fraction=_clamp(
                finite(base["max_total_open_exposure_fraction"], "max_total_open_exposure_fraction"), 0.0, 1.0
            ),
            max_market_exposure_fraction=_clamp(
                finite(base["max_market_exposure_fraction"], "max_market_exposure_fraction"), 0.0, 1.0
            ),
            max_trader_exposure_fraction=_clamp(
                finite(base["max_trader_exposure_fraction"], "max_trader_exposure_fraction"), 0.0, 1.0
            ),
            max_daily_loss_pct=_clamp(finite(base["max_daily_loss_pct"], "max_daily_loss_pct"), 0.0, 1.0),
            max_live_drawdown_pct=_clamp(
                finite(base["max_live_drawdown_pct"], "max_live_drawdown_pct"), 0.0, 1.0
            ),
            allowed_entry_price_bands=_normalize_segment_filter(
                base["allowed_entry_price_bands"],
                allowed_values=ENTRY_PRICE_BANDS,
                field_name="allowed_entry_price_bands",
            ),
            allowed_time_to_close_bands=_normalize_segment_filter(
                base["allowed_time_to_close_bands"],
                allowed_values=TIME_TO_CLOSE_BANDS,
                field_name="allowed_time_to_close_bands",
            ),
            allow_heuristic=_coerce_bool(base["allow_heuristic"], "allow_heuristic"),
            allow_xgboost=_coerce_bool(base["allow_xgboost"], "allow_xgboost"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def version(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def policy_to_config_payload(policy: ReplayPolicy | dict[str, Any]) -> dict[str, Any]:
    resolved = policy if isinstance(policy, ReplayPolicy) else ReplayPolicy.from_payload(policy)
    payload = resolved.as_dict()
    return {
        config_key: _config_payload_value(policy_key, payload[policy_key])
        for policy_key, config_key in REPLAY_POLICY_CONFIG_KEY_MAP.items()
        if policy_key in payload
    }


def _config_payload_value(policy_key: str, value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return ",".join(str(part) for part in value)
    if policy_key in {"heuristic_min_time_to_close_seconds", "model_min_time_to_close_seconds"}:
        return _format_duration_seconds(int(value))
    return value


def _format_duration_seconds(seconds: int) -> str:
    total_seconds = max(int(seconds), 0)
    if total_seconds == 0:
        return "0s"
    for unit_seconds, suffix in (
        (86400, "d"),
        (3600, "h"),
        (60, "m"),
    ):
        if total_seconds % unit_seconds == 0:
            return f"{total_seconds // unit_seconds}{suffix}"
    return f"{total_seconds}s"


def run_replay(
    *,
    policy: ReplayPolicy | dict[str, Any] | None = None,
    db_path: str | Path | None = None,
    label: str = "",
    notes: str = "",
    start_ts: int | None = None,
    end_ts: int | None = None,
    initial_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_policy = policy if isinstance(policy, ReplayPolicy) else ReplayPolicy.from_payload(policy)
    path = Path(db_path or TRADING_DB_PATH)
    now = int(time.time())

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_replay_schema(conn)

    run_row = _simulate(
        conn,
        resolved_policy,
        label=label,
        notes=notes,
        started_at=now,
        start_ts=start_ts,
        end_ts=end_ts,
        initial_state=initial_state,
    )

    conn.close()
    return run_row


def _simulate(
    conn: sqlite3.Connection,
    policy: ReplayPolicy,
    *,
    label: str,
    notes: str,
    started_at: int,
    start_ts: int | None,
    end_ts: int | None,
    initial_state: dict[str, Any] | None,
) -> dict[str, Any]:
    rows = conn.execute(
        f"""
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
            market_close_ts,
            COALESCE(exited_at, resolved_at, market_close_ts, placed_at) AS close_ts,
            resolved_at,
            exited_at,
            counterfactual_return,
            {resolved_pnl_expr()} AS resolved_pnl_usd,
            decision_context_json
        FROM trade_log
        WHERE COALESCE(source_action, 'buy')='buy'
          AND real_money=?
          AND {NON_CHALLENGER_EXPERIMENT_ARM_SQL}
          AND (? IS NULL OR placed_at >= ?)
          AND (? IS NULL OR placed_at < ?)
        ORDER BY placed_at ASC, id ASC
        """,
        (
            1 if policy.mode == "live" else 0,
            start_ts,
            start_ts,
            end_ts,
            end_ts,
        ),
    ).fetchall()

    continuity_seed = initial_state if isinstance(initial_state, dict) else {}
    open_positions: list[dict[str, Any]] = []
    for raw_position in continuity_seed.get("open_positions") or []:
        if not isinstance(raw_position, dict):
            continue
        size_usd = _coalesce_float(raw_position.get("size_usd"))
        pnl_usd = _coalesce_float(raw_position.get("pnl_usd"))
        if size_usd is None or size_usd <= 0 or pnl_usd is None:
            continue
        open_positions.append(
            {
                "close_ts": _coalesce_nonnegative_int(raw_position.get("close_ts"), default=10**12 + 1),
                "market_id": str(raw_position.get("market_id") or ""),
                "trader_address": str(raw_position.get("trader_address") or "").lower(),
                "size_usd": size_usd,
                "pnl_usd": pnl_usd,
                "signal_mode": _canonical_signal_mode(raw_position.get("signal_mode") or "heuristic"),
                "entry_price": _coalesce_float(raw_position.get("entry_price")),
                "source_status": str(raw_position.get("source_status") or ""),
                "time_to_close_band": str(raw_position.get("time_to_close_band") or ""),
                "carry_resolution": True,
            }
        )
    realized_pnl = _coalesce_float(continuity_seed.get("realized_pnl_usd"), 0.0) or 0.0
    peak_equity = 0.0
    min_equity = 0.0
    max_drawdown_pct = 0.0
    peak_open_exposure_usd = 0.0
    max_open_exposure_share = 0.0
    window_end_open_exposure_usd = 0.0
    window_end_open_exposure_share = 0.0
    window_end_live_guard_triggered = False
    window_end_daily_guard_triggered = False
    replay_rows: list[dict[str, Any]] = []
    carry_resolved_rows: list[dict[str, Any]] = []
    unresolved_count = 0
    carry_resolved_count = 0
    carry_resolved_size_usd = 0.0
    carry_resolved_win_count = 0
    live_guard_triggered = _coerce_state_bool(continuity_seed.get("live_guard_triggered"))
    live_guard_start_equity = max(
        _coalesce_float(continuity_seed.get("live_guard_start_equity"), policy.initial_bankroll_usd) or 0.0,
        0.0,
    )
    live_guard_stop_equity = max(live_guard_start_equity * (1.0 - policy.max_live_drawdown_pct), 0.0)
    daily_guard_start_equity = max(
        _coalesce_float(continuity_seed.get("daily_guard_start_equity"), policy.initial_bankroll_usd) or 0.0,
        0.0,
    )
    daily_guard_day_key = str(continuity_seed.get("daily_guard_day_key") or "")
    daily_guard_locked = _coerce_state_bool(continuity_seed.get("daily_guard_locked"))
    simulation_start_ts = int(start_ts) if start_ts is not None else 0
    simulation_end_ts = int(end_ts) if end_ts is not None else 10**12

    def account_equity() -> float:
        return max(policy.initial_bankroll_usd + realized_pnl, 0.0)

    def open_exposure() -> float:
        return sum(float(position["size_usd"]) for position in open_positions)

    def free_cash() -> float:
        return max(policy.initial_bankroll_usd + realized_pnl - open_exposure(), 0.0)

    def update_drawdown() -> None:
        nonlocal peak_equity, min_equity, max_drawdown_pct
        current_equity = account_equity()
        if current_equity > peak_equity:
            peak_equity = current_equity
        if current_equity < min_equity:
            min_equity = current_equity
        if peak_equity > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak_equity - current_equity) / peak_equity)

    def update_open_exposure_metrics() -> None:
        nonlocal peak_open_exposure_usd, max_open_exposure_share
        current_equity = account_equity()
        current_open_exposure = open_exposure()
        if current_open_exposure > peak_open_exposure_usd:
            peak_open_exposure_usd = current_open_exposure
        if current_equity > 0:
            max_open_exposure_share = max(
                max_open_exposure_share,
                current_open_exposure / current_equity,
            )

    def close_due_positions(now_ts: int, *, record_carry_resolution: bool) -> None:
        nonlocal carry_resolved_count, carry_resolved_size_usd, carry_resolved_win_count, realized_pnl
        remaining: list[dict[str, Any]] = []
        for position in open_positions:
            if int(position["close_ts"]) <= now_ts:
                pnl_usd = float(position["pnl_usd"])
                size_usd = float(position["size_usd"])
                realized_pnl += pnl_usd
                if record_carry_resolution and bool(position.get("carry_resolution")):
                    carry_resolved_count += 1
                    carry_resolved_size_usd += size_usd
                    if pnl_usd > 0:
                        carry_resolved_win_count += 1
                    carry_resolved_rows.append(
                        {
                            "decision": "carry_resolve",
                            "counts_as_trade": False,
                            "signal_mode": str(position.get("signal_mode") or ""),
                            "market_id": str(position.get("market_id") or ""),
                            "trader_address": str(position.get("trader_address") or "").lower(),
                            "entry_price": _coalesce_float(position.get("entry_price")),
                            "source_status": str(position.get("source_status") or ""),
                            "time_to_close_band": str(position.get("time_to_close_band") or ""),
                            "simulated_size_usd": size_usd,
                            "return_pct": (pnl_usd / size_usd) if size_usd > 0 else None,
                            "pnl_usd": pnl_usd,
                        }
                    )
            else:
                remaining.append(position)
        open_positions[:] = remaining
        update_drawdown()
        update_open_exposure_metrics()

    def sync_daily_guard(now_ts: int) -> None:
        nonlocal daily_guard_day_key, daily_guard_locked, daily_guard_start_equity
        current_day = time.strftime("%Y-%m-%d", time.localtime(now_ts))
        if current_day != daily_guard_day_key:
            daily_guard_day_key = current_day
            daily_guard_locked = False
        current_equity = account_equity()
        if not daily_guard_locked and current_equity > 0:
            daily_guard_start_equity = current_equity
            daily_guard_locked = True

    def pause_reason(now_ts: int) -> str | None:
        nonlocal live_guard_triggered
        current_equity = account_equity()
        if policy.mode == "live" and policy.max_live_drawdown_pct > 0:
            if live_guard_triggered or current_equity <= live_guard_stop_equity + 1e-9:
                live_guard_triggered = True
                return "live_drawdown_guard"
        if policy.max_daily_loss_pct > 0 and daily_guard_start_equity > 0:
            stop_equity = max(daily_guard_start_equity * (1.0 - policy.max_daily_loss_pct), 0.0)
            if current_equity <= stop_equity + 1e-9:
                return "daily_loss_guard"
        return None

    close_due_positions(simulation_start_ts, record_carry_resolution=False)
    if start_ts is not None:
        sync_daily_guard(simulation_start_ts)
    starting_equity = account_equity()
    peak_equity = starting_equity
    min_equity = starting_equity
    update_open_exposure_metrics()

    for row in rows:
        placed_at = int(row["placed_at"] or 0)
        close_due_positions(placed_at, record_carry_resolution=True)
        sync_daily_guard(placed_at)

        decision_context = _json_dict(row["decision_context_json"])
        signal = decision_context.get("signal") if isinstance(decision_context.get("signal"), dict) else {}
        signal_mode = _canonical_signal_mode(row["signal_mode"] or signal.get("mode") or "heuristic")
        nonfinite_signal_fields = _nonfinite_signal_fields(signal)
        close_ts = int(row["close_ts"] or placed_at)
        market_close_ts = int(row["market_close_ts"] or 0)
        horizon_close_ts = market_close_ts if market_close_ts > placed_at else close_ts
        time_to_close_seconds = max(0, horizon_close_ts - placed_at)
        time_to_close_band = _time_to_close_band(time_to_close_seconds)
        entry_price = _coalesce_float(
            signal.get("entry_price"),
            row["price_at_signal"],
            row["actual_entry_price"],
        )
        entry_price_band = _entry_price_band(entry_price)
        confidence = _coalesce_float(signal.get("confidence"), row["confidence"]) or 0.0
        market_score = _coalesce_float(signal.get("market", {}).get("score"))
        edge = _coalesce_float(signal.get("edge"))
        if edge is None and entry_price is not None:
            edge = confidence - entry_price
        effective_min_confidence = max(
            policy.min_confidence,
            _coalesce_float(signal.get("min_confidence")) or 0.0,
        )
        base_metadata = _base_trade_metadata(
            confidence=confidence,
            effective_min_confidence=effective_min_confidence,
            market_score=market_score,
            edge=edge,
            entry_price_band=entry_price_band,
            time_to_close_seconds=time_to_close_seconds,
            time_to_close_band=time_to_close_band,
            policy=policy,
        )
        if nonfinite_signal_fields:
            base_metadata["nonfinite_signal_fields"] = nonfinite_signal_fields

        segment_filter_reason = _segment_filter_reason(
            policy=policy,
            entry_price_band=entry_price_band,
            time_to_close_band=time_to_close_band,
        )
        if segment_filter_reason:
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
                    reason=segment_filter_reason,
                    source_status="filtered",
                    entry_price=entry_price,
                    time_to_close_seconds=time_to_close_seconds,
                    time_to_close_band=time_to_close_band,
                    requested_size_usd=0.0,
                    simulated_size_usd=0.0,
                    return_pct=None,
                    pnl_usd=None,
                    bankroll_after_usd=free_cash(),
                    open_exposure_after_usd=open_exposure(),
                    metadata=base_metadata,
                )
            )
            continue

        return_pct, source_status = _resolve_return_pct(row)

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
            base_metadata=base_metadata,
        )
        if not accepted or requested_size_usd <= 0:
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

        observed_entry_size_usd = _coalesce_float(row["actual_entry_size_usd"])
        simulated_size_usd = requested_size_usd
        if source_status.startswith("skipped_"):
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
                    reason="unproven_counterfactual_fill",
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
        if observed_entry_size_usd is not None and observed_entry_size_usd > 0:
            simulated_size_usd = min(requested_size_usd, observed_entry_size_usd)
            if simulated_size_usd < requested_size_usd - 1e-9:
                metadata["requested_size_capped_to_observed_usd"] = round(simulated_size_usd, 6)
        elif source_status.startswith("executed_"):
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
                    reason="unproven_fill_size",
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

        entry_pause_reason = pause_reason(placed_at)
        if entry_pause_reason:
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
                    reason=entry_pause_reason,
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
        if policy.max_total_open_exposure_fraction > 0 and total_open + simulated_size_usd > equity * policy.max_total_open_exposure_fraction + 1e-9:
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
        if policy.max_market_exposure_fraction > 0 and market_open + simulated_size_usd > equity * policy.max_market_exposure_fraction + 1e-9:
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
        if policy.max_trader_exposure_fraction > 0 and trader_open + simulated_size_usd > equity * policy.max_trader_exposure_fraction + 1e-9:
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

        if return_pct is None:
            unresolved_count += 1
            open_positions.append(
                {
                    "close_ts": 10**12 + 1,
                    "market_id": str(row["market_id"] or ""),
                    "trader_address": str(row["trader_address"] or "").lower(),
                    "size_usd": simulated_size_usd,
                    "pnl_usd": 0.0,
                    "signal_mode": signal_mode,
                    "entry_price": entry_price,
                    "source_status": source_status,
                    "time_to_close_band": time_to_close_band,
                    "carry_resolution": False,
                }
            )
            update_open_exposure_metrics()
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
                    simulated_size_usd=simulated_size_usd,
                    return_pct=None,
                    pnl_usd=None,
                    bankroll_after_usd=free_cash(),
                    open_exposure_after_usd=open_exposure(),
                    metadata=metadata,
                )
            )
            continue

        pnl_usd = simulated_size_usd * return_pct
        resolves_within_window = close_ts <= simulation_end_ts
        if close_ts <= placed_at:
            realized_pnl += pnl_usd
            update_drawdown()
            update_open_exposure_metrics()
        else:
            if not resolves_within_window:
                unresolved_count += 1
                metadata["window_carried"] = True
                metadata["eventual_close_ts"] = int(close_ts)
                metadata["eventual_return_pct"] = round(return_pct, 6)
                metadata["eventual_pnl_usd"] = round(pnl_usd, 6)
            open_positions.append(
                {
                    "close_ts": close_ts,
                    "market_id": str(row["market_id"] or ""),
                    "trader_address": str(row["trader_address"] or "").lower(),
                    "size_usd": simulated_size_usd,
                    "pnl_usd": pnl_usd,
                    "signal_mode": signal_mode,
                    "entry_price": entry_price,
                    "source_status": source_status,
                    "time_to_close_band": time_to_close_band,
                    "carry_resolution": False,
                }
            )
            update_open_exposure_metrics()
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
                simulated_size_usd=simulated_size_usd,
                return_pct=return_pct if resolves_within_window else None,
                pnl_usd=pnl_usd if resolves_within_window else None,
                bankroll_after_usd=free_cash(),
                open_exposure_after_usd=open_exposure(),
                metadata=metadata,
            )
        )

    close_due_positions(simulation_end_ts, record_carry_resolution=True)

    policy_json = json.dumps(policy.as_dict(), sort_keys=True, separators=(",", ":"))
    accepted_rows = [row for row in replay_rows if row["decision"] == "accept"]
    rejected_rows = [row for row in replay_rows if row["decision"] == "reject"]
    resolved_rows = [row for row in accepted_rows if row["pnl_usd"] is not None]
    wins = sum(1 for row in resolved_rows if float(row["pnl_usd"] or 0.0) > 0)
    total_resolved_count = len(resolved_rows) + carry_resolved_count
    total_resolved_size_usd = sum(float(row.get("simulated_size_usd") or 0.0) for row in resolved_rows) + carry_resolved_size_usd
    total_wins = wins + carry_resolved_win_count
    final_equity = round(account_equity(), 6)
    window_end_open_exposure_usd = open_exposure()
    if final_equity > 0:
        window_end_open_exposure_share = window_end_open_exposure_usd / final_equity
    elif window_end_open_exposure_usd > 0:
        window_end_open_exposure_share = 1.0
    else:
        window_end_open_exposure_share = 0.0
    window_end_live_guard_triggered = bool(
        policy.mode == "live"
        and policy.max_live_drawdown_pct > 0
        and (
            live_guard_triggered
            or final_equity <= live_guard_stop_equity + 1e-9
        )
    )
    window_end_daily_guard_triggered = bool(
        policy.max_daily_loss_pct > 0
        and daily_guard_start_equity > 0
        and final_equity <= max(daily_guard_start_equity * (1.0 - policy.max_daily_loss_pct), 0.0) + 1e-9
    )
    final_bankroll = round(final_equity - window_end_open_exposure_usd, 6)
    total_pnl_usd = round(final_equity - starting_equity, 6)
    accepted_size_usd = sum(float(row.get("simulated_size_usd") or 0.0) for row in accepted_rows)
    reject_reason_summary: dict[str, int] = {}
    for row in rejected_rows:
        reason = str(row.get("reason") or "").strip()
        if not reason:
            continue
        reject_reason_summary[reason] = reject_reason_summary.get(reason, 0) + 1
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
            "window_start_ts": start_ts,
            "window_end_ts": end_ts,
            "initial_bankroll_usd": round(starting_equity, 6),
            "final_bankroll_usd": final_bankroll,
            "total_pnl_usd": total_pnl_usd,
            "max_drawdown_pct": round(max_drawdown_pct, 6),
            "peak_open_exposure_usd": round(peak_open_exposure_usd, 6),
            "max_open_exposure_share": round(max_open_exposure_share, 6),
            "window_end_open_exposure_usd": round(window_end_open_exposure_usd, 6),
            "window_end_open_exposure_share": round(window_end_open_exposure_share, 6),
            "window_end_live_guard_triggered": 1 if window_end_live_guard_triggered else 0,
            "window_end_daily_guard_triggered": 1 if window_end_daily_guard_triggered else 0,
            "trade_count": len(replay_rows),
            "accepted_count": len(accepted_rows),
            "rejected_count": len(replay_rows) - len(accepted_rows),
            "unresolved_count": unresolved_count,
            "resolved_count": total_resolved_count,
            "win_rate": round(total_wins / total_resolved_count, 6) if total_resolved_count else None,
        },
    )
    _insert_replay_trades(conn, run_id, replay_rows)
    segment_metric_rows = _build_segment_metric_rows(replay_rows + carry_resolved_rows)
    signal_mode_summary = _segment_summary(segment_metric_rows, segment_kind="signal_mode")
    window_end_signal_mode_exposure: dict[str, dict[str, Any]] = {}
    for position in open_positions:
        mode = _canonical_signal_mode(position.get("signal_mode") or "heuristic")
        bucket = window_end_signal_mode_exposure.setdefault(
            mode,
            {
                "open_count": 0,
                "open_size_usd": 0.0,
            },
        )
        bucket["open_count"] += 1
        bucket["open_size_usd"] += float(position.get("size_usd") or 0.0)
    window_end_signal_mode_exposure = {
        mode: {
            "open_count": int(values["open_count"]),
            "open_size_usd": round(float(values["open_size_usd"]), 6),
        }
        for mode, values in sorted(window_end_signal_mode_exposure.items())
    }
    trader_concentration = _trader_concentration(segment_metric_rows)
    market_concentration = _market_concentration(segment_metric_rows)
    entry_price_band_concentration = _entry_price_band_concentration(segment_metric_rows)
    time_to_close_band_concentration = _time_to_close_band_concentration(segment_metric_rows)
    _insert_segment_metrics(conn, run_id, segment_metric_rows)
    conn.commit()

    result = {
        "run_id": run_id,
        "policy_version": policy.version(),
        "window_start_ts": start_ts,
        "window_end_ts": end_ts,
        "initial_bankroll_usd": round(starting_equity, 6),
        "final_equity_usd": final_equity,
        "final_bankroll_usd": final_bankroll,
        "peak_equity_usd": round(peak_equity, 6),
        "min_equity_usd": round(min_equity, 6),
        "peak_open_exposure_usd": round(peak_open_exposure_usd, 6),
        "window_end_open_exposure_usd": round(window_end_open_exposure_usd, 6),
        "total_pnl_usd": total_pnl_usd,
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "max_open_exposure_share": round(max_open_exposure_share, 6),
        "window_end_open_exposure_share": round(window_end_open_exposure_share, 6),
        "window_end_live_guard_triggered": 1 if window_end_live_guard_triggered else 0,
        "window_end_daily_guard_triggered": 1 if window_end_daily_guard_triggered else 0,
        "trade_count": len(replay_rows),
        "accepted_count": len(accepted_rows),
        "accepted_size_usd": round(accepted_size_usd, 6),
        "rejected_count": len(replay_rows) - len(accepted_rows),
        "unresolved_count": unresolved_count,
        "resolved_count": total_resolved_count,
        "resolved_size_usd": round(total_resolved_size_usd, 6),
        "win_rate": round(total_wins / total_resolved_count, 6) if total_resolved_count else None,
        "reject_reason_summary": {reason: int(count) for reason, count in sorted(reject_reason_summary.items())},
        "segment_leaders": _segment_leaders(segment_metric_rows),
        "signal_mode_summary": signal_mode_summary,
        "window_end_signal_mode_exposure": window_end_signal_mode_exposure,
        "trader_concentration": trader_concentration,
        "market_concentration": market_concentration,
        "entry_price_band_concentration": entry_price_band_concentration,
        "time_to_close_band_concentration": time_to_close_band_concentration,
    }
    if start_ts is not None or end_ts is not None or initial_state is not None:
        result["continuity_state"] = {
            "realized_pnl_usd": round(realized_pnl, 6),
            "open_positions": [
                {
                    "close_ts": int(position["close_ts"]),
                    "market_id": str(position["market_id"] or ""),
                    "trader_address": str(position["trader_address"] or "").lower(),
                    "size_usd": round(float(position["size_usd"] or 0.0), 6),
                    "pnl_usd": round(float(position["pnl_usd"] or 0.0), 6),
                    "signal_mode": str(position.get("signal_mode") or ""),
                    "entry_price": _coalesce_float(position.get("entry_price")),
                    "source_status": str(position.get("source_status") or ""),
                    "time_to_close_band": str(position.get("time_to_close_band") or ""),
                }
                for position in open_positions
            ],
            "live_guard_triggered": live_guard_triggered,
            "live_guard_start_equity": round(live_guard_start_equity, 6),
            "daily_guard_day_key": daily_guard_day_key,
            "daily_guard_locked": daily_guard_locked,
            "daily_guard_start_equity": round(daily_guard_start_equity, 6),
        }
    return result


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
    base_metadata: dict[str, Any],
) -> tuple[bool, str, float, dict[str, Any]]:
    metadata = dict(base_metadata)
    entry_price_band = str(metadata.get("entry_price_band") or "")
    time_to_close_seconds = int(metadata.get("time_to_close_seconds") or 0)
    if metadata.get("nonfinite_signal_fields"):
        return False, "nonfinite_signal_value", 0.0, metadata
    if available_cash <= 0:
        return False, "bankroll_depleted", 0.0, metadata
    if entry_price is None or not math.isfinite(entry_price) or not (0.0 < entry_price < 1.0):
        return False, "invalid_entry_price", 0.0, metadata
    if not math.isfinite(confidence) or not math.isfinite(effective_min_confidence):
        return False, "nonfinite_signal_value", 0.0, metadata
    if market_score is not None and not math.isfinite(market_score):
        return False, "nonfinite_signal_value", 0.0, metadata
    if edge is not None and not math.isfinite(edge):
        return False, "nonfinite_signal_value", 0.0, metadata
    if confidence < effective_min_confidence:
        return False, "confidence_below_floor", 0.0, metadata

    if signal_mode == "xgboost":
        if not policy.allow_xgboost:
            return False, "xgboost_disabled", 0.0, metadata
        metadata["mode_allowed_entry_price_bands"] = list(policy.xgboost_allowed_entry_price_bands)
        metadata["model_min_time_to_close_seconds"] = int(policy.model_min_time_to_close_seconds)
        if time_to_close_seconds < policy.model_min_time_to_close_seconds:
            return False, "model_time_to_close_filter", 0.0, metadata
        if (
            policy.xgboost_allowed_entry_price_bands
            and entry_price_band not in policy.xgboost_allowed_entry_price_bands
        ):
            return False, "xgboost_entry_price_band_filter", 0.0, metadata
        edge_threshold = policy.model_edge_mid_threshold
        if confidence >= policy.model_edge_high_confidence:
            edge_threshold = policy.model_edge_high_threshold
        elif confidence < policy.model_edge_mid_confidence:
            edge_threshold = policy.edge_threshold
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
    metadata["mode_allowed_entry_price_bands"] = list(policy.heuristic_allowed_entry_price_bands)
    metadata["heuristic_min_time_to_close_seconds"] = int(policy.heuristic_min_time_to_close_seconds)
    if time_to_close_seconds < policy.heuristic_min_time_to_close_seconds:
        return False, "heuristic_time_to_close_filter", 0.0, metadata
    if (
        policy.heuristic_allowed_entry_price_bands
        and entry_price_band not in policy.heuristic_allowed_entry_price_bands
    ):
        return False, "heuristic_entry_price_band_filter", 0.0, metadata
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
    if (
        not all(math.isfinite(value) for value in (confidence, market_price, bankroll_usd, min_confidence, min_bet, max_fraction))
        or confidence < min_confidence
        or bankroll_usd <= 0
        or not (0.01 < market_price < 0.99)
    ):
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
    if (
        not all(
            math.isfinite(value)
            for value in (
                score,
                bankroll_usd,
                min_confidence,
                min_bet,
                max_fraction,
                quoted_market_price,
                effective_market_price,
            )
        )
        or score < min_confidence
        or bankroll_usd <= 0
    ):
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
    if not all(math.isfinite(value) for value in (size, bankroll_usd, min_bet, max_fraction)):
        return 0.0
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


def _base_trade_metadata(
    *,
    confidence: float,
    effective_min_confidence: float,
    market_score: float | None,
    edge: float | None,
    entry_price_band: str,
    time_to_close_seconds: int,
    time_to_close_band: str,
    policy: ReplayPolicy,
) -> dict[str, Any]:
    return {
        "confidence": round(confidence, 6),
        "effective_min_confidence": round(effective_min_confidence, 6),
        "market_score": round(market_score, 6) if market_score is not None else None,
        "edge": round(edge, 6) if edge is not None else None,
        "entry_price_band": entry_price_band,
        "time_to_close_seconds": int(time_to_close_seconds),
        "time_to_close_band": time_to_close_band,
        "allowed_entry_price_bands": list(policy.allowed_entry_price_bands),
        "allowed_time_to_close_bands": list(policy.allowed_time_to_close_bands),
        "heuristic_allowed_entry_price_bands": list(policy.heuristic_allowed_entry_price_bands),
        "xgboost_allowed_entry_price_bands": list(policy.xgboost_allowed_entry_price_bands),
    }


def _segment_filter_reason(
    *,
    policy: ReplayPolicy,
    entry_price_band: str,
    time_to_close_band: str,
) -> str | None:
    if policy.allowed_entry_price_bands and entry_price_band not in policy.allowed_entry_price_bands:
        return "entry_price_band_filter"
    if policy.allowed_time_to_close_bands and time_to_close_band not in policy.allowed_time_to_close_bands:
        return "time_to_close_band_filter"
    return None


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
            "market_id": str(row["market_id"] or ""),
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
                    "accepted_size_usd": 0.0,
                    "resolved_count": 0.0,
                    "resolved_size_usd": 0.0,
                    "total_pnl_usd": 0.0,
                    "wins": 0.0,
                    "return_sum": 0.0,
                },
            )
            bucket["trade_count"] += 1 if bool(row.get("counts_as_trade", True)) else 0
            if row["decision"] == "accept":
                bucket["accepted_count"] += 1
                bucket["accepted_size_usd"] += float(row.get("simulated_size_usd") or 0.0)
            if row["pnl_usd"] is not None:
                bucket["resolved_count"] += 1
                bucket["resolved_size_usd"] += float(row.get("simulated_size_usd") or 0.0)
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
                "accepted_size_usd": round(values["accepted_size_usd"], 6),
                "resolved_count": resolved_count,
                "resolved_size_usd": round(values["resolved_size_usd"], 6),
                "total_pnl_usd": round(values["total_pnl_usd"], 6),
                "win_count": int(values["wins"]),
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
            row["accepted_size_usd"],
            row["resolved_count"],
            row["resolved_size_usd"],
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
                accepted_count, accepted_size_usd, resolved_count, resolved_size_usd, total_pnl_usd, win_rate, avg_return_pct
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
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


def _segment_summary(rows: list[dict[str, Any]], *, segment_kind: str) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row["segment_kind"]) != segment_kind:
            continue
        segment_value = str(row["segment_value"] or "")
        if not segment_value:
            continue
        summary[segment_value] = {
            "trade_count": int(row["trade_count"]),
            "accepted_count": int(row["accepted_count"]),
            "accepted_size_usd": round(float(row.get("accepted_size_usd") or 0.0), 6),
            "resolved_count": int(row["resolved_count"]),
            "resolved_size_usd": round(float(row.get("resolved_size_usd") or 0.0), 6),
            "total_pnl_usd": round(float(row["total_pnl_usd"] or 0.0), 6),
            "win_count": int(row.get("win_count") or 0),
            "win_rate": round(float(row["win_rate"]), 6) if row.get("win_rate") is not None else None,
            "avg_return_pct": round(float(row["avg_return_pct"]), 6) if row.get("avg_return_pct") is not None else None,
        }
    return summary


def _trader_concentration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _segment_concentration(
        rows,
        segment_kind="trader_address",
        segment_count_key="trader_count",
        count_key="top_accepted_trader_address",
        pnl_key="top_abs_pnl_trader_address",
        size_key="top_size_trader_address",
    )


def _market_concentration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _segment_concentration(
        rows,
        segment_kind="market_id",
        segment_count_key="market_count",
        count_key="top_accepted_market_id",
        pnl_key="top_abs_pnl_market_id",
        size_key="top_size_market_id",
    )


def _entry_price_band_concentration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _segment_concentration(
        rows,
        segment_kind="entry_price_band",
        segment_count_key="entry_price_band_count",
        count_key="top_accepted_entry_price_band",
        pnl_key="top_abs_pnl_entry_price_band",
        size_key="top_size_entry_price_band",
    )


def _time_to_close_band_concentration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _segment_concentration(
        rows,
        segment_kind="time_to_close_band",
        segment_count_key="time_to_close_band_count",
        count_key="top_accepted_time_to_close_band",
        pnl_key="top_abs_pnl_time_to_close_band",
        size_key="top_size_time_to_close_band",
    )


def _segment_concentration(
    rows: list[dict[str, Any]],
    *,
    segment_kind: str,
    segment_count_key: str,
    count_key: str,
    pnl_key: str,
    size_key: str,
) -> dict[str, Any]:
    trader_rows = [
        row
        for row in rows
        if str(row.get("segment_kind") or "") == segment_kind
        and str(row.get("segment_value") or "")
        and (
            int(row.get("accepted_count") or 0) > 0
            or int(row.get("resolved_count") or 0) > 0
        )
    ]
    if not trader_rows:
        return {
            segment_count_key: 0,
            count_key: "",
            "top_accepted_count": 0,
            "top_accepted_share": 0.0,
            "top_accepted_total_pnl_usd": 0.0,
            pnl_key: "",
            "top_abs_pnl_usd": 0.0,
            "top_abs_pnl_share": 0.0,
            size_key: "",
            "top_size_usd": 0.0,
            "top_size_share": 0.0,
        }

    total_accepted = sum(int(row.get("accepted_count") or 0) for row in trader_rows)
    total_accepted_size_usd = sum(float(row.get("accepted_size_usd") or 0.0) for row in trader_rows)
    top_accepted_row = max(
        trader_rows,
        key=lambda row: (
            int(row.get("accepted_count") or 0),
            float(row.get("accepted_size_usd") or 0.0),
            float(row.get("total_pnl_usd") or 0.0),
            str(row.get("segment_value") or ""),
        ),
    )
    total_abs_pnl_usd = sum(abs(float(row.get("total_pnl_usd") or 0.0)) for row in trader_rows)
    top_abs_pnl_row = max(
        trader_rows,
        key=lambda row: (
            abs(float(row.get("total_pnl_usd") or 0.0)),
            float(row.get("accepted_size_usd") or 0.0),
            int(row.get("accepted_count") or 0),
            str(row.get("segment_value") or ""),
        ),
    )
    top_size_row = max(
        trader_rows,
        key=lambda row: (
            float(row.get("accepted_size_usd") or 0.0),
            int(row.get("accepted_count") or 0),
            abs(float(row.get("total_pnl_usd") or 0.0)),
            str(row.get("segment_value") or ""),
        ),
    )
    top_abs_pnl_usd = abs(float(top_abs_pnl_row.get("total_pnl_usd") or 0.0))
    top_size_usd = float(top_size_row.get("accepted_size_usd") or 0.0)
    return {
        segment_count_key: len(trader_rows),
        count_key: str(top_accepted_row.get("segment_value") or ""),
        "top_accepted_count": int(top_accepted_row.get("accepted_count") or 0),
        "top_accepted_share": round(
            float(int(top_accepted_row.get("accepted_count") or 0)) / float(total_accepted),
            6,
        ) if total_accepted > 0 else 0.0,
        "top_accepted_total_pnl_usd": round(float(top_accepted_row.get("total_pnl_usd") or 0.0), 6),
        pnl_key: str(top_abs_pnl_row.get("segment_value") or ""),
        "top_abs_pnl_usd": round(top_abs_pnl_usd, 6),
        "top_abs_pnl_share": round(top_abs_pnl_usd / total_abs_pnl_usd, 6) if total_abs_pnl_usd > 0 else 0.0,
        size_key: str(top_size_row.get("segment_value") or ""),
        "top_size_usd": round(top_size_usd, 6),
        "top_size_share": round(top_size_usd / total_accepted_size_usd, 6) if total_accepted_size_usd > 0 else 0.0,
    }


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
            window_start_ts         INTEGER,
            window_end_ts           INTEGER,
            initial_bankroll_usd    REAL NOT NULL DEFAULT 0,
            final_bankroll_usd      REAL NOT NULL DEFAULT 0,
            total_pnl_usd           REAL NOT NULL DEFAULT 0,
            max_drawdown_pct        REAL,
            peak_open_exposure_usd  REAL NOT NULL DEFAULT 0,
            max_open_exposure_share REAL NOT NULL DEFAULT 0,
            window_end_open_exposure_usd REAL NOT NULL DEFAULT 0,
            window_end_open_exposure_share REAL NOT NULL DEFAULT 0,
            window_end_live_guard_triggered INTEGER NOT NULL DEFAULT 0,
            window_end_daily_guard_triggered INTEGER NOT NULL DEFAULT 0,
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
            accepted_size_usd REAL NOT NULL DEFAULT 0,
            resolved_count   INTEGER NOT NULL DEFAULT 0,
            resolved_size_usd REAL NOT NULL DEFAULT 0,
            total_pnl_usd    REAL NOT NULL DEFAULT 0,
            win_rate         REAL,
            avg_return_pct   REAL
        );

        CREATE INDEX IF NOT EXISTS idx_replay_runs_finished_at ON replay_runs(finished_at DESC);
        CREATE INDEX IF NOT EXISTS idx_replay_trades_run_id ON replay_trades(replay_run_id);
        CREATE INDEX IF NOT EXISTS idx_segment_metrics_run_kind ON segment_metrics(replay_run_id, segment_kind);
        """
    )
    for column_name in ("window_start_ts", "window_end_ts"):
        try:
            conn.execute(f"ALTER TABLE replay_runs ADD COLUMN {column_name} INTEGER")
        except sqlite3.OperationalError:
            pass
    for column_name, column_type in (
        ("peak_open_exposure_usd", "REAL NOT NULL DEFAULT 0"),
        ("max_open_exposure_share", "REAL NOT NULL DEFAULT 0"),
        ("window_end_open_exposure_usd", "REAL NOT NULL DEFAULT 0"),
        ("window_end_open_exposure_share", "REAL NOT NULL DEFAULT 0"),
        ("window_end_live_guard_triggered", "INTEGER NOT NULL DEFAULT 0"),
        ("window_end_daily_guard_triggered", "INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            conn.execute(f"ALTER TABLE replay_runs ADD COLUMN {column_name} {column_type}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE segment_metrics ADD COLUMN accepted_size_usd REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE segment_metrics ADD COLUMN resolved_size_usd REAL NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass


def _entry_price_band(value: float | None) -> str:
    return entry_price_band_label(value)


def _time_to_close_band(seconds: int) -> str:
    if seconds <= 300:
        return TIME_TO_CLOSE_BANDS[0]
    if seconds <= 1800:
        return TIME_TO_CLOSE_BANDS[1]
    if seconds <= 7200:
        return TIME_TO_CLOSE_BANDS[2]
    if seconds <= 43200:
        return TIME_TO_CLOSE_BANDS[3]
    if seconds <= 86400:
        return TIME_TO_CLOSE_BANDS[4]
    if seconds <= 259200:
        return TIME_TO_CLOSE_BANDS[5]
    return TIME_TO_CLOSE_BANDS[6]


def _canonical_signal_mode(raw: Any) -> str:
    normalized = str(raw or "").strip().lower()
    if normalized in {"model", "ml", "hist_gradient_boosting", "xgboost"}:
        return "xgboost"
    if not normalized:
        return "heuristic"
    return normalized


def _normalize_segment_filter(
    raw: Any,
    *,
    allowed_values: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        values = [str(part).strip() for part in raw]
    else:
        values = [str(raw).strip()]
    requested = {value for value in values if value}
    if not requested:
        return ()
    unknown = sorted(requested.difference(allowed_values))
    if unknown:
        raise ValueError(f"Unknown {field_name} values: {', '.join(unknown)}")
    return tuple(value for value in allowed_values if value in requested)


def _coerce_nonnegative_seconds(raw: Any, *, field_name: str) -> int:
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        seconds = float(raw)
    else:
        value = str(raw).strip().lower()
        if not value:
            return 0
        try:
            seconds = float(value)
        except ValueError:
            unit = value[-1:]
            number = value[:-1]
            unit_seconds = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
            if unit not in unit_seconds or not number:
                raise ValueError(f"{field_name} must be a non-negative duration or seconds value")
            try:
                seconds = float(number) * unit_seconds[unit]
            except ValueError as exc:
                raise ValueError(f"{field_name} must be a non-negative duration or seconds value") from exc
    if not math.isfinite(seconds):
        raise ValueError(f"{field_name} must be finite")
    if seconds < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return int(seconds)


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
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            return numeric
    return None


def _coalesce_nonnegative_int(value: Any, *, default: int) -> int:
    numeric = _coalesce_float(value)
    if numeric is None or numeric < 0:
        return default
    return int(numeric)


def _nonfinite_signal_fields(signal: dict[str, Any]) -> list[str]:
    fields: list[tuple[str, Any]] = [
        ("confidence", signal.get("confidence")),
        ("edge", signal.get("edge")),
        ("min_confidence", signal.get("min_confidence")),
    ]
    market = signal.get("market")
    if isinstance(market, dict):
        fields.append(("market.score", market.get("score")))
    nonfinite: list[str] = []
    for name, value in fields:
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric):
            nonfinite.append(name)
    return nonfinite


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _finite_float(raw: Any, field_name: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


def _coerce_bool(raw: Any, field_name: str) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and math.isfinite(float(raw)) and float(raw) in {0.0, 1.0}:
        return bool(int(raw))
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be boolean")


def _coerce_state_bool(raw: Any, *, default: bool = False) -> bool:
    if raw is None:
        return default
    try:
        return _coerce_bool(raw, "continuity_state boolean")
    except ValueError:
        return default


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
