from __future__ import annotations

from datetime import datetime
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import evaluator


class DailyPnlCloseTimestampTest(unittest.TestCase):
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

                with patch("evaluator.time.time", return_value=fixed_now):
                    report = evaluator.compute_performance_report("shadow")

                self.assertAlmostEqual(report["weekly_pnl_usd"], 12.0, places=6)

                daily_pnls = {row["day"]: row["pnl"] for row in report["daily_pnls"]}
                recent_day = datetime.fromtimestamp(recent_close_ts).strftime("%Y-%m-%d")
                stale_day = datetime.fromtimestamp(stale_close_ts).strftime("%Y-%m-%d")
                self.assertAlmostEqual(daily_pnls[recent_day], 12.0, places=6)
                self.assertAlmostEqual(daily_pnls[stale_day], 9.0, places=6)
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
