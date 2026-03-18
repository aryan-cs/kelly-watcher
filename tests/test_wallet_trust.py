from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
from wallet_trust import WalletTrustState, apply_wallet_trust_sizing, get_wallet_trust_state


def _insert_trade(
    conn,
    *,
    trade_id: str,
    trader_address: str,
    skipped: bool,
    resolved_pnl_usd: float | None = None,
) -> None:
    actual_entry_price = 0.50 if not skipped else None
    actual_entry_shares = 20.0 if not skipped else None
    actual_entry_size_usd = 10.0 if not skipped else None
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id, market_id, question, trader_address, side, source_action,
            price_at_signal, signal_size_usd, confidence, kelly_fraction,
            real_money, skipped, placed_at, actual_entry_price, actual_entry_shares,
            actual_entry_size_usd, shadow_pnl_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_id,
            f"market-{trade_id}",
            "Question",
            trader_address,
            "yes",
            "buy",
            0.50,
            10.0,
            0.70,
            0.10,
            0,
            1 if skipped else 0,
            1_700_000_000,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            resolved_pnl_usd,
        ),
    )


class WalletTrustTest(unittest.TestCase):
    def test_wallet_stays_in_cold_start_until_it_has_minimum_observed_buys(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                for idx in range(2):
                    _insert_trade(
                        conn,
                        trade_id=f"obs-{idx}",
                        trader_address="0xabc",
                        skipped=True,
                        resolved_pnl_usd=1.0,
                    )
                conn.commit()
                conn.close()

                with patch.dict(
                    os.environ,
                    {
                        "WALLET_COLD_START_MIN_OBSERVED_BUYS": "3",
                        "WALLET_DISCOVERY_MIN_OBSERVED_BUYS": "8",
                        "WALLET_DISCOVERY_MIN_RESOLVED_BUYS": "3",
                        "WALLET_DISCOVERY_SIZE_MULTIPLIER": "0.05",
                        "WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS": "15",
                        "WALLET_PROBATION_SIZE_MULTIPLIER": "0.20",
                    },
                    clear=False,
                ):
                    state = get_wallet_trust_state("0xabc")

                self.assertEqual(state.tier, "cold_start")
                self.assertEqual(state.observed_buy_count, 2)
                self.assertEqual(state.resolved_observed_buy_count, 2)
                self.assertEqual(
                    state.skip_reason,
                    "wallet is still in cold start, observed 2/3 buy opportunities",
                )
            finally:
                db.DB_PATH = original_db_path

    def test_wallet_moves_from_discovery_to_probation_to_trusted(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                for idx in range(5):
                    _insert_trade(
                        conn,
                        trade_id=f"discovery-{idx}",
                        trader_address="0xabc",
                        skipped=True,
                        resolved_pnl_usd=1.0 if idx < 2 else None,
                    )
                conn.commit()
                conn.close()

                with patch.dict(
                    os.environ,
                    {
                        "WALLET_COLD_START_MIN_OBSERVED_BUYS": "3",
                        "WALLET_DISCOVERY_MIN_OBSERVED_BUYS": "8",
                        "WALLET_DISCOVERY_MIN_RESOLVED_BUYS": "3",
                        "WALLET_DISCOVERY_SIZE_MULTIPLIER": "0.05",
                        "WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS": "15",
                        "WALLET_PROBATION_SIZE_MULTIPLIER": "0.20",
                    },
                    clear=False,
                ):
                    discovery_state = get_wallet_trust_state("0xabc")

                self.assertEqual(discovery_state.tier, "discovery")
                self.assertEqual(discovery_state.size_multiplier, 0.05)
                self.assertIn("wallet is in discovery", discovery_state.tier_note or "")

                conn = db.get_conn()
                for idx in range(3):
                    _insert_trade(
                        conn,
                        trade_id=f"resolved-seed-{idx}",
                        trader_address="0xabc",
                        skipped=True,
                        resolved_pnl_usd=1.0,
                    )
                for idx in range(5):
                    _insert_trade(
                        conn,
                        trade_id=f"copied-{idx}",
                        trader_address="0xabc",
                        skipped=False,
                        resolved_pnl_usd=1.0 if idx % 2 == 0 else -0.5,
                    )
                conn.commit()
                conn.close()

                with patch.dict(
                    os.environ,
                    {
                        "WALLET_COLD_START_MIN_OBSERVED_BUYS": "3",
                        "WALLET_DISCOVERY_MIN_OBSERVED_BUYS": "8",
                        "WALLET_DISCOVERY_MIN_RESOLVED_BUYS": "3",
                        "WALLET_DISCOVERY_SIZE_MULTIPLIER": "0.05",
                        "WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS": "15",
                        "WALLET_PROBATION_SIZE_MULTIPLIER": "0.20",
                    },
                    clear=False,
                ):
                    probation_state = get_wallet_trust_state("0xabc")

                self.assertEqual(probation_state.tier, "probation")
                self.assertEqual(probation_state.resolved_copied_buy_count, 5)
                self.assertAlmostEqual(probation_state.resolved_copied_win_rate or 0.0, 0.6, places=6)

                conn = db.get_conn()
                for idx in range(15):
                    _insert_trade(
                        conn,
                        trade_id=f"trusted-{idx}",
                        trader_address="0xabc",
                        skipped=False,
                        resolved_pnl_usd=0.25,
                    )
                conn.commit()
                conn.close()

                with patch.dict(
                    os.environ,
                    {
                        "WALLET_COLD_START_MIN_OBSERVED_BUYS": "3",
                        "WALLET_DISCOVERY_MIN_OBSERVED_BUYS": "8",
                        "WALLET_DISCOVERY_MIN_RESOLVED_BUYS": "3",
                        "WALLET_DISCOVERY_SIZE_MULTIPLIER": "0.05",
                        "WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS": "15",
                        "WALLET_PROBATION_SIZE_MULTIPLIER": "0.20",
                    },
                    clear=False,
                ):
                    trusted_state = get_wallet_trust_state("0xabc")

                self.assertEqual(trusted_state.tier, "trusted")
                self.assertEqual(trusted_state.resolved_copied_buy_count, 20)
                self.assertIsNone(trusted_state.skip_reason)
            finally:
                db.DB_PATH = original_db_path

    def test_probation_sizing_scales_down_and_tracks_effective_multiplier(self) -> None:
        trust_state = WalletTrustState(
            wallet_address="0xabc",
            tier="probation",
            size_multiplier=0.20,
            observed_buy_count=20,
            resolved_observed_buy_count=12,
            resolved_copied_buy_count=5,
            resolved_copied_win_rate=0.6,
            resolved_copied_avg_return=0.04,
            min_cold_start_observed_buy_count=3,
            min_observed_buy_count=8,
            min_resolved_observed_buy_count=3,
            min_resolved_copied_buy_count=15,
        )

        with patch.dict(os.environ, {"MIN_BET_USD": "1.00"}, clear=False):
            adjusted = apply_wallet_trust_sizing(
                {"dollar_size": 5.0, "kelly_f": 0.10, "full_kelly_f": 0.20},
                trust_state,
            )

        self.assertEqual(adjusted["dollar_size"], 1.0)
        self.assertAlmostEqual(adjusted["kelly_f"], 0.02, places=6)
        self.assertAlmostEqual(adjusted["full_kelly_f"], 0.04, places=6)
        self.assertAlmostEqual(adjusted["wallet_trust_effective_multiplier"], 0.20, places=6)
        self.assertIn("size scaled to 20%", adjusted["wallet_trust_note"])

    def test_discovery_sizing_scales_down_before_probation(self) -> None:
        trust_state = WalletTrustState(
            wallet_address="0xabc",
            tier="discovery",
            size_multiplier=0.05,
            observed_buy_count=4,
            resolved_observed_buy_count=1,
            resolved_copied_buy_count=0,
            resolved_copied_win_rate=None,
            resolved_copied_avg_return=None,
            min_cold_start_observed_buy_count=3,
            min_observed_buy_count=8,
            min_resolved_observed_buy_count=3,
            min_resolved_copied_buy_count=15,
        )

        with patch.dict(os.environ, {"MIN_BET_USD": "1.00"}, clear=False):
            adjusted = apply_wallet_trust_sizing(
                {"dollar_size": 20.0, "kelly_f": 0.10, "full_kelly_f": 0.20},
                trust_state,
            )

        self.assertEqual(adjusted["dollar_size"], 1.0)
        self.assertAlmostEqual(adjusted["wallet_trust_effective_multiplier"], 0.05, places=6)
        self.assertIn("wallet is in discovery", adjusted["wallet_trust_note"])


if __name__ == "__main__":
    unittest.main()
