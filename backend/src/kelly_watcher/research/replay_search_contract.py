from __future__ import annotations

import math
from typing import Any


REPLAY_SEARCH_SCORE_WEIGHT_KEYS = frozenset(
    {
        "accepted_window_count_penalty",
        "accepted_window_share_penalty",
        "accepting_window_accepted_concentration_index_penalty",
        "accepting_window_accepted_share_penalty",
        "accepting_window_accepted_size_concentration_index_penalty",
        "accepting_window_accepted_size_share_penalty",
        "avg_window_end_open_exposure_penalty",
        "carry_restart_window_penalty",
        "carry_window_penalty",
        "daily_guard_restart_window_penalty",
        "daily_guard_window_penalty",
        "drawdown_penalty",
        "entry_price_band_concentration_penalty",
        "entry_price_band_count_penalty",
        "entry_price_band_size_concentration_penalty",
        "live_guard_restart_window_penalty",
        "live_guard_window_penalty",
        "market_concentration_penalty",
        "market_count_penalty",
        "market_size_concentration_penalty",
        "mode_accepted_window_count_penalty",
        "mode_accepted_window_share_penalty",
        "mode_accepting_window_accepted_concentration_index_penalty",
        "mode_accepting_window_accepted_share_penalty",
        "mode_accepting_window_accepted_size_concentration_index_penalty",
        "mode_accepting_window_accepted_size_share_penalty",
        "mode_active_window_accepted_share_penalty",
        "mode_active_window_accepted_size_share_penalty",
        "mode_inactivity_penalty",
        "mode_loss_penalty",
        "mode_non_accepting_active_window_episode_penalty",
        "mode_non_accepting_active_window_streak_penalty",
        "mode_resolved_share_penalty",
        "mode_resolved_size_share_penalty",
        "mode_top_two_accepting_window_accepted_share_penalty",
        "mode_top_two_accepting_window_accepted_size_share_penalty",
        "mode_worst_active_window_accepted_penalty",
        "mode_worst_active_window_accepted_size_penalty",
        "mode_worst_window_resolved_share_penalty",
        "mode_worst_window_resolved_size_share_penalty",
        "non_accepting_active_window_episode_penalty",
        "non_accepting_active_window_streak_penalty",
        "open_exposure_penalty",
        "pause_guard_penalty",
        "resolved_share_penalty",
        "resolved_size_share_penalty",
        "time_to_close_band_concentration_penalty",
        "time_to_close_band_count_penalty",
        "time_to_close_band_size_concentration_penalty",
        "top_two_accepting_window_accepted_share_penalty",
        "top_two_accepting_window_accepted_size_share_penalty",
        "wallet_concentration_penalty",
        "wallet_count_penalty",
        "wallet_size_concentration_penalty",
        "window_end_open_exposure_penalty",
        "window_inactivity_penalty",
        "window_stddev_penalty",
        "worst_active_window_accepted_penalty",
        "worst_active_window_accepted_size_penalty",
        "worst_window_penalty",
        "worst_window_resolved_share_penalty",
        "worst_window_resolved_size_share_penalty",
    }
)


def validate_replay_search_score_weight_payload(
    payload: dict[str, Any],
    *,
    error_cls: type[Exception] = ValueError,
) -> dict[str, Any]:
    normalized_payload = {str(key): value for key, value in payload.items()}
    unknown_keys = sorted(key for key in normalized_payload if key not in REPLAY_SEARCH_SCORE_WEIGHT_KEYS)
    if unknown_keys:
        joined_unknown_keys = ", ".join(unknown_keys)
        raise error_cls(f"Unknown replay-search score-weight key(s): {joined_unknown_keys}")
    validated_payload: dict[str, float] = {}
    for key, raw_value in normalized_payload.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise error_cls(
                f"Replay-search score weight {key} must be a finite non-negative number"
            ) from exc
        if not math.isfinite(value) or value < 0.0:
            raise error_cls(
                f"Replay-search score weight {key} must be a finite non-negative number"
            )
        validated_payload[key] = value
    return validated_payload
