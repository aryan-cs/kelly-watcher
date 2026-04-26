from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.engine.beliefs as beliefs
import kelly_watcher.data.db as db
from kelly_watcher.engine.market_scorer import MarketFeatures
from kelly_watcher.engine.trader_scorer import TraderFeatures


def _insert_trade(
    conn,
    *,
    trade_id: str,
    skipped: bool,
    skip_reason: str | None,
    signal_mode: str | None,
    outcome: int,
    shadow_pnl_usd: float | None,
    counterfactual_return: float | None = None,
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
            actual_entry_price, actual_entry_shares, actual_entry_size_usd, shadow_pnl_usd, counterfactual_return,
            f_trader_win_rate, f_conviction_ratio, f_consistency, f_days_to_res, f_price,
            f_spread_pct, f_momentum_1h, f_volume_trend, f_oi_usd, f_bid_depth_usd, f_ask_depth_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            counterfactual_return,
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


def _trader_features() -> TraderFeatures:
    return TraderFeatures(
        win_rate=0.62,
        n_trades=40,
        consistency=0.35,
        account_age_d=90,
        volume_usd=25_000.0,
        avg_size_usd=40.0,
        diversity=12,
        conviction_ratio=1.1,
    )


def _market_features() -> MarketFeatures:
    return MarketFeatures(
        best_bid=0.49,
        best_ask=0.51,
        mid=0.50,
        execution_price=0.51,
        bid_depth_usd=5_000.0,
        ask_depth_usd=5_000.0,
        days_to_res=1.0,
        price_1h_ago=0.50,
        volume_24h_usd=20_000.0,
        volume_7d_avg_usd=18_000.0,
        oi_usd=100_000.0,
        top_holder_pct=0.20,
        order_size_usd=10.0,
    )


class BeliefsCounterfactualTest(unittest.TestCase):
    def test_adjust_heuristic_confidence_rejects_nonfinite_base_confidence_even_with_positive_priors(self) -> None:
        prior_map = {
            ("__global__", "all"): (50.0, 0.0),
            ("confidence", "conf:>=0.8"): (50.0, 0.0),
        }
        with patch.object(beliefs, "_load_belief_map", return_value=prior_map):
            adjustment = beliefs.adjust_heuristic_confidence(
                float("nan"),
                _trader_features(),
                _market_features(),
            )

        self.assertEqual(adjustment.adjusted_confidence, 0.0)
        self.assertEqual(adjustment.prior_confidence, 0.5)
        self.assertEqual(adjustment.blend, 0.0)
        self.assertEqual(adjustment.evidence, 0)

    def test_belief_buckets_treat_nonfinite_values_as_unknown(self) -> None:
        self.assertEqual(beliefs._bucket_confidence(float("nan")), "conf:unknown")
        self.assertEqual(beliefs._bucket_oi_usd(float("inf")), "oi:unknown")
        self.assertIsNone(beliefs._average_depth(float("inf"), float("nan")))
        self.assertIsNone(beliefs._depth_ratio(float("inf"), 1_000.0))

    def test_belief_label_ignores_nonfinite_realized_and_counterfactual_returns(self) -> None:
        executed_row = {
            "skipped": 0,
            "source_action": "buy",
            "actual_entry_price": 0.50,
            "actual_entry_shares": 20.0,
            "actual_entry_size_usd": 10.0,
            "resolved_pnl_usd": float("inf"),
        }
        rejected_row = {
            "skipped": 1,
            "source_action": "buy",
            "actual_entry_price": None,
            "actual_entry_shares": None,
            "actual_entry_size_usd": None,
            "signal_mode": "heuristic",
            "market_veto": None,
            "skip_reason": "confidence was 59.0%, below the 60.0% minimum needed to place a trade",
            "counterfactual_return": float("-inf"),
        }

        self.assertIsNone(beliefs._belief_label_and_weight(executed_row))
        self.assertIsNone(beliefs._belief_label_and_weight(rejected_row))

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
                    counterfactual_return=0.20,
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
                self.assertEqual(int(update_count), 2)
            finally:
                db.DB_PATH = original_db_path
                beliefs.invalidate_belief_cache()


if __name__ == "__main__":
    unittest.main()
