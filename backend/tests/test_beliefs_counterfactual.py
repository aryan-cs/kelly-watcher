from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import kelly_watcher.engine.beliefs as beliefs
import kelly_watcher.data.db as db


def _insert_trade(
    conn,
    *,
    trade_id: str,
    skipped: bool,
    skip_reason: str | None,
    signal_mode: str | None,
    outcome: int,
    shadow_pnl_usd: float | None,
) -> None:
    actual_entry_price = 0.50 if not skipped else None
    actual_entry_shares = 20.0 if not skipped else None
    actual_entry_size_usd = 10.0 if not skipped else None
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id, market_id, question, trader_address, side, source_action,
            price_at_signal, signal_size_usd, confidence, kelly_fraction,
            real_money, skipped, skip_reason, signal_mode, placed_at, outcome,
            actual_entry_price, actual_entry_shares, actual_entry_size_usd, shadow_pnl_usd,
            f_trader_win_rate, f_conviction_ratio, f_consistency, f_days_to_res, f_price,
            f_spread_pct, f_momentum_1h, f_volume_trend, f_oi_usd, f_bid_depth_usd, f_ask_depth_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_id,
            f"market-{trade_id}",
            "Question",
            "0xabc",
            "yes",
            "buy",
            0.70,
            10.0,
            0.59,
            0.10,
            0,
            1 if skipped else 0,
            skip_reason,
            signal_mode,
            1_700_000_000,
            outcome,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            shadow_pnl_usd,
            0.62,
            0.55,
            0.40,
            0.25,
            0.70,
            0.02,
            0.03,
            1.10,
            200_000.0,
            10_000.0,
            10_000.0,
        ),
    )


class BeliefsCounterfactualTest(unittest.TestCase):
    def test_sync_beliefs_learns_from_low_confidence_missed_winners_without_counting_operational_skips(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                beliefs.invalidate_belief_cache()
                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="executed-win",
                    skipped=False,
                    skip_reason=None,
                    signal_mode="heuristic",
                    outcome=1,
                    shadow_pnl_usd=2.5,
                )
                _insert_trade(
                    conn,
                    trade_id="missed-win",
                    skipped=True,
                    skip_reason="confidence was 59.0%, below the 60.0% minimum needed to place a trade",
                    signal_mode="heuristic",
                    outcome=1,
                    shadow_pnl_usd=None,
                )
                _insert_trade(
                    conn,
                    trade_id="risk-blocked-win",
                    skipped=True,
                    skip_reason="would exceed total open exposure from $15.00 to $45.00, above the 30.0% cap",
                    signal_mode="heuristic",
                    outcome=1,
                    shadow_pnl_usd=None,
                )
                conn.commit()
                conn.close()

                applied = beliefs.sync_belief_priors()

                conn = db.get_conn()
                global_row = conn.execute(
                    """
                    SELECT wins, losses
                    FROM belief_priors
                    WHERE feature_name='__global__' AND bucket='all'
                    """
                ).fetchone()
                update_count = conn.execute(
                    "SELECT COUNT(*) AS n FROM belief_updates"
                ).fetchone()["n"]
                conn.close()

                self.assertEqual(applied, 2)
                self.assertAlmostEqual(float(global_row["wins"]), 1.35, places=6)
                self.assertAlmostEqual(float(global_row["losses"]), 0.0, places=6)
                self.assertEqual(int(update_count), 3)
            finally:
                db.DB_PATH = original_db_path
                beliefs.invalidate_belief_cache()


if __name__ == "__main__":
    unittest.main()
