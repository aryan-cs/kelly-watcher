from __future__ import annotations

import inspect
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import kelly_watcher.data.db as db
import kelly_watcher.runtime.evaluator as evaluator
import main
import kelly_watcher.runtime.performance_preview as performance_preview
from config import shadow_bankroll_usd


def _insert_trade(
    conn,
    *,
    trade_id: str,
    segment_id: str | None,
    shadow_pnl_usd: float | None,
    resolved_at: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id,
            market_id,
            question,
            trader_address,
            side,
            source_action,
            price_at_signal,
            signal_size_usd,
            confidence,
            kelly_fraction,
            real_money,
            skipped,
            placed_at,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            remaining_entry_shares,
            remaining_entry_size_usd,
            remaining_source_shares,
            shadow_pnl_usd,
            actual_pnl_usd,
            outcome,
            resolved_at,
            segment_id,
            expected_edge,
            expected_fill_cost_usd,
            expected_exit_fee_usd,
            expected_close_fixed_cost_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_id,
            f"market-{trade_id}",
            "Question",
            f"wallet-{trade_id}",
            "yes",
            "buy",
            0.5,
            10.0,
            0.7,
            0.1,
            0,
            0,
            int((resolved_at or 1_700_000_100) - 10),
            0.5,
            20.0,
            10.0,
            20.0,
            10.0,
            20.0,
            shadow_pnl_usd,
            None,
            1 if shadow_pnl_usd and shadow_pnl_usd > 0 else 0 if shadow_pnl_usd is not None else None,
            resolved_at,
            segment_id,
            0.12,
            0.03,
            0.01,
            0.0,
        ),
    )


def _build_fixture(rows: list[tuple[str, str | None, float | None, int | None]]) -> dict[str, object]:
    tmpdir = TemporaryDirectory()
    root = Path(tmpdir.name)
    db_path = root / "data" / "trading.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    original_db_path = db.DB_PATH
    try:
        db.DB_PATH = db_path
        db.init_db()
        conn = db.get_conn()
        try:
            for trade_id, segment_id, shadow_pnl_usd, resolved_at in rows:
                _insert_trade(
                    conn,
                    trade_id=trade_id,
                    segment_id=segment_id,
                    shadow_pnl_usd=shadow_pnl_usd,
                    resolved_at=resolved_at,
                )
            conn.commit()
        finally:
            conn.close()
    finally:
        db.DB_PATH = original_db_path

    return {
        "tmpdir": tmpdir,
        "db_path": db_path,
    }


def _compute_segment_report(db_path: Path, *, min_resolved: int) -> dict[str, object]:
    signature = inspect.signature(evaluator.compute_segment_shadow_report)
    if "db_path" not in signature.parameters:
        raise unittest.SkipTest("kelly_watcher.runtime.evaluator.compute_segment_shadow_report does not yet accept db_path.")
    result = evaluator.compute_segment_shadow_report(db_path=db_path, min_resolved=min_resolved)
    if isinstance(result, dict):
        return result
    if hasattr(result, "__dict__"):
        return dict(vars(result))
    raise AssertionError(f"Unsupported report type: {type(result)!r}")


def _payload_value(payload: dict[str, object], *candidate_keys: str) -> tuple[str, object] | None:
    for key in candidate_keys:
        if key in payload:
            return key, payload[key]
    return None


class RoutedShadowEvidenceTest(unittest.TestCase):
    def test_report_distinguishes_routed_fixed_segment_rows_from_legacy_unassigned_rows(self) -> None:
        fixture = _build_fixture(
            [
                ("routed-win", "hot_short", 4.0, 1_700_000_100),
                ("legacy-win", None, 1.5, 1_700_000_120),
            ]
        )
        db_path = fixture["db_path"]
        assert isinstance(db_path, Path)

        report = _compute_segment_report(db_path, min_resolved=1)
        state = main._segment_shadow_state_payload(report)

        self.assertEqual(report["legacy_unassigned_resolved"], 1)
        self.assertEqual(report["ready_count"], 1)
        self.assertIn("legacy/unassigned resolved", str(report["summary"]))
        self.assertEqual(state["shadow_segment_ready_count"], 1)

        routed = next(row for row in report["segments"] if row["segment_id"] == "hot_short")
        legacy = next(row for row in report["segments"] if row["segment_id"] == evaluator.UNASSIGNED_SEGMENT_ID)
        self.assertEqual(routed["resolved"], 1)
        self.assertEqual(routed["health"], "ready")
        self.assertEqual(legacy["resolved"], 1)
        self.assertEqual(legacy["health"], "legacy")

    def test_all_legacy_history_is_not_marked_ready_for_routed_segment_evidence(self) -> None:
        fixture = _build_fixture(
            [
                ("legacy-1", None, 3.0, 1_700_000_200),
                ("legacy-2", "", 2.0, 1_700_000_220),
            ]
        )
        db_path = fixture["db_path"]
        assert isinstance(db_path, Path)

        report = _compute_segment_report(db_path, min_resolved=20)
        state = main._segment_shadow_state_payload(report)

        self.assertEqual(report["legacy_unassigned_resolved"], 2)
        self.assertEqual(report["ready_count"], 0)
        self.assertEqual(state["shadow_segment_ready_count"], 0)
        self.assertIn("legacy/unassigned resolved", str(report["summary"]))

        legacy = next(row for row in report["segments"] if row["segment_id"] == evaluator.UNASSIGNED_SEGMENT_ID)
        self.assertEqual(legacy["resolved"], 2)
        self.assertEqual(legacy["health"], "legacy")

    def test_tracker_preview_summary_publishes_routed_only_metrics_separately_from_legacy_history(self) -> None:
        fixture = _build_fixture(
            [
                ("routed-win", "hot_short", 4.0, 1_700_000_100),
                ("legacy-loss", None, -2.0, 1_700_000_120),
            ]
        )
        db_path = fixture["db_path"]
        assert isinstance(db_path, Path)

        summary = performance_preview.compute_tracker_preview_summary(
            mode="shadow",
            db_path=db_path,
            use_bot_state_balance=False,
        )

        self.assertAlmostEqual(summary.total_pnl, 0.0, places=6)
        self.assertEqual(summary.resolved, 2)
        self.assertEqual(summary.routed_history_status, "mixed")
        self.assertEqual(summary.routed_acted, 1)
        self.assertEqual(summary.routed_resolved, 1)
        self.assertEqual(summary.routed_wins, 1)
        self.assertAlmostEqual(float(summary.routed_total_pnl or 0.0), 10.0, places=6)
        self.assertIsNotNone(summary.routed_return_pct)
        self.assertAlmostEqual(
            float(summary.routed_return_pct or 0.0),
            round(10.0 / float(shadow_bankroll_usd() or 1.0), 4),
            places=6,
        )
        self.assertTrue(summary.routed_profit_factor and summary.routed_profit_factor > 1000)
        self.assertAlmostEqual(float(summary.routed_expectancy_usd or 0.0), 10.0, places=6)
        self.assertEqual(summary.routed_legacy_acted, 1)
        self.assertEqual(summary.routed_legacy_resolved, 1)
        self.assertAlmostEqual(float(summary.routed_coverage_pct or 0.0), 0.5, places=6)

    def test_tracker_preview_summary_since_ts_scopes_shadow_metrics_to_epoch_boundary(self) -> None:
        fixture = _build_fixture(
            [
                ("legacy-win", None, 4.0, 1_700_000_100),
                ("epoch-win", "warm_mid", 3.0, 1_700_001_100),
            ]
        )
        db_path = fixture["db_path"]
        assert isinstance(db_path, Path)

        summary = performance_preview.compute_tracker_preview_summary(
            mode="shadow",
            db_path=db_path,
            use_bot_state_balance=False,
            since_ts=1_700_001_000,
        )

        self.assertEqual(summary.acted, 1)
        self.assertEqual(summary.resolved, 1)
        self.assertAlmostEqual(float(summary.total_pnl or 0.0), 10.0, places=6)
        self.assertEqual(summary.routed_resolved, 1)
        self.assertEqual(summary.routed_legacy_resolved, 0)
        self.assertEqual(summary.routed_history_status, "routed_only")

    def test_tracker_preview_summary_does_not_mix_pre_epoch_legacy_history_into_current_window(self) -> None:
        fixture = _build_fixture(
            [
                ("legacy-win", None, 4.0, 1_700_000_100),
                ("epoch-win", "warm_mid", 3.0, 1_700_001_100),
            ]
        )
        db_path = fixture["db_path"]
        assert isinstance(db_path, Path)

        with patch(
            "kelly_watcher.runtime.performance_preview.read_shadow_evidence_epoch",
            return_value={
                "shadow_evidence_epoch_known": True,
                "shadow_evidence_epoch_started_at": 1_700_001_000,
                "shadow_evidence_epoch_source": "shadow_reset",
            },
        ):
            current_summary = performance_preview.compute_tracker_preview_summary(
                mode="shadow",
                db_path=db_path,
                use_bot_state_balance=False,
                apply_shadow_evidence_epoch=True,
            )

        all_history_summary = performance_preview.compute_tracker_preview_summary(
            mode="shadow",
            db_path=db_path,
            use_bot_state_balance=False,
        )

        self.assertEqual(current_summary.shadow_evidence_epoch_started_at, 1_700_001_000)
        self.assertEqual(current_summary.shadow_evidence_epoch_source, "shadow_reset")
        self.assertEqual(current_summary.resolved, 1)
        self.assertEqual(current_summary.routed_resolved, 1)
        self.assertEqual(current_summary.routed_legacy_resolved, 0)
        self.assertEqual(current_summary.routed_history_status, "routed_only")

        self.assertEqual(all_history_summary.resolved, 2)
        self.assertEqual(all_history_summary.routed_resolved, 1)
        self.assertEqual(all_history_summary.routed_legacy_resolved, 1)
        self.assertEqual(all_history_summary.routed_history_status, "mixed")
        self.assertNotEqual(current_summary.resolved, all_history_summary.resolved)

    def test_tracker_preview_summary_prefers_latest_promotion_within_epoch(self) -> None:
        fixture = _build_fixture(
            [
                ("pre-promotion-win", "warm_mid", 4.0, 1_700_001_100),
                ("post-promotion-win", "warm_mid", 3.0, 1_700_001_700),
            ]
        )
        db_path = fixture["db_path"]
        assert isinstance(db_path, Path)

        with patch(
            "kelly_watcher.runtime.performance_preview.read_shadow_evidence_epoch",
            return_value={
                "shadow_evidence_epoch_known": True,
                "shadow_evidence_epoch_started_at": 1_700_001_000,
                "shadow_evidence_epoch_source": "shadow_reset",
            },
        ), patch(
            "kelly_watcher.runtime.performance_preview._latest_applied_replay_promotion_at",
            return_value=1_700_001_500,
        ):
            summary = performance_preview.compute_tracker_preview_summary(
                mode="shadow",
                db_path=db_path,
                use_bot_state_balance=False,
                apply_shadow_evidence_epoch=True,
            )

        self.assertEqual(summary.resolved, 1)
        self.assertEqual(summary.routed_resolved, 1)
        self.assertEqual(summary.routed_legacy_resolved, 0)
        self.assertAlmostEqual(float(summary.total_pnl or 0.0), 10.0, places=6)

    def test_tracker_preview_summary_since_ts_keeps_pre_epoch_open_positions_in_account_equity(self) -> None:
        fixture = _build_fixture(
            [
                ("legacy-open", None, None, None),
                ("epoch-win", "warm_mid", 3.0, 1_700_001_100),
            ]
        )
        db_path = fixture["db_path"]
        assert isinstance(db_path, Path)

        summary = performance_preview.compute_tracker_preview_summary(
            mode="shadow",
            db_path=db_path,
            use_bot_state_balance=False,
            since_ts=1_700_001_000,
        )

        expected_equity = round(float(shadow_bankroll_usd() or 0.0) + 10.0, 3)
        expected_exposure = round(10.0 / expected_equity, 4)
        self.assertEqual(summary.acted, 1)
        self.assertEqual(summary.resolved, 1)
        self.assertAlmostEqual(float(summary.total_pnl or 0.0), 10.0, places=6)
        self.assertAlmostEqual(float(summary.current_balance or 0.0), float(shadow_bankroll_usd() or 0.0), places=6)
        self.assertAlmostEqual(float(summary.current_equity or 0.0), expected_equity, places=6)
        self.assertAlmostEqual(float(summary.exposure_pct or 0.0), expected_exposure, places=6)

    def test_db_recovery_shadow_state_reports_routed_vs_legacy_counts_if_published(self) -> None:
        preview = SimpleNamespace(
            acted=2,
            resolved=2,
            total_pnl=5.0,
            return_pct=0.05,
            profit_factor=1.5,
            expectancy_usd=2.5,
            data_warning="",
        )
        report = {
            "status": "mixed",
            "total_segments": 1,
            "ready_count": 1,
            "blocked_count": 0,
            "summary": "1 legacy/unassigned resolved",
            "segments": [
                {"segment_id": "hot_short", "resolved": 1, "health": "ready"},
                {"segment_id": evaluator.UNASSIGNED_SEGMENT_ID, "resolved": 1, "health": "legacy"},
            ],
            "legacy_unassigned_resolved": 1,
        }

        payload = main._db_recovery_shadow_state_payload(
            candidate_path=Path("/tmp/routed-shadow.db"),
            preview=preview,
            report=report,
        )

        routed_entry = _payload_value(
            payload,
            "db_recovery_shadow_routed_resolved",
            "db_recovery_shadow_fixed_segment_resolved",
            "db_recovery_shadow_segment_routed_resolved",
        )
        legacy_entry = _payload_value(
            payload,
            "db_recovery_shadow_legacy_resolved",
            "db_recovery_shadow_legacy_unassigned_resolved",
            "db_recovery_shadow_segment_legacy_resolved",
        )
        routed_ready_entry = _payload_value(
            payload,
            "db_recovery_shadow_routed_ready",
            "db_recovery_shadow_fixed_segment_ready",
            "db_recovery_shadow_segment_routed_ready",
        )

        if routed_entry is None or legacy_entry is None:
            raise unittest.SkipTest("Routed/legacy db recovery fields are not published yet.")

        self.assertEqual(routed_entry[1], 1)
        self.assertEqual(legacy_entry[1], 1)
        if routed_ready_entry is not None:
            min_entry = _payload_value(
                payload,
                "db_recovery_shadow_min_resolved",
                "db_recovery_shadow_routed_min_resolved",
                "db_recovery_shadow_segment_min_resolved",
            )
            if min_entry is not None:
                self.assertEqual(bool(routed_ready_entry[1]), 1 >= int(min_entry[1] or 0))
            else:
                self.assertTrue(routed_ready_entry[1])

    def test_routed_shadow_gate_state_reports_threshold_ready_and_coverage(self) -> None:
        report = {
            "status": "mixed",
            "history_status": "mixed",
            "routed_resolved": 4,
            "legacy_unassigned_resolved": 1,
            "routed_coverage_pct": 4 / 5,
        }
        payload = main._routed_shadow_gate_state(report, min_resolved=5)

        self.assertTrue(payload["routed_shadow_state_known"])
        self.assertEqual(payload["routed_shadow_status"], "insufficient")
        self.assertEqual(payload["routed_shadow_min_resolved"], 5)
        self.assertEqual(payload["routed_shadow_routed_resolved"], 4)
        self.assertEqual(payload["routed_shadow_legacy_resolved"], 1)
        self.assertEqual(payload["routed_shadow_total_resolved"], 5)
        self.assertAlmostEqual(float(payload["routed_shadow_coverage_pct"]), 4 / 5, places=6)
        self.assertFalse(payload["routed_shadow_ready"])
        self.assertIn("need 4/5 routed resolved shadow trades", str(payload["routed_shadow_block_reason"]))
        self.assertIn("legacy/unassigned resolved", str(payload["routed_shadow_block_reason"]))

    def test_routed_history_block_reason_blocks_when_routed_resolved_is_below_required_minimum(self) -> None:
        report = {
            "status": "mixed",
            "history_status": "mixed",
            "routed_resolved": 4,
            "legacy_unassigned_resolved": 1,
            "routed_coverage_pct": 4 / 5,
            "summary": "need 4/5 routed resolved shadow trades",
            "segments": [
                {"segment_id": "hot_short", "resolved": 4, "health": "ready"},
                {"segment_id": evaluator.UNASSIGNED_SEGMENT_ID, "resolved": 1, "health": "legacy"},
            ],
        }

        with patch.object(main, "compute_segment_shadow_report", return_value=report):
            message = main._segment_routed_history_trust_block_reason("Replay search")

        self.assertIn("Replay search blocked", message)
        self.assertIn("routed", message.lower())
        self.assertIn("4", message)
        self.assertIn("20", message)
        self.assertIn("legacy/unassigned", message)
        self.assertTrue("below" in message.lower() or "need" in message.lower())

    def test_routed_history_block_reason_does_not_block_when_mixed_history_meets_routed_minimum(self) -> None:
        report = {
            "status": "mixed",
            "history_status": "mixed",
            "routed_resolved": 20,
            "legacy_unassigned_resolved": 2,
            "routed_coverage_pct": 20 / 22,
            "summary": "20 routed resolved, 2 legacy/unassigned resolved",
            "segments": [
                {"segment_id": "hot_short", "resolved": 20, "health": "ready"},
                {"segment_id": evaluator.UNASSIGNED_SEGMENT_ID, "resolved": 2, "health": "legacy"},
            ],
        }

        with patch.object(main, "compute_segment_shadow_report", return_value=report):
            message = main._segment_routed_history_trust_block_reason("Replay search")

        self.assertEqual(message, "")


if __name__ == "__main__":
    unittest.main()
