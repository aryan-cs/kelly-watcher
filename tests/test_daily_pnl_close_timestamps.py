from __future__ import annotations

from datetime import datetime
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.data.db as db
import kelly_watcher.runtime.evaluator as evaluator


class DailyPnlCloseTimestampTest(unittest.TestCase):
    def test_perf_snapshots_schema_adds_scope_columns_on_existing_db(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "data" / "trading.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE perf_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        snapshot_at INTEGER NOT NULL,
                        mode TEXT NOT NULL,
                        n_signals INTEGER NOT NULL,
                        n_acted INTEGER NOT NULL,
                        n_resolved INTEGER NOT NULL,
                        win_rate REAL,
                        total_pnl_usd REAL,
                        avg_confidence REAL,
                        sharpe REAL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = db_path
                db.init_db()
                conn = db.get_conn()
                try:
                    columns = {row["name"] for row in conn.execute("PRAGMA table_info(perf_snapshots)").fetchall()}
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path

            self.assertIn("scope", columns)
            self.assertIn("since_ts", columns)
            self.assertIn("epoch_started_at", columns)
            self.assertIn("epoch_source", columns)
            self.assertIn("legacy_resolved_excluded", columns)

    def test_performance_report_uses_close_timestamps_for_weekly_and_daily_pnl(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                fixed_now = 2_000_000_000
                recent_close_ts = fixed_now - 60
                recent_resolve_ts = fixed_now - 120
                stale_close_ts = fixed_now - 9 * 86400

                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd, exited_at, resolved_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "close-weekly-1",
                            "market-close-1",
                            "Closed this week after an old entry",
                            "0xaaa",
                            "yes",
                            "buy",
                            0.45,
                            10.0,
                            0.70,
                            0.10,
                            0,
                            0,
                            fixed_now - 10 * 86400,
                            0.45,
                            22.222222,
                            10.0,
                            5.0,
                            recent_close_ts,
                            recent_close_ts,
                        ),
                        (
                            "close-weekly-2",
                            "market-close-2",
                            "Resolved this week after an old entry",
                            "0xbbb",
                            "yes",
                            "buy",
                            0.40,
                            12.0,
                            0.72,
                            0.12,
                            0,
                            0,
                            fixed_now - 8 * 86400,
                            0.40,
                            30.0,
                            12.0,
                            7.0,
                            None,
                            recent_resolve_ts,
                        ),
                        (
                            "close-stale",
                            "market-close-3",
                            "Closed before the weekly window",
                            "0xccc",
                            "yes",
                            "buy",
                            0.55,
                            8.0,
                            0.62,
                            0.08,
                            0,
                            0,
                            fixed_now - 11 * 86400,
                            0.55,
                            14.545455,
                            8.0,
                            9.0,
                            stale_close_ts,
                            stale_close_ts,
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                with patch("kelly_watcher.runtime.evaluator.time.time", return_value=fixed_now):
                    report = evaluator.compute_performance_report("shadow")

                self.assertAlmostEqual(report["weekly_pnl_usd"], 12.0, places=6)

                daily_pnls = {row["day"]: row["pnl"] for row in report["daily_pnls"]}
                recent_day = datetime.fromtimestamp(recent_close_ts).strftime("%Y-%m-%d")
                stale_day = datetime.fromtimestamp(stale_close_ts).strftime("%Y-%m-%d")
                self.assertAlmostEqual(daily_pnls[recent_day], 12.0, places=6)
                self.assertAlmostEqual(daily_pnls[stale_day], 9.0, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_shadow_performance_report_can_scope_to_current_evidence_window(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                epoch_started_at = 1_700_000_000

                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd, exited_at, resolved_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "legacy-1",
                            "market-legacy",
                            "Legacy trade",
                            "0xlegacy",
                            "yes",
                            "buy",
                            0.45,
                            10.0,
                            0.70,
                            0.10,
                            0,
                            0,
                            epoch_started_at - 10,
                            0.45,
                            22.222222,
                            10.0,
                            5.0,
                            epoch_started_at + 100,
                            epoch_started_at + 100,
                        ),
                        (
                            "current-1",
                            "market-current",
                            "Current trade",
                            "0xcurrent",
                            "yes",
                            "buy",
                            0.40,
                            12.0,
                            0.72,
                            0.12,
                            0,
                            0,
                            epoch_started_at + 10,
                            0.40,
                            30.0,
                            12.0,
                            7.0,
                            epoch_started_at + 200,
                            epoch_started_at + 200,
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                with patch(
                    "kelly_watcher.runtime.evaluator.read_shadow_evidence_epoch",
                    return_value={
                        "shadow_evidence_epoch_started_at": epoch_started_at,
                        "shadow_evidence_epoch_source": "shadow_reset",
                    },
                ):
                    report = evaluator.compute_performance_report(
                        "shadow",
                        apply_shadow_evidence_epoch=True,
                    )

                self.assertEqual(report["scope"], "current_evidence_window")
                self.assertEqual(report["since_ts"], epoch_started_at)
                self.assertEqual(report["shadow_evidence_epoch_started_at"], epoch_started_at)
                self.assertEqual(report["shadow_evidence_epoch_source"], "shadow_reset")
                self.assertEqual(report["resolved"], 1)
                self.assertEqual(report["all_time_resolved"], 2)
                self.assertEqual(report["legacy_resolved_excluded"], 1)
                self.assertAlmostEqual(report["total_pnl_usd"], 7.0, places=6)
                self.assertEqual(len(report["daily_pnls"]), 1)
                self.assertEqual(report["top_traders"][0]["trader_address"], "0xcurrent")
            finally:
                db.DB_PATH = original_db_path

    def test_shadow_performance_report_prefers_latest_promotion_within_epoch(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                epoch_started_at = 1_700_000_000
                promotion_applied_at = epoch_started_at + 50

                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd, exited_at, resolved_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "pre-promotion-1",
                            "market-pre",
                            "Pre-promotion trade",
                            "0xpre",
                            "yes",
                            "buy",
                            0.45,
                            10.0,
                            0.70,
                            0.10,
                            0,
                            0,
                            epoch_started_at + 10,
                            0.45,
                            22.222222,
                            10.0,
                            5.0,
                            epoch_started_at + 100,
                            epoch_started_at + 100,
                        ),
                        (
                            "post-promotion-1",
                            "market-post",
                            "Post-promotion trade",
                            "0xpost",
                            "yes",
                            "buy",
                            0.40,
                            12.0,
                            0.72,
                            0.12,
                            0,
                            0,
                            epoch_started_at + 60,
                            0.40,
                            30.0,
                            12.0,
                            7.0,
                            epoch_started_at + 200,
                            epoch_started_at + 200,
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                with patch(
                    "kelly_watcher.runtime.evaluator.read_shadow_evidence_epoch",
                    return_value={
                        "shadow_evidence_epoch_started_at": epoch_started_at,
                        "shadow_evidence_epoch_source": "shadow_reset",
                    },
                ), patch(
                    "kelly_watcher.runtime.evaluator._latest_applied_replay_promotion_at",
                    return_value=promotion_applied_at,
                ):
                    report = evaluator.compute_performance_report(
                        "shadow",
                        apply_shadow_evidence_epoch=True,
                    )

                self.assertEqual(report["scope"], "current_evidence_window")
                self.assertEqual(report["since_ts"], promotion_applied_at)
                self.assertEqual(report["shadow_evidence_epoch_started_at"], epoch_started_at)
                self.assertEqual(report["resolved"], 1)
                self.assertEqual(report["all_time_resolved"], 2)
                self.assertEqual(report["legacy_resolved_excluded"], 1)
                self.assertAlmostEqual(report["total_pnl_usd"], 7.0, places=6)
                self.assertEqual(report["top_traders"][0]["trader_address"], "0xpost")
            finally:
                db.DB_PATH = original_db_path

    def test_persist_performance_snapshot_records_shadow_scope_metadata(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                epoch_started_at = 1_700_000_000
                fixed_now = epoch_started_at + 600

                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
                        actual_entry_size_usd, shadow_pnl_usd, exited_at, resolved_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "legacy-snapshot-1",
                            "market-legacy",
                            "Legacy trade",
                            "0xlegacy",
                            "yes",
                            "buy",
                            0.45,
                            10.0,
                            0.70,
                            0.10,
                            0,
                            0,
                            epoch_started_at - 10,
                            0.45,
                            22.222222,
                            10.0,
                            5.0,
                            epoch_started_at + 100,
                            epoch_started_at + 100,
                        ),
                        (
                            "current-snapshot-1",
                            "market-current",
                            "Current trade",
                            "0xcurrent",
                            "yes",
                            "buy",
                            0.40,
                            12.0,
                            0.72,
                            0.12,
                            0,
                            0,
                            epoch_started_at + 10,
                            0.40,
                            30.0,
                            12.0,
                            7.0,
                            epoch_started_at + 200,
                            epoch_started_at + 200,
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                with patch(
                    "kelly_watcher.runtime.evaluator.read_shadow_evidence_epoch",
                    return_value={
                        "shadow_evidence_epoch_started_at": epoch_started_at,
                        "shadow_evidence_epoch_source": "shadow_reset",
                    },
                ), patch("kelly_watcher.runtime.evaluator.time.time", return_value=fixed_now):
                    evaluator.persist_performance_snapshot("shadow")

                conn = db.get_conn()
                try:
                    row = conn.execute(
                        """
                        SELECT
                            mode,
                            scope,
                            since_ts,
                            epoch_started_at,
                            epoch_source,
                            legacy_resolved_excluded,
                            n_resolved,
                            total_pnl_usd
                        FROM perf_snapshots
                        ORDER BY id DESC
                        LIMIT 1
                        """
                    ).fetchone()
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path

            self.assertEqual(row["mode"], "shadow")
            self.assertEqual(row["scope"], "current_evidence_window")
            self.assertEqual(row["since_ts"], epoch_started_at)
            self.assertEqual(row["epoch_started_at"], epoch_started_at)
            self.assertEqual(row["epoch_source"], "shadow_reset")
            self.assertEqual(row["legacy_resolved_excluded"], 1)
            self.assertEqual(row["n_resolved"], 1)
            self.assertAlmostEqual(float(row["total_pnl_usd"] or 0.0), 7.0, places=6)


if __name__ == "__main__":
    unittest.main()
