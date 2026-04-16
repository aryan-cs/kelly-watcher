from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import kelly_watcher.dashboard_api as dashboard_api
import kelly_watcher.data.db as db
import kelly_watcher.runtime.evaluator as evaluator
import kelly_watcher.runtime.performance_preview as performance_preview
import kelly_watcher.main as main
def _insert_trade(
    conn,
    *,
    trade_id: str,
    placed_at: int,
    resolved_at: int | None = None,
    skipped: int = 0,
    remaining_entry_shares: float = 0.0,
    remaining_entry_size_usd: float = 0.0,
    shadow_pnl_usd: float | None = None,
    segment_id: str | None = None,
    policy_id: str | None = None,
    policy_bundle_version: int = 0,
    promotion_epoch_id: int = 0,
    experiment_arm: str = "champion",
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
            policy_id,
            policy_bundle_version,
            promotion_epoch_id,
            experiment_arm,
            expected_edge,
            expected_fill_cost_usd,
            expected_exit_fee_usd,
            expected_close_fixed_cost_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            skipped,
            placed_at,
            0.5,
            20.0,
            10.0,
            remaining_entry_shares,
            remaining_entry_size_usd,
            remaining_entry_shares,
            shadow_pnl_usd,
            None,
            1 if shadow_pnl_usd and shadow_pnl_usd > 0 else 0 if shadow_pnl_usd is not None else None,
            resolved_at,
            segment_id,
            policy_id,
            policy_bundle_version,
            promotion_epoch_id,
            experiment_arm,
            0.12,
            0.03,
            0.01,
            0.0,
        ),
    )


def _build_archived_fixture() -> tuple[TemporaryDirectory[str], Path]:
    tmpdir = TemporaryDirectory()
    db_path = Path(tmpdir.name) / "data" / "trading.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    original_db_path = db.DB_PATH
    try:
        db.DB_PATH = db_path
        db.init_db()
        conn = db.get_conn()
        try:
            _insert_trade(
                conn,
                trade_id="archived-resolved",
                placed_at=700,
                resolved_at=800,
                shadow_pnl_usd=2.0,
                segment_id="hot_short",
                policy_id="champion.hot_short",
                policy_bundle_version=3,
                promotion_epoch_id=7,
            )
            _insert_trade(
                conn,
                trade_id="preserved-resolved",
                placed_at=930,
                resolved_at=950,
                shadow_pnl_usd=1.5,
                segment_id="warm_mid",
                policy_id="champion.warm_mid",
                policy_bundle_version=4,
                promotion_epoch_id=8,
            )
            _insert_trade(
                conn,
                trade_id="archived-skipped",
                placed_at=650,
                skipped=1,
                remaining_entry_shares=0.0,
                remaining_entry_size_usd=0.0,
                shadow_pnl_usd=None,
            )
            _insert_trade(
                conn,
                trade_id="open-position",
                placed_at=600,
                remaining_entry_shares=20.0,
                remaining_entry_size_usd=10.0,
                shadow_pnl_usd=None,
                segment_id="discovery_long",
            )
            conn.commit()
        finally:
            conn.close()
    finally:
        db.DB_PATH = original_db_path

    result = db.archive_old_trade_log_rows(
        path=db_path,
        cutoff_ts=1_000,
        preserve_since_ts=900,
        batch_size=10,
        vacuum=False,
    )
    if not bool(result.get("ok")):
        raise AssertionError(f"archive failed: {result}")
    return tmpdir, db_path


class TradeLogArchiveTest(unittest.TestCase):
    def test_trade_log_archive_state_reports_sizes_and_eligible_counts(self) -> None:
        tmpdir, db_path = _build_archived_fixture()
        self.addCleanup(tmpdir.cleanup)

        state = db.trade_log_archive_state(
            path=db_path,
            cutoff_ts=1_000,
            preserve_since_ts=900,
        )

        self.assertTrue(state["trade_log_archive_state_known"])
        self.assertEqual(state["trade_log_archive_status"], "idle")
        self.assertGreater(int(state["trade_log_archive_active_db_size_bytes"] or 0), 0)
        self.assertGreaterEqual(
            int(state["trade_log_archive_active_db_allocated_bytes"] or 0),
            int(state["trade_log_archive_active_db_size_bytes"] or 0),
        )
        self.assertGreater(int(state["trade_log_archive_archive_db_size_bytes"] or 0), 0)
        self.assertGreaterEqual(
            int(state["trade_log_archive_archive_db_allocated_bytes"] or 0),
            int(state["trade_log_archive_archive_db_size_bytes"] or 0),
        )
        self.assertEqual(int(state["trade_log_archive_active_row_count"] or 0), 2)
        self.assertEqual(int(state["trade_log_archive_archive_row_count"] or 0), 2)
        self.assertEqual(int(state["trade_log_archive_eligible_row_count"] or 0), 0)
        self.assertEqual(int(state["trade_log_archive_cutoff_ts"] or 0), 1_000)
        self.assertEqual(int(state["trade_log_archive_preserve_since_ts"] or 0), 900)
        self.assertIn("no eligible", str(state["trade_log_archive_message"]).lower())

    def test_archive_moves_only_cold_closed_rows_and_preserves_lineage_fields(self) -> None:
        tmpdir, db_path = _build_archived_fixture()
        self.addCleanup(tmpdir.cleanup)

        live_conn = db.get_conn_for_path(db_path, apply_runtime_pragmas=False)
        archive_conn = db.get_conn_for_path(db.trade_log_archive_db_path(db_path), apply_runtime_pragmas=False)
        try:
            live_rows = live_conn.execute(
                "SELECT trade_id FROM trade_log ORDER BY trade_id"
            ).fetchall()
            archive_row = archive_conn.execute(
                """
                SELECT trade_id, segment_id, policy_id, policy_bundle_version, promotion_epoch_id, experiment_arm
                FROM trade_log
                WHERE trade_id='archived-resolved'
                """
            ).fetchone()
        finally:
            live_conn.close()
            archive_conn.close()

        self.assertEqual(
            [str(row["trade_id"]) for row in live_rows],
            ["open-position", "preserved-resolved"],
        )
        self.assertIsNotNone(archive_row)
        assert archive_row is not None
        self.assertEqual(str(archive_row["segment_id"]), "hot_short")
        self.assertEqual(str(archive_row["policy_id"]), "champion.hot_short")
        self.assertEqual(int(archive_row["policy_bundle_version"] or 0), 3)
        self.assertEqual(int(archive_row["promotion_epoch_id"] or 0), 7)
        self.assertEqual(str(archive_row["experiment_arm"]), "champion")

    def test_trade_log_read_conn_reads_hot_and_archived_rows_together(self) -> None:
        tmpdir, db_path = _build_archived_fixture()
        self.addCleanup(tmpdir.cleanup)

        conn = db.get_trade_log_read_conn(db_path, apply_runtime_pragmas=False)
        try:
            rows = conn.execute(
                "SELECT trade_id FROM trade_log ORDER BY trade_id"
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(
            [str(row["trade_id"]) for row in rows],
            ["archived-resolved", "archived-skipped", "open-position", "preserved-resolved"],
        )

    def test_resolved_shadow_trade_count_uses_archive_for_all_history_but_not_recent_scope(self) -> None:
        tmpdir, db_path = _build_archived_fixture()
        self.addCleanup(tmpdir.cleanup)

        with patch.object(db, "DB_PATH", db_path), patch.object(main, "DB_PATH", db_path):
            all_time = main._resolved_shadow_trade_count_filtered()
            recent_only = main._resolved_shadow_trade_count_filtered(since_ts=900)

        self.assertEqual(all_time, 2)
        self.assertEqual(recent_only, 1)

    def test_tracker_preview_summary_keeps_all_history_totals_after_archive(self) -> None:
        tmpdir, db_path = _build_archived_fixture()
        self.addCleanup(tmpdir.cleanup)

        summary = performance_preview.compute_tracker_preview_summary(
            mode="shadow",
            db_path=db_path,
            use_bot_state_balance=False,
        )

        self.assertEqual(summary.resolved, 2)
        self.assertAlmostEqual(float(summary.total_pnl or 0.0), 20.0, places=6)
        self.assertEqual(summary.routed_resolved, 2)

    def test_segment_shadow_report_counts_archived_routed_rows(self) -> None:
        tmpdir, db_path = _build_archived_fixture()
        self.addCleanup(tmpdir.cleanup)

        report = evaluator.compute_segment_shadow_report(db_path=db_path, min_resolved=1)
        segments = {str(row["segment_id"]): row for row in report["segments"]}

        self.assertEqual(int(segments["hot_short"]["resolved"] or 0), 1)
        self.assertEqual(int(segments["warm_mid"]["resolved"] or 0), 1)
        self.assertEqual(report["legacy_unassigned_resolved"], 0)

    def test_dashboard_query_rows_reads_trade_log_union_after_archive(self) -> None:
        tmpdir, db_path = _build_archived_fixture()
        self.addCleanup(tmpdir.cleanup)

        with patch.object(dashboard_api, "DB_PATH", db_path):
            rows = dashboard_api._query_rows(
                """
                SELECT
                  COUNT(*) AS n,
                  ROUND(SUM(CASE WHEN resolved_at IS NOT NULL THEN COALESCE(shadow_pnl_usd, 0) ELSE 0 END), 3) AS pnl
                FROM trade_log
                """,
                [],
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["n"] or 0), 4)
        self.assertAlmostEqual(float(rows[0]["pnl"] or 0.0), 3.5, places=6)


if __name__ == "__main__":
    unittest.main()
