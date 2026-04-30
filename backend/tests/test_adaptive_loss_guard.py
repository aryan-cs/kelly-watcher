from __future__ import annotations

from types import SimpleNamespace

import kelly_watcher.engine.adaptive_loss_guard as guard


def _patch_guard_config(monkeypatch, *, total_resolved: int, snapshot: dict[tuple[str, str], guard.SegmentStats]) -> None:
    monkeypatch.setattr(guard, "adaptive_loss_guard_enabled", lambda: True)
    monkeypatch.setattr(guard, "adaptive_loss_guard_min_total_resolved", lambda: 25)
    monkeypatch.setattr(guard, "adaptive_loss_guard_min_segment_resolved", lambda: 8)
    monkeypatch.setattr(guard, "adaptive_loss_guard_max_segment_pnl_usd", lambda: -5.0)
    monkeypatch.setattr(guard, "adaptive_loss_guard_max_segment_avg_return", lambda: -0.08)
    monkeypatch.setattr(guard, "adaptive_loss_guard_max_segment_win_rate", lambda: 0.40)
    monkeypatch.setattr(guard, "_segment_snapshot", lambda: (snapshot, total_resolved))


def test_adaptive_loss_guard_blocks_bad_resolved_segment(monkeypatch) -> None:
    snapshot = {
        ("market_family", "crypto"): guard.SegmentStats(
            resolved=20,
            pnl_usd=-40.0,
            size_usd=120.0,
            wins=7,
        )
    }
    _patch_guard_config(monkeypatch, total_resolved=50, snapshot=snapshot)

    reason = guard.adaptive_loss_guard_reason(
        event=SimpleNamespace(
            question="Will Bitcoin reach $90,000 in April?",
            side="up",
            price=0.66,
            timestamp=1000,
            market_close_ts=1000 + 4 * 3600,
        ),
        signal={"entry_price": 0.66, "time_to_close_seconds": 4 * 3600},
    )

    assert reason is not None
    assert "market_family=crypto" in reason
    assert "20 resolved" in reason


def test_adaptive_loss_guard_fails_open_without_enough_evidence(monkeypatch) -> None:
    snapshot = {
        ("source_side", "down"): guard.SegmentStats(
            resolved=9,
            pnl_usd=-37.0,
            size_usd=70.0,
            wins=3,
        )
    }
    _patch_guard_config(monkeypatch, total_resolved=12, snapshot=snapshot)

    reason = guard.adaptive_loss_guard_reason(
        event=SimpleNamespace(
            question="Will Bitcoin dip below $77,000?",
            side="down",
            price=0.66,
            timestamp=1000,
            market_close_ts=1000 + 4 * 3600,
        ),
        signal={"entry_price": 0.66, "time_to_close_seconds": 4 * 3600},
    )

    assert reason is None
