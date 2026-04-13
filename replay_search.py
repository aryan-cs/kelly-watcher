from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from replay import ReplayPolicy, policy_to_config_payload, run_replay
from runtime_paths import TRADING_DB_PATH


def _load_payload(*, file_path: str, inline_json: str) -> dict[str, Any] | None:
    if file_path:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    if inline_json:
        return json.loads(inline_json)
    return None


def _load_base_policy(args: argparse.Namespace) -> ReplayPolicy:
    payload = _load_payload(file_path=args.base_policy_file, inline_json=args.base_policy_json)
    return ReplayPolicy.from_payload(payload)


def _load_grid(args: argparse.Namespace) -> dict[str, list[Any]]:
    payload = _load_payload(file_path=args.grid_file, inline_json=args.grid_json)
    if payload is None:
        raise ValueError("A grid payload is required via --grid-file or --grid-json")
    if not isinstance(payload, dict):
        raise ValueError("Grid payload must be a JSON object")

    base_keys = ReplayPolicy.default().as_dict().keys()
    grid: dict[str, list[Any]] = {}
    for key, value in payload.items():
        if key not in base_keys:
            raise ValueError(f"Unknown replay policy key in grid: {key}")
        if isinstance(value, list):
            values = value
        else:
            values = [value]
        if not values:
            raise ValueError(f"Grid key {key} must have at least one value")
        grid[str(key)] = values
    if not grid:
        raise ValueError("Grid payload must include at least one varying key")
    return grid


def _iter_policy_overrides(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    return [
        dict(zip(keys, combo, strict=True))
        for combo in itertools.product(*(grid[key] for key in keys))
    ]


def _score_breakdown(
    result: dict[str, Any],
    *,
    initial_bankroll_usd: float,
    drawdown_penalty: float,
    window_stddev_penalty: float,
    worst_window_penalty: float,
    pause_guard_penalty: float,
    daily_guard_window_penalty: float = 0.0,
    live_guard_window_penalty: float = 0.0,
    daily_guard_restart_window_penalty: float = 0.0,
    live_guard_restart_window_penalty: float = 0.0,
    open_exposure_penalty: float = 0.0,
    window_end_open_exposure_penalty: float = 0.0,
    avg_window_end_open_exposure_penalty: float = 0.0,
    carry_window_penalty: float = 0.0,
    carry_restart_window_penalty: float = 0.0,
    resolved_share_penalty: float = 0.0,
    resolved_size_share_penalty: float = 0.0,
    worst_window_resolved_share_penalty: float = 0.0,
    worst_window_resolved_size_share_penalty: float = 0.0,
    mode_resolved_share_penalty: float = 0.0,
    mode_resolved_size_share_penalty: float = 0.0,
    mode_worst_window_resolved_share_penalty: float = 0.0,
    mode_worst_window_resolved_size_share_penalty: float = 0.0,
    mode_active_window_accepted_share_penalty: float = 0.0,
    mode_active_window_accepted_size_share_penalty: float = 0.0,
    mode_loss_penalty: float = 0.0,
    mode_inactivity_penalty: float = 0.0,
    allow_heuristic: bool = True,
    allow_xgboost: bool = True,
    wallet_concentration_penalty: float = 0.0,
    market_concentration_penalty: float = 0.0,
    worst_active_window_accepted_penalty: float = 0.0,
    worst_active_window_accepted_size_penalty: float = 0.0,
    mode_worst_active_window_accepted_penalty: float = 0.0,
    mode_worst_active_window_accepted_size_penalty: float = 0.0,
    entry_price_band_concentration_penalty: float = 0.0,
    time_to_close_band_concentration_penalty: float = 0.0,
    window_inactivity_penalty: float = 0.0,
    wallet_count_penalty: float = 0.0,
    market_count_penalty: float = 0.0,
    entry_price_band_count_penalty: float = 0.0,
    time_to_close_band_count_penalty: float = 0.0,
    wallet_size_concentration_penalty: float = 0.0,
    market_size_concentration_penalty: float = 0.0,
    entry_price_band_size_concentration_penalty: float = 0.0,
    time_to_close_band_size_concentration_penalty: float = 0.0,
) -> dict[str, float]:
    pnl = float(result.get("total_pnl_usd") or 0.0)
    max_drawdown_pct = float(result.get("max_drawdown_pct") or 0.0)
    max_open_exposure_share = float(result.get("max_open_exposure_share") or 0.0)
    max_window_end_open_exposure_share = float(
        result.get("max_window_end_open_exposure_share")
        or result.get("window_end_open_exposure_share")
        or 0.0
    )
    avg_window_end_open_exposure_share = _avg_window_end_open_exposure_share(result)
    carry_window_share = _carry_window_share(result)
    carry_restart_window_share = _carry_restart_window_share(result)
    daily_guard_window_share = _daily_guard_window_share(result)
    live_guard_window_share = _live_guard_window_share(result)
    daily_guard_restart_window_share = _daily_guard_restart_window_share(result)
    live_guard_restart_window_share = _live_guard_restart_window_share(result)
    window_pnl_stddev_usd = float(result.get("window_pnl_stddev_usd") or 0.0)
    worst_window_pnl_usd = float(result.get("worst_window_pnl_usd") or 0.0)
    worst_window_loss_usd = max(-worst_window_pnl_usd, 0.0)
    pause_guard_reject_share = _pause_guard_reject_share(result)
    accepted_count = int(result.get("accepted_count") or 0)
    resolved_count = int(result.get("resolved_count") or 0)
    accepted_size_usd = float(result.get("accepted_size_usd") or 0.0)
    resolved_size_usd = float(result.get("resolved_size_usd") or 0.0)
    worst_window_resolved_share = _global_worst_active_window_resolved_share(result)
    worst_window_resolved_size_share = _global_worst_active_window_resolved_size_share(result)
    unresolved_share = (
        float(max(accepted_count - resolved_count, 0)) / float(accepted_count)
        if accepted_count > 0 else 0.0
    )
    unresolved_size_share = (
        max(accepted_size_usd - resolved_size_usd, 0.0) / accepted_size_usd
        if accepted_size_usd > 0
        else 0.0
    )
    worst_window_unresolved_share = max(1.0 - worst_window_resolved_share, 0.0) if accepted_count > 0 else 0.0
    worst_window_unresolved_size_share = max(1.0 - worst_window_resolved_size_share, 0.0) if accepted_size_usd > 0 else 0.0
    signal_mode_summary = _signal_mode_summary(result)
    window_count = max(int(result.get("window_count") or 0), 0)
    inactive_window_count = int(result.get("inactive_window_count") or 0)
    active_window_count = max(int(result.get("active_window_count") or 0), 0)
    accepted_window_count = max(int(result.get("accepted_window_count") or 0), 0)
    if accepted_window_count <= 0 and accepted_size_usd > 0:
        accepted_window_count = max(active_window_count, 1)
    worst_active_window_accepted_count = int(result.get("worst_active_window_accepted_count") or 0)
    worst_active_window_accepted_size_usd = float(result.get("worst_active_window_accepted_size_usd") or 0.0)
    avg_active_window_accepted_size_usd = (
        accepted_size_usd / float(accepted_window_count)
        if accepted_window_count > 0
        else 0.0
    )
    enabled_modes = []
    if allow_heuristic:
        enabled_modes.append("heuristic")
    if allow_xgboost:
        enabled_modes.append("xgboost")
    mode_loss_penalty_usd = mode_loss_penalty * sum(
        max(-float(signal_mode_summary.get(mode, {}).get("total_pnl_usd") or 0.0), 0.0)
        for mode in enabled_modes
        if int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0) > 0
    )
    mode_resolved_share_candidates = [
        (
            float(
                max(
                    int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0)
                    - int(signal_mode_summary.get(mode, {}).get("resolved_count") or 0),
                    0,
                )
            ) / float(int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0))
        )
        for mode in enabled_modes
        if int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0) > 0
    ]
    mode_resolved_share_risk = max(mode_resolved_share_candidates) if mode_resolved_share_candidates else 0.0
    mode_resolved_size_share_candidates = [
        max(1.0 - _resolved_size_share(signal_mode_summary, mode), 0.0)
        for mode in enabled_modes
        if float(signal_mode_summary.get(mode, {}).get("accepted_size_usd") or 0.0) > 0
    ]
    mode_resolved_size_share_risk = (
        max(mode_resolved_size_share_candidates)
        if mode_resolved_size_share_candidates else 0.0
    )
    mode_worst_window_resolved_share_candidates = [
        max(1.0 - _worst_active_window_resolved_share(signal_mode_summary, mode), 0.0)
        for mode in enabled_modes
        if int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0) > 0
    ]
    mode_worst_window_resolved_share_risk = (
        max(mode_worst_window_resolved_share_candidates)
        if mode_worst_window_resolved_share_candidates else 0.0
    )
    mode_worst_window_resolved_size_share_candidates = [
        max(1.0 - _worst_active_window_resolved_size_share(signal_mode_summary, mode), 0.0)
        for mode in enabled_modes
        if float(signal_mode_summary.get(mode, {}).get("accepted_size_usd") or 0.0) > 0
    ]
    mode_worst_window_resolved_size_share_risk = (
        max(mode_worst_window_resolved_size_share_candidates)
        if mode_worst_window_resolved_size_share_candidates else 0.0
    )
    worst_active_window_accepted_risk = (
        1.0 / float(worst_active_window_accepted_count)
        if active_window_count > 0 and worst_active_window_accepted_count > 0
        else 0.0
    )
    worst_active_window_accepted_size_risk = (
        max(1.0 - min(worst_active_window_accepted_size_usd / avg_active_window_accepted_size_usd, 1.0), 0.0)
        if accepted_window_count > 1
        and worst_active_window_accepted_size_usd > 0
        and avg_active_window_accepted_size_usd > 0
        else 0.0
    )
    mode_worst_active_window_accepted_candidates = [
        1.0 / float(int(signal_mode_summary.get(mode, {}).get("worst_active_window_accepted_count") or 0))
        for mode in enabled_modes
        if int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0) > 0
        and int(signal_mode_summary.get(mode, {}).get("worst_active_window_accepted_count") or 0) > 0
    ]
    mode_accepted_window_counts = {
        mode: (
            max(int(signal_mode_summary.get(mode, {}).get("accepted_window_count") or 0), 0)
            or (
                max(window_count - int(signal_mode_summary.get(mode, {}).get("inactive_window_count") or 0), 0)
                if float(signal_mode_summary.get(mode, {}).get("accepted_size_usd") or 0.0) > 0
                else 0
            )
        )
        for mode in enabled_modes
    }
    mode_worst_active_window_accepted_size_candidates = [
        max(
            1.0 - min(
                float(signal_mode_summary.get(mode, {}).get("worst_active_window_accepted_size_usd") or 0.0)
                / (
                    float(signal_mode_summary.get(mode, {}).get("accepted_size_usd") or 0.0)
                    / float(mode_accepted_window_counts.get(mode, 0))
                ),
                1.0,
            ),
            0.0,
        )
        for mode in enabled_modes
        if float(signal_mode_summary.get(mode, {}).get("accepted_size_usd") or 0.0) > 0
        and float(signal_mode_summary.get(mode, {}).get("worst_active_window_accepted_size_usd") or 0.0) > 0
        and mode_accepted_window_counts.get(mode, 0) > 1
    ]
    mode_worst_active_window_accepted_risk = (
        max(mode_worst_active_window_accepted_candidates)
        if mode_worst_active_window_accepted_candidates else 0.0
    )
    mode_worst_active_window_accepted_size_risk = (
        max(mode_worst_active_window_accepted_size_candidates)
        if mode_worst_active_window_accepted_size_candidates else 0.0
    )
    mode_active_window_accepted_share_risk = 0.0
    mode_active_window_accepted_size_share_risk = 0.0
    if allow_heuristic and allow_xgboost:
        heuristic_accepted_count = int(signal_mode_summary.get("heuristic", {}).get("accepted_count") or 0)
        xgboost_accepted_count = int(signal_mode_summary.get("xgboost", {}).get("accepted_count") or 0)
        if heuristic_accepted_count > 0 and xgboost_accepted_count > 0:
            mode_active_window_accepted_share_risk = max(
                _max_active_window_accepted_share(signal_mode_summary, "heuristic"),
                max(1.0 - _min_active_window_accepted_share(signal_mode_summary, "xgboost"), 0.0),
            )
        heuristic_accepted_size_usd = float(signal_mode_summary.get("heuristic", {}).get("accepted_size_usd") or 0.0)
        xgboost_accepted_size_usd = float(signal_mode_summary.get("xgboost", {}).get("accepted_size_usd") or 0.0)
        if heuristic_accepted_size_usd > 0 and xgboost_accepted_size_usd > 0:
            mode_active_window_accepted_size_share_risk = max(
                _max_active_window_accepted_size_share(signal_mode_summary, "heuristic"),
                max(1.0 - _min_active_window_accepted_size_share(signal_mode_summary, "xgboost"), 0.0),
            )
    mode_inactivity_share = max(
        (
            float(int(signal_mode_summary.get(mode, {}).get("inactive_window_count") or 0)) / float(window_count)
            if window_count > 0 else 0.0
        )
        for mode in enabled_modes
    ) if enabled_modes else 0.0
    window_inactivity_share = (
        float(inactive_window_count) / float(window_count)
        if window_count > 0 else 0.0
    )
    wallet_concentration_share = max(
        float(_trader_concentration(result).get("top_accepted_share") or 0.0),
        float(_trader_concentration(result).get("top_abs_pnl_share") or 0.0),
    )
    market_concentration_share = max(
        float(_market_concentration(result).get("top_accepted_share") or 0.0),
        float(_market_concentration(result).get("top_abs_pnl_share") or 0.0),
    )
    entry_price_band_concentration_share = max(
        float(_entry_price_band_concentration(result).get("top_accepted_share") or 0.0),
        float(_entry_price_band_concentration(result).get("top_abs_pnl_share") or 0.0),
    )
    time_to_close_band_concentration_share = max(
        float(_time_to_close_band_concentration(result).get("top_accepted_share") or 0.0),
        float(_time_to_close_band_concentration(result).get("top_abs_pnl_share") or 0.0),
    )
    wallet_size_concentration_share = float(_trader_concentration(result).get("top_size_share") or 0.0)
    market_size_concentration_share = float(_market_concentration(result).get("top_size_share") or 0.0)
    entry_price_band_size_concentration_share = float(_entry_price_band_concentration(result).get("top_size_share") or 0.0)
    time_to_close_band_size_concentration_share = float(_time_to_close_band_concentration(result).get("top_size_share") or 0.0)
    wallet_count_risk = _inverse_count_risk(_trader_concentration(result).get("trader_count"))
    market_count_risk = _inverse_count_risk(_market_concentration(result).get("market_count"))
    entry_price_band_count_risk = _inverse_count_risk(_entry_price_band_concentration(result).get("entry_price_band_count"))
    time_to_close_band_count_risk = _inverse_count_risk(_time_to_close_band_concentration(result).get("time_to_close_band_count"))
    drawdown_penalty_usd = initial_bankroll_usd * drawdown_penalty * max_drawdown_pct
    window_stddev_penalty_usd = window_stddev_penalty * window_pnl_stddev_usd
    worst_window_penalty_usd = worst_window_penalty * worst_window_loss_usd
    pause_guard_penalty_usd = initial_bankroll_usd * pause_guard_penalty * pause_guard_reject_share
    daily_guard_window_penalty_usd = initial_bankroll_usd * daily_guard_window_penalty * daily_guard_window_share
    live_guard_window_penalty_usd = initial_bankroll_usd * live_guard_window_penalty * live_guard_window_share
    daily_guard_restart_window_penalty_usd = initial_bankroll_usd * daily_guard_restart_window_penalty * daily_guard_restart_window_share
    live_guard_restart_window_penalty_usd = initial_bankroll_usd * live_guard_restart_window_penalty * live_guard_restart_window_share
    open_exposure_penalty_usd = initial_bankroll_usd * open_exposure_penalty * max_open_exposure_share
    window_end_open_exposure_penalty_usd = (
        initial_bankroll_usd
        * window_end_open_exposure_penalty
        * max_window_end_open_exposure_share
    )
    avg_window_end_open_exposure_penalty_usd = (
        initial_bankroll_usd
        * avg_window_end_open_exposure_penalty
        * avg_window_end_open_exposure_share
    )
    carry_window_penalty_usd = initial_bankroll_usd * carry_window_penalty * carry_window_share
    carry_restart_window_penalty_usd = initial_bankroll_usd * carry_restart_window_penalty * carry_restart_window_share
    resolved_share_penalty_usd = initial_bankroll_usd * resolved_share_penalty * unresolved_share
    resolved_size_share_penalty_usd = initial_bankroll_usd * resolved_size_share_penalty * unresolved_size_share
    worst_window_resolved_share_penalty_usd = initial_bankroll_usd * worst_window_resolved_share_penalty * worst_window_unresolved_share
    worst_window_resolved_size_share_penalty_usd = initial_bankroll_usd * worst_window_resolved_size_share_penalty * worst_window_unresolved_size_share
    mode_resolved_share_penalty_usd = initial_bankroll_usd * mode_resolved_share_penalty * mode_resolved_share_risk
    mode_resolved_size_share_penalty_usd = initial_bankroll_usd * mode_resolved_size_share_penalty * mode_resolved_size_share_risk
    mode_worst_window_resolved_share_penalty_usd = initial_bankroll_usd * mode_worst_window_resolved_share_penalty * mode_worst_window_resolved_share_risk
    mode_worst_window_resolved_size_share_penalty_usd = initial_bankroll_usd * mode_worst_window_resolved_size_share_penalty * mode_worst_window_resolved_size_share_risk
    mode_active_window_accepted_share_penalty_usd = initial_bankroll_usd * mode_active_window_accepted_share_penalty * mode_active_window_accepted_share_risk
    mode_active_window_accepted_size_share_penalty_usd = initial_bankroll_usd * mode_active_window_accepted_size_share_penalty * mode_active_window_accepted_size_share_risk
    worst_active_window_accepted_penalty_usd = initial_bankroll_usd * worst_active_window_accepted_penalty * worst_active_window_accepted_risk
    worst_active_window_accepted_size_penalty_usd = initial_bankroll_usd * worst_active_window_accepted_size_penalty * worst_active_window_accepted_size_risk
    mode_worst_active_window_accepted_penalty_usd = initial_bankroll_usd * mode_worst_active_window_accepted_penalty * mode_worst_active_window_accepted_risk
    mode_worst_active_window_accepted_size_penalty_usd = initial_bankroll_usd * mode_worst_active_window_accepted_size_penalty * mode_worst_active_window_accepted_size_risk
    wallet_concentration_penalty_usd = initial_bankroll_usd * wallet_concentration_penalty * wallet_concentration_share
    market_concentration_penalty_usd = initial_bankroll_usd * market_concentration_penalty * market_concentration_share
    entry_price_band_concentration_penalty_usd = initial_bankroll_usd * entry_price_band_concentration_penalty * entry_price_band_concentration_share
    time_to_close_band_concentration_penalty_usd = initial_bankroll_usd * time_to_close_band_concentration_penalty * time_to_close_band_concentration_share
    wallet_count_penalty_usd = initial_bankroll_usd * wallet_count_penalty * wallet_count_risk
    market_count_penalty_usd = initial_bankroll_usd * market_count_penalty * market_count_risk
    entry_price_band_count_penalty_usd = initial_bankroll_usd * entry_price_band_count_penalty * entry_price_band_count_risk
    time_to_close_band_count_penalty_usd = initial_bankroll_usd * time_to_close_band_count_penalty * time_to_close_band_count_risk
    wallet_size_concentration_penalty_usd = initial_bankroll_usd * wallet_size_concentration_penalty * wallet_size_concentration_share
    market_size_concentration_penalty_usd = initial_bankroll_usd * market_size_concentration_penalty * market_size_concentration_share
    entry_price_band_size_concentration_penalty_usd = initial_bankroll_usd * entry_price_band_size_concentration_penalty * entry_price_band_size_concentration_share
    time_to_close_band_size_concentration_penalty_usd = initial_bankroll_usd * time_to_close_band_size_concentration_penalty * time_to_close_band_size_concentration_share
    mode_inactivity_penalty_usd = initial_bankroll_usd * mode_inactivity_penalty * mode_inactivity_share
    window_inactivity_penalty_usd = initial_bankroll_usd * window_inactivity_penalty * window_inactivity_share
    score_usd = (
        pnl
        - drawdown_penalty_usd
        - window_stddev_penalty_usd
        - worst_window_penalty_usd
        - pause_guard_penalty_usd
        - daily_guard_window_penalty_usd
        - live_guard_window_penalty_usd
        - daily_guard_restart_window_penalty_usd
        - live_guard_restart_window_penalty_usd
        - open_exposure_penalty_usd
        - window_end_open_exposure_penalty_usd
        - avg_window_end_open_exposure_penalty_usd
        - carry_window_penalty_usd
        - carry_restart_window_penalty_usd
        - resolved_share_penalty_usd
        - resolved_size_share_penalty_usd
        - worst_window_resolved_share_penalty_usd
        - worst_window_resolved_size_share_penalty_usd
        - mode_resolved_share_penalty_usd
        - mode_resolved_size_share_penalty_usd
        - mode_worst_window_resolved_share_penalty_usd
        - mode_worst_window_resolved_size_share_penalty_usd
        - mode_active_window_accepted_share_penalty_usd
        - mode_active_window_accepted_size_share_penalty_usd
        - worst_active_window_accepted_penalty_usd
        - worst_active_window_accepted_size_penalty_usd
        - mode_worst_active_window_accepted_penalty_usd
        - mode_worst_active_window_accepted_size_penalty_usd
        - mode_loss_penalty_usd
        - mode_inactivity_penalty_usd
        - window_inactivity_penalty_usd
        - wallet_count_penalty_usd
        - market_count_penalty_usd
        - entry_price_band_count_penalty_usd
        - time_to_close_band_count_penalty_usd
        - wallet_concentration_penalty_usd
        - market_concentration_penalty_usd
        - entry_price_band_concentration_penalty_usd
        - time_to_close_band_concentration_penalty_usd
        - wallet_size_concentration_penalty_usd
        - market_size_concentration_penalty_usd
        - entry_price_band_size_concentration_penalty_usd
        - time_to_close_band_size_concentration_penalty_usd
    )
    return {
        "pnl_usd": round(pnl, 6),
        "drawdown_penalty_usd": round(drawdown_penalty_usd, 6),
        "window_stddev_penalty_usd": round(window_stddev_penalty_usd, 6),
        "worst_window_penalty_usd": round(worst_window_penalty_usd, 6),
        "pause_guard_penalty_usd": round(pause_guard_penalty_usd, 6),
        "daily_guard_window_penalty_usd": round(daily_guard_window_penalty_usd, 6),
        "live_guard_window_penalty_usd": round(live_guard_window_penalty_usd, 6),
        "daily_guard_restart_window_penalty_usd": round(daily_guard_restart_window_penalty_usd, 6),
        "live_guard_restart_window_penalty_usd": round(live_guard_restart_window_penalty_usd, 6),
        "open_exposure_penalty_usd": round(open_exposure_penalty_usd, 6),
        "window_end_open_exposure_penalty_usd": round(window_end_open_exposure_penalty_usd, 6),
        "avg_window_end_open_exposure_penalty_usd": round(avg_window_end_open_exposure_penalty_usd, 6),
        "carry_window_penalty_usd": round(carry_window_penalty_usd, 6),
        "carry_restart_window_penalty_usd": round(carry_restart_window_penalty_usd, 6),
        "resolved_share_penalty_usd": round(resolved_share_penalty_usd, 6),
        "resolved_size_share_penalty_usd": round(resolved_size_share_penalty_usd, 6),
        "worst_window_resolved_share_penalty_usd": round(worst_window_resolved_share_penalty_usd, 6),
        "worst_window_resolved_size_share_penalty_usd": round(worst_window_resolved_size_share_penalty_usd, 6),
        "mode_resolved_share_penalty_usd": round(mode_resolved_share_penalty_usd, 6),
        "mode_resolved_size_share_penalty_usd": round(mode_resolved_size_share_penalty_usd, 6),
        "mode_worst_window_resolved_share_penalty_usd": round(mode_worst_window_resolved_share_penalty_usd, 6),
        "mode_worst_window_resolved_size_share_penalty_usd": round(mode_worst_window_resolved_size_share_penalty_usd, 6),
        "mode_active_window_accepted_share_penalty_usd": round(mode_active_window_accepted_share_penalty_usd, 6),
        "mode_active_window_accepted_size_share_penalty_usd": round(mode_active_window_accepted_size_share_penalty_usd, 6),
        "worst_active_window_accepted_penalty_usd": round(worst_active_window_accepted_penalty_usd, 6),
        "worst_active_window_accepted_size_penalty_usd": round(worst_active_window_accepted_size_penalty_usd, 6),
        "mode_worst_active_window_accepted_penalty_usd": round(mode_worst_active_window_accepted_penalty_usd, 6),
        "mode_worst_active_window_accepted_size_penalty_usd": round(mode_worst_active_window_accepted_size_penalty_usd, 6),
        "mode_loss_penalty_usd": round(mode_loss_penalty_usd, 6),
        "mode_inactivity_penalty_usd": round(mode_inactivity_penalty_usd, 6),
        "window_inactivity_penalty_usd": round(window_inactivity_penalty_usd, 6),
        "wallet_count_penalty_usd": round(wallet_count_penalty_usd, 6),
        "market_count_penalty_usd": round(market_count_penalty_usd, 6),
        "entry_price_band_count_penalty_usd": round(entry_price_band_count_penalty_usd, 6),
        "time_to_close_band_count_penalty_usd": round(time_to_close_band_count_penalty_usd, 6),
        "wallet_concentration_penalty_usd": round(wallet_concentration_penalty_usd, 6),
        "market_concentration_penalty_usd": round(market_concentration_penalty_usd, 6),
        "entry_price_band_concentration_penalty_usd": round(entry_price_band_concentration_penalty_usd, 6),
        "time_to_close_band_concentration_penalty_usd": round(time_to_close_band_concentration_penalty_usd, 6),
        "wallet_size_concentration_penalty_usd": round(wallet_size_concentration_penalty_usd, 6),
        "market_size_concentration_penalty_usd": round(market_size_concentration_penalty_usd, 6),
        "entry_price_band_size_concentration_penalty_usd": round(entry_price_band_size_concentration_penalty_usd, 6),
        "time_to_close_band_size_concentration_penalty_usd": round(time_to_close_band_size_concentration_penalty_usd, 6),
        "score_usd": round(score_usd, 6),
    }


def _score_result(
    result: dict[str, Any],
    *,
    initial_bankroll_usd: float,
    drawdown_penalty: float,
    window_stddev_penalty: float,
    worst_window_penalty: float,
    pause_guard_penalty: float,
    daily_guard_window_penalty: float = 0.0,
    live_guard_window_penalty: float = 0.0,
    daily_guard_restart_window_penalty: float = 0.0,
    live_guard_restart_window_penalty: float = 0.0,
    open_exposure_penalty: float = 0.0,
    window_end_open_exposure_penalty: float = 0.0,
    avg_window_end_open_exposure_penalty: float = 0.0,
    carry_window_penalty: float = 0.0,
    carry_restart_window_penalty: float = 0.0,
    resolved_share_penalty: float = 0.0,
    resolved_size_share_penalty: float = 0.0,
    worst_window_resolved_share_penalty: float = 0.0,
    worst_window_resolved_size_share_penalty: float = 0.0,
    mode_resolved_share_penalty: float = 0.0,
    mode_resolved_size_share_penalty: float = 0.0,
    mode_worst_window_resolved_share_penalty: float = 0.0,
    mode_worst_window_resolved_size_share_penalty: float = 0.0,
    mode_active_window_accepted_share_penalty: float = 0.0,
    mode_active_window_accepted_size_share_penalty: float = 0.0,
    mode_loss_penalty: float = 0.0,
    mode_inactivity_penalty: float = 0.0,
    allow_heuristic: bool = True,
    allow_xgboost: bool = True,
    wallet_concentration_penalty: float = 0.0,
    market_concentration_penalty: float = 0.0,
    worst_active_window_accepted_penalty: float = 0.0,
    worst_active_window_accepted_size_penalty: float = 0.0,
    mode_worst_active_window_accepted_penalty: float = 0.0,
    mode_worst_active_window_accepted_size_penalty: float = 0.0,
    entry_price_band_concentration_penalty: float = 0.0,
    time_to_close_band_concentration_penalty: float = 0.0,
    window_inactivity_penalty: float = 0.0,
    wallet_count_penalty: float = 0.0,
    market_count_penalty: float = 0.0,
    entry_price_band_count_penalty: float = 0.0,
    time_to_close_band_count_penalty: float = 0.0,
    wallet_size_concentration_penalty: float = 0.0,
    market_size_concentration_penalty: float = 0.0,
    entry_price_band_size_concentration_penalty: float = 0.0,
    time_to_close_band_size_concentration_penalty: float = 0.0,
) -> float:
    return float(
        _score_breakdown(
            result,
            initial_bankroll_usd=initial_bankroll_usd,
            drawdown_penalty=drawdown_penalty,
            window_stddev_penalty=window_stddev_penalty,
            worst_window_penalty=worst_window_penalty,
            pause_guard_penalty=pause_guard_penalty,
            daily_guard_window_penalty=daily_guard_window_penalty,
            live_guard_window_penalty=live_guard_window_penalty,
            daily_guard_restart_window_penalty=daily_guard_restart_window_penalty,
            live_guard_restart_window_penalty=live_guard_restart_window_penalty,
            open_exposure_penalty=open_exposure_penalty,
            window_end_open_exposure_penalty=window_end_open_exposure_penalty,
            avg_window_end_open_exposure_penalty=avg_window_end_open_exposure_penalty,
            carry_window_penalty=carry_window_penalty,
            carry_restart_window_penalty=carry_restart_window_penalty,
            resolved_share_penalty=resolved_share_penalty,
            resolved_size_share_penalty=resolved_size_share_penalty,
            worst_window_resolved_share_penalty=worst_window_resolved_share_penalty,
            worst_window_resolved_size_share_penalty=worst_window_resolved_size_share_penalty,
            mode_resolved_share_penalty=mode_resolved_share_penalty,
            mode_resolved_size_share_penalty=mode_resolved_size_share_penalty,
            mode_worst_window_resolved_share_penalty=mode_worst_window_resolved_share_penalty,
            mode_worst_window_resolved_size_share_penalty=mode_worst_window_resolved_size_share_penalty,
            mode_active_window_accepted_share_penalty=mode_active_window_accepted_share_penalty,
            mode_active_window_accepted_size_share_penalty=mode_active_window_accepted_size_share_penalty,
            worst_active_window_accepted_penalty=worst_active_window_accepted_penalty,
            worst_active_window_accepted_size_penalty=worst_active_window_accepted_size_penalty,
            mode_worst_active_window_accepted_penalty=mode_worst_active_window_accepted_penalty,
            mode_worst_active_window_accepted_size_penalty=mode_worst_active_window_accepted_size_penalty,
            mode_loss_penalty=mode_loss_penalty,
            mode_inactivity_penalty=mode_inactivity_penalty,
            window_inactivity_penalty=window_inactivity_penalty,
            wallet_count_penalty=wallet_count_penalty,
            market_count_penalty=market_count_penalty,
            entry_price_band_count_penalty=entry_price_band_count_penalty,
            time_to_close_band_count_penalty=time_to_close_band_count_penalty,
            wallet_size_concentration_penalty=wallet_size_concentration_penalty,
            market_size_concentration_penalty=market_size_concentration_penalty,
            entry_price_band_size_concentration_penalty=entry_price_band_size_concentration_penalty,
            time_to_close_band_size_concentration_penalty=time_to_close_band_size_concentration_penalty,
            allow_heuristic=allow_heuristic,
            allow_xgboost=allow_xgboost,
            wallet_concentration_penalty=wallet_concentration_penalty,
            market_concentration_penalty=market_concentration_penalty,
            entry_price_band_concentration_penalty=entry_price_band_concentration_penalty,
            time_to_close_band_concentration_penalty=time_to_close_band_concentration_penalty,
        )["score_usd"]
    )


def _with_score_breakdown(
    result: dict[str, Any],
    *,
    initial_bankroll_usd: float,
    drawdown_penalty: float,
    window_stddev_penalty: float,
    worst_window_penalty: float,
    pause_guard_penalty: float,
    daily_guard_window_penalty: float = 0.0,
    live_guard_window_penalty: float = 0.0,
    daily_guard_restart_window_penalty: float = 0.0,
    live_guard_restart_window_penalty: float = 0.0,
    open_exposure_penalty: float = 0.0,
    window_end_open_exposure_penalty: float = 0.0,
    avg_window_end_open_exposure_penalty: float = 0.0,
    carry_window_penalty: float = 0.0,
    carry_restart_window_penalty: float = 0.0,
    resolved_share_penalty: float = 0.0,
    resolved_size_share_penalty: float = 0.0,
    worst_window_resolved_share_penalty: float = 0.0,
    worst_window_resolved_size_share_penalty: float = 0.0,
    mode_resolved_share_penalty: float = 0.0,
    mode_resolved_size_share_penalty: float = 0.0,
    mode_worst_window_resolved_share_penalty: float = 0.0,
    mode_worst_window_resolved_size_share_penalty: float = 0.0,
    mode_active_window_accepted_share_penalty: float = 0.0,
    mode_active_window_accepted_size_share_penalty: float = 0.0,
    mode_loss_penalty: float = 0.0,
    mode_inactivity_penalty: float = 0.0,
    allow_heuristic: bool = True,
    allow_xgboost: bool = True,
    wallet_concentration_penalty: float = 0.0,
    market_concentration_penalty: float = 0.0,
    worst_active_window_accepted_penalty: float = 0.0,
    worst_active_window_accepted_size_penalty: float = 0.0,
    mode_worst_active_window_accepted_penalty: float = 0.0,
    mode_worst_active_window_accepted_size_penalty: float = 0.0,
    entry_price_band_concentration_penalty: float = 0.0,
    time_to_close_band_concentration_penalty: float = 0.0,
    window_inactivity_penalty: float = 0.0,
    wallet_count_penalty: float = 0.0,
    market_count_penalty: float = 0.0,
    entry_price_band_count_penalty: float = 0.0,
    time_to_close_band_count_penalty: float = 0.0,
    wallet_size_concentration_penalty: float = 0.0,
    market_size_concentration_penalty: float = 0.0,
    entry_price_band_size_concentration_penalty: float = 0.0,
    time_to_close_band_size_concentration_penalty: float = 0.0,
) -> dict[str, Any]:
    payload = dict(result)
    payload["score_breakdown"] = _score_breakdown(
        payload,
        initial_bankroll_usd=initial_bankroll_usd,
        drawdown_penalty=drawdown_penalty,
        window_stddev_penalty=window_stddev_penalty,
        worst_window_penalty=worst_window_penalty,
        pause_guard_penalty=pause_guard_penalty,
        daily_guard_window_penalty=daily_guard_window_penalty,
        live_guard_window_penalty=live_guard_window_penalty,
        daily_guard_restart_window_penalty=daily_guard_restart_window_penalty,
        live_guard_restart_window_penalty=live_guard_restart_window_penalty,
        open_exposure_penalty=open_exposure_penalty,
        window_end_open_exposure_penalty=window_end_open_exposure_penalty,
        avg_window_end_open_exposure_penalty=avg_window_end_open_exposure_penalty,
        carry_window_penalty=carry_window_penalty,
        carry_restart_window_penalty=carry_restart_window_penalty,
        resolved_share_penalty=resolved_share_penalty,
        resolved_size_share_penalty=resolved_size_share_penalty,
        worst_window_resolved_share_penalty=worst_window_resolved_share_penalty,
        worst_window_resolved_size_share_penalty=worst_window_resolved_size_share_penalty,
        mode_resolved_share_penalty=mode_resolved_share_penalty,
        mode_resolved_size_share_penalty=mode_resolved_size_share_penalty,
        mode_worst_window_resolved_share_penalty=mode_worst_window_resolved_share_penalty,
        mode_worst_window_resolved_size_share_penalty=mode_worst_window_resolved_size_share_penalty,
        mode_active_window_accepted_share_penalty=mode_active_window_accepted_share_penalty,
        mode_active_window_accepted_size_share_penalty=mode_active_window_accepted_size_share_penalty,
        worst_active_window_accepted_penalty=worst_active_window_accepted_penalty,
        worst_active_window_accepted_size_penalty=worst_active_window_accepted_size_penalty,
        mode_worst_active_window_accepted_penalty=mode_worst_active_window_accepted_penalty,
        mode_worst_active_window_accepted_size_penalty=mode_worst_active_window_accepted_size_penalty,
        mode_loss_penalty=mode_loss_penalty,
        mode_inactivity_penalty=mode_inactivity_penalty,
        window_inactivity_penalty=window_inactivity_penalty,
        wallet_count_penalty=wallet_count_penalty,
        market_count_penalty=market_count_penalty,
        entry_price_band_count_penalty=entry_price_band_count_penalty,
        time_to_close_band_count_penalty=time_to_close_band_count_penalty,
        wallet_size_concentration_penalty=wallet_size_concentration_penalty,
        market_size_concentration_penalty=market_size_concentration_penalty,
        entry_price_band_size_concentration_penalty=entry_price_band_size_concentration_penalty,
        time_to_close_band_size_concentration_penalty=time_to_close_band_size_concentration_penalty,
        allow_heuristic=allow_heuristic,
        allow_xgboost=allow_xgboost,
        wallet_concentration_penalty=wallet_concentration_penalty,
        market_concentration_penalty=market_concentration_penalty,
        entry_price_band_concentration_penalty=entry_price_band_concentration_penalty,
        time_to_close_band_concentration_penalty=time_to_close_band_concentration_penalty,
    )
    return payload


def _signal_mode_summary(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = result.get("signal_mode_summary")
    if not isinstance(raw, dict):
        return {}
    result_window_count = max(int(result.get("window_count") or 0), 0)
    summary: dict[str, dict[str, Any]] = {}
    for raw_mode, raw_values in raw.items():
        mode = _canonical_signal_mode(raw_mode)
        if not mode or not isinstance(raw_values, dict):
            continue
        bucket = summary.setdefault(
            mode,
            {
                "trade_count": 0,
                "accepted_count": 0,
                "accepted_size_usd": 0.0,
                "accepted_window_count": 0,
                "resolved_count": 0,
                "resolved_size_usd": 0.0,
                "total_pnl_usd": 0.0,
                "positive_window_count": 0,
                "negative_window_count": 0,
                "inactive_window_count": 0,
                "worst_window_pnl_usd": None,
                "best_window_pnl_usd": None,
                "worst_window_resolved_share": None,
                "worst_window_resolved_size_share": None,
                "worst_active_window_resolved_share": None,
                "worst_active_window_resolved_size_share": None,
                "worst_active_window_accepted_count": None,
                "worst_active_window_accepted_size_usd": None,
                "min_active_window_accepted_share": None,
                "max_active_window_accepted_share": None,
                "min_active_window_accepted_size_share": None,
                "max_active_window_accepted_size_share": None,
                "win_count": 0,
                "win_rate": None,
            },
        )
        raw_total_active_accepted_count = sum(
            int(values.get("accepted_count") or 0)
            for key, values in raw.items()
            if _canonical_signal_mode(key) and isinstance(values, dict)
        )
        raw_total_active_accepted_size_usd = sum(
            float(values.get("accepted_size_usd") or 0.0)
            for key, values in raw.items()
            if _canonical_signal_mode(key) and isinstance(values, dict)
        )
        raw_total_pnl_usd = float(raw_values.get("total_pnl_usd") or 0.0)
        raw_worst_window_pnl_usd = raw_values.get("worst_window_pnl_usd")
        raw_best_window_pnl_usd = raw_values.get("best_window_pnl_usd")
        resolved_worst_window_pnl_usd = (
            float(raw_worst_window_pnl_usd)
            if raw_worst_window_pnl_usd is not None
            else raw_total_pnl_usd
        )
        resolved_best_window_pnl_usd = (
            float(raw_best_window_pnl_usd)
            if raw_best_window_pnl_usd is not None
            else raw_total_pnl_usd
        )
        raw_worst_window_resolved_share = raw_values.get("worst_window_resolved_share")
        resolved_worst_window_resolved_share = (
            float(raw_worst_window_resolved_share)
            if raw_worst_window_resolved_share is not None
            else _resolved_share_from_counts(raw_values.get("accepted_count"), raw_values.get("resolved_count"))
        )
        raw_worst_window_resolved_size_share = raw_values.get("worst_window_resolved_size_share")
        resolved_worst_window_resolved_size_share = (
            float(raw_worst_window_resolved_size_share)
            if raw_worst_window_resolved_size_share is not None
            else _resolved_share_from_sizes(raw_values.get("accepted_size_usd"), raw_values.get("resolved_size_usd"))
        )
        raw_worst_active_window_resolved_share = raw_values.get("worst_active_window_resolved_share")
        resolved_worst_active_window_resolved_share = (
            float(raw_worst_active_window_resolved_share)
            if raw_worst_active_window_resolved_share is not None
            else resolved_worst_window_resolved_share
        )
        raw_worst_active_window_resolved_size_share = raw_values.get("worst_active_window_resolved_size_share")
        resolved_worst_active_window_resolved_size_share = (
            float(raw_worst_active_window_resolved_size_share)
            if raw_worst_active_window_resolved_size_share is not None
            else (
                resolved_worst_window_resolved_size_share
                if float(raw_values.get("accepted_size_usd") or 0.0) > 0
                else 1.0
            )
        )
        raw_worst_active_window_accepted_count = raw_values.get("worst_active_window_accepted_count")
        resolved_worst_active_window_accepted_count = (
            int(raw_worst_active_window_accepted_count)
            if raw_worst_active_window_accepted_count is not None
            else (
                int(raw_values.get("accepted_count") or 0)
                if int(raw_values.get("accepted_count") or 0) > 0
                else None
            )
        )
        raw_worst_active_window_accepted_size_usd = raw_values.get("worst_active_window_accepted_size_usd")
        resolved_worst_active_window_accepted_size_usd = (
            float(raw_worst_active_window_accepted_size_usd)
            if raw_worst_active_window_accepted_size_usd is not None
            else (
                float(raw_values.get("accepted_size_usd") or 0.0)
                if float(raw_values.get("accepted_size_usd") or 0.0) > 0
                else None
            )
        )
        raw_min_active_window_accepted_share = raw_values.get("min_active_window_accepted_share")
        resolved_min_active_window_accepted_share = (
            float(raw_min_active_window_accepted_share)
            if raw_min_active_window_accepted_share is not None
            else (
                float(int(raw_values.get("accepted_count") or 0)) / float(raw_total_active_accepted_count)
                if raw_total_active_accepted_count > 0
                else None
            )
        )
        raw_max_active_window_accepted_share = raw_values.get("max_active_window_accepted_share")
        resolved_max_active_window_accepted_share = (
            float(raw_max_active_window_accepted_share)
            if raw_max_active_window_accepted_share is not None
            else (
                float(int(raw_values.get("accepted_count") or 0)) / float(raw_total_active_accepted_count)
                if raw_total_active_accepted_count > 0
                else None
            )
        )
        raw_min_active_window_accepted_size_share = raw_values.get("min_active_window_accepted_size_share")
        resolved_min_active_window_accepted_size_share = (
            float(raw_min_active_window_accepted_size_share)
            if raw_min_active_window_accepted_size_share is not None
            else (
                float(raw_values.get("accepted_size_usd") or 0.0) / float(raw_total_active_accepted_size_usd)
                if raw_total_active_accepted_size_usd > 0
                else None
            )
        )
        raw_max_active_window_accepted_size_share = raw_values.get("max_active_window_accepted_size_share")
        resolved_max_active_window_accepted_size_share = (
            float(raw_max_active_window_accepted_size_share)
            if raw_max_active_window_accepted_size_share is not None
            else (
                float(raw_values.get("accepted_size_usd") or 0.0) / float(raw_total_active_accepted_size_usd)
                if raw_total_active_accepted_size_usd > 0
                else None
            )
        )
        resolved_positive_window_count = (
            int(raw_values.get("positive_window_count") or 0)
            if raw_values.get("positive_window_count") is not None
            else 1 if raw_total_pnl_usd > 0 else 0
        )
        resolved_negative_window_count = (
            int(raw_values.get("negative_window_count") or 0)
            if raw_values.get("negative_window_count") is not None
            else 1 if raw_total_pnl_usd < 0 else 0
        )
        resolved_inactive_window_count = (
            int(raw_values.get("inactive_window_count") or 0)
            if raw_values.get("inactive_window_count") is not None
            else 0
        )
        resolved_accepted_window_count = (
            int(raw_values.get("accepted_window_count") or 0)
            if raw_values.get("accepted_window_count") is not None
            else max(result_window_count - resolved_inactive_window_count, 0)
            if result_window_count > 1
            and (
                int(raw_values.get("accepted_count") or 0) > 0
                or float(raw_values.get("accepted_size_usd") or 0.0) > 0
            )
            else 1
            if (
                int(raw_values.get("accepted_count") or 0) > 0
                or float(raw_values.get("accepted_size_usd") or 0.0) > 0
            )
            else 0
        )
        bucket["trade_count"] += int(raw_values.get("trade_count") or 0)
        bucket["accepted_count"] += int(raw_values.get("accepted_count") or 0)
        bucket["accepted_size_usd"] += float(raw_values.get("accepted_size_usd") or 0.0)
        bucket["accepted_window_count"] += resolved_accepted_window_count
        bucket["resolved_count"] += int(raw_values.get("resolved_count") or 0)
        bucket["resolved_size_usd"] += float(raw_values.get("resolved_size_usd") or 0.0)
        bucket["total_pnl_usd"] += raw_total_pnl_usd
        bucket["positive_window_count"] += resolved_positive_window_count
        bucket["negative_window_count"] += resolved_negative_window_count
        bucket["inactive_window_count"] += resolved_inactive_window_count
        bucket["worst_window_pnl_usd"] = (
            resolved_worst_window_pnl_usd
            if bucket["worst_window_pnl_usd"] is None
            else min(float(bucket["worst_window_pnl_usd"]), resolved_worst_window_pnl_usd)
        )
        bucket["worst_window_resolved_share"] = (
            resolved_worst_window_resolved_share
            if bucket["worst_window_resolved_share"] is None
            else min(float(bucket["worst_window_resolved_share"]), resolved_worst_window_resolved_share)
        )
        bucket["worst_window_resolved_size_share"] = (
            resolved_worst_window_resolved_size_share
            if bucket["worst_window_resolved_size_share"] is None
            else min(float(bucket["worst_window_resolved_size_share"]), resolved_worst_window_resolved_size_share)
        )
        bucket["worst_active_window_resolved_share"] = (
            resolved_worst_active_window_resolved_share
            if bucket["worst_active_window_resolved_share"] is None
            else min(float(bucket["worst_active_window_resolved_share"]), resolved_worst_active_window_resolved_share)
        )
        bucket["worst_active_window_resolved_size_share"] = (
            resolved_worst_active_window_resolved_size_share
            if bucket["worst_active_window_resolved_size_share"] is None
            else min(float(bucket["worst_active_window_resolved_size_share"]), resolved_worst_active_window_resolved_size_share)
        )
        if resolved_worst_active_window_accepted_count is not None:
            bucket["worst_active_window_accepted_count"] = (
                resolved_worst_active_window_accepted_count
                if bucket["worst_active_window_accepted_count"] is None
                else min(int(bucket["worst_active_window_accepted_count"]), resolved_worst_active_window_accepted_count)
            )
        if resolved_worst_active_window_accepted_size_usd is not None:
            bucket["worst_active_window_accepted_size_usd"] = (
                resolved_worst_active_window_accepted_size_usd
                if bucket["worst_active_window_accepted_size_usd"] is None
                else min(float(bucket["worst_active_window_accepted_size_usd"]), resolved_worst_active_window_accepted_size_usd)
            )
        if resolved_min_active_window_accepted_share is not None:
            bucket["min_active_window_accepted_share"] = (
                resolved_min_active_window_accepted_share
                if bucket["min_active_window_accepted_share"] is None
                else min(float(bucket["min_active_window_accepted_share"]), resolved_min_active_window_accepted_share)
            )
        if resolved_max_active_window_accepted_share is not None:
            bucket["max_active_window_accepted_share"] = (
                resolved_max_active_window_accepted_share
                if bucket["max_active_window_accepted_share"] is None
                else max(float(bucket["max_active_window_accepted_share"]), resolved_max_active_window_accepted_share)
            )
        if resolved_min_active_window_accepted_size_share is not None:
            bucket["min_active_window_accepted_size_share"] = (
                resolved_min_active_window_accepted_size_share
                if bucket["min_active_window_accepted_size_share"] is None
                else min(float(bucket["min_active_window_accepted_size_share"]), resolved_min_active_window_accepted_size_share)
            )
        if resolved_max_active_window_accepted_size_share is not None:
            bucket["max_active_window_accepted_size_share"] = (
                resolved_max_active_window_accepted_size_share
                if bucket["max_active_window_accepted_size_share"] is None
                else max(float(bucket["max_active_window_accepted_size_share"]), resolved_max_active_window_accepted_size_share)
            )
        bucket["best_window_pnl_usd"] = (
            resolved_best_window_pnl_usd
            if bucket["best_window_pnl_usd"] is None
            else max(float(bucket["best_window_pnl_usd"]), resolved_best_window_pnl_usd)
        )
        bucket["win_count"] += int(raw_values.get("win_count") or 0)
    for mode, values in summary.items():
        values["win_rate"] = (
            float(values["win_count"]) / float(values["resolved_count"])
            if int(values["resolved_count"] or 0) > 0
            else None
        )
        values["accepted_size_usd"] = round(float(values["accepted_size_usd"]), 6)
        values["accepted_window_count"] = int(values["accepted_window_count"])
        values["resolved_size_usd"] = round(float(values["resolved_size_usd"]), 6)
        values["worst_window_pnl_usd"] = (
            round(float(values["worst_window_pnl_usd"]), 6)
            if values["worst_window_pnl_usd"] is not None
            else None
        )
        values["worst_window_resolved_share"] = (
            round(float(values["worst_window_resolved_share"]), 6)
            if values["worst_window_resolved_share"] is not None
            else None
        )
        values["worst_window_resolved_size_share"] = (
            round(float(values["worst_window_resolved_size_share"]), 6)
            if values["worst_window_resolved_size_share"] is not None
            else None
        )
        values["worst_active_window_resolved_share"] = (
            round(float(values["worst_active_window_resolved_share"]), 6)
            if values["worst_active_window_resolved_share"] is not None
            else None
        )
        values["worst_active_window_resolved_size_share"] = (
            round(float(values["worst_active_window_resolved_size_share"]), 6)
            if values["worst_active_window_resolved_size_share"] is not None
            else None
        )
        values["worst_active_window_accepted_count"] = (
            int(values["worst_active_window_accepted_count"])
            if values["worst_active_window_accepted_count"] is not None
            else None
        )
        values["worst_active_window_accepted_size_usd"] = (
            round(float(values["worst_active_window_accepted_size_usd"]), 6)
            if values["worst_active_window_accepted_size_usd"] is not None
            else None
        )
        values["min_active_window_accepted_share"] = (
            round(float(values["min_active_window_accepted_share"]), 6)
            if values["min_active_window_accepted_share"] is not None
            else None
        )
        values["max_active_window_accepted_share"] = (
            round(float(values["max_active_window_accepted_share"]), 6)
            if values["max_active_window_accepted_share"] is not None
            else None
        )
        values["min_active_window_accepted_size_share"] = (
            round(float(values["min_active_window_accepted_size_share"]), 6)
            if values["min_active_window_accepted_size_share"] is not None
            else None
        )
        values["max_active_window_accepted_size_share"] = (
            round(float(values["max_active_window_accepted_size_share"]), 6)
            if values["max_active_window_accepted_size_share"] is not None
            else None
        )
        values["best_window_pnl_usd"] = (
            round(float(values["best_window_pnl_usd"]), 6)
            if values["best_window_pnl_usd"] is not None
            else None
        )
    return summary


def _canonical_signal_mode(raw: Any) -> str:
    normalized = str(raw or "").strip().lower()
    if normalized in {"model", "ml", "hist_gradient_boosting", "xgboost"}:
        return "xgboost"
    if not normalized:
        return "heuristic"
    return normalized


def _accepted_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    total_accepted = sum(int(values.get("accepted_count") or 0) for values in signal_mode_summary.values())
    if total_accepted <= 0:
        return 0.0
    return float(int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0)) / float(total_accepted)


def _accepted_size_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    total_accepted_size_usd = sum(float(values.get("accepted_size_usd") or 0.0) for values in signal_mode_summary.values())
    if total_accepted_size_usd <= 0:
        return 0.0
    return float(signal_mode_summary.get(mode, {}).get("accepted_size_usd") or 0.0) / float(total_accepted_size_usd)


def _min_active_window_accepted_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("min_active_window_accepted_share")
    if raw_value is not None:
        return float(raw_value)
    total_accepted = sum(int(values.get("accepted_count") or 0) for values in signal_mode_summary.values())
    if total_accepted <= 0:
        return 1.0
    return _accepted_share(signal_mode_summary, mode)


def _max_active_window_accepted_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("max_active_window_accepted_share")
    if raw_value is not None:
        return float(raw_value)
    total_accepted = sum(int(values.get("accepted_count") or 0) for values in signal_mode_summary.values())
    if total_accepted <= 0:
        return 0.0
    return _accepted_share(signal_mode_summary, mode)


def _min_active_window_accepted_size_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("min_active_window_accepted_size_share")
    if raw_value is not None:
        return float(raw_value)
    total_accepted_size_usd = sum(float(values.get("accepted_size_usd") or 0.0) for values in signal_mode_summary.values())
    if total_accepted_size_usd <= 0:
        return 1.0
    return _accepted_size_share(signal_mode_summary, mode)


def _max_active_window_accepted_size_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("max_active_window_accepted_size_share")
    if raw_value is not None:
        return float(raw_value)
    total_accepted_size_usd = sum(float(values.get("accepted_size_usd") or 0.0) for values in signal_mode_summary.values())
    if total_accepted_size_usd <= 0:
        return 0.0
    return _accepted_size_share(signal_mode_summary, mode)


def _resolved_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    accepted_count = int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0)
    if accepted_count <= 0:
        return 0.0
    return float(int(signal_mode_summary.get(mode, {}).get("resolved_count") or 0)) / float(accepted_count)


def _resolved_size_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    accepted_size_usd = float(signal_mode_summary.get(mode, {}).get("accepted_size_usd") or 0.0)
    if accepted_size_usd <= 0:
        return 0.0
    resolved_size_usd = float(signal_mode_summary.get(mode, {}).get("resolved_size_usd") or 0.0)
    return min(max(resolved_size_usd / accepted_size_usd, 0.0), 1.0)


def _worst_window_pnl(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("worst_window_pnl_usd")
    if raw_value is None:
        return 0.0
    return float(raw_value)


def _worst_window_resolved_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("worst_window_resolved_share")
    if raw_value is None:
        return _resolved_share(signal_mode_summary, mode)
    return float(raw_value)


def _worst_active_window_resolved_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("worst_active_window_resolved_share")
    if raw_value is not None:
        return float(raw_value)
    if int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0) <= 0:
        return 1.0
    return _worst_window_resolved_share(signal_mode_summary, mode)


def _worst_window_resolved_size_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("worst_window_resolved_size_share")
    if raw_value is None:
        return _resolved_size_share(signal_mode_summary, mode)
    return float(raw_value)


def _worst_active_window_resolved_size_share(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("worst_active_window_resolved_size_share")
    if raw_value is not None:
        return float(raw_value)
    if float(signal_mode_summary.get(mode, {}).get("accepted_size_usd") or 0.0) <= 0:
        return 1.0
    return _worst_window_resolved_size_share(signal_mode_summary, mode)


def _worst_active_window_accepted_size_usd(signal_mode_summary: dict[str, dict[str, Any]], mode: str) -> float:
    raw_value = signal_mode_summary.get(mode, {}).get("worst_active_window_accepted_size_usd")
    if raw_value is None:
        return 0.0
    return float(raw_value)


def _global_worst_active_window_resolved_share(result: dict[str, Any]) -> float:
    raw_value = result.get("worst_active_window_resolved_share")
    if raw_value is not None:
        return float(raw_value)
    if int(result.get("accepted_count") or 0) <= 0:
        return 1.0
    return float(result.get("worst_window_resolved_share") or 0.0)


def _global_worst_active_window_resolved_size_share(result: dict[str, Any]) -> float:
    raw_value = result.get("worst_active_window_resolved_size_share")
    if raw_value is not None:
        return float(raw_value)
    if float(result.get("accepted_size_usd") or 0.0) <= 0:
        return 1.0
    return float(result.get("worst_window_resolved_size_share") or 0.0)


def _resolved_share_from_counts(accepted_count: Any, resolved_count: Any) -> float:
    accepted = int(accepted_count or 0)
    if accepted <= 0:
        return 0.0
    return float(int(resolved_count or 0)) / float(accepted)


def _resolved_share_from_sizes(accepted_size_usd: Any, resolved_size_usd: Any) -> float:
    accepted = float(accepted_size_usd or 0.0)
    if accepted <= 0:
        return 0.0
    resolved = float(resolved_size_usd or 0.0)
    return min(max(resolved / accepted, 0.0), 1.0)


def _mode_has_participation(values: dict[str, Any]) -> bool:
    if int(values.get("accepted_count") or 0) > 0:
        return True
    if float(values.get("accepted_size_usd") or 0.0) > 0:
        return True
    if int(values.get("resolved_count") or 0) > 0:
        return True
    if float(values.get("resolved_size_usd") or 0.0) > 0:
        return True
    if abs(float(values.get("total_pnl_usd") or 0.0)) > 1e-9:
        return True
    return False


def _window_has_participation(result: dict[str, Any]) -> bool:
    if int(result.get("accepted_count") or 0) > 0:
        return True
    if float(result.get("accepted_size_usd") or 0.0) > 0:
        return True
    if int(result.get("resolved_count") or 0) > 0:
        return True
    if float(result.get("resolved_size_usd") or 0.0) > 0:
        return True
    if abs(float(result.get("total_pnl_usd") or 0.0)) > 1e-9:
        return True
    if float(result.get("peak_open_exposure_usd") or 0.0) > 0:
        return True
    if float(result.get("window_end_open_exposure_usd") or 0.0) > 0:
        return True
    if int(result.get("window_end_live_guard_triggered") or 0) > 0:
        return True
    if int(result.get("window_end_daily_guard_triggered") or 0) > 0:
        return True
    return False


def _window_equity_summary(
    result: dict[str, Any],
    *,
    default_start_equity: float,
) -> tuple[float, float, float, float]:
    start_equity = max(float(result.get("initial_bankroll_usd") or default_start_equity), 0.0)
    final_equity = result.get("final_equity_usd")
    if final_equity is None:
        if result.get("final_bankroll_usd") is not None:
            final_equity = result.get("final_bankroll_usd")
        else:
            final_equity = start_equity + float(result.get("total_pnl_usd") or 0.0)
    final_equity_value = max(float(final_equity or 0.0), 0.0)
    peak_equity_value = max(
        float(result.get("peak_equity_usd") or max(start_equity, final_equity_value)),
        0.0,
    )
    min_equity_value = max(
        float(result.get("min_equity_usd") or min(start_equity, final_equity_value)),
        0.0,
    )
    return start_equity, final_equity_value, peak_equity_value, min_equity_value


def _scaled_equity_value(
    *,
    local_value: float,
    local_start_equity: float,
    stitched_start_equity: float,
) -> float:
    if local_start_equity > 0:
        return max(stitched_start_equity * (local_value / local_start_equity), 0.0)
    if stitched_start_equity <= 0:
        return 0.0
    return max(local_value, 0.0)


def _stitched_max_drawdown_pct(
    window_results: list[dict[str, Any]],
    *,
    initial_bankroll_usd: float,
) -> float:
    stitched_equity = max(float(initial_bankroll_usd or 0.0), 0.0)
    stitched_peak_equity = stitched_equity
    stitched_max_drawdown_pct = 0.0
    for row in window_results:
        local_start_equity, local_final_equity, local_peak_equity, local_min_equity = _window_equity_summary(
            row,
            default_start_equity=initial_bankroll_usd,
        )
        window_min_equity = _scaled_equity_value(
            local_value=local_min_equity,
            local_start_equity=local_start_equity,
            stitched_start_equity=stitched_equity,
        )
        window_peak_equity = _scaled_equity_value(
            local_value=local_peak_equity,
            local_start_equity=local_start_equity,
            stitched_start_equity=stitched_equity,
        )
        window_final_equity = _scaled_equity_value(
            local_value=local_final_equity,
            local_start_equity=local_start_equity,
            stitched_start_equity=stitched_equity,
        )
        if stitched_peak_equity > 0:
            stitched_max_drawdown_pct = max(
                stitched_max_drawdown_pct,
                (stitched_peak_equity - window_min_equity) / stitched_peak_equity,
            )
        stitched_max_drawdown_pct = max(
            stitched_max_drawdown_pct,
            float(row.get("max_drawdown_pct") or 0.0),
        )
        if window_peak_equity > stitched_peak_equity:
            stitched_peak_equity = window_peak_equity
        stitched_equity = window_final_equity
    return round(stitched_max_drawdown_pct, 6)


def _with_worst_window_resolved_share(result: dict[str, Any]) -> dict[str, Any]:
    if "worst_window_resolved_share" in result:
        if (
            "worst_active_window_resolved_share" in result
            and "worst_window_resolved_size_share" in result
            and "worst_active_window_resolved_size_share" in result
        ):
            return result
        enriched = dict(result)
        accepted_count = int(enriched.get("accepted_count") or 0)
        accepted_size_usd = float(enriched.get("accepted_size_usd") or 0.0)
        enriched["worst_active_window_resolved_share"] = (
            round(_resolved_share_from_counts(accepted_count, enriched.get("resolved_count")), 6)
            if accepted_count > 0
            else 1.0
        )
        enriched["worst_window_resolved_size_share"] = round(
            _resolved_share_from_sizes(enriched.get("accepted_size_usd"), enriched.get("resolved_size_usd")),
            6,
        )
        enriched["worst_active_window_resolved_size_share"] = (
            round(_resolved_share_from_sizes(accepted_size_usd, enriched.get("resolved_size_usd")), 6)
            if accepted_size_usd > 0
            else 1.0
        )
        return enriched
    enriched = dict(result)
    enriched["worst_window_resolved_share"] = round(
        _resolved_share_from_counts(enriched.get("accepted_count"), enriched.get("resolved_count")),
        6,
    )
    accepted_count = int(enriched.get("accepted_count") or 0)
    accepted_size_usd = float(enriched.get("accepted_size_usd") or 0.0)
    enriched["worst_active_window_resolved_share"] = (
        round(_resolved_share_from_counts(accepted_count, enriched.get("resolved_count")), 6)
        if accepted_count > 0
        else 1.0
    )
    enriched["worst_window_resolved_size_share"] = round(
        _resolved_share_from_sizes(enriched.get("accepted_size_usd"), enriched.get("resolved_size_usd")),
        6,
    )
    enriched["worst_active_window_resolved_size_share"] = (
        round(_resolved_share_from_sizes(accepted_size_usd, enriched.get("resolved_size_usd")), 6)
        if accepted_size_usd > 0
        else 1.0
    )
    return enriched


def _with_window_activity_fields(result: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(result)
    total_pnl_usd = float(enriched.get("total_pnl_usd") or 0.0)
    accepted_count = int(enriched.get("accepted_count") or 0)
    accepted_size_usd = float(enriched.get("accepted_size_usd") or 0.0)
    window_count = int(enriched.get("window_count") or 1)
    initial_bankroll_usd = max(float(enriched.get("initial_bankroll_usd") or 0.0), 0.0)
    enriched.setdefault("window_count", window_count)
    if "final_equity_usd" not in enriched and window_count == 1:
        if enriched.get("final_bankroll_usd") is not None:
            enriched["final_equity_usd"] = round(float(enriched.get("final_bankroll_usd") or 0.0), 6)
        else:
            enriched["final_equity_usd"] = round(initial_bankroll_usd + total_pnl_usd, 6)
    if "peak_equity_usd" not in enriched and window_count == 1:
        final_equity_usd = float(enriched.get("final_equity_usd") or initial_bankroll_usd)
        enriched["peak_equity_usd"] = round(max(initial_bankroll_usd, final_equity_usd), 6)
    if "min_equity_usd" not in enriched and window_count == 1:
        final_equity_usd = float(enriched.get("final_equity_usd") or initial_bankroll_usd)
        enriched["min_equity_usd"] = round(min(initial_bankroll_usd, final_equity_usd), 6)
    if "peak_open_exposure_usd" not in enriched:
        enriched["peak_open_exposure_usd"] = 0.0
    if "max_open_exposure_share" not in enriched:
        enriched["max_open_exposure_share"] = 0.0
    if "window_end_open_exposure_usd" not in enriched:
        final_bankroll_raw = enriched.get("final_bankroll_usd")
        if final_bankroll_raw is None:
            enriched["window_end_open_exposure_usd"] = 0.0
        else:
            final_equity_usd = max(float(enriched.get("final_equity_usd") or 0.0), 0.0)
            final_bankroll_usd = float(final_bankroll_raw or 0.0)
            enriched["window_end_open_exposure_usd"] = round(
                max(final_equity_usd - final_bankroll_usd, 0.0),
                6,
            )
    if "window_end_open_exposure_share" not in enriched:
        final_equity_usd = max(float(enriched.get("final_equity_usd") or 0.0), 0.0)
        window_end_open_exposure_usd = float(enriched.get("window_end_open_exposure_usd") or 0.0)
        if final_equity_usd > 0:
            enriched["window_end_open_exposure_share"] = round(
                window_end_open_exposure_usd / final_equity_usd,
                6,
            )
        elif window_end_open_exposure_usd > 0:
            enriched["window_end_open_exposure_share"] = 1.0
        else:
            enriched["window_end_open_exposure_share"] = 0.0
    if "max_window_end_open_exposure_usd" not in enriched:
        enriched["max_window_end_open_exposure_usd"] = round(
            float(enriched.get("window_end_open_exposure_usd") or 0.0),
            6,
        )
    if "max_window_end_open_exposure_share" not in enriched:
        enriched["max_window_end_open_exposure_share"] = round(
            float(enriched.get("window_end_open_exposure_share") or 0.0),
            6,
        )
    if "avg_window_end_open_exposure_share" not in enriched:
        enriched["avg_window_end_open_exposure_share"] = round(
            float(enriched.get("window_end_open_exposure_share") or 0.0),
            6,
        )
    if "carry_window_count" not in enriched:
        enriched["carry_window_count"] = 1 if float(enriched.get("window_end_open_exposure_usd") or 0.0) > 0 else 0
    if "live_guard_window_count" not in enriched:
        enriched["live_guard_window_count"] = 1 if int(enriched.get("window_end_live_guard_triggered") or 0) > 0 else 0
    if "daily_guard_window_count" not in enriched:
        enriched["daily_guard_window_count"] = 1 if int(enriched.get("window_end_daily_guard_triggered") or 0) > 0 else 0
    if "daily_guard_restart_window_count" not in enriched:
        enriched["daily_guard_restart_window_count"] = 0
    if "daily_guard_restart_window_opportunity_count" not in enriched:
        enriched["daily_guard_restart_window_opportunity_count"] = 0
    if "live_guard_restart_window_count" not in enriched:
        enriched["live_guard_restart_window_count"] = 0
    if "live_guard_restart_window_opportunity_count" not in enriched:
        enriched["live_guard_restart_window_opportunity_count"] = 0
    if "positive_window_count" not in enriched and window_count == 1:
        enriched["positive_window_count"] = 1 if total_pnl_usd > 0 else 0
    if "negative_window_count" not in enriched and window_count == 1:
        enriched["negative_window_count"] = 1 if total_pnl_usd < 0 else 0
    if "active_window_count" not in enriched:
        if window_count == 1:
            enriched["active_window_count"] = 1 if _window_has_participation(enriched) else 0
        elif "inactive_window_count" in enriched:
            enriched["active_window_count"] = max(window_count - int(enriched.get("inactive_window_count") or 0), 0)
    if "inactive_window_count" not in enriched:
        if window_count == 1:
            enriched["inactive_window_count"] = 0 if _window_has_participation(enriched) else 1
        elif "active_window_count" in enriched:
            enriched["inactive_window_count"] = max(window_count - int(enriched.get("active_window_count") or 0), 0)
    if "carry_window_share" not in enriched:
        carry_window_count = int(enriched.get("carry_window_count") or 0)
        active_window_count = int(enriched.get("active_window_count") or 0)
        if window_count == 1:
            enriched["carry_window_share"] = 1.0 if carry_window_count > 0 else 0.0
        elif active_window_count > 0:
            enriched["carry_window_share"] = round(
                float(carry_window_count) / float(active_window_count),
                6,
            )
        else:
            enriched["carry_window_share"] = 0.0
    if "carry_restart_window_count" not in enriched:
        enriched["carry_restart_window_count"] = 0
    if "carry_restart_window_opportunity_count" not in enriched:
        enriched["carry_restart_window_opportunity_count"] = 0
    if "carry_restart_window_share" not in enriched:
        carry_restart_window_count = int(enriched.get("carry_restart_window_count") or 0)
        carry_restart_window_opportunity_count = int(enriched.get("carry_restart_window_opportunity_count") or 0)
        if carry_restart_window_opportunity_count > 0:
            enriched["carry_restart_window_share"] = round(
                float(carry_restart_window_count) / float(carry_restart_window_opportunity_count),
                6,
            )
        else:
            enriched["carry_restart_window_share"] = 0.0
    if "daily_guard_restart_window_share" not in enriched:
        daily_guard_restart_window_count = int(enriched.get("daily_guard_restart_window_count") or 0)
        daily_guard_restart_window_opportunity_count = int(enriched.get("daily_guard_restart_window_opportunity_count") or 0)
        if daily_guard_restart_window_opportunity_count > 0:
            enriched["daily_guard_restart_window_share"] = round(
                float(daily_guard_restart_window_count) / float(daily_guard_restart_window_opportunity_count),
                6,
            )
        else:
            enriched["daily_guard_restart_window_share"] = 0.0
    if "live_guard_restart_window_share" not in enriched:
        live_guard_restart_window_count = int(enriched.get("live_guard_restart_window_count") or 0)
        live_guard_restart_window_opportunity_count = int(enriched.get("live_guard_restart_window_opportunity_count") or 0)
        if live_guard_restart_window_opportunity_count > 0:
            enriched["live_guard_restart_window_share"] = round(
                float(live_guard_restart_window_count) / float(live_guard_restart_window_opportunity_count),
                6,
            )
        else:
            enriched["live_guard_restart_window_share"] = 0.0
    if "live_guard_window_share" not in enriched:
        live_guard_window_count = int(enriched.get("live_guard_window_count") or 0)
        active_window_count = int(enriched.get("active_window_count") or 0)
        if window_count == 1:
            enriched["live_guard_window_share"] = 1.0 if live_guard_window_count > 0 else 0.0
        elif active_window_count > 0:
            enriched["live_guard_window_share"] = round(
                float(live_guard_window_count) / float(active_window_count),
                6,
            )
        else:
            enriched["live_guard_window_share"] = 0.0
    if "daily_guard_window_share" not in enriched:
        daily_guard_window_count = int(enriched.get("daily_guard_window_count") or 0)
        active_window_count = int(enriched.get("active_window_count") or 0)
        if window_count == 1:
            enriched["daily_guard_window_share"] = 1.0 if daily_guard_window_count > 0 else 0.0
        elif active_window_count > 0:
            enriched["daily_guard_window_share"] = round(
                float(daily_guard_window_count) / float(active_window_count),
                6,
            )
        else:
            enriched["daily_guard_window_share"] = 0.0
    if "worst_active_window_accepted_count" not in enriched and window_count == 1:
        enriched["worst_active_window_accepted_count"] = accepted_count if accepted_count > 0 else 0
    if "worst_active_window_accepted_size_usd" not in enriched and window_count == 1:
        enriched["worst_active_window_accepted_size_usd"] = round(accepted_size_usd, 6) if accepted_size_usd > 0 else 0.0
    if "accepted_window_count" not in enriched:
        if window_count == 1:
            enriched["accepted_window_count"] = 1 if accepted_count > 0 or accepted_size_usd > 0 else 0
        elif active_window_count > 0 and accepted_size_usd > 0:
            enriched["accepted_window_count"] = active_window_count
        else:
            enriched["accepted_window_count"] = 0
    return enriched


def _reject_reason_summary(result: dict[str, Any]) -> dict[str, int]:
    raw = result.get("reject_reason_summary")
    if not isinstance(raw, dict):
        return {}
    summary: dict[str, int] = {}
    for reason, count in raw.items():
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            continue
        summary[normalized_reason] = summary.get(normalized_reason, 0) + int(count or 0)
    return summary


def _pause_guard_reject_share(result: dict[str, Any]) -> float:
    trade_count = int(result.get("trade_count") or 0)
    if trade_count <= 0:
        return 0.0
    reject_reason_summary = _reject_reason_summary(result)
    pause_rejects = int(reject_reason_summary.get("daily_loss_guard") or 0) + int(reject_reason_summary.get("live_drawdown_guard") or 0)
    return float(pause_rejects) / float(trade_count)


def _active_window_count(result: dict[str, Any]) -> int:
    active_window_count = int(result.get("active_window_count") or 0)
    if active_window_count > 0:
        return active_window_count
    window_count = int(result.get("window_count") or 0)
    if window_count <= 1:
        return 1 if int(result.get("accepted_count") or 0) > 0 else 0
    return max(window_count - int(result.get("inactive_window_count") or 0), 0)


def _carry_window_share(result: dict[str, Any]) -> float:
    raw_share = result.get("carry_window_share")
    if raw_share is not None:
        return _clamp_fraction(float(raw_share))
    carry_window_count = int(result.get("carry_window_count") or 0)
    active_window_count = _active_window_count(result)
    if active_window_count > 0:
        return _clamp_fraction(float(carry_window_count) / float(active_window_count))
    return 0.0


def _carry_restart_window_share(result: dict[str, Any]) -> float:
    raw_share = result.get("carry_restart_window_share")
    if raw_share is not None:
        return _clamp_fraction(float(raw_share))
    carry_restart_window_count = int(result.get("carry_restart_window_count") or 0)
    carry_restart_window_opportunity_count = int(result.get("carry_restart_window_opportunity_count") or 0)
    if carry_restart_window_opportunity_count > 0:
        return _clamp_fraction(float(carry_restart_window_count) / float(carry_restart_window_opportunity_count))
    return 0.0


def _daily_guard_restart_window_share(result: dict[str, Any]) -> float:
    raw_share = result.get("daily_guard_restart_window_share")
    if raw_share is not None:
        return _clamp_fraction(float(raw_share))
    daily_guard_restart_window_count = int(result.get("daily_guard_restart_window_count") or 0)
    daily_guard_restart_window_opportunity_count = int(result.get("daily_guard_restart_window_opportunity_count") or 0)
    if daily_guard_restart_window_opportunity_count > 0:
        return _clamp_fraction(float(daily_guard_restart_window_count) / float(daily_guard_restart_window_opportunity_count))
    return 0.0


def _live_guard_restart_window_share(result: dict[str, Any]) -> float:
    raw_share = result.get("live_guard_restart_window_share")
    if raw_share is not None:
        return _clamp_fraction(float(raw_share))
    live_guard_restart_window_count = int(result.get("live_guard_restart_window_count") or 0)
    live_guard_restart_window_opportunity_count = int(result.get("live_guard_restart_window_opportunity_count") or 0)
    if live_guard_restart_window_opportunity_count > 0:
        return _clamp_fraction(float(live_guard_restart_window_count) / float(live_guard_restart_window_opportunity_count))
    return 0.0


def _avg_window_end_open_exposure_share(result: dict[str, Any]) -> float:
    raw_share = result.get("avg_window_end_open_exposure_share")
    if raw_share is not None:
        return _clamp_fraction(float(raw_share))
    return _clamp_fraction(
        float(
            result.get("max_window_end_open_exposure_share")
            or result.get("window_end_open_exposure_share")
            or 0.0
        )
    )


def _live_guard_window_share(result: dict[str, Any]) -> float:
    raw_share = result.get("live_guard_window_share")
    if raw_share is not None:
        return _clamp_fraction(float(raw_share))
    live_guard_window_count = int(result.get("live_guard_window_count") or 0)
    active_window_count = _active_window_count(result)
    if active_window_count > 0:
        return _clamp_fraction(float(live_guard_window_count) / float(active_window_count))
    return 0.0


def _daily_guard_window_share(result: dict[str, Any]) -> float:
    raw_share = result.get("daily_guard_window_share")
    if raw_share is not None:
        return _clamp_fraction(float(raw_share))
    daily_guard_window_count = int(result.get("daily_guard_window_count") or 0)
    active_window_count = _active_window_count(result)
    if active_window_count > 0:
        return _clamp_fraction(float(daily_guard_window_count) / float(active_window_count))
    return 0.0


def _trader_concentration(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("trader_concentration")
    if not isinstance(raw, dict):
        return {}
    return raw


def _market_concentration(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("market_concentration")
    if not isinstance(raw, dict):
        return {}
    return raw


def _entry_price_band_concentration(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("entry_price_band_concentration")
    if not isinstance(raw, dict):
        return {}
    return raw


def _time_to_close_band_concentration(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("time_to_close_band_concentration")
    if not isinstance(raw, dict):
        return {}
    return raw


def _inverse_count_risk(raw_count: Any) -> float:
    count = int(raw_count or 0)
    if count <= 0:
        return 0.0
    return 1.0 / float(count)


def _clamp_fraction(raw: float) -> float:
    return min(max(float(raw), 0.0), 1.0)


def _constraint_failures(
    result: dict[str, Any],
    *,
    allow_heuristic: bool,
    allow_xgboost: bool,
    min_accepted_count: int,
    min_resolved_count: int,
    min_resolved_share: float,
    min_resolved_size_share: float,
    min_win_rate: float,
    min_total_pnl_usd: float,
    max_drawdown_pct: float,
    max_open_exposure_share: float = 0.0,
    min_worst_window_pnl_usd: float,
    min_worst_window_resolved_share: float,
    min_worst_window_resolved_size_share: float,
    max_worst_window_drawdown_pct: float,
    min_heuristic_accepted_count: int,
    min_xgboost_accepted_count: int,
    min_heuristic_resolved_count: int,
    min_xgboost_resolved_count: int,
    min_heuristic_win_rate: float,
    min_xgboost_win_rate: float,
    min_heuristic_resolved_share: float,
    min_xgboost_resolved_share: float,
    min_heuristic_resolved_size_share: float,
    min_xgboost_resolved_size_share: float,
    min_heuristic_pnl_usd: float,
    min_xgboost_pnl_usd: float,
    min_heuristic_worst_window_pnl_usd: float,
    min_xgboost_worst_window_pnl_usd: float,
    min_heuristic_worst_window_resolved_share: float,
    min_xgboost_worst_window_resolved_share: float,
    min_heuristic_worst_window_resolved_size_share: float,
    min_xgboost_worst_window_resolved_size_share: float,
    min_heuristic_positive_window_count: int,
    min_xgboost_positive_window_count: int,
    min_heuristic_worst_active_window_accepted_count: int,
    min_heuristic_worst_active_window_accepted_size_usd: float,
    min_xgboost_worst_active_window_accepted_count: int,
    min_xgboost_worst_active_window_accepted_size_usd: float,
    max_heuristic_inactive_window_count: int,
    max_xgboost_inactive_window_count: int,
    max_heuristic_accepted_share: float,
    max_heuristic_accepted_size_share: float,
    max_heuristic_active_window_accepted_share: float,
    max_heuristic_active_window_accepted_size_share: float,
    min_xgboost_accepted_share: float,
    min_xgboost_accepted_size_share: float,
    min_xgboost_active_window_accepted_share: float,
    min_xgboost_active_window_accepted_size_share: float,
    max_pause_guard_reject_share: float,
    max_daily_guard_window_share: float = 0.0,
    max_live_guard_window_share: float = 0.0,
    max_daily_guard_restart_window_share: float = 0.0,
    max_live_guard_restart_window_share: float = 0.0,
    min_active_window_count: int,
    max_inactive_window_count: int,
    min_trader_count: int,
    min_market_count: int,
    min_entry_price_band_count: int,
    min_time_to_close_band_count: int,
    max_top_trader_accepted_share: float,
    max_top_trader_abs_pnl_share: float,
    max_top_trader_size_share: float,
    max_top_market_accepted_share: float,
    max_top_market_abs_pnl_share: float,
    max_top_market_size_share: float,
    max_top_entry_price_band_accepted_share: float,
    max_top_entry_price_band_abs_pnl_share: float,
    max_top_entry_price_band_size_share: float,
    max_top_time_to_close_band_accepted_share: float,
    max_top_time_to_close_band_abs_pnl_share: float,
    max_top_time_to_close_band_size_share: float,
    min_worst_active_window_accepted_count: int = 0,
    min_worst_active_window_accepted_size_usd: float = 0.0,
    max_window_end_open_exposure_share: float = 0.0,
    max_avg_window_end_open_exposure_share: float = 0.0,
    max_carry_window_share: float = 0.0,
    max_carry_restart_window_share: float = 0.0,
) -> list[str]:
    failures: list[str] = []
    accepted_count = int(result.get("accepted_count") or 0)
    resolved_count = int(result.get("resolved_count") or 0)
    resolved_share = _resolved_share_from_counts(accepted_count, resolved_count)
    resolved_size_share = _resolved_share_from_sizes(result.get("accepted_size_usd"), result.get("resolved_size_usd"))
    signal_mode_summary = _signal_mode_summary(result)
    trader_concentration = _trader_concentration(result)
    market_concentration = _market_concentration(result)
    entry_price_band_concentration = _entry_price_band_concentration(result)
    time_to_close_band_concentration = _time_to_close_band_concentration(result)
    raw_win_rate = result.get("win_rate")
    win_rate = float(raw_win_rate) if raw_win_rate is not None else None
    total_pnl_usd = float(result.get("total_pnl_usd") or 0.0)
    drawdown_pct = float(result.get("max_drawdown_pct") or 0.0)
    open_exposure_share = float(result.get("max_open_exposure_share") or 0.0)
    window_end_open_exposure_share = float(
        result.get("max_window_end_open_exposure_share")
        or result.get("window_end_open_exposure_share")
        or 0.0
    )
    avg_window_end_open_exposure_share = _avg_window_end_open_exposure_share(result)
    carry_window_share = _carry_window_share(result)
    carry_restart_window_share = _carry_restart_window_share(result)
    daily_guard_window_share = _daily_guard_window_share(result)
    live_guard_window_share = _live_guard_window_share(result)
    daily_guard_restart_window_share = _daily_guard_restart_window_share(result)
    live_guard_restart_window_share = _live_guard_restart_window_share(result)
    worst_window_pnl_usd = float(result.get("worst_window_pnl_usd") or 0.0)
    worst_window_resolved_share = _global_worst_active_window_resolved_share(result)
    worst_window_resolved_size_share = _global_worst_active_window_resolved_size_share(result)
    worst_window_drawdown_pct = float(result.get("worst_window_drawdown_pct") or 0.0)
    active_window_count = int(result.get("active_window_count") or 0)
    inactive_window_count = int(result.get("inactive_window_count") or 0)
    worst_active_window_accepted_count = int(result.get("worst_active_window_accepted_count") or 0)
    worst_active_window_accepted_size_usd = float(result.get("worst_active_window_accepted_size_usd") or 0.0)

    if accepted_count < max(min_accepted_count, 0):
        failures.append("accepted_count")
    if resolved_count < max(min_resolved_count, 0):
        failures.append("resolved_count")
    if min_resolved_share > 0 and resolved_share < min_resolved_share:
        failures.append("resolved_share")
    if min_resolved_size_share > 0 and resolved_size_share < min_resolved_size_share:
        failures.append("resolved_size_share")
    if min_win_rate > 0 and (win_rate is None or win_rate < min_win_rate):
        failures.append("win_rate")
    if total_pnl_usd < min_total_pnl_usd:
        failures.append("total_pnl_usd")
    if max_drawdown_pct > 0 and drawdown_pct > max_drawdown_pct:
        failures.append("max_drawdown_pct")
    if max_open_exposure_share > 0 and open_exposure_share > max_open_exposure_share:
        failures.append("max_open_exposure_share")
    if (
        max_window_end_open_exposure_share > 0
        and window_end_open_exposure_share > max_window_end_open_exposure_share
    ):
        failures.append("max_window_end_open_exposure_share")
    if (
        max_avg_window_end_open_exposure_share > 0
        and avg_window_end_open_exposure_share > max_avg_window_end_open_exposure_share
    ):
        failures.append("avg_window_end_open_exposure_share")
    if max_carry_window_share > 0 and carry_window_share > max_carry_window_share:
        failures.append("carry_window_share")
    if max_carry_restart_window_share > 0 and carry_restart_window_share > max_carry_restart_window_share:
        failures.append("carry_restart_window_share")
    if max_daily_guard_window_share > 0 and daily_guard_window_share > max_daily_guard_window_share:
        failures.append("daily_guard_window_share")
    if max_live_guard_window_share > 0 and live_guard_window_share > max_live_guard_window_share:
        failures.append("live_guard_window_share")
    if max_daily_guard_restart_window_share > 0 and daily_guard_restart_window_share > max_daily_guard_restart_window_share:
        failures.append("daily_guard_restart_window_share")
    if max_live_guard_restart_window_share > 0 and live_guard_restart_window_share > max_live_guard_restart_window_share:
        failures.append("live_guard_restart_window_share")
    if worst_window_pnl_usd < min_worst_window_pnl_usd:
        failures.append("worst_window_pnl_usd")
    if min_worst_window_resolved_share > 0 and worst_window_resolved_share < min_worst_window_resolved_share:
        failures.append("worst_window_resolved_share")
    if min_worst_window_resolved_size_share > 0 and worst_window_resolved_size_share < min_worst_window_resolved_size_share:
        failures.append("worst_window_resolved_size_share")
    if max_worst_window_drawdown_pct > 0 and worst_window_drawdown_pct > max_worst_window_drawdown_pct:
        failures.append("worst_window_drawdown_pct")
    if allow_heuristic and int(signal_mode_summary.get("heuristic", {}).get("accepted_count") or 0) < max(min_heuristic_accepted_count, 0):
        failures.append("heuristic_accepted_count")
    if allow_xgboost and int(signal_mode_summary.get("xgboost", {}).get("accepted_count") or 0) < max(min_xgboost_accepted_count, 0):
        failures.append("xgboost_accepted_count")
    if allow_heuristic and int(signal_mode_summary.get("heuristic", {}).get("resolved_count") or 0) < max(min_heuristic_resolved_count, 0):
        failures.append("heuristic_resolved_count")
    if allow_xgboost and int(signal_mode_summary.get("xgboost", {}).get("resolved_count") or 0) < max(min_xgboost_resolved_count, 0):
        failures.append("xgboost_resolved_count")
    heuristic_win_rate = signal_mode_summary.get("heuristic", {}).get("win_rate")
    xgboost_win_rate = signal_mode_summary.get("xgboost", {}).get("win_rate")
    if allow_heuristic and min_heuristic_win_rate > 0 and (heuristic_win_rate is None or float(heuristic_win_rate) < min_heuristic_win_rate):
        failures.append("heuristic_win_rate")
    if allow_xgboost and min_xgboost_win_rate > 0 and (xgboost_win_rate is None or float(xgboost_win_rate) < min_xgboost_win_rate):
        failures.append("xgboost_win_rate")
    if allow_heuristic and min_heuristic_resolved_share > 0 and _resolved_share(signal_mode_summary, "heuristic") < min_heuristic_resolved_share:
        failures.append("heuristic_resolved_share")
    if allow_xgboost and min_xgboost_resolved_share > 0 and _resolved_share(signal_mode_summary, "xgboost") < min_xgboost_resolved_share:
        failures.append("xgboost_resolved_share")
    if (
        allow_heuristic
        and min_heuristic_resolved_size_share > 0
        and _resolved_size_share(signal_mode_summary, "heuristic") < min_heuristic_resolved_size_share
    ):
        failures.append("heuristic_resolved_size_share")
    if (
        allow_xgboost
        and min_xgboost_resolved_size_share > 0
        and _resolved_size_share(signal_mode_summary, "xgboost") < min_xgboost_resolved_size_share
    ):
        failures.append("xgboost_resolved_size_share")
    if allow_heuristic and float(signal_mode_summary.get("heuristic", {}).get("total_pnl_usd") or 0.0) < min_heuristic_pnl_usd:
        failures.append("heuristic_total_pnl_usd")
    if allow_xgboost and float(signal_mode_summary.get("xgboost", {}).get("total_pnl_usd") or 0.0) < min_xgboost_pnl_usd:
        failures.append("xgboost_total_pnl_usd")
    if allow_heuristic and _worst_window_pnl(signal_mode_summary, "heuristic") < min_heuristic_worst_window_pnl_usd:
        failures.append("heuristic_worst_window_pnl_usd")
    if allow_xgboost and _worst_window_pnl(signal_mode_summary, "xgboost") < min_xgboost_worst_window_pnl_usd:
        failures.append("xgboost_worst_window_pnl_usd")
    if allow_heuristic and min_heuristic_worst_window_resolved_share > 0 and _worst_active_window_resolved_share(signal_mode_summary, "heuristic") < min_heuristic_worst_window_resolved_share:
        failures.append("heuristic_worst_window_resolved_share")
    if allow_xgboost and min_xgboost_worst_window_resolved_share > 0 and _worst_active_window_resolved_share(signal_mode_summary, "xgboost") < min_xgboost_worst_window_resolved_share:
        failures.append("xgboost_worst_window_resolved_share")
    if (
        allow_heuristic
        and min_heuristic_worst_window_resolved_size_share > 0
        and _worst_active_window_resolved_size_share(signal_mode_summary, "heuristic") < min_heuristic_worst_window_resolved_size_share
    ):
        failures.append("heuristic_worst_window_resolved_size_share")
    if (
        allow_xgboost
        and min_xgboost_worst_window_resolved_size_share > 0
        and _worst_active_window_resolved_size_share(signal_mode_summary, "xgboost") < min_xgboost_worst_window_resolved_size_share
    ):
        failures.append("xgboost_worst_window_resolved_size_share")
    if allow_heuristic and int(signal_mode_summary.get("heuristic", {}).get("positive_window_count") or 0) < max(min_heuristic_positive_window_count, 0):
        failures.append("heuristic_positive_window_count")
    if allow_xgboost and int(signal_mode_summary.get("xgboost", {}).get("positive_window_count") or 0) < max(min_xgboost_positive_window_count, 0):
        failures.append("xgboost_positive_window_count")
    heuristic_inactive_window_count = int(signal_mode_summary.get("heuristic", {}).get("inactive_window_count") or 0)
    xgboost_inactive_window_count = int(signal_mode_summary.get("xgboost", {}).get("inactive_window_count") or 0)
    heuristic_worst_active_window_accepted_count = signal_mode_summary.get("heuristic", {}).get("worst_active_window_accepted_count")
    heuristic_worst_active_window_accepted_size_usd = signal_mode_summary.get("heuristic", {}).get("worst_active_window_accepted_size_usd")
    xgboost_worst_active_window_accepted_count = signal_mode_summary.get("xgboost", {}).get("worst_active_window_accepted_count")
    xgboost_worst_active_window_accepted_size_usd = signal_mode_summary.get("xgboost", {}).get("worst_active_window_accepted_size_usd")
    if allow_heuristic and max_heuristic_inactive_window_count >= 0 and heuristic_inactive_window_count > max_heuristic_inactive_window_count:
        failures.append("heuristic_inactive_window_count")
    if allow_xgboost and max_xgboost_inactive_window_count >= 0 and xgboost_inactive_window_count > max_xgboost_inactive_window_count:
        failures.append("xgboost_inactive_window_count")
    if (
        allow_heuristic
        and min_heuristic_worst_active_window_accepted_count > 0
        and int(signal_mode_summary.get("heuristic", {}).get("accepted_count") or 0) > 0
        and int(heuristic_worst_active_window_accepted_count or 0) < min_heuristic_worst_active_window_accepted_count
    ):
        failures.append("heuristic_worst_active_window_accepted_count")
    if (
        allow_xgboost
        and min_xgboost_worst_active_window_accepted_count > 0
        and int(signal_mode_summary.get("xgboost", {}).get("accepted_count") or 0) > 0
        and int(xgboost_worst_active_window_accepted_count or 0) < min_xgboost_worst_active_window_accepted_count
    ):
        failures.append("xgboost_worst_active_window_accepted_count")
    if (
        allow_heuristic
        and min_heuristic_worst_active_window_accepted_size_usd > 0
        and float(signal_mode_summary.get("heuristic", {}).get("accepted_size_usd") or 0.0) > 0
        and float(heuristic_worst_active_window_accepted_size_usd or 0.0) < min_heuristic_worst_active_window_accepted_size_usd
    ):
        failures.append("heuristic_worst_active_window_accepted_size_usd")
    if (
        allow_xgboost
        and min_xgboost_worst_active_window_accepted_size_usd > 0
        and float(signal_mode_summary.get("xgboost", {}).get("accepted_size_usd") or 0.0) > 0
        and float(xgboost_worst_active_window_accepted_size_usd or 0.0) < min_xgboost_worst_active_window_accepted_size_usd
    ):
        failures.append("xgboost_worst_active_window_accepted_size_usd")
    mix_modes_enabled = allow_heuristic and allow_xgboost
    heuristic_accepted_share = _accepted_share(signal_mode_summary, "heuristic")
    xgboost_accepted_share = _accepted_share(signal_mode_summary, "xgboost")
    heuristic_accepted_size_share = _accepted_size_share(signal_mode_summary, "heuristic")
    xgboost_accepted_size_share = _accepted_size_share(signal_mode_summary, "xgboost")
    if mix_modes_enabled and max_heuristic_accepted_share > 0 and heuristic_accepted_share > max_heuristic_accepted_share:
        failures.append("heuristic_accepted_share")
    if mix_modes_enabled and min_xgboost_accepted_share > 0 and xgboost_accepted_share < min_xgboost_accepted_share:
        failures.append("xgboost_accepted_share")
    if mix_modes_enabled and max_heuristic_accepted_size_share > 0 and heuristic_accepted_size_share > max_heuristic_accepted_size_share:
        failures.append("heuristic_accepted_size_share")
    if mix_modes_enabled and min_xgboost_accepted_size_share > 0 and xgboost_accepted_size_share < min_xgboost_accepted_size_share:
        failures.append("xgboost_accepted_size_share")
    heuristic_max_active_window_accepted_share = _max_active_window_accepted_share(signal_mode_summary, "heuristic")
    xgboost_min_active_window_accepted_share = _min_active_window_accepted_share(signal_mode_summary, "xgboost")
    heuristic_max_active_window_accepted_size_share = _max_active_window_accepted_size_share(signal_mode_summary, "heuristic")
    xgboost_min_active_window_accepted_size_share = _min_active_window_accepted_size_share(signal_mode_summary, "xgboost")
    if (
        mix_modes_enabled
        and max_heuristic_active_window_accepted_share > 0
        and heuristic_max_active_window_accepted_share > max_heuristic_active_window_accepted_share
    ):
        failures.append("heuristic_active_window_accepted_share")
    if (
        mix_modes_enabled
        and min_xgboost_active_window_accepted_share > 0
        and xgboost_min_active_window_accepted_share < min_xgboost_active_window_accepted_share
    ):
        failures.append("xgboost_active_window_accepted_share")
    if (
        mix_modes_enabled
        and max_heuristic_active_window_accepted_size_share > 0
        and heuristic_max_active_window_accepted_size_share > max_heuristic_active_window_accepted_size_share
    ):
        failures.append("heuristic_active_window_accepted_size_share")
    if (
        mix_modes_enabled
        and min_xgboost_active_window_accepted_size_share > 0
        and xgboost_min_active_window_accepted_size_share < min_xgboost_active_window_accepted_size_share
    ):
        failures.append("xgboost_active_window_accepted_size_share")
    if max_pause_guard_reject_share > 0 and _pause_guard_reject_share(result) > max_pause_guard_reject_share:
        failures.append("pause_guard_reject_share")
    if active_window_count < max(min_active_window_count, 0):
        failures.append("active_window_count")
    if max_inactive_window_count >= 0 and inactive_window_count > max_inactive_window_count:
        failures.append("inactive_window_count")
    if worst_active_window_accepted_count < max(min_worst_active_window_accepted_count, 0):
        failures.append("worst_active_window_accepted_count")
    if min_worst_active_window_accepted_size_usd > 0 and worst_active_window_accepted_size_usd < min_worst_active_window_accepted_size_usd:
        failures.append("worst_active_window_accepted_size_usd")
    if int(trader_concentration.get("trader_count") or 0) < max(min_trader_count, 0):
        failures.append("trader_count")
    if int(market_concentration.get("market_count") or 0) < max(min_market_count, 0):
        failures.append("market_count")
    if int(entry_price_band_concentration.get("entry_price_band_count") or 0) < max(min_entry_price_band_count, 0):
        failures.append("entry_price_band_count")
    if int(time_to_close_band_concentration.get("time_to_close_band_count") or 0) < max(min_time_to_close_band_count, 0):
        failures.append("time_to_close_band_count")
    if max_top_trader_accepted_share > 0 and float(trader_concentration.get("top_accepted_share") or 0.0) > max_top_trader_accepted_share:
        failures.append("top_trader_accepted_share")
    if max_top_trader_abs_pnl_share > 0 and float(trader_concentration.get("top_abs_pnl_share") or 0.0) > max_top_trader_abs_pnl_share:
        failures.append("top_trader_abs_pnl_share")
    if max_top_trader_size_share > 0 and float(trader_concentration.get("top_size_share") or 0.0) > max_top_trader_size_share:
        failures.append("top_trader_size_share")
    if max_top_market_accepted_share > 0 and float(market_concentration.get("top_accepted_share") or 0.0) > max_top_market_accepted_share:
        failures.append("top_market_accepted_share")
    if max_top_market_abs_pnl_share > 0 and float(market_concentration.get("top_abs_pnl_share") or 0.0) > max_top_market_abs_pnl_share:
        failures.append("top_market_abs_pnl_share")
    if max_top_market_size_share > 0 and float(market_concentration.get("top_size_share") or 0.0) > max_top_market_size_share:
        failures.append("top_market_size_share")
    if max_top_entry_price_band_accepted_share > 0 and float(entry_price_band_concentration.get("top_accepted_share") or 0.0) > max_top_entry_price_band_accepted_share:
        failures.append("top_entry_price_band_accepted_share")
    if max_top_entry_price_band_abs_pnl_share > 0 and float(entry_price_band_concentration.get("top_abs_pnl_share") or 0.0) > max_top_entry_price_band_abs_pnl_share:
        failures.append("top_entry_price_band_abs_pnl_share")
    if max_top_entry_price_band_size_share > 0 and float(entry_price_band_concentration.get("top_size_share") or 0.0) > max_top_entry_price_band_size_share:
        failures.append("top_entry_price_band_size_share")
    if max_top_time_to_close_band_accepted_share > 0 and float(time_to_close_band_concentration.get("top_accepted_share") or 0.0) > max_top_time_to_close_band_accepted_share:
        failures.append("top_time_to_close_band_accepted_share")
    if max_top_time_to_close_band_abs_pnl_share > 0 and float(time_to_close_band_concentration.get("top_abs_pnl_share") or 0.0) > max_top_time_to_close_band_abs_pnl_share:
        failures.append("top_time_to_close_band_abs_pnl_share")
    if max_top_time_to_close_band_size_share > 0 and float(time_to_close_band_concentration.get("top_size_share") or 0.0) > max_top_time_to_close_band_size_share:
        failures.append("top_time_to_close_band_size_share")
    return failures


def _compact_override_summary(payload: dict[str, Any]) -> str:
    if not payload:
        return "default"
    parts = [f"{key}={payload[key]}" for key in sorted(payload)]
    return ", ".join(parts)


def _print_ranked_summary(results: list[dict[str, Any]], *, top: int, title: str) -> None:
    print(title, file=sys.stderr)
    for index, row in enumerate(results[:top], start=1):
        failures = row.get("constraint_failures") or []
        feasibility_suffix = "" if not failures else f" | reject {','.join(str(value) for value in failures)}"
        signal_mode_summary = _signal_mode_summary(row["result"])
        mode_parts: list[str] = []
        for mode, label in (("heuristic", "heur"), ("xgboost", "xgb")):
            accepted_count = int(signal_mode_summary.get(mode, {}).get("accepted_count") or 0)
            if accepted_count > 0:
                mode_parts.append(
                    f"{label} {accepted_count} ({_accepted_share(signal_mode_summary, mode) * 100:.0f}%)"
                    f" sz {_accepted_size_share(signal_mode_summary, mode) * 100:.0f}%"
                )
        mode_suffix = f" | modes {' / '.join(mode_parts)}" if mode_parts else ""
        pause_guard_share = _pause_guard_reject_share(row["result"])
        live_guard_window_count = int(row["result"].get("live_guard_window_count") or 0)
        daily_guard_window_count = int(row["result"].get("daily_guard_window_count") or 0)
        active_window_count = _active_window_count(row["result"])
        pause_parts: list[str] = []
        if pause_guard_share > 0:
            pause_parts.append(f"pause {pause_guard_share * 100:.0f}%")
        if active_window_count > 0 and daily_guard_window_count > 0:
            pause_parts.append(f"d-freq {daily_guard_window_count}/{active_window_count}")
        if active_window_count > 0 and live_guard_window_count > 0:
            pause_parts.append(f"p-freq {live_guard_window_count}/{active_window_count}")
        daily_guard_restart_window_count = int(row["result"].get("daily_guard_restart_window_count") or 0)
        daily_guard_restart_window_opportunity_count = int(row["result"].get("daily_guard_restart_window_opportunity_count") or 0)
        if daily_guard_restart_window_opportunity_count > 0:
            pause_parts.append(f"d-rst {daily_guard_restart_window_count}/{daily_guard_restart_window_opportunity_count}")
        elif daily_guard_restart_window_count > 0:
            pause_parts.append("d-rst yes")
        live_guard_restart_window_count = int(row["result"].get("live_guard_restart_window_count") or 0)
        live_guard_restart_window_opportunity_count = int(row["result"].get("live_guard_restart_window_opportunity_count") or 0)
        if live_guard_restart_window_opportunity_count > 0:
            pause_parts.append(f"p-rst {live_guard_restart_window_count}/{live_guard_restart_window_opportunity_count}")
        elif live_guard_restart_window_count > 0:
            pause_parts.append("p-rst yes")
        pause_suffix = f" | {' '.join(pause_parts)}" if pause_parts else ""
        trader_concentration = _trader_concentration(row["result"])
        market_concentration = _market_concentration(row["result"])
        entry_price_band_concentration = _entry_price_band_concentration(row["result"])
        time_to_close_band_concentration = _time_to_close_band_concentration(row["result"])
        concentration_parts: list[str] = []
        top_accepted_share = float(trader_concentration.get("top_accepted_share") or 0.0)
        top_abs_pnl_share = float(trader_concentration.get("top_abs_pnl_share") or 0.0)
        top_size_share = float(trader_concentration.get("top_size_share") or 0.0)
        top_market_accepted_share = float(market_concentration.get("top_accepted_share") or 0.0)
        top_market_abs_pnl_share = float(market_concentration.get("top_abs_pnl_share") or 0.0)
        top_market_size_share = float(market_concentration.get("top_size_share") or 0.0)
        top_entry_band_accepted_share = float(entry_price_band_concentration.get("top_accepted_share") or 0.0)
        top_entry_band_abs_pnl_share = float(entry_price_band_concentration.get("top_abs_pnl_share") or 0.0)
        top_entry_band_size_share = float(entry_price_band_concentration.get("top_size_share") or 0.0)
        top_horizon_accepted_share = float(time_to_close_band_concentration.get("top_accepted_share") or 0.0)
        top_horizon_abs_pnl_share = float(time_to_close_band_concentration.get("top_abs_pnl_share") or 0.0)
        top_horizon_size_share = float(time_to_close_band_concentration.get("top_size_share") or 0.0)
        if top_accepted_share > 0:
            concentration_parts.append(f"wallet n {top_accepted_share * 100:.0f}%")
        if top_abs_pnl_share > 0:
            concentration_parts.append(f"wallet pnl {top_abs_pnl_share * 100:.0f}%")
        if top_size_share > 0:
            concentration_parts.append(f"wallet sz {top_size_share * 100:.0f}%")
        if top_market_accepted_share > 0:
            concentration_parts.append(f"market n {top_market_accepted_share * 100:.0f}%")
        if top_market_abs_pnl_share > 0:
            concentration_parts.append(f"market pnl {top_market_abs_pnl_share * 100:.0f}%")
        if top_market_size_share > 0:
            concentration_parts.append(f"market sz {top_market_size_share * 100:.0f}%")
        if top_entry_band_accepted_share > 0:
            concentration_parts.append(f"band n {top_entry_band_accepted_share * 100:.0f}%")
        if top_entry_band_abs_pnl_share > 0:
            concentration_parts.append(f"band pnl {top_entry_band_abs_pnl_share * 100:.0f}%")
        if top_entry_band_size_share > 0:
            concentration_parts.append(f"band sz {top_entry_band_size_share * 100:.0f}%")
        if top_horizon_accepted_share > 0:
            concentration_parts.append(f"hzn n {top_horizon_accepted_share * 100:.0f}%")
        if top_horizon_abs_pnl_share > 0:
            concentration_parts.append(f"hzn pnl {top_horizon_abs_pnl_share * 100:.0f}%")
        if top_horizon_size_share > 0:
            concentration_parts.append(f"hzn sz {top_horizon_size_share * 100:.0f}%")
        concentration_suffix = f" | {' / '.join(concentration_parts)}" if concentration_parts else ""
        window_count = int(row["result"].get("window_count") or 0)
        carry_window_count = int(row["result"].get("carry_window_count") or 0)
        avg_window_end_open_exposure_share = _avg_window_end_open_exposure_share(row["result"])
        carry_suffix = ""
        window_suffix = ""
        if window_count > 1:
            positive_window_count = int(row["result"].get("positive_window_count") or 0)
            active_window_count = int(row["result"].get("active_window_count") or 0)
            worst_active_window_accepted_count = int(row["result"].get("worst_active_window_accepted_count") or 0)
            worst_active_window_accepted_size_usd = float(row["result"].get("worst_active_window_accepted_size_usd") or 0.0)
            worst_window_pnl_usd = float(row["result"].get("worst_window_pnl_usd") or 0.0)
            carry_summary = (
                f"{carry_window_count}/{active_window_count}"
                if active_window_count > 0
                else ("yes" if carry_window_count > 0 else "0")
            )
            carry_restart_window_count = int(row["result"].get("carry_restart_window_count") or 0)
            carry_restart_window_opportunity_count = int(row["result"].get("carry_restart_window_opportunity_count") or 0)
            carry_restart_suffix = (
                f" carry-rst {carry_restart_window_count}/{carry_restart_window_opportunity_count}"
                if carry_restart_window_opportunity_count > 0
                else (" carry-rst yes" if carry_restart_window_count > 0 else "")
            )
            window_suffix = (
                f" | windows {positive_window_count}/{window_count}+"
                f" active {active_window_count}/{window_count}"
                f" carry {carry_summary}"
                f"{carry_restart_suffix}"
                f" carry-avg {avg_window_end_open_exposure_share * 100:.0f}%"
                f" worst-act {worst_active_window_accepted_count}"
                f" worst-act$ {worst_active_window_accepted_size_usd:.2f}"
                f" | worst {worst_window_pnl_usd:+.2f}"
            )
        elif carry_window_count > 0 or avg_window_end_open_exposure_share > 0:
            carry_suffix = f" | carry yes carry-avg {avg_window_end_open_exposure_share * 100:.0f}%"
        print(
            "  "
            f"{index}. score {row['score']:+.2f} | pnl {row['result']['total_pnl_usd']:+.2f} | "
            f"dd {float(row['result'].get('max_drawdown_pct') or 0.0) * 100:.1f}% | "
            f"acc {int(row['result'].get('accepted_count') or 0)} | "
            f"win {float(row['result'].get('win_rate') or 0.0) * 100:.1f}% | "
            f"{_compact_override_summary(row['overrides'])}{mode_suffix}{pause_suffix}{concentration_suffix}{carry_suffix}{window_suffix}{feasibility_suffix}",
            file=sys.stderr,
        )


def _evaluate_candidate(
    *,
    policy: ReplayPolicy,
    db_path: Path | None,
    label: str,
    notes: str,
    windows: list[tuple[int | None, int | None]],
) -> dict[str, Any]:
    if len(windows) == 1 and windows[0] == (None, None):
        return _with_window_activity_fields(
            _with_worst_window_resolved_share(
                run_replay(
                    policy=policy,
                    db_path=db_path,
                    label=label,
                    notes=notes,
                )
            )
        )

    window_results: list[dict[str, Any]] = []
    continuity_state: dict[str, Any] | None = None
    for window_index, (start_ts, end_ts) in enumerate(windows, start=1):
        raw_result = run_replay(
            policy=policy,
            db_path=db_path,
            label=f"{label}-w{window_index:02d}",
            notes=notes,
            start_ts=start_ts,
            end_ts=end_ts,
            initial_state=continuity_state,
        )
        continuity_state = raw_result.pop("continuity_state", None)
        window_results.append(
            _with_window_activity_fields(
                _with_worst_window_resolved_share(
                    raw_result
                )
            )
        )
    return _aggregate_window_results(
        window_results,
        initial_bankroll_usd=policy.initial_bankroll_usd,
    )


def _resolve_db_path(raw_path: str) -> Path | None:
    return Path(raw_path) if raw_path else Path(TRADING_DB_PATH)


def _latest_trade_ts(*, db_path: Path | None, mode: str) -> int:
    target_path = db_path or Path(TRADING_DB_PATH)
    conn = sqlite3.connect(str(target_path))
    try:
        row = conn.execute(
            """
            SELECT MAX(placed_at)
            FROM trade_log
            WHERE COALESCE(source_action, 'buy')='buy'
              AND real_money=?
            """,
            (1 if mode == "live" else 0,),
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        raise ValueError("No replayable trades found for the selected mode")
    return int(row[0])


def _build_time_windows(
    *,
    db_path: Path | None,
    mode: str,
    window_days: int,
    window_count: int,
) -> list[tuple[int | None, int | None]]:
    if window_days <= 0 or window_count <= 1:
        return [(None, None)]

    window_seconds = max(window_days, 1) * 86400
    latest_ts = _latest_trade_ts(db_path=db_path, mode=mode)
    windows: list[tuple[int, int]] = []
    end_ts = latest_ts + 1
    for _ in range(max(window_count, 1)):
        start_ts = max(0, end_ts - window_seconds)
        windows.append((start_ts, end_ts))
        end_ts = start_ts
    windows.reverse()
    return windows


def _aggregate_window_results(
    window_results: list[dict[str, Any]],
    *,
    initial_bankroll_usd: float,
) -> dict[str, Any]:
    pnl_values = [float(row.get("total_pnl_usd") or 0.0) for row in window_results]
    drawdown_values = [float(row.get("max_drawdown_pct") or 0.0) for row in window_results]
    total_pnl = sum(float(row.get("total_pnl_usd") or 0.0) for row in window_results)
    accepted_count = sum(int(row.get("accepted_count") or 0) for row in window_results)
    accepted_size_usd = sum(float(row.get("accepted_size_usd") or 0.0) for row in window_results)
    resolved_count = sum(int(row.get("resolved_count") or 0) for row in window_results)
    resolved_size_usd = sum(float(row.get("resolved_size_usd") or 0.0) for row in window_results)
    rejected_count = sum(int(row.get("rejected_count") or 0) for row in window_results)
    trade_count = sum(int(row.get("trade_count") or 0) for row in window_results)
    unresolved_count = max(accepted_count - resolved_count, 0)
    weighted_wins = sum(
        float(row.get("win_rate") or 0.0) * int(row.get("resolved_count") or 0)
        for row in window_results
    )
    worst_window_drawdown_pct = max(drawdown_values, default=0.0)
    max_drawdown_pct = _stitched_max_drawdown_pct(
        window_results,
        initial_bankroll_usd=initial_bankroll_usd,
    )
    peak_open_exposure_usd = max(
        (float(row.get("peak_open_exposure_usd") or 0.0) for row in window_results),
        default=0.0,
    )
    max_open_exposure_share = max(
        (float(row.get("max_open_exposure_share") or 0.0) for row in window_results),
        default=0.0,
    )
    max_window_end_open_exposure_usd = max(
        (
            float(
                row.get("max_window_end_open_exposure_usd")
                or row.get("window_end_open_exposure_usd")
                or 0.0
            )
            for row in window_results
        ),
        default=0.0,
    )
    max_window_end_open_exposure_share = max(
        (
            float(
                row.get("max_window_end_open_exposure_share")
                or row.get("window_end_open_exposure_share")
                or 0.0
            )
            for row in window_results
        ),
        default=0.0,
    )
    positive_window_count = sum(1 for pnl in pnl_values if pnl > 0)
    negative_window_count = sum(1 for pnl in pnl_values if pnl < 0)
    active_rows = [row for row in window_results if _window_has_participation(row)]
    accepting_rows = [
        row
        for row in window_results
        if int(row.get("accepted_count") or 0) > 0
        or float(row.get("accepted_size_usd") or 0.0) > 0
    ]
    active_window_count = len(active_rows)
    accepted_window_count = len(accepting_rows)
    inactive_window_count = max(len(window_results) - active_window_count, 0)
    avg_window_end_open_exposure_share = (
        sum(
            float(
                row.get("max_window_end_open_exposure_share")
                or row.get("window_end_open_exposure_share")
                or 0.0
            )
            for row in active_rows
        ) / float(active_window_count)
        if active_window_count > 0
        else 0.0
    )
    carry_window_count = sum(
        1
        for row in window_results
        if float(
            row.get("max_window_end_open_exposure_usd")
            or row.get("window_end_open_exposure_usd")
            or 0.0
        ) > 0
    )
    carry_window_share = (
        float(carry_window_count) / float(active_window_count)
        if active_window_count > 0
        else 0.0
    )
    carry_restart_window_count = 0
    carry_restart_window_opportunity_count = 0
    daily_guard_restart_window_count = 0
    daily_guard_restart_window_opportunity_count = 0
    live_guard_restart_window_count = 0
    live_guard_restart_window_opportunity_count = 0
    carry_restart_pending = False
    daily_guard_restart_pending = False
    live_guard_restart_pending = False
    last_window_index = max(len(window_results) - 1, 0)
    for index, row in enumerate(window_results):
        if _window_has_participation(row):
            if carry_restart_pending:
                carry_restart_window_count += 1
                carry_restart_pending = False
            if daily_guard_restart_pending:
                daily_guard_restart_window_count += 1
                daily_guard_restart_pending = False
            if live_guard_restart_pending:
                live_guard_restart_window_count += 1
                live_guard_restart_pending = False
        if index >= last_window_index:
            continue
        if float(row.get("window_end_open_exposure_usd") or 0.0) > 0 and not carry_restart_pending:
            carry_restart_window_opportunity_count += 1
            carry_restart_pending = True
        if int(row.get("window_end_daily_guard_triggered") or 0) > 0 and not daily_guard_restart_pending:
            daily_guard_restart_window_opportunity_count += 1
            daily_guard_restart_pending = True
        if int(row.get("window_end_live_guard_triggered") or 0) > 0 and not live_guard_restart_pending:
            live_guard_restart_window_opportunity_count += 1
            live_guard_restart_pending = True
    carry_restart_window_share = (
        float(carry_restart_window_count) / float(carry_restart_window_opportunity_count)
        if carry_restart_window_opportunity_count > 0
        else 0.0
    )
    daily_guard_restart_window_share = (
        float(daily_guard_restart_window_count) / float(daily_guard_restart_window_opportunity_count)
        if daily_guard_restart_window_opportunity_count > 0
        else 0.0
    )
    live_guard_restart_window_share = (
        float(live_guard_restart_window_count) / float(live_guard_restart_window_opportunity_count)
        if live_guard_restart_window_opportunity_count > 0
        else 0.0
    )
    live_guard_window_count = sum(
        1
        for row in window_results
        if int(row.get("window_end_live_guard_triggered") or 0) > 0
    )
    daily_guard_window_count = sum(
        1
        for row in window_results
        if int(row.get("window_end_daily_guard_triggered") or 0) > 0
    )
    live_guard_window_share = (
        float(live_guard_window_count) / float(active_window_count)
        if active_window_count > 0
        else 0.0
    )
    daily_guard_window_share = (
        float(daily_guard_window_count) / float(active_window_count)
        if active_window_count > 0
        else 0.0
    )
    worst_active_window_accepted_count = min(
        (int(row.get("accepted_count") or 0) for row in window_results if int(row.get("accepted_count") or 0) > 0),
        default=0,
    )
    worst_active_window_accepted_size_usd = min(
        (float(row.get("accepted_size_usd") or 0.0) for row in window_results if float(row.get("accepted_size_usd") or 0.0) > 0),
        default=0.0,
    )
    worst_window_resolved_share = min(
        (
            _resolved_share_from_counts(row.get("accepted_count"), row.get("resolved_count"))
            for row in window_results
        ),
        default=0.0,
    )
    worst_window_resolved_size_share = min(
        (
            _resolved_share_from_sizes(row.get("accepted_size_usd"), row.get("resolved_size_usd"))
            for row in window_results
        ),
        default=0.0,
    )
    worst_active_window_resolved_share = min(
        (
            _resolved_share_from_counts(row.get("accepted_count"), row.get("resolved_count"))
            for row in window_results
            if int(row.get("accepted_count") or 0) > 0
        ),
        default=1.0 if accepted_count <= 0 else None,
    )
    worst_active_window_resolved_size_share = min(
        (
            _resolved_share_from_sizes(row.get("accepted_size_usd"), row.get("resolved_size_usd"))
            for row in window_results
            if float(row.get("accepted_size_usd") or 0.0) > 0
        ),
        default=1.0 if accepted_size_usd <= 0 else None,
    )
    window_avg_pnl_usd = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0
    window_pnl_stddev_usd = (
        math.sqrt(sum((value - window_avg_pnl_usd) ** 2 for value in pnl_values) / len(pnl_values))
        if pnl_values
        else 0.0
    )
    reject_reason_summary: dict[str, int] = {}
    signal_mode_totals: dict[str, dict[str, float]] = {}
    normalized_window_mode_summaries: list[dict[str, dict[str, Any]]] = []
    all_modes: set[str] = {"heuristic", "xgboost"}
    for window_result in window_results:
        for reason, count in _reject_reason_summary(window_result).items():
            reject_reason_summary[reason] = reject_reason_summary.get(reason, 0) + int(count or 0)
        window_mode_summary = _signal_mode_summary(window_result)
        normalized_window_mode_summaries.append(window_mode_summary)
        all_modes.update(window_mode_summary.keys())
        raw_window_end_signal_mode_exposure = window_result.get("window_end_signal_mode_exposure")
        if isinstance(raw_window_end_signal_mode_exposure, dict):
            all_modes.update(
                _canonical_signal_mode(raw_mode)
                for raw_mode in raw_window_end_signal_mode_exposure.keys()
                if _canonical_signal_mode(raw_mode)
            )
    for window_result, window_mode_summary in zip(window_results, normalized_window_mode_summaries):
        raw_window_end_signal_mode_exposure = window_result.get("window_end_signal_mode_exposure")
        window_end_signal_mode_exposure = (
            raw_window_end_signal_mode_exposure
            if isinstance(raw_window_end_signal_mode_exposure, dict)
            else {}
        )
        for mode in all_modes:
            values = window_mode_summary.get(mode) or {}
            exposure_values = window_end_signal_mode_exposure.get(mode) or {}
            bucket = signal_mode_totals.setdefault(
                mode,
                {
                    "trade_count": 0.0,
                    "accepted_count": 0.0,
                    "accepted_size_usd": 0.0,
                    "accepted_window_count": 0.0,
                    "resolved_count": 0.0,
                    "resolved_size_usd": 0.0,
                    "total_pnl_usd": 0.0,
                    "positive_window_count": 0.0,
                    "negative_window_count": 0.0,
                    "inactive_window_count": 0.0,
                    "window_pnls": [],
                    "window_resolved_shares": [],
                    "window_resolved_size_shares": [],
                    "active_window_resolved_shares": [],
                    "active_window_resolved_size_shares": [],
                    "active_window_accepted_counts": [],
                    "active_window_accepted_sizes_usd": [],
                    "active_window_accepted_shares": [],
                    "active_window_accepted_size_shares": [],
                    "win_count": 0.0,
                },
            )
            mode_accepted_count = int(values.get("accepted_count") or 0)
            mode_accepted_size_usd = float(values.get("accepted_size_usd") or 0.0)
            mode_resolved_count = int(values.get("resolved_count") or 0)
            mode_resolved_size_usd = float(values.get("resolved_size_usd") or 0.0)
            mode_open_count = int(exposure_values.get("open_count") or 0)
            mode_open_size_usd = float(exposure_values.get("open_size_usd") or 0.0)
            is_inactive_window = (
                not _mode_has_participation(values)
                and mode_open_count <= 0
                and mode_open_size_usd <= 0
            )
            bucket["trade_count"] += int(values.get("trade_count") or 0)
            bucket["accepted_count"] += mode_accepted_count
            bucket["accepted_size_usd"] += mode_accepted_size_usd
            bucket["accepted_window_count"] += 1 if mode_accepted_count > 0 or mode_accepted_size_usd > 0 else 0
            bucket["resolved_count"] += mode_resolved_count
            bucket["resolved_size_usd"] += mode_resolved_size_usd
            window_pnl_usd = float(values.get("total_pnl_usd") or 0.0)
            bucket["total_pnl_usd"] += window_pnl_usd
            bucket["positive_window_count"] += 1 if window_pnl_usd > 0 else 0
            bucket["negative_window_count"] += 1 if window_pnl_usd < 0 else 0
            bucket["inactive_window_count"] += 1 if is_inactive_window else 0
            bucket["window_pnls"].append(window_pnl_usd)
            window_resolved_share = _resolved_share_from_counts(mode_accepted_count, mode_resolved_count)
            window_resolved_size_share = _resolved_share_from_sizes(mode_accepted_size_usd, mode_resolved_size_usd)
            bucket["window_resolved_shares"].append(window_resolved_share)
            bucket["window_resolved_size_shares"].append(window_resolved_size_share)
            if int(window_result.get("accepted_count") or 0) > 0:
                bucket["active_window_accepted_shares"].append(
                    float(mode_accepted_count) / float(int(window_result.get("accepted_count") or 0))
                )
            if float(window_result.get("accepted_size_usd") or 0.0) > 0:
                bucket["active_window_accepted_size_shares"].append(
                    float(mode_accepted_size_usd) / float(float(window_result.get("accepted_size_usd") or 0.0))
                )
            if mode_accepted_count > 0:
                bucket["active_window_resolved_shares"].append(window_resolved_share)
                bucket["active_window_accepted_counts"].append(mode_accepted_count)
            if mode_accepted_size_usd > 0:
                bucket["active_window_resolved_size_shares"].append(window_resolved_size_share)
                bucket["active_window_accepted_sizes_usd"].append(mode_accepted_size_usd)
            bucket["win_count"] += int(values.get("win_count") or 0)
    signal_mode_summary = {
        mode: {
            "trade_count": int(values["trade_count"]),
            "accepted_count": int(values["accepted_count"]),
            "accepted_size_usd": round(values["accepted_size_usd"], 6),
            "accepted_window_count": int(values["accepted_window_count"]),
            "resolved_count": int(values["resolved_count"]),
            "resolved_size_usd": round(values["resolved_size_usd"], 6),
            "total_pnl_usd": round(values["total_pnl_usd"], 6),
            "positive_window_count": int(values["positive_window_count"]),
            "negative_window_count": int(values["negative_window_count"]),
            "inactive_window_count": int(values["inactive_window_count"]),
            "worst_window_pnl_usd": round(min(values["window_pnls"]), 6) if values["window_pnls"] else None,
            "worst_window_resolved_share": round(min(values["window_resolved_shares"]), 6) if values["window_resolved_shares"] else None,
            "worst_window_resolved_size_share": round(min(values["window_resolved_size_shares"]), 6) if values["window_resolved_size_shares"] else None,
            "worst_active_window_resolved_share": (
                round(min(values["active_window_resolved_shares"]), 6)
                if values["active_window_resolved_shares"]
                else 1.0
            ),
            "worst_active_window_resolved_size_share": (
                round(min(values["active_window_resolved_size_shares"]), 6)
                if values["active_window_resolved_size_shares"]
                else 1.0
            ),
            "worst_active_window_accepted_count": (
                int(min(values["active_window_accepted_counts"]))
                if values["active_window_accepted_counts"]
                else None
            ),
            "worst_active_window_accepted_size_usd": (
                round(float(min(values["active_window_accepted_sizes_usd"])), 6)
                if values["active_window_accepted_sizes_usd"]
                else None
            ),
            "min_active_window_accepted_share": (
                round(float(min(values["active_window_accepted_shares"])), 6)
                if values["active_window_accepted_shares"]
                else None
            ),
            "max_active_window_accepted_share": (
                round(float(max(values["active_window_accepted_shares"])), 6)
                if values["active_window_accepted_shares"]
                else None
            ),
            "min_active_window_accepted_size_share": (
                round(float(min(values["active_window_accepted_size_shares"])), 6)
                if values["active_window_accepted_size_shares"]
                else None
            ),
            "max_active_window_accepted_size_share": (
                round(float(max(values["active_window_accepted_size_shares"])), 6)
                if values["active_window_accepted_size_shares"]
                else None
            ),
            "best_window_pnl_usd": round(max(values["window_pnls"]), 6) if values["window_pnls"] else None,
            "win_count": int(values["win_count"]),
            "win_rate": round(values["win_count"] / values["resolved_count"], 6) if values["resolved_count"] > 0 else None,
        }
        for mode, values in signal_mode_totals.items()
    }
    trader_concentration_rows = [row.get("trader_concentration") for row in window_results if isinstance(row.get("trader_concentration"), dict)]
    market_concentration_rows = [row.get("market_concentration") for row in window_results if isinstance(row.get("market_concentration"), dict)]
    entry_price_band_concentration_rows = [
        row.get("entry_price_band_concentration")
        for row in window_results
        if isinstance(row.get("entry_price_band_concentration"), dict)
    ]
    time_to_close_band_concentration_rows = [
        row.get("time_to_close_band_concentration")
        for row in window_results
        if isinstance(row.get("time_to_close_band_concentration"), dict)
    ]
    active_trader_concentration_rows = [
        row.get("trader_concentration")
        for row in window_results
        if int(row.get("accepted_count") or 0) > 0 and isinstance(row.get("trader_concentration"), dict)
    ]
    active_market_concentration_rows = [
        row.get("market_concentration")
        for row in window_results
        if int(row.get("accepted_count") or 0) > 0 and isinstance(row.get("market_concentration"), dict)
    ]
    active_entry_price_band_concentration_rows = [
        row.get("entry_price_band_concentration")
        for row in window_results
        if int(row.get("accepted_count") or 0) > 0 and isinstance(row.get("entry_price_band_concentration"), dict)
    ]
    active_time_to_close_band_concentration_rows = [
        row.get("time_to_close_band_concentration")
        for row in window_results
        if int(row.get("accepted_count") or 0) > 0 and isinstance(row.get("time_to_close_band_concentration"), dict)
    ]
    top_accepted_window = max(
        trader_concentration_rows,
        key=lambda row: float((row or {}).get("top_accepted_share") or 0.0),
        default=None,
    )
    top_abs_pnl_window = max(
        trader_concentration_rows,
        key=lambda row: float((row or {}).get("top_abs_pnl_share") or 0.0),
        default=None,
    )
    top_size_window = max(
        trader_concentration_rows,
        key=lambda row: float((row or {}).get("top_size_share") or 0.0),
        default=None,
    )
    trader_concentration = {
        "window_mode": "max_window",
        "top_accepted_trader_address": str((top_accepted_window or {}).get("top_accepted_trader_address") or ""),
        "top_accepted_count": int((top_accepted_window or {}).get("top_accepted_count") or 0),
        "top_accepted_share": round(float((top_accepted_window or {}).get("top_accepted_share") or 0.0), 6),
        "top_accepted_total_pnl_usd": round(float((top_accepted_window or {}).get("top_accepted_total_pnl_usd") or 0.0), 6),
        "top_abs_pnl_trader_address": str((top_abs_pnl_window or {}).get("top_abs_pnl_trader_address") or ""),
        "top_abs_pnl_usd": round(float((top_abs_pnl_window or {}).get("top_abs_pnl_usd") or 0.0), 6),
        "top_abs_pnl_share": round(float((top_abs_pnl_window or {}).get("top_abs_pnl_share") or 0.0), 6),
        "top_size_trader_address": str((top_size_window or {}).get("top_size_trader_address") or ""),
        "top_size_usd": round(float((top_size_window or {}).get("top_size_usd") or 0.0), 6),
        "top_size_share": round(float((top_size_window or {}).get("top_size_share") or 0.0), 6),
        "trader_count": min((int((row or {}).get("trader_count") or 0) for row in active_trader_concentration_rows), default=0),
        "peak_trader_count": max((int((row or {}).get("trader_count") or 0) for row in trader_concentration_rows), default=0),
    }
    top_market_accepted_window = max(
        market_concentration_rows,
        key=lambda row: float((row or {}).get("top_accepted_share") or 0.0),
        default=None,
    )
    top_market_abs_pnl_window = max(
        market_concentration_rows,
        key=lambda row: float((row or {}).get("top_abs_pnl_share") or 0.0),
        default=None,
    )
    top_market_size_window = max(
        market_concentration_rows,
        key=lambda row: float((row or {}).get("top_size_share") or 0.0),
        default=None,
    )
    market_concentration = {
        "window_mode": "max_window",
        "top_accepted_market_id": str((top_market_accepted_window or {}).get("top_accepted_market_id") or ""),
        "top_accepted_count": int((top_market_accepted_window or {}).get("top_accepted_count") or 0),
        "top_accepted_share": round(float((top_market_accepted_window or {}).get("top_accepted_share") or 0.0), 6),
        "top_accepted_total_pnl_usd": round(float((top_market_accepted_window or {}).get("top_accepted_total_pnl_usd") or 0.0), 6),
        "top_abs_pnl_market_id": str((top_market_abs_pnl_window or {}).get("top_abs_pnl_market_id") or ""),
        "top_abs_pnl_usd": round(float((top_market_abs_pnl_window or {}).get("top_abs_pnl_usd") or 0.0), 6),
        "top_abs_pnl_share": round(float((top_market_abs_pnl_window or {}).get("top_abs_pnl_share") or 0.0), 6),
        "top_size_market_id": str((top_market_size_window or {}).get("top_size_market_id") or ""),
        "top_size_usd": round(float((top_market_size_window or {}).get("top_size_usd") or 0.0), 6),
        "top_size_share": round(float((top_market_size_window or {}).get("top_size_share") or 0.0), 6),
        "market_count": min((int((row or {}).get("market_count") or 0) for row in active_market_concentration_rows), default=0),
        "peak_market_count": max((int((row or {}).get("market_count") or 0) for row in market_concentration_rows), default=0),
    }
    top_entry_price_band_accepted_window = max(
        entry_price_band_concentration_rows,
        key=lambda row: float((row or {}).get("top_accepted_share") or 0.0),
        default=None,
    )
    top_entry_price_band_abs_pnl_window = max(
        entry_price_band_concentration_rows,
        key=lambda row: float((row or {}).get("top_abs_pnl_share") or 0.0),
        default=None,
    )
    top_entry_price_band_size_window = max(
        entry_price_band_concentration_rows,
        key=lambda row: float((row or {}).get("top_size_share") or 0.0),
        default=None,
    )
    entry_price_band_concentration = {
        "window_mode": "max_window",
        "top_accepted_entry_price_band": str((top_entry_price_band_accepted_window or {}).get("top_accepted_entry_price_band") or ""),
        "top_accepted_count": int((top_entry_price_band_accepted_window or {}).get("top_accepted_count") or 0),
        "top_accepted_share": round(float((top_entry_price_band_accepted_window or {}).get("top_accepted_share") or 0.0), 6),
        "top_accepted_total_pnl_usd": round(float((top_entry_price_band_accepted_window or {}).get("top_accepted_total_pnl_usd") or 0.0), 6),
        "top_abs_pnl_entry_price_band": str((top_entry_price_band_abs_pnl_window or {}).get("top_abs_pnl_entry_price_band") or ""),
        "top_abs_pnl_usd": round(float((top_entry_price_band_abs_pnl_window or {}).get("top_abs_pnl_usd") or 0.0), 6),
        "top_abs_pnl_share": round(float((top_entry_price_band_abs_pnl_window or {}).get("top_abs_pnl_share") or 0.0), 6),
        "top_size_entry_price_band": str((top_entry_price_band_size_window or {}).get("top_size_entry_price_band") or ""),
        "top_size_usd": round(float((top_entry_price_band_size_window or {}).get("top_size_usd") or 0.0), 6),
        "top_size_share": round(float((top_entry_price_band_size_window or {}).get("top_size_share") or 0.0), 6),
        "entry_price_band_count": min((int((row or {}).get("entry_price_band_count") or 0) for row in active_entry_price_band_concentration_rows), default=0),
        "peak_entry_price_band_count": max((int((row or {}).get("entry_price_band_count") or 0) for row in entry_price_band_concentration_rows), default=0),
    }
    top_time_to_close_band_accepted_window = max(
        time_to_close_band_concentration_rows,
        key=lambda row: float((row or {}).get("top_accepted_share") or 0.0),
        default=None,
    )
    top_time_to_close_band_abs_pnl_window = max(
        time_to_close_band_concentration_rows,
        key=lambda row: float((row or {}).get("top_abs_pnl_share") or 0.0),
        default=None,
    )
    top_time_to_close_band_size_window = max(
        time_to_close_band_concentration_rows,
        key=lambda row: float((row or {}).get("top_size_share") or 0.0),
        default=None,
    )
    time_to_close_band_concentration = {
        "window_mode": "max_window",
        "top_accepted_time_to_close_band": str((top_time_to_close_band_accepted_window or {}).get("top_accepted_time_to_close_band") or ""),
        "top_accepted_count": int((top_time_to_close_band_accepted_window or {}).get("top_accepted_count") or 0),
        "top_accepted_share": round(float((top_time_to_close_band_accepted_window or {}).get("top_accepted_share") or 0.0), 6),
        "top_accepted_total_pnl_usd": round(float((top_time_to_close_band_accepted_window or {}).get("top_accepted_total_pnl_usd") or 0.0), 6),
        "top_abs_pnl_time_to_close_band": str((top_time_to_close_band_abs_pnl_window or {}).get("top_abs_pnl_time_to_close_band") or ""),
        "top_abs_pnl_usd": round(float((top_time_to_close_band_abs_pnl_window or {}).get("top_abs_pnl_usd") or 0.0), 6),
        "top_abs_pnl_share": round(float((top_time_to_close_band_abs_pnl_window or {}).get("top_abs_pnl_share") or 0.0), 6),
        "top_size_time_to_close_band": str((top_time_to_close_band_size_window or {}).get("top_size_time_to_close_band") or ""),
        "top_size_usd": round(float((top_time_to_close_band_size_window or {}).get("top_size_usd") or 0.0), 6),
        "top_size_share": round(float((top_time_to_close_band_size_window or {}).get("top_size_share") or 0.0), 6),
        "time_to_close_band_count": min((int((row or {}).get("time_to_close_band_count") or 0) for row in active_time_to_close_band_concentration_rows), default=0),
        "peak_time_to_close_band_count": max((int((row or {}).get("time_to_close_band_count") or 0) for row in time_to_close_band_concentration_rows), default=0),
    }
    final_equity_usd = initial_bankroll_usd + total_pnl
    final_window = window_results[-1] if window_results else {}
    window_end_open_exposure_usd = float(final_window.get("window_end_open_exposure_usd") or 0.0)
    if final_equity_usd > 0:
        window_end_open_exposure_share = window_end_open_exposure_usd / final_equity_usd
    elif window_end_open_exposure_usd > 0:
        window_end_open_exposure_share = 1.0
    else:
        window_end_open_exposure_share = 0.0
    final_bankroll_usd = final_equity_usd - window_end_open_exposure_usd
    return {
        "window_count": len(window_results),
        "window_results": window_results,
        "initial_bankroll_usd": initial_bankroll_usd,
        "final_equity_usd": round(final_equity_usd, 6),
        "final_bankroll_usd": round(final_bankroll_usd, 6),
        "total_pnl_usd": round(total_pnl, 6),
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "peak_open_exposure_usd": round(peak_open_exposure_usd, 6),
        "max_open_exposure_share": round(max_open_exposure_share, 6),
        "window_end_open_exposure_usd": round(window_end_open_exposure_usd, 6),
        "window_end_open_exposure_share": round(window_end_open_exposure_share, 6),
        "max_window_end_open_exposure_usd": round(max_window_end_open_exposure_usd, 6),
        "max_window_end_open_exposure_share": round(max_window_end_open_exposure_share, 6),
        "avg_window_end_open_exposure_share": round(avg_window_end_open_exposure_share, 6),
        "carry_window_count": carry_window_count,
        "carry_window_share": round(carry_window_share, 6),
        "carry_restart_window_count": carry_restart_window_count,
        "carry_restart_window_opportunity_count": carry_restart_window_opportunity_count,
        "carry_restart_window_share": round(carry_restart_window_share, 6),
        "daily_guard_restart_window_count": daily_guard_restart_window_count,
        "daily_guard_restart_window_opportunity_count": daily_guard_restart_window_opportunity_count,
        "daily_guard_restart_window_share": round(daily_guard_restart_window_share, 6),
        "live_guard_restart_window_count": live_guard_restart_window_count,
        "live_guard_restart_window_opportunity_count": live_guard_restart_window_opportunity_count,
        "live_guard_restart_window_share": round(live_guard_restart_window_share, 6),
        "live_guard_window_count": live_guard_window_count,
        "live_guard_window_share": round(live_guard_window_share, 6),
        "daily_guard_window_count": daily_guard_window_count,
        "daily_guard_window_share": round(daily_guard_window_share, 6),
        "trade_count": trade_count,
        "accepted_count": accepted_count,
        "accepted_size_usd": round(accepted_size_usd, 6),
        "rejected_count": rejected_count,
        "unresolved_count": unresolved_count,
        "resolved_count": resolved_count,
        "resolved_size_usd": round(resolved_size_usd, 6),
        "win_rate": round(weighted_wins / resolved_count, 6) if resolved_count else None,
        "positive_window_count": positive_window_count,
        "negative_window_count": negative_window_count,
        "active_window_count": active_window_count,
        "accepted_window_count": accepted_window_count,
        "inactive_window_count": inactive_window_count,
        "worst_active_window_accepted_count": worst_active_window_accepted_count,
        "worst_active_window_accepted_size_usd": round(worst_active_window_accepted_size_usd, 6),
        "window_avg_pnl_usd": round(window_avg_pnl_usd, 6),
        "window_pnl_stddev_usd": round(window_pnl_stddev_usd, 6),
        "worst_window_pnl_usd": round(min(pnl_values, default=0.0), 6),
        "worst_window_resolved_share": round(worst_window_resolved_share, 6),
        "worst_window_resolved_size_share": round(worst_window_resolved_size_share, 6),
        "worst_active_window_resolved_share": (
            round(float(worst_active_window_resolved_share), 6)
            if worst_active_window_resolved_share is not None
            else None
        ),
        "worst_active_window_resolved_size_share": (
            round(float(worst_active_window_resolved_size_share), 6)
            if worst_active_window_resolved_size_share is not None
            else None
        ),
        "best_window_pnl_usd": round(max(pnl_values, default=0.0), 6),
        "worst_window_drawdown_pct": round(worst_window_drawdown_pct, 6),
        "reject_reason_summary": {reason: int(count) for reason, count in sorted(reject_reason_summary.items())},
        "signal_mode_summary": signal_mode_summary,
        "trader_concentration": trader_concentration,
        "market_concentration": market_concentration,
        "entry_price_band_concentration": entry_price_band_concentration,
        "time_to_close_band_concentration": time_to_close_band_concentration,
    }


def _ensure_table_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl_type in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")


def _ensure_search_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS replay_search_runs (
            id                            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at                    INTEGER NOT NULL,
            finished_at                   INTEGER NOT NULL,
            label_prefix                  TEXT NOT NULL DEFAULT '',
            status                        TEXT NOT NULL DEFAULT '',
            base_policy_json              TEXT NOT NULL DEFAULT '{}',
            grid_json                     TEXT NOT NULL DEFAULT '{}',
            constraints_json              TEXT NOT NULL DEFAULT '{}',
            notes                         TEXT NOT NULL DEFAULT '',
            window_days                   INTEGER NOT NULL DEFAULT 0,
            window_count                  INTEGER NOT NULL DEFAULT 1,
            drawdown_penalty              REAL NOT NULL DEFAULT 0,
            window_stddev_penalty         REAL NOT NULL DEFAULT 0,
            worst_window_penalty          REAL NOT NULL DEFAULT 0,
            pause_guard_penalty           REAL NOT NULL DEFAULT 0,
            daily_guard_window_penalty    REAL NOT NULL DEFAULT 0,
            live_guard_window_penalty     REAL NOT NULL DEFAULT 0,
            daily_guard_restart_window_penalty REAL NOT NULL DEFAULT 0,
            live_guard_restart_window_penalty REAL NOT NULL DEFAULT 0,
            open_exposure_penalty         REAL NOT NULL DEFAULT 0,
            window_end_open_exposure_penalty REAL NOT NULL DEFAULT 0,
            avg_window_end_open_exposure_penalty REAL NOT NULL DEFAULT 0,
            carry_window_penalty          REAL NOT NULL DEFAULT 0,
            carry_restart_window_penalty  REAL NOT NULL DEFAULT 0,
            resolved_share_penalty        REAL NOT NULL DEFAULT 0,
            resolved_size_share_penalty   REAL NOT NULL DEFAULT 0,
            worst_window_resolved_share_penalty REAL NOT NULL DEFAULT 0,
            worst_window_resolved_size_share_penalty REAL NOT NULL DEFAULT 0,
            mode_resolved_share_penalty   REAL NOT NULL DEFAULT 0,
            mode_resolved_size_share_penalty REAL NOT NULL DEFAULT 0,
            mode_worst_window_resolved_share_penalty REAL NOT NULL DEFAULT 0,
            mode_worst_window_resolved_size_share_penalty REAL NOT NULL DEFAULT 0,
            mode_active_window_accepted_share_penalty REAL NOT NULL DEFAULT 0,
            mode_active_window_accepted_size_share_penalty REAL NOT NULL DEFAULT 0,
            worst_active_window_accepted_penalty REAL NOT NULL DEFAULT 0,
            worst_active_window_accepted_size_penalty REAL NOT NULL DEFAULT 0,
            mode_worst_active_window_accepted_penalty REAL NOT NULL DEFAULT 0,
            mode_worst_active_window_accepted_size_penalty REAL NOT NULL DEFAULT 0,
            mode_loss_penalty             REAL NOT NULL DEFAULT 0,
            mode_inactivity_penalty       REAL NOT NULL DEFAULT 0,
            window_inactivity_penalty     REAL NOT NULL DEFAULT 0,
            wallet_count_penalty          REAL NOT NULL DEFAULT 0,
            market_count_penalty          REAL NOT NULL DEFAULT 0,
            entry_price_band_count_penalty REAL NOT NULL DEFAULT 0,
            time_to_close_band_count_penalty REAL NOT NULL DEFAULT 0,
            wallet_concentration_penalty  REAL NOT NULL DEFAULT 0,
            market_concentration_penalty  REAL NOT NULL DEFAULT 0,
            entry_price_band_concentration_penalty REAL NOT NULL DEFAULT 0,
            time_to_close_band_concentration_penalty REAL NOT NULL DEFAULT 0,
            wallet_size_concentration_penalty REAL NOT NULL DEFAULT 0,
            market_size_concentration_penalty REAL NOT NULL DEFAULT 0,
            entry_price_band_size_concentration_penalty REAL NOT NULL DEFAULT 0,
            time_to_close_band_size_concentration_penalty REAL NOT NULL DEFAULT 0,
            candidate_count               INTEGER NOT NULL DEFAULT 0,
            feasible_count                INTEGER NOT NULL DEFAULT 0,
            rejected_count                INTEGER NOT NULL DEFAULT 0,
            current_candidate_score       REAL,
            current_candidate_feasible    INTEGER NOT NULL DEFAULT 0,
            current_candidate_total_pnl_usd REAL,
            current_candidate_max_drawdown_pct REAL,
            current_candidate_constraint_failures_json TEXT NOT NULL DEFAULT '[]',
            current_candidate_result_json TEXT NOT NULL DEFAULT '{}',
            best_vs_current_pnl_usd       REAL,
            best_vs_current_score         REAL,
            best_feasible_candidate_index INTEGER,
            best_feasible_score           REAL,
            best_feasible_total_pnl_usd   REAL,
            best_feasible_max_drawdown_pct REAL
        );

        CREATE TABLE IF NOT EXISTS replay_search_candidates (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_search_run_id      INTEGER NOT NULL,
            candidate_index           INTEGER NOT NULL,
            score                     REAL NOT NULL DEFAULT 0,
            feasible                  INTEGER NOT NULL DEFAULT 0,
            constraint_failures_json  TEXT NOT NULL DEFAULT '[]',
            overrides_json            TEXT NOT NULL DEFAULT '{}',
            policy_json               TEXT NOT NULL DEFAULT '{}',
            config_json               TEXT NOT NULL DEFAULT '{}',
            result_json               TEXT NOT NULL DEFAULT '{}',
            total_pnl_usd             REAL NOT NULL DEFAULT 0,
            max_drawdown_pct          REAL,
            accepted_count            INTEGER NOT NULL DEFAULT 0,
            resolved_count            INTEGER NOT NULL DEFAULT 0,
            win_rate                  REAL,
            positive_window_count     INTEGER NOT NULL DEFAULT 0,
            negative_window_count     INTEGER NOT NULL DEFAULT 0,
            worst_window_pnl_usd      REAL,
            worst_window_drawdown_pct REAL,
            window_pnl_stddev_usd     REAL,
            FOREIGN KEY (replay_search_run_id) REFERENCES replay_search_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_replay_search_runs_finished_at ON replay_search_runs(finished_at DESC);
        CREATE INDEX IF NOT EXISTS idx_replay_search_candidates_run_id ON replay_search_candidates(replay_search_run_id);
        """
    )
    _ensure_table_columns(
        conn,
        "replay_search_runs",
        {
            "status": "TEXT NOT NULL DEFAULT ''",
            "base_policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "grid_json": "TEXT NOT NULL DEFAULT '{}'",
            "constraints_json": "TEXT NOT NULL DEFAULT '{}'",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "window_days": "INTEGER NOT NULL DEFAULT 0",
            "window_count": "INTEGER NOT NULL DEFAULT 1",
            "drawdown_penalty": "REAL NOT NULL DEFAULT 0",
            "window_stddev_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_window_penalty": "REAL NOT NULL DEFAULT 0",
            "pause_guard_penalty": "REAL NOT NULL DEFAULT 0",
            "daily_guard_window_penalty": "REAL NOT NULL DEFAULT 0",
            "live_guard_window_penalty": "REAL NOT NULL DEFAULT 0",
            "daily_guard_restart_window_penalty": "REAL NOT NULL DEFAULT 0",
            "live_guard_restart_window_penalty": "REAL NOT NULL DEFAULT 0",
            "open_exposure_penalty": "REAL NOT NULL DEFAULT 0",
            "window_end_open_exposure_penalty": "REAL NOT NULL DEFAULT 0",
            "avg_window_end_open_exposure_penalty": "REAL NOT NULL DEFAULT 0",
            "carry_window_penalty": "REAL NOT NULL DEFAULT 0",
            "carry_restart_window_penalty": "REAL NOT NULL DEFAULT 0",
            "resolved_share_penalty": "REAL NOT NULL DEFAULT 0",
            "resolved_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_window_resolved_share_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_window_resolved_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_resolved_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_resolved_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_worst_window_resolved_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_worst_window_resolved_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_active_window_accepted_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_active_window_accepted_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_active_window_accepted_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_active_window_accepted_size_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_worst_active_window_accepted_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_worst_active_window_accepted_size_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_loss_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_inactivity_penalty": "REAL NOT NULL DEFAULT 0",
            "window_inactivity_penalty": "REAL NOT NULL DEFAULT 0",
            "wallet_count_penalty": "REAL NOT NULL DEFAULT 0",
            "market_count_penalty": "REAL NOT NULL DEFAULT 0",
            "entry_price_band_count_penalty": "REAL NOT NULL DEFAULT 0",
            "time_to_close_band_count_penalty": "REAL NOT NULL DEFAULT 0",
            "wallet_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "market_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "entry_price_band_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "time_to_close_band_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "wallet_size_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "market_size_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "entry_price_band_size_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "time_to_close_band_size_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "candidate_count": "INTEGER NOT NULL DEFAULT 0",
            "feasible_count": "INTEGER NOT NULL DEFAULT 0",
            "rejected_count": "INTEGER NOT NULL DEFAULT 0",
            "current_candidate_score": "REAL",
            "current_candidate_feasible": "INTEGER NOT NULL DEFAULT 0",
            "current_candidate_total_pnl_usd": "REAL",
            "current_candidate_max_drawdown_pct": "REAL",
            "current_candidate_constraint_failures_json": "TEXT NOT NULL DEFAULT '[]'",
            "current_candidate_result_json": "TEXT NOT NULL DEFAULT '{}'",
            "best_vs_current_pnl_usd": "REAL",
            "best_vs_current_score": "REAL",
            "best_feasible_candidate_index": "INTEGER",
            "best_feasible_score": "REAL",
            "best_feasible_total_pnl_usd": "REAL",
            "best_feasible_max_drawdown_pct": "REAL",
        },
    )
    _ensure_table_columns(
        conn,
        "replay_search_candidates",
        {
            "feasible": "INTEGER NOT NULL DEFAULT 0",
            "is_current_policy": "INTEGER NOT NULL DEFAULT 0",
            "constraint_failures_json": "TEXT NOT NULL DEFAULT '[]'",
            "overrides_json": "TEXT NOT NULL DEFAULT '{}'",
            "policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "config_json": "TEXT NOT NULL DEFAULT '{}'",
            "result_json": "TEXT NOT NULL DEFAULT '{}'",
            "total_pnl_usd": "REAL NOT NULL DEFAULT 0",
            "max_drawdown_pct": "REAL",
            "accepted_count": "INTEGER NOT NULL DEFAULT 0",
            "resolved_count": "INTEGER NOT NULL DEFAULT 0",
            "win_rate": "REAL",
            "positive_window_count": "INTEGER NOT NULL DEFAULT 0",
            "negative_window_count": "INTEGER NOT NULL DEFAULT 0",
            "worst_window_pnl_usd": "REAL",
            "worst_window_drawdown_pct": "REAL",
            "window_pnl_stddev_usd": "REAL",
        },
    )


def _persist_search_results(
    *,
    db_path: Path | None,
    started_at: int,
    finished_at: int,
    label_prefix: str,
    notes: str,
    base_policy: ReplayPolicy,
    grid: dict[str, list[Any]],
    constraints: dict[str, Any],
    drawdown_penalty: float,
    window_stddev_penalty: float,
    worst_window_penalty: float,
    pause_guard_penalty: float,
    daily_guard_window_penalty: float,
    live_guard_window_penalty: float,
    daily_guard_restart_window_penalty: float,
    live_guard_restart_window_penalty: float,
    open_exposure_penalty: float,
    window_end_open_exposure_penalty: float,
    avg_window_end_open_exposure_penalty: float,
    carry_window_penalty: float,
    carry_restart_window_penalty: float,
    resolved_share_penalty: float,
    resolved_size_share_penalty: float,
    worst_window_resolved_share_penalty: float,
    worst_window_resolved_size_share_penalty: float,
    mode_resolved_share_penalty: float,
    mode_resolved_size_share_penalty: float,
    mode_worst_window_resolved_share_penalty: float,
    mode_worst_window_resolved_size_share_penalty: float,
    mode_active_window_accepted_share_penalty: float,
    mode_active_window_accepted_size_share_penalty: float,
    worst_active_window_accepted_penalty: float,
    worst_active_window_accepted_size_penalty: float,
    mode_worst_active_window_accepted_penalty: float,
    mode_worst_active_window_accepted_size_penalty: float,
    mode_loss_penalty: float,
    mode_inactivity_penalty: float,
    window_inactivity_penalty: float,
    wallet_count_penalty: float,
    market_count_penalty: float,
    entry_price_band_count_penalty: float,
    time_to_close_band_count_penalty: float,
    wallet_concentration_penalty: float,
    market_concentration_penalty: float,
    entry_price_band_concentration_penalty: float,
    time_to_close_band_concentration_penalty: float,
    wallet_size_concentration_penalty: float,
    market_size_concentration_penalty: float,
    entry_price_band_size_concentration_penalty: float,
    time_to_close_band_size_concentration_penalty: float,
    window_days: int,
    window_count: int,
    current_candidate: dict[str, Any] | None,
    persist_current_candidate: bool,
    ranked: list[dict[str, Any]],
    feasible: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> int:
    target_path = db_path or Path(TRADING_DB_PATH)
    conn = sqlite3.connect(str(target_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        _ensure_search_schema(conn)
        best_feasible = feasible[0] if feasible else None
        run_values = (
            started_at,
            finished_at,
            label_prefix,
            "completed",
            json.dumps(base_policy.as_dict(), sort_keys=True, separators=(",", ":")),
            json.dumps(grid, sort_keys=True, separators=(",", ":"), default=str),
            json.dumps(constraints, sort_keys=True, separators=(",", ":"), default=str),
            notes,
            window_days,
            window_count,
            drawdown_penalty,
            window_stddev_penalty,
            worst_window_penalty,
            pause_guard_penalty,
            daily_guard_window_penalty,
            live_guard_window_penalty,
            daily_guard_restart_window_penalty,
            live_guard_restart_window_penalty,
            open_exposure_penalty,
            window_end_open_exposure_penalty,
            avg_window_end_open_exposure_penalty,
            carry_window_penalty,
            carry_restart_window_penalty,
            resolved_share_penalty,
            resolved_size_share_penalty,
            worst_window_resolved_share_penalty,
            worst_window_resolved_size_share_penalty,
            mode_resolved_share_penalty,
            mode_resolved_size_share_penalty,
            mode_worst_window_resolved_share_penalty,
            mode_worst_window_resolved_size_share_penalty,
            mode_active_window_accepted_share_penalty,
            mode_active_window_accepted_size_share_penalty,
            worst_active_window_accepted_penalty,
            worst_active_window_accepted_size_penalty,
            mode_worst_active_window_accepted_penalty,
            mode_worst_active_window_accepted_size_penalty,
            mode_loss_penalty,
            mode_inactivity_penalty,
            window_inactivity_penalty,
            wallet_count_penalty,
            market_count_penalty,
            entry_price_band_count_penalty,
            time_to_close_band_count_penalty,
            wallet_concentration_penalty,
            market_concentration_penalty,
            entry_price_band_concentration_penalty,
            time_to_close_band_concentration_penalty,
            wallet_size_concentration_penalty,
            market_size_concentration_penalty,
            entry_price_band_size_concentration_penalty,
            time_to_close_band_size_concentration_penalty,
            len(ranked),
            len(feasible),
            len(rejected),
            float(current_candidate["score"]) if current_candidate else None,
            0 if current_candidate and current_candidate["constraint_failures"] else 1 if current_candidate else 0,
            float(current_candidate["result"].get("total_pnl_usd") or 0.0) if current_candidate else None,
            float(current_candidate["result"].get("max_drawdown_pct") or 0.0) if current_candidate else None,
            json.dumps(current_candidate["constraint_failures"], separators=(",", ":"), default=str) if current_candidate else "[]",
            json.dumps(current_candidate["result"], sort_keys=True, separators=(",", ":"), default=str) if current_candidate else "{}",
            (
                float(best_feasible["result"].get("total_pnl_usd") or 0.0)
                - float(current_candidate["result"].get("total_pnl_usd") or 0.0)
            ) if best_feasible and current_candidate else None,
            (
                float(best_feasible["score"]) - float(current_candidate["score"])
            ) if best_feasible and current_candidate else None,
            int(best_feasible["index"]) if best_feasible else None,
            float(best_feasible["score"]) if best_feasible else None,
            float(best_feasible["result"].get("total_pnl_usd") or 0.0) if best_feasible else None,
            float(best_feasible["result"].get("max_drawdown_pct") or 0.0) if best_feasible else None,
        )
        run_placeholders = ",".join("?" for _ in run_values)
        cursor = conn.execute(
            f"""
            INSERT INTO replay_search_runs (
                started_at, finished_at, label_prefix, status, base_policy_json, grid_json,
                constraints_json, notes, window_days, window_count, drawdown_penalty,
                window_stddev_penalty, worst_window_penalty, pause_guard_penalty, daily_guard_window_penalty, live_guard_window_penalty, daily_guard_restart_window_penalty, live_guard_restart_window_penalty, open_exposure_penalty, window_end_open_exposure_penalty, avg_window_end_open_exposure_penalty, carry_window_penalty, carry_restart_window_penalty, resolved_share_penalty, resolved_size_share_penalty, worst_window_resolved_share_penalty, worst_window_resolved_size_share_penalty, mode_resolved_share_penalty, mode_resolved_size_share_penalty, mode_worst_window_resolved_share_penalty, mode_worst_window_resolved_size_share_penalty, mode_active_window_accepted_share_penalty, mode_active_window_accepted_size_share_penalty, worst_active_window_accepted_penalty, worst_active_window_accepted_size_penalty, mode_worst_active_window_accepted_penalty, mode_worst_active_window_accepted_size_penalty, mode_loss_penalty, mode_inactivity_penalty, window_inactivity_penalty, wallet_count_penalty, market_count_penalty, entry_price_band_count_penalty, time_to_close_band_count_penalty, wallet_concentration_penalty, market_concentration_penalty, entry_price_band_concentration_penalty, time_to_close_band_concentration_penalty,
                wallet_size_concentration_penalty, market_size_concentration_penalty, entry_price_band_size_concentration_penalty, time_to_close_band_size_concentration_penalty,
                candidate_count, feasible_count, rejected_count, current_candidate_score, current_candidate_feasible,
                current_candidate_total_pnl_usd, current_candidate_max_drawdown_pct, current_candidate_constraint_failures_json, current_candidate_result_json,
                best_vs_current_pnl_usd, best_vs_current_score,
                best_feasible_candidate_index, best_feasible_score,
                best_feasible_total_pnl_usd, best_feasible_max_drawdown_pct
            ) VALUES ({run_placeholders})
            """,
            run_values,
        )
        search_run_id = int(cursor.lastrowid)
        inserts = []
        if current_candidate and persist_current_candidate:
            current_result = current_candidate["result"]
            inserts.append(
                (
                    search_run_id,
                    0,
                    float(current_candidate["score"]),
                    0 if current_candidate["constraint_failures"] else 1,
                    1,
                    json.dumps(current_candidate["constraint_failures"], separators=(",", ":"), default=str),
                    json.dumps({}, separators=(",", ":"), default=str),
                    json.dumps(current_candidate["policy"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(current_candidate["config"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(current_result, sort_keys=True, separators=(",", ":"), default=str),
                    float(current_result.get("total_pnl_usd") or 0.0),
                    float(current_result.get("max_drawdown_pct") or 0.0),
                    int(current_result.get("accepted_count") or 0),
                    int(current_result.get("resolved_count") or 0),
                    float(current_result.get("win_rate") or 0.0) if current_result.get("win_rate") is not None else None,
                    int(current_result.get("positive_window_count") or 0),
                    int(current_result.get("negative_window_count") or 0),
                    float(current_result.get("worst_window_pnl_usd") or 0.0) if current_result.get("worst_window_pnl_usd") is not None else None,
                    float(current_result.get("worst_window_drawdown_pct") or 0.0) if current_result.get("worst_window_drawdown_pct") is not None else None,
                    float(current_result.get("window_pnl_stddev_usd") or 0.0) if current_result.get("window_pnl_stddev_usd") is not None else None,
                )
            )
        for row in ranked:
            result = row["result"]
            inserts.append(
                (
                    search_run_id,
                    int(row["index"]),
                    float(row["score"]),
                    0 if row["constraint_failures"] else 1,
                    0,
                    json.dumps(row["constraint_failures"], separators=(",", ":"), default=str),
                    json.dumps(row["overrides"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(row["policy"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(row["config"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(result, sort_keys=True, separators=(",", ":"), default=str),
                    float(result.get("total_pnl_usd") or 0.0),
                    float(result.get("max_drawdown_pct") or 0.0),
                    int(result.get("accepted_count") or 0),
                    int(result.get("resolved_count") or 0),
                    float(result.get("win_rate") or 0.0) if result.get("win_rate") is not None else None,
                    int(result.get("positive_window_count") or 0),
                    int(result.get("negative_window_count") or 0),
                    float(result.get("worst_window_pnl_usd") or 0.0) if result.get("worst_window_pnl_usd") is not None else None,
                    float(result.get("worst_window_drawdown_pct") or 0.0) if result.get("worst_window_drawdown_pct") is not None else None,
                    float(result.get("window_pnl_stddev_usd") or 0.0) if result.get("window_pnl_stddev_usd") is not None else None,
                )
            )
        if inserts:
            conn.executemany(
                """
                INSERT INTO replay_search_candidates (
                    replay_search_run_id, candidate_index, score, feasible, is_current_policy,
                    constraint_failures_json, overrides_json, policy_json, config_json, result_json,
                    total_pnl_usd, max_drawdown_pct, accepted_count, resolved_count,
                    win_rate, positive_window_count, negative_window_count,
                    worst_window_pnl_usd, worst_window_drawdown_pct, window_pnl_stddev_usd
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                inserts,
            )
        conn.commit()
        return search_run_id
    finally:
        conn.close()


def main() -> None:
    started_at = int(time.time())
    parser = argparse.ArgumentParser(description="Run a replay policy sweep over a parameter grid.")
    parser.add_argument("--db", default="", help="Path to a trading.db snapshot. Defaults to the runtime DB.")
    parser.add_argument("--label-prefix", default="sweep", help="Label prefix stored with each replay run.")
    parser.add_argument("--notes", default="", help="Optional notes stored with each replay run.")
    parser.add_argument("--base-policy-file", default="", help="JSON file with base replay policy overrides.")
    parser.add_argument("--base-policy-json", default="", help="Inline JSON payload with base replay policy overrides.")
    parser.add_argument("--grid-file", default="", help="JSON file describing the parameter grid to sweep.")
    parser.add_argument("--grid-json", default="", help="Inline JSON object describing the parameter grid to sweep.")
    parser.add_argument("--top", type=int, default=10, help="How many ranked candidates to print in the stderr summary.")
    parser.add_argument(
        "--drawdown-penalty",
        type=float,
        default=1.0,
        help="Penalty multiplier applied to max drawdown in bankroll-dollar terms when ranking candidates.",
    )
    parser.add_argument("--window-stddev-penalty", type=float, default=0.0, help="Penalty per dollar of cross-window P&L standard deviation.")
    parser.add_argument("--worst-window-penalty", type=float, default=0.0, help="Penalty per dollar of worst-window loss magnitude.")
    parser.add_argument("--pause-guard-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay pause-guard reject share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--daily-guard-window-penalty", type=float, default=0.0, help="Penalty multiplier applied to the share of active replay windows that end with the daily-loss guard effectively triggered.")
    parser.add_argument("--live-guard-window-penalty", type=float, default=0.0, help="Penalty multiplier applied to the share of active live-mode replay windows that end with the live drawdown guard effectively triggered.")
    parser.add_argument("--daily-guard-restart-window-penalty", type=float, default=0.0, help="Penalty multiplier applied to the share of daily-guard restart opportunities that eventually resume on a later active replay window.")
    parser.add_argument("--live-guard-restart-window-penalty", type=float, default=0.0, help="Penalty multiplier applied to the share of live-guard restart opportunities that eventually resume on a later active replay window.")
    parser.add_argument("--open-exposure-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay peak open-exposure share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--window-end-open-exposure-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay window-end carried open-exposure share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--avg-window-end-open-exposure-penalty", type=float, default=0.0, help="Penalty multiplier applied to average active-window carried open-exposure share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--carry-window-penalty", type=float, default=0.0, help="Penalty multiplier applied to the share of active replay windows that end with carried open exposure in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--carry-restart-window-penalty", type=float, default=0.0, help="Penalty multiplier applied to the share of carry restart opportunities that eventually resume on a later active replay window.")
    parser.add_argument("--resolved-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to unresolved accepted-share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--resolved-size-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to unresolved accepted deployed-dollar share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--worst-window-resolved-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to unresolved-share in the worst replay window in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--worst-window-resolved-size-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to unresolved deployed-dollar share in the worst active replay window in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--mode-resolved-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to the worst enabled scorer unresolved-share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--mode-resolved-size-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to the worst enabled scorer unresolved deployed-dollar share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--mode-worst-window-resolved-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to the worst enabled scorer unresolved-share in its worst replay window in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--mode-worst-window-resolved-size-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to the worst enabled scorer unresolved deployed-dollar share in its worst active replay window in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--mode-active-window-accepted-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to the worst enabled scorer active-window accepted-trade mix imbalance in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--mode-active-window-accepted-size-share-penalty", type=float, default=0.0, help="Penalty multiplier applied to the worst enabled scorer active-window deployed-dollar mix imbalance in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--worst-active-window-accepted-penalty", type=float, default=0.0, help="Penalty multiplier applied to inverse accepted depth in the sparsest active replay window in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--worst-active-window-accepted-size-penalty", type=float, default=0.0, help="Penalty multiplier applied to deployed-dollar sparsity in the shallowest active replay window relative to the candidate's own average active-window size.")
    parser.add_argument("--mode-worst-active-window-accepted-penalty", type=float, default=0.0, help="Penalty multiplier applied to inverse accepted depth in the sparsest active enabled scorer window in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--mode-worst-active-window-accepted-size-penalty", type=float, default=0.0, help="Penalty multiplier applied to deployed-dollar sparsity in the shallowest active enabled scorer window relative to that scorer's own average active-window size.")
    parser.add_argument("--mode-loss-penalty", type=float, default=0.0, help="Penalty per replay dollar lost by any active scorer path when ranking candidates.")
    parser.add_argument("--mode-inactivity-penalty", type=float, default=0.0, help="Penalty multiplier applied to the worst enabled scorer inactive-window share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--window-inactivity-penalty", type=float, default=0.0, help="Penalty multiplier applied to global replay inactive-window share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--wallet-count-penalty", type=float, default=0.0, help="Penalty multiplier applied to inverse distinct-wallet breadth in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--market-count-penalty", type=float, default=0.0, help="Penalty multiplier applied to inverse distinct-market breadth in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--entry-price-band-count-penalty", type=float, default=0.0, help="Penalty multiplier applied to inverse distinct entry-price-band breadth in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--time-to-close-band-count-penalty", type=float, default=0.0, help="Penalty multiplier applied to inverse distinct time-to-close-band breadth in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--wallet-concentration-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay wallet concentration share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--market-concentration-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay market concentration share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--entry-price-band-concentration-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay entry-price-band concentration share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--time-to-close-band-concentration-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay time-to-close-band concentration share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--wallet-size-concentration-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay wallet deployed-dollar concentration share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--market-size-concentration-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay market deployed-dollar concentration share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--entry-price-band-size-concentration-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay entry-price-band deployed-dollar concentration share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--time-to-close-band-size-concentration-penalty", type=float, default=0.0, help="Penalty multiplier applied to replay time-to-close-band deployed-dollar concentration share in bankroll-dollar terms when ranking candidates.")
    parser.add_argument("--max-combos", type=int, default=256, help="Safety cap on total grid combinations.")
    parser.add_argument("--window-days", type=int, default=0, help="Replay over rolling windows of this many days instead of the full history.")
    parser.add_argument("--window-count", type=int, default=1, help="How many most-recent rolling windows to evaluate when --window-days is set.")
    parser.add_argument("--min-positive-windows", type=int, default=0, help="Minimum count of positive-P&L windows required for feasibility.")
    parser.add_argument("--min-active-windows", type=int, default=0, help="Minimum count of active replay windows required for a candidate to be feasible.")
    parser.add_argument("--max-inactive-windows", type=int, default=-1, help="Maximum count of inactive replay windows allowed before a candidate is rejected.")
    parser.add_argument("--min-worst-active-window-accepted-count", type=int, default=0, help="Minimum accepted-trade count required in the sparsest active replay window.")
    parser.add_argument("--min-worst-active-window-accepted-size-usd", type=float, default=0.0, help="Minimum accepted deployed dollars required in the shallowest active replay window.")
    parser.add_argument("--min-accepted-count", type=int, default=0, help="Minimum accepted trades required for a candidate to be feasible.")
    parser.add_argument("--min-resolved-count", type=int, default=0, help="Minimum resolved trades required for a candidate to be feasible.")
    parser.add_argument("--min-resolved-share", type=float, default=0.0, help="Minimum fraction of accepted replay trades that must be resolved.")
    parser.add_argument("--min-resolved-size-share", type=float, default=0.0, help="Minimum fraction of accepted replay deployed dollars that must be resolved.")
    parser.add_argument("--min-win-rate", type=float, default=0.0, help="Minimum replay win rate required for a candidate to be feasible.")
    parser.add_argument("--min-total-pnl-usd", type=float, default=-1_000_000_000.0, help="Minimum total replay P&L required for a candidate to be feasible.")
    parser.add_argument("--max-drawdown-pct", type=float, default=0.0, help="Maximum replay drawdown allowed for a candidate to be feasible.")
    parser.add_argument("--max-open-exposure-share", type=float, default=0.0, help="Maximum open-exposure share of replay equity allowed at any point during the replay.")
    parser.add_argument("--max-window-end-open-exposure-share", type=float, default=0.0, help="Maximum carried open-exposure share allowed at the end of any replay window.")
    parser.add_argument("--max-carry-window-share", type=float, default=0.0, help="Maximum share of active replay windows allowed to end with carried open exposure.")
    parser.add_argument("--min-worst-window-pnl-usd", type=float, default=-1_000_000_000.0, help="Minimum allowed P&L for the worst replay window.")
    parser.add_argument("--min-worst-window-resolved-share", type=float, default=0.0, help="Minimum resolved-share required for the worst replay window.")
    parser.add_argument("--min-worst-window-resolved-size-share", type=float, default=0.0, help="Minimum deployed-dollar resolved-share required for the worst active replay window.")
    parser.add_argument("--max-worst-window-drawdown-pct", type=float, default=0.0, help="Maximum allowed drawdown for the worst replay window.")
    parser.add_argument("--min-heuristic-accepted-count", type=int, default=0, help="Minimum accepted heuristic trades required for a candidate to be feasible.")
    parser.add_argument("--min-xgboost-accepted-count", type=int, default=0, help="Minimum accepted xgboost trades required for a candidate to be feasible.")
    parser.add_argument("--min-heuristic-resolved-count", type=int, default=0, help="Minimum resolved heuristic trades required for a candidate to be feasible.")
    parser.add_argument("--min-xgboost-resolved-count", type=int, default=0, help="Minimum resolved xgboost trades required for a candidate to be feasible.")
    parser.add_argument("--min-heuristic-win-rate", type=float, default=0.0, help="Minimum heuristic win rate required for a candidate to be feasible.")
    parser.add_argument("--min-xgboost-win-rate", type=float, default=0.0, help="Minimum xgboost win rate required for a candidate to be feasible.")
    parser.add_argument("--min-heuristic-resolved-share", type=float, default=0.0, help="Minimum fraction of accepted heuristic trades that must be resolved.")
    parser.add_argument("--min-xgboost-resolved-share", type=float, default=0.0, help="Minimum fraction of accepted xgboost trades that must be resolved.")
    parser.add_argument("--min-heuristic-resolved-size-share", type=float, default=0.0, help="Minimum fraction of accepted heuristic deployed dollars that must be resolved.")
    parser.add_argument("--min-xgboost-resolved-size-share", type=float, default=0.0, help="Minimum fraction of accepted xgboost deployed dollars that must be resolved.")
    parser.add_argument("--min-heuristic-pnl-usd", type=float, default=0.0, help="Minimum replay P&L contribution required from heuristic trades.")
    parser.add_argument("--min-xgboost-pnl-usd", type=float, default=0.0, help="Minimum replay P&L contribution required from xgboost trades.")
    parser.add_argument("--min-heuristic-worst-window-pnl-usd", type=float, default=-1_000_000_000.0, help="Minimum allowed heuristic P&L in the worst replay window for that scorer path.")
    parser.add_argument("--min-xgboost-worst-window-pnl-usd", type=float, default=-1_000_000_000.0, help="Minimum allowed xgboost P&L in the worst replay window for that scorer path.")
    parser.add_argument("--min-heuristic-worst-window-resolved-share", type=float, default=0.0, help="Minimum resolved-share required for heuristic in its worst replay window.")
    parser.add_argument("--min-xgboost-worst-window-resolved-share", type=float, default=0.0, help="Minimum resolved-share required for xgboost in its worst replay window.")
    parser.add_argument("--min-heuristic-worst-window-resolved-size-share", type=float, default=0.0, help="Minimum deployed-dollar resolved-share required for heuristic in its worst active replay window.")
    parser.add_argument("--min-xgboost-worst-window-resolved-size-share", type=float, default=0.0, help="Minimum deployed-dollar resolved-share required for xgboost in its worst active replay window.")
    parser.add_argument("--min-heuristic-positive-windows", type=int, default=0, help="Minimum count of positive replay windows required from heuristic.")
    parser.add_argument("--min-xgboost-positive-windows", type=int, default=0, help="Minimum count of positive replay windows required from xgboost.")
    parser.add_argument("--min-heuristic-worst-active-window-accepted-count", type=int, default=0, help="Minimum accepted-trade count required in heuristic's sparsest active replay window.")
    parser.add_argument("--min-xgboost-worst-active-window-accepted-count", type=int, default=0, help="Minimum accepted-trade count required in xgboost's sparsest active replay window.")
    parser.add_argument("--min-heuristic-worst-active-window-accepted-size-usd", type=float, default=0.0, help="Minimum accepted deployed dollars required in heuristic's shallowest active replay window.")
    parser.add_argument("--min-xgboost-worst-active-window-accepted-size-usd", type=float, default=0.0, help="Minimum accepted deployed dollars required in xgboost's shallowest active replay window.")
    parser.add_argument("--max-heuristic-inactive-windows", type=int, default=-1, help="Maximum count of replay windows where heuristic may be inactive before a candidate is rejected.")
    parser.add_argument("--max-xgboost-inactive-windows", type=int, default=-1, help="Maximum count of replay windows where xgboost may be inactive before a candidate is rejected.")
    parser.add_argument("--max-heuristic-accepted-share", type=float, default=0.0, help="Maximum fraction of accepted replay trades allowed to come from heuristic.")
    parser.add_argument("--max-heuristic-accepted-size-share", type=float, default=0.0, help="Maximum fraction of accepted replay deployed dollars allowed to come from heuristic.")
    parser.add_argument("--min-xgboost-accepted-share", type=float, default=0.0, help="Minimum fraction of accepted replay trades required to come from xgboost.")
    parser.add_argument("--min-xgboost-accepted-size-share", type=float, default=0.0, help="Minimum fraction of accepted replay deployed dollars required to come from xgboost.")
    parser.add_argument("--max-heuristic-active-window-accepted-share", type=float, default=0.0, help="Maximum heuristic accepted-trade share allowed in any active replay window.")
    parser.add_argument("--max-heuristic-active-window-accepted-size-share", type=float, default=0.0, help="Maximum heuristic deployed-dollar share allowed in any active replay window.")
    parser.add_argument("--min-xgboost-active-window-accepted-share", type=float, default=0.0, help="Minimum xgboost accepted-trade share required in each active replay window.")
    parser.add_argument("--min-xgboost-active-window-accepted-size-share", type=float, default=0.0, help="Minimum xgboost deployed-dollar share required in each active replay window.")
    parser.add_argument("--max-pause-guard-reject-share", type=float, default=0.0, help="Maximum fraction of replay trades allowed to be rejected by daily-loss or live-drawdown pause guards.")
    parser.add_argument("--max-daily-guard-window-share", type=float, default=0.0, help="Maximum share of active replay windows allowed to end with the daily-loss guard effectively triggered.")
    parser.add_argument("--max-live-guard-window-share", type=float, default=0.0, help="Maximum share of active live-mode replay windows allowed to end with the live drawdown guard effectively triggered.")
    parser.add_argument("--max-daily-guard-restart-window-share", type=float, default=0.0, help="Maximum share of daily-guard restart opportunities allowed to resume on a later active replay window.")
    parser.add_argument("--max-live-guard-restart-window-share", type=float, default=0.0, help="Maximum share of live-guard restart opportunities allowed to resume on a later active replay window.")
    parser.add_argument("--max-avg-window-end-open-exposure-share", type=float, default=0.0, help="Maximum average share of equity left open at the end of active replay windows.")
    parser.add_argument("--max-carry-restart-window-share", type=float, default=0.0, help="Maximum share of carry restart opportunities allowed to resume on a later active replay window.")
    parser.add_argument("--min-trader-count", type=int, default=0, help="Minimum distinct trader count required for a candidate to be feasible.")
    parser.add_argument("--min-market-count", type=int, default=0, help="Minimum distinct market count required for a candidate to be feasible.")
    parser.add_argument("--min-entry-price-band-count", type=int, default=0, help="Minimum distinct entry-price-band count required for a candidate to be feasible.")
    parser.add_argument("--min-time-to-close-band-count", type=int, default=0, help="Minimum distinct time-to-close-band count required for a candidate to be feasible.")
    parser.add_argument("--max-top-trader-accepted-share", type=float, default=0.0, help="Maximum fraction of accepted replay trades allowed to come from a single trader.")
    parser.add_argument("--max-top-trader-abs-pnl-share", type=float, default=0.0, help="Maximum fraction of absolute replay P&L allowed to come from a single trader.")
    parser.add_argument("--max-top-trader-size-share", type=float, default=0.0, help="Maximum fraction of deployed replay dollars allowed to come from a single trader.")
    parser.add_argument("--max-top-market-accepted-share", type=float, default=0.0, help="Maximum fraction of accepted replay trades allowed to come from a single market.")
    parser.add_argument("--max-top-market-abs-pnl-share", type=float, default=0.0, help="Maximum fraction of absolute replay P&L allowed to come from a single market.")
    parser.add_argument("--max-top-market-size-share", type=float, default=0.0, help="Maximum fraction of deployed replay dollars allowed to come from a single market.")
    parser.add_argument("--max-top-entry-price-band-accepted-share", type=float, default=0.0, help="Maximum fraction of accepted replay trades allowed to come from a single entry-price band.")
    parser.add_argument("--max-top-entry-price-band-abs-pnl-share", type=float, default=0.0, help="Maximum fraction of absolute replay P&L allowed to come from a single entry-price band.")
    parser.add_argument("--max-top-entry-price-band-size-share", type=float, default=0.0, help="Maximum fraction of deployed replay dollars allowed to come from a single entry-price band.")
    parser.add_argument("--max-top-time-to-close-band-accepted-share", type=float, default=0.0, help="Maximum fraction of accepted replay trades allowed to come from a single time-to-close band.")
    parser.add_argument("--max-top-time-to-close-band-abs-pnl-share", type=float, default=0.0, help="Maximum fraction of absolute replay P&L allowed to come from a single time-to-close band.")
    parser.add_argument("--max-top-time-to-close-band-size-share", type=float, default=0.0, help="Maximum fraction of deployed replay dollars allowed to come from a single time-to-close band.")
    args = parser.parse_args()

    base_policy = _load_base_policy(args)
    grid = _load_grid(args)
    overrides_list = _iter_policy_overrides(grid)
    if len(overrides_list) > max(args.max_combos, 1):
        raise ValueError(f"Grid expands to {len(overrides_list)} combinations, above --max-combos={args.max_combos}")

    db_path = _resolve_db_path(args.db)
    windows = _build_time_windows(
        db_path=db_path,
        mode=base_policy.mode,
        window_days=max(args.window_days, 0),
        window_count=max(args.window_count, 1),
    )
    current_result = _evaluate_candidate(
        policy=base_policy,
        db_path=db_path,
        label=f"{args.label_prefix}-current",
        notes=args.notes,
        windows=windows,
    )
    current_result = _with_score_breakdown(
        current_result,
        initial_bankroll_usd=base_policy.initial_bankroll_usd,
        drawdown_penalty=max(args.drawdown_penalty, 0.0),
        window_stddev_penalty=max(args.window_stddev_penalty, 0.0),
        worst_window_penalty=max(args.worst_window_penalty, 0.0),
        pause_guard_penalty=max(args.pause_guard_penalty, 0.0),
        daily_guard_window_penalty=max(args.daily_guard_window_penalty, 0.0),
        live_guard_window_penalty=max(args.live_guard_window_penalty, 0.0),
        daily_guard_restart_window_penalty=max(args.daily_guard_restart_window_penalty, 0.0),
        live_guard_restart_window_penalty=max(args.live_guard_restart_window_penalty, 0.0),
        open_exposure_penalty=max(args.open_exposure_penalty, 0.0),
        window_end_open_exposure_penalty=max(args.window_end_open_exposure_penalty, 0.0),
        avg_window_end_open_exposure_penalty=max(args.avg_window_end_open_exposure_penalty, 0.0),
        carry_window_penalty=max(args.carry_window_penalty, 0.0),
        carry_restart_window_penalty=max(args.carry_restart_window_penalty, 0.0),
        resolved_share_penalty=max(args.resolved_share_penalty, 0.0),
        resolved_size_share_penalty=max(args.resolved_size_share_penalty, 0.0),
        worst_window_resolved_share_penalty=max(args.worst_window_resolved_share_penalty, 0.0),
        worst_window_resolved_size_share_penalty=max(args.worst_window_resolved_size_share_penalty, 0.0),
        mode_resolved_share_penalty=max(args.mode_resolved_share_penalty, 0.0),
        mode_resolved_size_share_penalty=max(args.mode_resolved_size_share_penalty, 0.0),
        mode_worst_window_resolved_share_penalty=max(args.mode_worst_window_resolved_share_penalty, 0.0),
        mode_worst_window_resolved_size_share_penalty=max(args.mode_worst_window_resolved_size_share_penalty, 0.0),
        mode_active_window_accepted_share_penalty=max(args.mode_active_window_accepted_share_penalty, 0.0),
        mode_active_window_accepted_size_share_penalty=max(args.mode_active_window_accepted_size_share_penalty, 0.0),
        worst_active_window_accepted_penalty=max(args.worst_active_window_accepted_penalty, 0.0),
        worst_active_window_accepted_size_penalty=max(args.worst_active_window_accepted_size_penalty, 0.0),
        mode_worst_active_window_accepted_penalty=max(args.mode_worst_active_window_accepted_penalty, 0.0),
        mode_worst_active_window_accepted_size_penalty=max(args.mode_worst_active_window_accepted_size_penalty, 0.0),
        mode_loss_penalty=max(args.mode_loss_penalty, 0.0),
        mode_inactivity_penalty=max(args.mode_inactivity_penalty, 0.0),
        window_inactivity_penalty=max(args.window_inactivity_penalty, 0.0),
        wallet_count_penalty=max(args.wallet_count_penalty, 0.0),
        market_count_penalty=max(args.market_count_penalty, 0.0),
        entry_price_band_count_penalty=max(args.entry_price_band_count_penalty, 0.0),
        time_to_close_band_count_penalty=max(args.time_to_close_band_count_penalty, 0.0),
        wallet_size_concentration_penalty=max(args.wallet_size_concentration_penalty, 0.0),
        market_size_concentration_penalty=max(args.market_size_concentration_penalty, 0.0),
        entry_price_band_size_concentration_penalty=max(args.entry_price_band_size_concentration_penalty, 0.0),
        time_to_close_band_size_concentration_penalty=max(args.time_to_close_band_size_concentration_penalty, 0.0),
        allow_heuristic=bool(base_policy.allow_heuristic),
        allow_xgboost=bool(base_policy.allow_xgboost),
        wallet_concentration_penalty=max(args.wallet_concentration_penalty, 0.0),
        market_concentration_penalty=max(args.market_concentration_penalty, 0.0),
        entry_price_band_concentration_penalty=max(args.entry_price_band_concentration_penalty, 0.0),
        time_to_close_band_concentration_penalty=max(args.time_to_close_band_concentration_penalty, 0.0),
    )
    current_constraint_failures = _constraint_failures(
        current_result,
        allow_heuristic=bool(base_policy.allow_heuristic),
        allow_xgboost=bool(base_policy.allow_xgboost),
        min_accepted_count=args.min_accepted_count,
        min_resolved_count=args.min_resolved_count,
        min_resolved_share=_clamp_fraction(args.min_resolved_share),
        min_resolved_size_share=_clamp_fraction(args.min_resolved_size_share),
        min_win_rate=max(args.min_win_rate, 0.0),
        min_total_pnl_usd=float(args.min_total_pnl_usd),
        max_drawdown_pct=max(args.max_drawdown_pct, 0.0),
        max_open_exposure_share=_clamp_fraction(args.max_open_exposure_share),
        max_window_end_open_exposure_share=_clamp_fraction(args.max_window_end_open_exposure_share),
        max_avg_window_end_open_exposure_share=_clamp_fraction(args.max_avg_window_end_open_exposure_share),
        max_carry_window_share=_clamp_fraction(args.max_carry_window_share),
        max_carry_restart_window_share=_clamp_fraction(args.max_carry_restart_window_share),
        max_live_guard_window_share=_clamp_fraction(args.max_live_guard_window_share),
        min_worst_window_pnl_usd=args.min_worst_window_pnl_usd,
        min_worst_window_resolved_share=_clamp_fraction(args.min_worst_window_resolved_share),
        min_worst_window_resolved_size_share=_clamp_fraction(args.min_worst_window_resolved_size_share),
        max_worst_window_drawdown_pct=max(args.max_worst_window_drawdown_pct, 0.0),
        min_heuristic_accepted_count=max(args.min_heuristic_accepted_count, 0),
        min_xgboost_accepted_count=max(args.min_xgboost_accepted_count, 0),
        min_heuristic_resolved_count=max(args.min_heuristic_resolved_count, 0),
        min_xgboost_resolved_count=max(args.min_xgboost_resolved_count, 0),
        min_heuristic_win_rate=_clamp_fraction(args.min_heuristic_win_rate),
        min_xgboost_win_rate=_clamp_fraction(args.min_xgboost_win_rate),
        min_heuristic_resolved_share=_clamp_fraction(args.min_heuristic_resolved_share),
        min_xgboost_resolved_share=_clamp_fraction(args.min_xgboost_resolved_share),
        min_heuristic_resolved_size_share=_clamp_fraction(args.min_heuristic_resolved_size_share),
        min_xgboost_resolved_size_share=_clamp_fraction(args.min_xgboost_resolved_size_share),
        min_heuristic_pnl_usd=float(args.min_heuristic_pnl_usd),
        min_xgboost_pnl_usd=float(args.min_xgboost_pnl_usd),
        min_heuristic_worst_window_pnl_usd=float(args.min_heuristic_worst_window_pnl_usd),
        min_xgboost_worst_window_pnl_usd=float(args.min_xgboost_worst_window_pnl_usd),
        min_heuristic_worst_window_resolved_share=_clamp_fraction(args.min_heuristic_worst_window_resolved_share),
        min_xgboost_worst_window_resolved_share=_clamp_fraction(args.min_xgboost_worst_window_resolved_share),
        min_heuristic_worst_window_resolved_size_share=_clamp_fraction(args.min_heuristic_worst_window_resolved_size_share),
        min_xgboost_worst_window_resolved_size_share=_clamp_fraction(args.min_xgboost_worst_window_resolved_size_share),
        min_heuristic_positive_window_count=max(args.min_heuristic_positive_windows, 0),
        min_xgboost_positive_window_count=max(args.min_xgboost_positive_windows, 0),
        min_heuristic_worst_active_window_accepted_count=max(args.min_heuristic_worst_active_window_accepted_count, 0),
        min_heuristic_worst_active_window_accepted_size_usd=max(args.min_heuristic_worst_active_window_accepted_size_usd, 0.0),
        min_xgboost_worst_active_window_accepted_count=max(args.min_xgboost_worst_active_window_accepted_count, 0),
        min_xgboost_worst_active_window_accepted_size_usd=max(args.min_xgboost_worst_active_window_accepted_size_usd, 0.0),
        max_heuristic_inactive_window_count=int(args.max_heuristic_inactive_windows),
        max_xgboost_inactive_window_count=int(args.max_xgboost_inactive_windows),
        max_heuristic_accepted_share=_clamp_fraction(args.max_heuristic_accepted_share),
        max_heuristic_accepted_size_share=_clamp_fraction(args.max_heuristic_accepted_size_share),
        max_heuristic_active_window_accepted_share=_clamp_fraction(args.max_heuristic_active_window_accepted_share),
        max_heuristic_active_window_accepted_size_share=_clamp_fraction(args.max_heuristic_active_window_accepted_size_share),
        min_xgboost_accepted_share=_clamp_fraction(args.min_xgboost_accepted_share),
        min_xgboost_accepted_size_share=_clamp_fraction(args.min_xgboost_accepted_size_share),
        min_xgboost_active_window_accepted_share=_clamp_fraction(args.min_xgboost_active_window_accepted_share),
        min_xgboost_active_window_accepted_size_share=_clamp_fraction(args.min_xgboost_active_window_accepted_size_share),
        max_pause_guard_reject_share=_clamp_fraction(args.max_pause_guard_reject_share),
        max_daily_guard_window_share=_clamp_fraction(args.max_daily_guard_window_share),
        max_daily_guard_restart_window_share=_clamp_fraction(args.max_daily_guard_restart_window_share),
        min_active_window_count=max(args.min_active_windows, 0),
        max_inactive_window_count=int(args.max_inactive_windows),
        min_worst_active_window_accepted_count=max(args.min_worst_active_window_accepted_count, 0),
        min_worst_active_window_accepted_size_usd=max(args.min_worst_active_window_accepted_size_usd, 0.0),
        min_trader_count=max(args.min_trader_count, 0),
        min_market_count=max(args.min_market_count, 0),
        min_entry_price_band_count=max(args.min_entry_price_band_count, 0),
        min_time_to_close_band_count=max(args.min_time_to_close_band_count, 0),
        max_top_trader_accepted_share=_clamp_fraction(args.max_top_trader_accepted_share),
        max_top_trader_abs_pnl_share=_clamp_fraction(args.max_top_trader_abs_pnl_share),
        max_top_trader_size_share=_clamp_fraction(args.max_top_trader_size_share),
        max_top_market_accepted_share=_clamp_fraction(args.max_top_market_accepted_share),
        max_top_market_abs_pnl_share=_clamp_fraction(args.max_top_market_abs_pnl_share),
        max_top_market_size_share=_clamp_fraction(args.max_top_market_size_share),
        max_top_entry_price_band_accepted_share=_clamp_fraction(args.max_top_entry_price_band_accepted_share),
        max_top_entry_price_band_abs_pnl_share=_clamp_fraction(args.max_top_entry_price_band_abs_pnl_share),
        max_top_entry_price_band_size_share=_clamp_fraction(args.max_top_entry_price_band_size_share),
        max_top_time_to_close_band_accepted_share=_clamp_fraction(args.max_top_time_to_close_band_accepted_share),
        max_top_time_to_close_band_abs_pnl_share=_clamp_fraction(args.max_top_time_to_close_band_abs_pnl_share),
        max_top_time_to_close_band_size_share=_clamp_fraction(args.max_top_time_to_close_band_size_share),
        max_live_guard_restart_window_share=_clamp_fraction(args.max_live_guard_restart_window_share),
    )
    if int(current_result.get("positive_window_count") or 0) < max(args.min_positive_windows, 0):
        current_constraint_failures.append("positive_window_count")
    current_candidate = {
        "index": 0,
        "score": round(
            _score_result(
                current_result,
                initial_bankroll_usd=base_policy.initial_bankroll_usd,
                drawdown_penalty=max(args.drawdown_penalty, 0.0),
                window_stddev_penalty=max(args.window_stddev_penalty, 0.0),
                worst_window_penalty=max(args.worst_window_penalty, 0.0),
                pause_guard_penalty=max(args.pause_guard_penalty, 0.0),
                daily_guard_window_penalty=max(args.daily_guard_window_penalty, 0.0),
                live_guard_window_penalty=max(args.live_guard_window_penalty, 0.0),
                daily_guard_restart_window_penalty=max(args.daily_guard_restart_window_penalty, 0.0),
                live_guard_restart_window_penalty=max(args.live_guard_restart_window_penalty, 0.0),
                open_exposure_penalty=max(args.open_exposure_penalty, 0.0),
                window_end_open_exposure_penalty=max(args.window_end_open_exposure_penalty, 0.0),
                avg_window_end_open_exposure_penalty=max(args.avg_window_end_open_exposure_penalty, 0.0),
                carry_window_penalty=max(args.carry_window_penalty, 0.0),
                carry_restart_window_penalty=max(args.carry_restart_window_penalty, 0.0),
                resolved_share_penalty=max(args.resolved_share_penalty, 0.0),
                resolved_size_share_penalty=max(args.resolved_size_share_penalty, 0.0),
                worst_window_resolved_share_penalty=max(args.worst_window_resolved_share_penalty, 0.0),
                worst_window_resolved_size_share_penalty=max(args.worst_window_resolved_size_share_penalty, 0.0),
                mode_resolved_share_penalty=max(args.mode_resolved_share_penalty, 0.0),
                mode_resolved_size_share_penalty=max(args.mode_resolved_size_share_penalty, 0.0),
                mode_worst_window_resolved_share_penalty=max(args.mode_worst_window_resolved_share_penalty, 0.0),
                mode_worst_window_resolved_size_share_penalty=max(args.mode_worst_window_resolved_size_share_penalty, 0.0),
                mode_active_window_accepted_share_penalty=max(args.mode_active_window_accepted_share_penalty, 0.0),
                mode_active_window_accepted_size_share_penalty=max(args.mode_active_window_accepted_size_share_penalty, 0.0),
                worst_active_window_accepted_penalty=max(args.worst_active_window_accepted_penalty, 0.0),
                worst_active_window_accepted_size_penalty=max(args.worst_active_window_accepted_size_penalty, 0.0),
                mode_worst_active_window_accepted_penalty=max(args.mode_worst_active_window_accepted_penalty, 0.0),
                mode_worst_active_window_accepted_size_penalty=max(args.mode_worst_active_window_accepted_size_penalty, 0.0),
                mode_loss_penalty=max(args.mode_loss_penalty, 0.0),
                mode_inactivity_penalty=max(args.mode_inactivity_penalty, 0.0),
                window_inactivity_penalty=max(args.window_inactivity_penalty, 0.0),
                wallet_count_penalty=max(args.wallet_count_penalty, 0.0),
                market_count_penalty=max(args.market_count_penalty, 0.0),
                entry_price_band_count_penalty=max(args.entry_price_band_count_penalty, 0.0),
                time_to_close_band_count_penalty=max(args.time_to_close_band_count_penalty, 0.0),
                wallet_size_concentration_penalty=max(args.wallet_size_concentration_penalty, 0.0),
                market_size_concentration_penalty=max(args.market_size_concentration_penalty, 0.0),
                entry_price_band_size_concentration_penalty=max(args.entry_price_band_size_concentration_penalty, 0.0),
                time_to_close_band_size_concentration_penalty=max(args.time_to_close_band_size_concentration_penalty, 0.0),
                allow_heuristic=bool(base_policy.allow_heuristic),
                allow_xgboost=bool(base_policy.allow_xgboost),
                wallet_concentration_penalty=max(args.wallet_concentration_penalty, 0.0),
                market_concentration_penalty=max(args.market_concentration_penalty, 0.0),
                entry_price_band_concentration_penalty=max(args.entry_price_band_concentration_penalty, 0.0),
                time_to_close_band_concentration_penalty=max(args.time_to_close_band_concentration_penalty, 0.0),
            ),
            6,
        ),
        "overrides": {},
        "policy": base_policy.as_dict(),
        "config": policy_to_config_payload(base_policy),
        "result": current_result,
        "constraint_failures": current_constraint_failures,
        "is_current_policy": True,
        "policy_version": base_policy.version(),
    }
    candidates: list[dict[str, Any]] = []
    for index, overrides in enumerate(overrides_list, start=1):
        policy_payload = base_policy.as_dict()
        policy_payload.update(overrides)
        policy = ReplayPolicy.from_payload(policy_payload)
        policy_version = policy.version()
        result = current_result if policy_version == current_candidate["policy_version"] else _evaluate_candidate(
            policy=policy,
            db_path=db_path,
            label=f"{args.label_prefix}-{index:03d}",
            notes=args.notes,
            windows=windows,
        )
        if "score_breakdown" not in result:
            result = _with_score_breakdown(
                result,
                initial_bankroll_usd=policy.initial_bankroll_usd,
                drawdown_penalty=max(args.drawdown_penalty, 0.0),
                window_stddev_penalty=max(args.window_stddev_penalty, 0.0),
                worst_window_penalty=max(args.worst_window_penalty, 0.0),
                pause_guard_penalty=max(args.pause_guard_penalty, 0.0),
                daily_guard_window_penalty=max(args.daily_guard_window_penalty, 0.0),
                live_guard_window_penalty=max(args.live_guard_window_penalty, 0.0),
                daily_guard_restart_window_penalty=max(args.daily_guard_restart_window_penalty, 0.0),
                live_guard_restart_window_penalty=max(args.live_guard_restart_window_penalty, 0.0),
                open_exposure_penalty=max(args.open_exposure_penalty, 0.0),
                window_end_open_exposure_penalty=max(args.window_end_open_exposure_penalty, 0.0),
                avg_window_end_open_exposure_penalty=max(args.avg_window_end_open_exposure_penalty, 0.0),
                carry_window_penalty=max(args.carry_window_penalty, 0.0),
                resolved_share_penalty=max(args.resolved_share_penalty, 0.0),
                resolved_size_share_penalty=max(args.resolved_size_share_penalty, 0.0),
                worst_window_resolved_share_penalty=max(args.worst_window_resolved_share_penalty, 0.0),
                worst_window_resolved_size_share_penalty=max(args.worst_window_resolved_size_share_penalty, 0.0),
                mode_resolved_share_penalty=max(args.mode_resolved_share_penalty, 0.0),
                mode_resolved_size_share_penalty=max(args.mode_resolved_size_share_penalty, 0.0),
                mode_worst_window_resolved_share_penalty=max(args.mode_worst_window_resolved_share_penalty, 0.0),
                mode_worst_window_resolved_size_share_penalty=max(args.mode_worst_window_resolved_size_share_penalty, 0.0),
                mode_active_window_accepted_share_penalty=max(args.mode_active_window_accepted_share_penalty, 0.0),
                mode_active_window_accepted_size_share_penalty=max(args.mode_active_window_accepted_size_share_penalty, 0.0),
                worst_active_window_accepted_penalty=max(args.worst_active_window_accepted_penalty, 0.0),
                worst_active_window_accepted_size_penalty=max(args.worst_active_window_accepted_size_penalty, 0.0),
                mode_worst_active_window_accepted_penalty=max(args.mode_worst_active_window_accepted_penalty, 0.0),
                mode_worst_active_window_accepted_size_penalty=max(args.mode_worst_active_window_accepted_size_penalty, 0.0),
                mode_loss_penalty=max(args.mode_loss_penalty, 0.0),
                mode_inactivity_penalty=max(args.mode_inactivity_penalty, 0.0),
                window_inactivity_penalty=max(args.window_inactivity_penalty, 0.0),
                wallet_count_penalty=max(args.wallet_count_penalty, 0.0),
                market_count_penalty=max(args.market_count_penalty, 0.0),
                entry_price_band_count_penalty=max(args.entry_price_band_count_penalty, 0.0),
                time_to_close_band_count_penalty=max(args.time_to_close_band_count_penalty, 0.0),
                wallet_size_concentration_penalty=max(args.wallet_size_concentration_penalty, 0.0),
                market_size_concentration_penalty=max(args.market_size_concentration_penalty, 0.0),
                entry_price_band_size_concentration_penalty=max(args.entry_price_band_size_concentration_penalty, 0.0),
                time_to_close_band_size_concentration_penalty=max(args.time_to_close_band_size_concentration_penalty, 0.0),
                allow_heuristic=bool(policy.allow_heuristic),
                allow_xgboost=bool(policy.allow_xgboost),
                wallet_concentration_penalty=max(args.wallet_concentration_penalty, 0.0),
                market_concentration_penalty=max(args.market_concentration_penalty, 0.0),
                entry_price_band_concentration_penalty=max(args.entry_price_band_concentration_penalty, 0.0),
                time_to_close_band_concentration_penalty=max(args.time_to_close_band_concentration_penalty, 0.0),
            )
        score = _score_result(
            result,
            initial_bankroll_usd=policy.initial_bankroll_usd,
            drawdown_penalty=max(args.drawdown_penalty, 0.0),
            window_stddev_penalty=max(args.window_stddev_penalty, 0.0),
            worst_window_penalty=max(args.worst_window_penalty, 0.0),
            pause_guard_penalty=max(args.pause_guard_penalty, 0.0),
            daily_guard_window_penalty=max(args.daily_guard_window_penalty, 0.0),
            live_guard_window_penalty=max(args.live_guard_window_penalty, 0.0),
            daily_guard_restart_window_penalty=max(args.daily_guard_restart_window_penalty, 0.0),
            live_guard_restart_window_penalty=max(args.live_guard_restart_window_penalty, 0.0),
            open_exposure_penalty=max(args.open_exposure_penalty, 0.0),
            window_end_open_exposure_penalty=max(args.window_end_open_exposure_penalty, 0.0),
            avg_window_end_open_exposure_penalty=max(args.avg_window_end_open_exposure_penalty, 0.0),
            carry_window_penalty=max(args.carry_window_penalty, 0.0),
            carry_restart_window_penalty=max(args.carry_restart_window_penalty, 0.0),
            resolved_share_penalty=max(args.resolved_share_penalty, 0.0),
            resolved_size_share_penalty=max(args.resolved_size_share_penalty, 0.0),
            worst_window_resolved_share_penalty=max(args.worst_window_resolved_share_penalty, 0.0),
            worst_window_resolved_size_share_penalty=max(args.worst_window_resolved_size_share_penalty, 0.0),
            mode_resolved_share_penalty=max(args.mode_resolved_share_penalty, 0.0),
            mode_resolved_size_share_penalty=max(args.mode_resolved_size_share_penalty, 0.0),
            mode_worst_window_resolved_share_penalty=max(args.mode_worst_window_resolved_share_penalty, 0.0),
            mode_worst_window_resolved_size_share_penalty=max(args.mode_worst_window_resolved_size_share_penalty, 0.0),
            mode_active_window_accepted_share_penalty=max(args.mode_active_window_accepted_share_penalty, 0.0),
            mode_active_window_accepted_size_share_penalty=max(args.mode_active_window_accepted_size_share_penalty, 0.0),
            worst_active_window_accepted_penalty=max(args.worst_active_window_accepted_penalty, 0.0),
            worst_active_window_accepted_size_penalty=max(args.worst_active_window_accepted_size_penalty, 0.0),
            mode_worst_active_window_accepted_penalty=max(args.mode_worst_active_window_accepted_penalty, 0.0),
            mode_worst_active_window_accepted_size_penalty=max(args.mode_worst_active_window_accepted_size_penalty, 0.0),
            mode_loss_penalty=max(args.mode_loss_penalty, 0.0),
            mode_inactivity_penalty=max(args.mode_inactivity_penalty, 0.0),
            window_inactivity_penalty=max(args.window_inactivity_penalty, 0.0),
            wallet_count_penalty=max(args.wallet_count_penalty, 0.0),
            market_count_penalty=max(args.market_count_penalty, 0.0),
            entry_price_band_count_penalty=max(args.entry_price_band_count_penalty, 0.0),
            time_to_close_band_count_penalty=max(args.time_to_close_band_count_penalty, 0.0),
            wallet_size_concentration_penalty=max(args.wallet_size_concentration_penalty, 0.0),
            market_size_concentration_penalty=max(args.market_size_concentration_penalty, 0.0),
            entry_price_band_size_concentration_penalty=max(args.entry_price_band_size_concentration_penalty, 0.0),
            time_to_close_band_size_concentration_penalty=max(args.time_to_close_band_size_concentration_penalty, 0.0),
            allow_heuristic=bool(policy.allow_heuristic),
            allow_xgboost=bool(policy.allow_xgboost),
            wallet_concentration_penalty=max(args.wallet_concentration_penalty, 0.0),
            market_concentration_penalty=max(args.market_concentration_penalty, 0.0),
            entry_price_band_concentration_penalty=max(args.entry_price_band_concentration_penalty, 0.0),
            time_to_close_band_concentration_penalty=max(args.time_to_close_band_concentration_penalty, 0.0),
        )
        constraint_failures = _constraint_failures(
            result,
            allow_heuristic=bool(policy.allow_heuristic),
            allow_xgboost=bool(policy.allow_xgboost),
            min_accepted_count=args.min_accepted_count,
            min_resolved_count=args.min_resolved_count,
            min_resolved_share=_clamp_fraction(args.min_resolved_share),
            min_resolved_size_share=_clamp_fraction(args.min_resolved_size_share),
            min_win_rate=max(args.min_win_rate, 0.0),
            min_total_pnl_usd=float(args.min_total_pnl_usd),
            max_drawdown_pct=max(args.max_drawdown_pct, 0.0),
            max_open_exposure_share=_clamp_fraction(args.max_open_exposure_share),
            max_window_end_open_exposure_share=_clamp_fraction(args.max_window_end_open_exposure_share),
            max_avg_window_end_open_exposure_share=_clamp_fraction(args.max_avg_window_end_open_exposure_share),
            max_carry_window_share=_clamp_fraction(args.max_carry_window_share),
            max_carry_restart_window_share=_clamp_fraction(args.max_carry_restart_window_share),
            max_live_guard_window_share=_clamp_fraction(args.max_live_guard_window_share),
            min_worst_window_pnl_usd=args.min_worst_window_pnl_usd,
            min_worst_window_resolved_share=_clamp_fraction(args.min_worst_window_resolved_share),
            min_worst_window_resolved_size_share=_clamp_fraction(args.min_worst_window_resolved_size_share),
            max_worst_window_drawdown_pct=max(args.max_worst_window_drawdown_pct, 0.0),
            min_heuristic_accepted_count=max(args.min_heuristic_accepted_count, 0),
            min_xgboost_accepted_count=max(args.min_xgboost_accepted_count, 0),
            min_heuristic_resolved_count=max(args.min_heuristic_resolved_count, 0),
            min_xgboost_resolved_count=max(args.min_xgboost_resolved_count, 0),
            min_heuristic_win_rate=_clamp_fraction(args.min_heuristic_win_rate),
            min_xgboost_win_rate=_clamp_fraction(args.min_xgboost_win_rate),
            min_heuristic_resolved_share=_clamp_fraction(args.min_heuristic_resolved_share),
            min_xgboost_resolved_share=_clamp_fraction(args.min_xgboost_resolved_share),
            min_heuristic_resolved_size_share=_clamp_fraction(args.min_heuristic_resolved_size_share),
            min_xgboost_resolved_size_share=_clamp_fraction(args.min_xgboost_resolved_size_share),
            min_heuristic_pnl_usd=float(args.min_heuristic_pnl_usd),
            min_xgboost_pnl_usd=float(args.min_xgboost_pnl_usd),
            min_heuristic_worst_window_pnl_usd=float(args.min_heuristic_worst_window_pnl_usd),
            min_xgboost_worst_window_pnl_usd=float(args.min_xgboost_worst_window_pnl_usd),
            min_heuristic_worst_window_resolved_share=_clamp_fraction(args.min_heuristic_worst_window_resolved_share),
            min_xgboost_worst_window_resolved_share=_clamp_fraction(args.min_xgboost_worst_window_resolved_share),
            min_heuristic_worst_window_resolved_size_share=_clamp_fraction(args.min_heuristic_worst_window_resolved_size_share),
            min_xgboost_worst_window_resolved_size_share=_clamp_fraction(args.min_xgboost_worst_window_resolved_size_share),
            min_heuristic_positive_window_count=max(args.min_heuristic_positive_windows, 0),
            min_xgboost_positive_window_count=max(args.min_xgboost_positive_windows, 0),
            min_heuristic_worst_active_window_accepted_count=max(args.min_heuristic_worst_active_window_accepted_count, 0),
            min_heuristic_worst_active_window_accepted_size_usd=max(args.min_heuristic_worst_active_window_accepted_size_usd, 0.0),
            min_xgboost_worst_active_window_accepted_count=max(args.min_xgboost_worst_active_window_accepted_count, 0),
            min_xgboost_worst_active_window_accepted_size_usd=max(args.min_xgboost_worst_active_window_accepted_size_usd, 0.0),
            max_heuristic_inactive_window_count=int(args.max_heuristic_inactive_windows),
            max_xgboost_inactive_window_count=int(args.max_xgboost_inactive_windows),
            max_heuristic_accepted_share=_clamp_fraction(args.max_heuristic_accepted_share),
            max_heuristic_accepted_size_share=_clamp_fraction(args.max_heuristic_accepted_size_share),
            max_heuristic_active_window_accepted_share=_clamp_fraction(args.max_heuristic_active_window_accepted_share),
            max_heuristic_active_window_accepted_size_share=_clamp_fraction(args.max_heuristic_active_window_accepted_size_share),
            min_xgboost_accepted_share=_clamp_fraction(args.min_xgboost_accepted_share),
            min_xgboost_accepted_size_share=_clamp_fraction(args.min_xgboost_accepted_size_share),
            min_xgboost_active_window_accepted_share=_clamp_fraction(args.min_xgboost_active_window_accepted_share),
            min_xgboost_active_window_accepted_size_share=_clamp_fraction(args.min_xgboost_active_window_accepted_size_share),
            max_pause_guard_reject_share=_clamp_fraction(args.max_pause_guard_reject_share),
            max_daily_guard_window_share=_clamp_fraction(args.max_daily_guard_window_share),
            max_daily_guard_restart_window_share=_clamp_fraction(args.max_daily_guard_restart_window_share),
            min_active_window_count=max(args.min_active_windows, 0),
            max_inactive_window_count=int(args.max_inactive_windows),
            min_worst_active_window_accepted_count=max(args.min_worst_active_window_accepted_count, 0),
            min_worst_active_window_accepted_size_usd=max(args.min_worst_active_window_accepted_size_usd, 0.0),
            min_trader_count=max(args.min_trader_count, 0),
            min_market_count=max(args.min_market_count, 0),
            min_entry_price_band_count=max(args.min_entry_price_band_count, 0),
            min_time_to_close_band_count=max(args.min_time_to_close_band_count, 0),
            max_top_trader_accepted_share=_clamp_fraction(args.max_top_trader_accepted_share),
            max_top_trader_abs_pnl_share=_clamp_fraction(args.max_top_trader_abs_pnl_share),
            max_top_trader_size_share=_clamp_fraction(args.max_top_trader_size_share),
            max_top_market_accepted_share=_clamp_fraction(args.max_top_market_accepted_share),
            max_top_market_abs_pnl_share=_clamp_fraction(args.max_top_market_abs_pnl_share),
            max_top_market_size_share=_clamp_fraction(args.max_top_market_size_share),
            max_top_entry_price_band_accepted_share=_clamp_fraction(args.max_top_entry_price_band_accepted_share),
            max_top_entry_price_band_abs_pnl_share=_clamp_fraction(args.max_top_entry_price_band_abs_pnl_share),
            max_top_entry_price_band_size_share=_clamp_fraction(args.max_top_entry_price_band_size_share),
            max_top_time_to_close_band_accepted_share=_clamp_fraction(args.max_top_time_to_close_band_accepted_share),
            max_top_time_to_close_band_abs_pnl_share=_clamp_fraction(args.max_top_time_to_close_band_abs_pnl_share),
            max_top_time_to_close_band_size_share=_clamp_fraction(args.max_top_time_to_close_band_size_share),
            max_live_guard_restart_window_share=_clamp_fraction(args.max_live_guard_restart_window_share),
        )
        if int(result.get("positive_window_count") or 0) < max(args.min_positive_windows, 0):
            constraint_failures.append("positive_window_count")
        candidates.append(
            {
                "index": index,
                "score": round(score, 6),
                "overrides": overrides,
                "policy": policy.as_dict(),
                "config": policy_to_config_payload(policy),
                "result": result,
                "constraint_failures": constraint_failures,
                "is_current_policy": False,
                "policy_version": policy_version,
            }
        )

    current_matches_grid = any(row["policy_version"] == current_candidate["policy_version"] for row in candidates)
    ranked = sorted(
        candidates,
        key=lambda row: (
            float(row["score"]),
            float(row["result"].get("total_pnl_usd") or 0.0),
            -float(row["result"].get("max_drawdown_pct") or 0.0),
            float(row["result"].get("win_rate") or 0.0),
        ),
        reverse=True,
    )
    feasible = [row for row in ranked if not row["constraint_failures"]]
    rejected = [row for row in ranked if row["constraint_failures"]]
    constraints = {
        "min_accepted_count": max(args.min_accepted_count, 0),
        "min_resolved_count": max(args.min_resolved_count, 0),
        "min_resolved_share": _clamp_fraction(args.min_resolved_share),
        "min_resolved_size_share": _clamp_fraction(args.min_resolved_size_share),
        "min_win_rate": max(args.min_win_rate, 0.0),
        "min_total_pnl_usd": float(args.min_total_pnl_usd),
        "max_drawdown_pct": max(args.max_drawdown_pct, 0.0),
        "max_open_exposure_share": _clamp_fraction(args.max_open_exposure_share),
        "max_window_end_open_exposure_share": _clamp_fraction(args.max_window_end_open_exposure_share),
        "max_avg_window_end_open_exposure_share": _clamp_fraction(args.max_avg_window_end_open_exposure_share),
        "max_carry_window_share": _clamp_fraction(args.max_carry_window_share),
        "min_positive_windows": max(args.min_positive_windows, 0),
        "min_active_windows": max(args.min_active_windows, 0),
        "max_inactive_windows": int(args.max_inactive_windows),
        "min_worst_active_window_accepted_count": max(args.min_worst_active_window_accepted_count, 0),
        "min_worst_active_window_accepted_size_usd": max(args.min_worst_active_window_accepted_size_usd, 0.0),
        "min_worst_window_pnl_usd": args.min_worst_window_pnl_usd,
        "min_worst_window_resolved_share": _clamp_fraction(args.min_worst_window_resolved_share),
        "min_worst_window_resolved_size_share": _clamp_fraction(args.min_worst_window_resolved_size_share),
        "max_worst_window_drawdown_pct": max(args.max_worst_window_drawdown_pct, 0.0),
        "min_heuristic_accepted_count": max(args.min_heuristic_accepted_count, 0),
        "min_xgboost_accepted_count": max(args.min_xgboost_accepted_count, 0),
        "min_heuristic_resolved_count": max(args.min_heuristic_resolved_count, 0),
        "min_xgboost_resolved_count": max(args.min_xgboost_resolved_count, 0),
        "min_heuristic_win_rate": _clamp_fraction(args.min_heuristic_win_rate),
        "min_xgboost_win_rate": _clamp_fraction(args.min_xgboost_win_rate),
        "min_heuristic_resolved_share": _clamp_fraction(args.min_heuristic_resolved_share),
        "min_xgboost_resolved_share": _clamp_fraction(args.min_xgboost_resolved_share),
        "min_heuristic_resolved_size_share": _clamp_fraction(args.min_heuristic_resolved_size_share),
        "min_xgboost_resolved_size_share": _clamp_fraction(args.min_xgboost_resolved_size_share),
        "min_heuristic_pnl_usd": float(args.min_heuristic_pnl_usd),
        "min_xgboost_pnl_usd": float(args.min_xgboost_pnl_usd),
        "min_heuristic_worst_window_pnl_usd": float(args.min_heuristic_worst_window_pnl_usd),
        "min_xgboost_worst_window_pnl_usd": float(args.min_xgboost_worst_window_pnl_usd),
        "min_heuristic_worst_window_resolved_share": _clamp_fraction(args.min_heuristic_worst_window_resolved_share),
        "min_xgboost_worst_window_resolved_share": _clamp_fraction(args.min_xgboost_worst_window_resolved_share),
        "min_heuristic_worst_window_resolved_size_share": _clamp_fraction(args.min_heuristic_worst_window_resolved_size_share),
        "min_xgboost_worst_window_resolved_size_share": _clamp_fraction(args.min_xgboost_worst_window_resolved_size_share),
        "min_heuristic_positive_windows": max(args.min_heuristic_positive_windows, 0),
        "min_xgboost_positive_windows": max(args.min_xgboost_positive_windows, 0),
        "min_heuristic_worst_active_window_accepted_count": max(args.min_heuristic_worst_active_window_accepted_count, 0),
        "min_heuristic_worst_active_window_accepted_size_usd": max(args.min_heuristic_worst_active_window_accepted_size_usd, 0.0),
        "min_xgboost_worst_active_window_accepted_count": max(args.min_xgboost_worst_active_window_accepted_count, 0),
        "min_xgboost_worst_active_window_accepted_size_usd": max(args.min_xgboost_worst_active_window_accepted_size_usd, 0.0),
        "max_heuristic_inactive_windows": int(args.max_heuristic_inactive_windows),
        "max_xgboost_inactive_windows": int(args.max_xgboost_inactive_windows),
        "max_heuristic_accepted_share": _clamp_fraction(args.max_heuristic_accepted_share),
        "max_heuristic_accepted_size_share": _clamp_fraction(args.max_heuristic_accepted_size_share),
        "max_heuristic_active_window_accepted_share": _clamp_fraction(args.max_heuristic_active_window_accepted_share),
        "max_heuristic_active_window_accepted_size_share": _clamp_fraction(args.max_heuristic_active_window_accepted_size_share),
        "min_xgboost_accepted_share": _clamp_fraction(args.min_xgboost_accepted_share),
        "min_xgboost_accepted_size_share": _clamp_fraction(args.min_xgboost_accepted_size_share),
        "min_xgboost_active_window_accepted_share": _clamp_fraction(args.min_xgboost_active_window_accepted_share),
        "min_xgboost_active_window_accepted_size_share": _clamp_fraction(args.min_xgboost_active_window_accepted_size_share),
        "max_pause_guard_reject_share": _clamp_fraction(args.max_pause_guard_reject_share),
        "max_daily_guard_window_share": _clamp_fraction(args.max_daily_guard_window_share),
        "max_live_guard_window_share": _clamp_fraction(args.max_live_guard_window_share),
        "max_daily_guard_restart_window_share": _clamp_fraction(args.max_daily_guard_restart_window_share),
        "max_live_guard_restart_window_share": _clamp_fraction(args.max_live_guard_restart_window_share),
        "max_carry_restart_window_share": _clamp_fraction(args.max_carry_restart_window_share),
        "min_trader_count": max(args.min_trader_count, 0),
        "min_market_count": max(args.min_market_count, 0),
        "min_entry_price_band_count": max(args.min_entry_price_band_count, 0),
        "min_time_to_close_band_count": max(args.min_time_to_close_band_count, 0),
        "max_top_trader_accepted_share": _clamp_fraction(args.max_top_trader_accepted_share),
        "max_top_trader_abs_pnl_share": _clamp_fraction(args.max_top_trader_abs_pnl_share),
        "max_top_trader_size_share": _clamp_fraction(args.max_top_trader_size_share),
        "max_top_market_accepted_share": _clamp_fraction(args.max_top_market_accepted_share),
        "max_top_market_abs_pnl_share": _clamp_fraction(args.max_top_market_abs_pnl_share),
        "max_top_market_size_share": _clamp_fraction(args.max_top_market_size_share),
        "max_top_entry_price_band_accepted_share": _clamp_fraction(args.max_top_entry_price_band_accepted_share),
        "max_top_entry_price_band_abs_pnl_share": _clamp_fraction(args.max_top_entry_price_band_abs_pnl_share),
        "max_top_entry_price_band_size_share": _clamp_fraction(args.max_top_entry_price_band_size_share),
        "max_top_time_to_close_band_accepted_share": _clamp_fraction(args.max_top_time_to_close_band_accepted_share),
        "max_top_time_to_close_band_abs_pnl_share": _clamp_fraction(args.max_top_time_to_close_band_abs_pnl_share),
        "max_top_time_to_close_band_size_share": _clamp_fraction(args.max_top_time_to_close_band_size_share),
    }
    finished_at = int(time.time())
    search_run_id = _persist_search_results(
        db_path=db_path,
        started_at=started_at,
        finished_at=finished_at,
        label_prefix=args.label_prefix,
        notes=args.notes,
        base_policy=base_policy,
        grid=grid,
        constraints=constraints,
        drawdown_penalty=max(args.drawdown_penalty, 0.0),
        window_stddev_penalty=max(args.window_stddev_penalty, 0.0),
        worst_window_penalty=max(args.worst_window_penalty, 0.0),
        pause_guard_penalty=max(args.pause_guard_penalty, 0.0),
        daily_guard_window_penalty=max(args.daily_guard_window_penalty, 0.0),
        live_guard_window_penalty=max(args.live_guard_window_penalty, 0.0),
        daily_guard_restart_window_penalty=max(args.daily_guard_restart_window_penalty, 0.0),
        live_guard_restart_window_penalty=max(args.live_guard_restart_window_penalty, 0.0),
        open_exposure_penalty=max(args.open_exposure_penalty, 0.0),
        window_end_open_exposure_penalty=max(args.window_end_open_exposure_penalty, 0.0),
        avg_window_end_open_exposure_penalty=max(args.avg_window_end_open_exposure_penalty, 0.0),
        carry_window_penalty=max(args.carry_window_penalty, 0.0),
        carry_restart_window_penalty=max(args.carry_restart_window_penalty, 0.0),
        resolved_share_penalty=max(args.resolved_share_penalty, 0.0),
        resolved_size_share_penalty=max(args.resolved_size_share_penalty, 0.0),
        worst_window_resolved_share_penalty=max(args.worst_window_resolved_share_penalty, 0.0),
        worst_window_resolved_size_share_penalty=max(args.worst_window_resolved_size_share_penalty, 0.0),
        mode_resolved_share_penalty=max(args.mode_resolved_share_penalty, 0.0),
        mode_resolved_size_share_penalty=max(args.mode_resolved_size_share_penalty, 0.0),
        mode_worst_window_resolved_share_penalty=max(args.mode_worst_window_resolved_share_penalty, 0.0),
        mode_worst_window_resolved_size_share_penalty=max(args.mode_worst_window_resolved_size_share_penalty, 0.0),
        mode_active_window_accepted_share_penalty=max(args.mode_active_window_accepted_share_penalty, 0.0),
        mode_active_window_accepted_size_share_penalty=max(args.mode_active_window_accepted_size_share_penalty, 0.0),
        worst_active_window_accepted_penalty=max(args.worst_active_window_accepted_penalty, 0.0),
        worst_active_window_accepted_size_penalty=max(args.worst_active_window_accepted_size_penalty, 0.0),
        mode_worst_active_window_accepted_penalty=max(args.mode_worst_active_window_accepted_penalty, 0.0),
        mode_worst_active_window_accepted_size_penalty=max(args.mode_worst_active_window_accepted_size_penalty, 0.0),
        mode_loss_penalty=max(args.mode_loss_penalty, 0.0),
        mode_inactivity_penalty=max(args.mode_inactivity_penalty, 0.0),
        window_inactivity_penalty=max(args.window_inactivity_penalty, 0.0),
        wallet_count_penalty=max(args.wallet_count_penalty, 0.0),
        market_count_penalty=max(args.market_count_penalty, 0.0),
        entry_price_band_count_penalty=max(args.entry_price_band_count_penalty, 0.0),
        time_to_close_band_count_penalty=max(args.time_to_close_band_count_penalty, 0.0),
        wallet_concentration_penalty=max(args.wallet_concentration_penalty, 0.0),
        market_concentration_penalty=max(args.market_concentration_penalty, 0.0),
        entry_price_band_concentration_penalty=max(args.entry_price_band_concentration_penalty, 0.0),
        time_to_close_band_concentration_penalty=max(args.time_to_close_band_concentration_penalty, 0.0),
        wallet_size_concentration_penalty=max(args.wallet_size_concentration_penalty, 0.0),
        market_size_concentration_penalty=max(args.market_size_concentration_penalty, 0.0),
        entry_price_band_size_concentration_penalty=max(args.entry_price_band_size_concentration_penalty, 0.0),
        time_to_close_band_size_concentration_penalty=max(args.time_to_close_band_size_concentration_penalty, 0.0),
        window_days=max(args.window_days, 0),
        window_count=max(args.window_count, 1),
        current_candidate=current_candidate,
        persist_current_candidate=not current_matches_grid,
        ranked=ranked,
        feasible=feasible,
        rejected=rejected,
    )
    print(
        json.dumps(
            {
                "search_run_id": search_run_id,
                "base_policy": base_policy.as_dict(),
                "grid": grid,
                "windows": [{"start_ts": start_ts, "end_ts": end_ts} for start_ts, end_ts in windows],
                "drawdown_penalty": max(args.drawdown_penalty, 0.0),
                "window_stddev_penalty": max(args.window_stddev_penalty, 0.0),
                "worst_window_penalty": max(args.worst_window_penalty, 0.0),
                "pause_guard_penalty": max(args.pause_guard_penalty, 0.0),
                "daily_guard_window_penalty": max(args.daily_guard_window_penalty, 0.0),
                "live_guard_window_penalty": max(args.live_guard_window_penalty, 0.0),
                "daily_guard_restart_window_penalty": max(args.daily_guard_restart_window_penalty, 0.0),
                "live_guard_restart_window_penalty": max(args.live_guard_restart_window_penalty, 0.0),
                "open_exposure_penalty": max(args.open_exposure_penalty, 0.0),
                "window_end_open_exposure_penalty": max(args.window_end_open_exposure_penalty, 0.0),
                "avg_window_end_open_exposure_penalty": max(args.avg_window_end_open_exposure_penalty, 0.0),
                "carry_window_penalty": max(args.carry_window_penalty, 0.0),
                "carry_restart_window_penalty": max(args.carry_restart_window_penalty, 0.0),
                "resolved_share_penalty": max(args.resolved_share_penalty, 0.0),
                "resolved_size_share_penalty": max(args.resolved_size_share_penalty, 0.0),
                "worst_window_resolved_share_penalty": max(args.worst_window_resolved_share_penalty, 0.0),
                "worst_window_resolved_size_share_penalty": max(args.worst_window_resolved_size_share_penalty, 0.0),
                "mode_resolved_share_penalty": max(args.mode_resolved_share_penalty, 0.0),
                "mode_resolved_size_share_penalty": max(args.mode_resolved_size_share_penalty, 0.0),
                "mode_worst_window_resolved_share_penalty": max(args.mode_worst_window_resolved_share_penalty, 0.0),
                "mode_worst_window_resolved_size_share_penalty": max(args.mode_worst_window_resolved_size_share_penalty, 0.0),
                "mode_active_window_accepted_share_penalty": max(args.mode_active_window_accepted_share_penalty, 0.0),
                "mode_active_window_accepted_size_share_penalty": max(args.mode_active_window_accepted_size_share_penalty, 0.0),
                "worst_active_window_accepted_penalty": max(args.worst_active_window_accepted_penalty, 0.0),
                "worst_active_window_accepted_size_penalty": max(args.worst_active_window_accepted_size_penalty, 0.0),
                "mode_worst_active_window_accepted_penalty": max(args.mode_worst_active_window_accepted_penalty, 0.0),
                "mode_worst_active_window_accepted_size_penalty": max(args.mode_worst_active_window_accepted_size_penalty, 0.0),
                "mode_loss_penalty": max(args.mode_loss_penalty, 0.0),
                "mode_inactivity_penalty": max(args.mode_inactivity_penalty, 0.0),
                "window_inactivity_penalty": max(args.window_inactivity_penalty, 0.0),
                "wallet_count_penalty": max(args.wallet_count_penalty, 0.0),
                "market_count_penalty": max(args.market_count_penalty, 0.0),
                "entry_price_band_count_penalty": max(args.entry_price_band_count_penalty, 0.0),
                "time_to_close_band_count_penalty": max(args.time_to_close_band_count_penalty, 0.0),
                "wallet_concentration_penalty": max(args.wallet_concentration_penalty, 0.0),
                "market_concentration_penalty": max(args.market_concentration_penalty, 0.0),
                "entry_price_band_concentration_penalty": max(args.entry_price_band_concentration_penalty, 0.0),
                "time_to_close_band_concentration_penalty": max(args.time_to_close_band_concentration_penalty, 0.0),
                "wallet_size_concentration_penalty": max(args.wallet_size_concentration_penalty, 0.0),
                "market_size_concentration_penalty": max(args.market_size_concentration_penalty, 0.0),
                "entry_price_band_size_concentration_penalty": max(args.entry_price_band_size_concentration_penalty, 0.0),
                "time_to_close_band_size_concentration_penalty": max(args.time_to_close_band_size_concentration_penalty, 0.0),
                "constraints": constraints,
                "candidate_count": len(ranked),
                "feasible_count": len(feasible),
                "rejected_count": len(rejected),
                "current_candidate_matches_grid": current_matches_grid,
                "current_candidate": current_candidate,
                "best_feasible_config": feasible[0]["config"] if feasible else None,
                "best_vs_current_pnl_usd": (
                    float(feasible[0]["result"].get("total_pnl_usd") or 0.0)
                    - float(current_candidate["result"].get("total_pnl_usd") or 0.0)
                ) if feasible else None,
                "best_vs_current_score": (
                    float(feasible[0]["score"]) - float(current_candidate["score"])
                ) if feasible else None,
                "best_feasible": feasible[0] if feasible else None,
                "ranked": ranked,
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(file=sys.stderr)
    _print_ranked_summary(feasible if feasible else ranked, top=max(args.top, 1), title="Replay sweep top candidates:")
    if rejected:
        print(file=sys.stderr)
        _print_ranked_summary(rejected, top=min(max(args.top, 1), len(rejected)), title="Replay sweep rejected candidates:")


if __name__ == "__main__":
    main()
