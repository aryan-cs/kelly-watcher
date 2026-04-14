from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import db
import evaluator
import main
import shadow_evidence
from segment_policy import SEGMENT_FALLBACK, SEGMENT_IDS


def _insert_trade(
    conn,
    *,
    trade_id: str,
    segment_id: str | None,
    experiment_arm: str = "champion",
    shadow_pnl_usd: float | None = None,
    resolved_at: int | None = None,
    confidence: float = 0.7,
    price_at_signal: float = 0.5,
    signal_size_usd: float = 10.0,
    actual_entry_price: float = 0.5,
    actual_entry_shares: float = 20.0,
    actual_entry_size_usd: float = 10.0,
    entry_fee_usd: float = 0.0,
    entry_fixed_cost_usd: float = 0.0,
    expected_edge: float = 0.12,
    expected_fill_cost_usd: float = 0.03,
    expected_exit_fee_usd: float = 0.01,
    expected_close_fixed_cost_usd: float = 0.0,
    outcome: int | None = None,
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
            entry_fee_usd,
            entry_fixed_cost_usd,
            remaining_entry_shares,
            remaining_entry_size_usd,
            remaining_source_shares,
            shadow_pnl_usd,
            actual_pnl_usd,
            outcome,
            resolved_at,
            experiment_arm,
            segment_id,
            expected_edge,
            expected_fill_cost_usd,
            expected_exit_fee_usd,
            expected_close_fixed_cost_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_id,
            f"market-{trade_id}",
            "Question",
            f"wallet-{trade_id}",
            "yes",
            "buy",
            price_at_signal,
            signal_size_usd,
            confidence,
            0.1,
            0,
            0,
            int((resolved_at or 1_700_000_100) - 10),
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            entry_fee_usd,
            entry_fixed_cost_usd,
            actual_entry_shares,
            actual_entry_size_usd,
            actual_entry_shares,
            shadow_pnl_usd,
            None,
            outcome if outcome is not None else 1 if shadow_pnl_usd is not None and shadow_pnl_usd > 0 else 0 if shadow_pnl_usd is not None else None,
            resolved_at,
            experiment_arm,
            segment_id,
            expected_edge,
            expected_fill_cost_usd,
            expected_exit_fee_usd,
            expected_close_fixed_cost_usd,
        ),
    )


class SegmentShadowReportTest(TestCase):
    def test_report_keeps_fixed_segments_separate_from_legacy_unassigned_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                try:
                    _insert_trade(
                        conn,
                        trade_id="hot-win",
                        segment_id="hot_short",
                        shadow_pnl_usd=4.0,
                        resolved_at=1_700_000_100,
                    )
                    _insert_trade(
                        conn,
                        trade_id="hot-open",
                        segment_id="hot_short",
                        shadow_pnl_usd=None,
                        resolved_at=None,
                    )
                    _insert_trade(
                        conn,
                        trade_id="warm-loss",
                        segment_id="warm_mid",
                        shadow_pnl_usd=-2.0,
                        resolved_at=1_700_000_120,
                    )
                    _insert_trade(
                        conn,
                        trade_id="legacy-win",
                        segment_id=None,
                        shadow_pnl_usd=1.5,
                        resolved_at=1_700_000_140,
                    )
                    _insert_trade(
                        conn,
                        trade_id="challenger-win",
                        segment_id="hot_short",
                        experiment_arm="challenger_v1",
                        shadow_pnl_usd=9.0,
                        resolved_at=1_700_000_160,
                    )
                    conn.commit()
                finally:
                    conn.close()

                report = evaluator.compute_segment_shadow_report(min_resolved=1)
                payload = main._segment_shadow_state_payload(report)
                summary_rows = json.loads(str(payload["shadow_segment_summary_json"]))

                self.assertEqual(report["total_segments"], len(SEGMENT_IDS) + 1)
                self.assertEqual(report["ready_count"], 2)
                self.assertEqual(report["positive_count"], 1)
                self.assertEqual(report["negative_count"], 1)
                self.assertEqual(report["history_status"], "mixed")
                self.assertEqual(report["routed_resolved"], 2)
                self.assertEqual(report["legacy_unassigned_resolved"], 1)
                self.assertAlmostEqual(float(report["routed_coverage_pct"]), 2 / 3, places=6)
                self.assertEqual(report["legacy_unassigned_resolved"], 1)
                self.assertEqual(payload["routed_shadow_status"], "insufficient")
                self.assertEqual(payload["routed_shadow_min_resolved"], 20)
                self.assertEqual(payload["routed_shadow_routed_resolved"], 2)
                self.assertEqual(payload["routed_shadow_legacy_resolved"], 1)
                self.assertEqual(payload["routed_shadow_total_resolved"], 3)
                self.assertAlmostEqual(float(payload["routed_shadow_coverage_pct"]), 2 / 3, places=6)
                self.assertFalse(payload["routed_shadow_ready"])
                self.assertIn("legacy/unassigned resolved", str(report["summary"]))
                self.assertIn("fixed-segment resolved", str(report["summary"]))

                hot_short = next(row for row in report["segments"] if row["segment_id"] == "hot_short")
                self.assertEqual(hot_short["signals"], 2)
                self.assertEqual(hot_short["acted"], 2)
                self.assertEqual(hot_short["resolved"], 1)
                self.assertEqual(hot_short["health"], "ready")
                self.assertIsNone(hot_short["profit_factor"])
                self.assertEqual(hot_short["profit_factor_text"], "inf")

                warm_mid = next(row for row in report["segments"] if row["segment_id"] == "warm_mid")
                self.assertEqual(warm_mid["health"], "blocked")

                fallback = next(row for row in report["segments"] if row["segment_id"] == SEGMENT_FALLBACK)
                self.assertEqual(fallback["resolved"], 0)
                self.assertEqual(fallback["health"], "insufficient")

                legacy = next(row for row in report["segments"] if row["segment_id"] == evaluator.UNASSIGNED_SEGMENT_ID)
                self.assertEqual(legacy["resolved"], 1)
                self.assertEqual(legacy["health"], "legacy")

                hot_short_json = next(row for row in summary_rows if row["segment_id"] == "hot_short")
                self.assertIsNone(hot_short_json["profit_factor"])
                self.assertEqual(hot_short_json["profit_factor_text"], "inf")
            finally:
                db.DB_PATH = original_db_path

    def test_report_does_not_relabel_missing_segment_ids_as_fallback(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                try:
                    _insert_trade(
                        conn,
                        trade_id="legacy-only",
                        segment_id="",
                        shadow_pnl_usd=3.0,
                        resolved_at=1_700_000_200,
                    )
                    conn.commit()
                finally:
                    conn.close()

                report = evaluator.compute_segment_shadow_report(min_resolved=20)

                fallback = next(row for row in report["segments"] if row["segment_id"] == SEGMENT_FALLBACK)
                legacy = next(row for row in report["segments"] if row["segment_id"] == evaluator.UNASSIGNED_SEGMENT_ID)

                self.assertEqual(fallback["resolved"], 0)
                self.assertEqual(fallback["signals"], 0)
                self.assertEqual(legacy["resolved"], 1)
                self.assertEqual(legacy["health"], "legacy")
                self.assertEqual(report["ready_count"], 0)
                self.assertEqual(report["status"], "legacy_only")
                self.assertEqual(report["history_status"], "legacy_only")
                self.assertEqual(report["routed_resolved"], 0)
                self.assertIn("legacy/unassigned resolved", str(report["summary"]))
                self.assertIn("predate fixed segment routing", str(report["summary"]))
            finally:
                db.DB_PATH = original_db_path

    def test_report_scopes_current_shadow_history_to_fresh_epoch_boundary(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                try:
                    _insert_trade(
                        conn,
                        trade_id="pre-epoch-legacy",
                        segment_id=None,
                        shadow_pnl_usd=2.5,
                        resolved_at=1_700_000_100,
                    )
                    _insert_trade(
                        conn,
                        trade_id="post-epoch-routed",
                        segment_id="hot_short",
                        shadow_pnl_usd=3.5,
                        resolved_at=1_700_001_100,
                    )
                    conn.commit()
                finally:
                    conn.close()

                epoch_file = Path(tmpdir) / "data" / "shadow_evidence_epoch.json"
                shadow_evidence.write_shadow_evidence_epoch(
                    started_at=1_700_001_000,
                    source="shadow_reset",
                    request_id="epoch-1",
                    message="fresh evidence epoch",
                    path=epoch_file,
                )

                with patch.object(main, "SHADOW_EVIDENCE_EPOCH_FILE", epoch_file):
                    since_ts, promotion = main._current_shadow_segment_scope_since_ts({"applied_at": 1_700_000_500})
                    report = evaluator.compute_segment_shadow_report(min_resolved=1, since_ts=since_ts)
                    payload = main._segment_shadow_state_payload(report)
            finally:
                db.DB_PATH = original_db_path

            self.assertEqual(since_ts, 1_700_001_000)
            self.assertEqual(promotion, {"applied_at": 1_700_000_500})
            self.assertEqual(report["scope"], "since_ts")
            self.assertEqual(report["since_ts"], 1_700_001_000)
            self.assertEqual(report["routed_resolved"], 1)
            self.assertEqual(report["legacy_unassigned_resolved"], 0)
            self.assertEqual(report["ready_count"], 1)
            self.assertNotIn("legacy/unassigned resolved", str(report["summary"]))
            self.assertEqual(payload["shadow_evidence_epoch_started_at"], 1_700_001_000)
            self.assertEqual(payload["shadow_segment_scope_started_at"], 1_700_001_000)
            self.assertEqual(payload["shadow_segment_ready_count"], 1)
            self.assertEqual(payload["shadow_segment_legacy_resolved"], 0)
            self.assertEqual(payload["shadow_segment_history_status"], "routed_only")

    def test_daily_report_skips_when_db_integrity_fails(self) -> None:
        with patch(
            "evaluator.database_integrity_state",
            return_value={
                "db_integrity_known": True,
                "db_integrity_ok": False,
                "db_integrity_message": "database disk image is malformed",
            },
        ), patch("evaluator.send_alert") as send_alert, patch(
            "evaluator.resolve_shadow_trades"
        ) as resolve_shadow_trades:
            evaluator.daily_report()

        resolve_shadow_trades.assert_not_called()
        send_alert.assert_called_once()
        self.assertIn("skipped", str(send_alert.call_args.args[0]).lower())
        self.assertIn("integrity", str(send_alert.call_args.args[0]).lower())

    def test_daily_report_uses_epoch_scoped_shadow_reporting_and_segment_scope(self) -> None:
        shadow_report = {
            "resolved": 4,
            "win_rate": 0.75,
            "total_pnl_usd": 12.5,
            "return_pct": 0.0125,
            "weekly_pnl_usd": 6.0,
            "profit_factor": 1.4,
            "expectancy_usd": 3.125,
            "expectancy_pct": 0.104,
            "exposure_pct": 0.0,
            "max_drawdown_pct": 0.02,
            "sharpe": 1.7,
            "avg_confidence": 0.66,
            "avg_size_usd": 10.0,
            "top_traders": [],
            "scope": "current_evidence_window",
            "since_ts": 1_700_123_456,
            "shadow_evidence_epoch_source": "shadow_reset",
            "legacy_resolved_excluded": 8,
        }
        live_report = {
            "acted": 0,
            "resolved": 0,
            "win_rate": 0.0,
            "total_pnl_usd": 0.0,
            "return_pct": None,
            "weekly_pnl_usd": 0.0,
            "profit_factor": None,
            "expectancy_usd": None,
            "expectancy_pct": None,
            "exposure_pct": None,
            "max_drawdown_pct": None,
            "sharpe": 0.0,
            "avg_confidence": 0.0,
            "avg_size_usd": 0.0,
            "top_traders": [],
            "scope": "all_history",
            "since_ts": 0,
            "shadow_evidence_epoch_source": "",
            "legacy_resolved_excluded": 0,
        }
        with patch(
            "evaluator.database_integrity_state",
            return_value={
                "db_integrity_known": True,
                "db_integrity_ok": True,
                "db_integrity_message": "",
            },
        ), patch("evaluator.resolve_shadow_trades"), patch(
            "evaluator.compute_performance_report",
            side_effect=[shadow_report, live_report],
        ) as compute_report, patch(
            "evaluator._current_shadow_segment_report_since_ts",
            return_value=1_700_123_456,
        ), patch(
            "evaluator.compute_segment_shadow_report",
            return_value={"total_segments": 1, "status": "blocked", "summary": "need more resolved"},
        ) as segment_report, patch(
            "evaluator.persist_performance_snapshot"
        ) as persist_snapshot, patch("evaluator.send_alert") as send_alert:
            evaluator.daily_report()

        compute_report.assert_any_call("shadow", apply_shadow_evidence_epoch=True)
        segment_report.assert_called_once_with(mode="shadow", since_ts=1_700_123_456)
        persist_snapshot.assert_any_call("shadow")
        persist_snapshot.assert_any_call("live")
        alert_text = str(send_alert.call_args.args[0])
        self.assertIn("shadow scope: current evidence window", alert_text.lower())
        self.assertIn("legacy/all-time resolved trades excluded", alert_text.lower())

    def test_report_blocks_segment_on_large_calibration_gap(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                try:
                    for index in range(8):
                        _insert_trade(
                            conn,
                            trade_id=f"cal-win-{index}",
                            segment_id="hot_short",
                            shadow_pnl_usd=5.0,
                            resolved_at=1_700_000_200 + index,
                            confidence=0.9,
                        )
                    for index in range(12):
                        _insert_trade(
                            conn,
                            trade_id=f"cal-loss-{index}",
                            segment_id="hot_short",
                            shadow_pnl_usd=-1.0,
                            resolved_at=1_700_000_300 + index,
                            confidence=0.9,
                        )
                    conn.commit()
                finally:
                    conn.close()

                report = evaluator.compute_segment_shadow_report(min_resolved=20)
                hot_short = next(row for row in report["segments"] if row["segment_id"] == "hot_short")

                self.assertEqual(hot_short["health"], "blocked")
                self.assertIn("cal 0.500", " | ".join(hot_short["failure_reasons"]))
                self.assertAlmostEqual(float(hot_short["calibration_gap"]), 0.5, places=6)
                self.assertAlmostEqual(float(hot_short["win_rate"]), 0.4, places=6)
                self.assertAlmostEqual(float(hot_short["brier_score"]), 0.49, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_report_blocks_segment_on_fill_cost_slippage(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                try:
                    for index in range(10):
                        _insert_trade(
                            conn,
                            trade_id=f"slip-win-{index}",
                            segment_id="warm_mid",
                            shadow_pnl_usd=2.0,
                            resolved_at=1_700_000_400 + index,
                            confidence=0.55,
                            entry_fee_usd=0.06,
                            entry_fixed_cost_usd=0.04,
                            expected_fill_cost_usd=0.02,
                        )
                    for index in range(10):
                        _insert_trade(
                            conn,
                            trade_id=f"slip-loss-{index}",
                            segment_id="warm_mid",
                            shadow_pnl_usd=-1.0,
                            resolved_at=1_700_000_500 + index,
                            confidence=0.55,
                            entry_fee_usd=0.06,
                            entry_fixed_cost_usd=0.04,
                            expected_fill_cost_usd=0.02,
                        )
                    conn.commit()
                finally:
                    conn.close()

                report = evaluator.compute_segment_shadow_report(min_resolved=20)
                warm_mid = next(row for row in report["segments"] if row["segment_id"] == "warm_mid")

                self.assertEqual(warm_mid["health"], "blocked")
                self.assertIn("fill slip $0.080 > $0.050", " | ".join(warm_mid["failure_reasons"]))
                self.assertAlmostEqual(float(warm_mid["avg_expected_fill_cost_usd"]), 0.02, places=6)
                self.assertAlmostEqual(float(warm_mid["avg_realized_fill_cost_usd"]), 0.10, places=6)
                self.assertAlmostEqual(float(warm_mid["avg_fill_cost_slippage_usd"]), 0.08, places=6)
                self.assertAlmostEqual(float(warm_mid["max_fill_cost_slippage_usd"]), 0.05, places=6)
            finally:
                db.DB_PATH = original_db_path
