from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.data.db as db
from kelly_watcher.engine.wallet_trust import (
    WalletTrustState,
    allow_duplicate_side_override,
    apply_wallet_trust_sizing,
    get_wallet_trust_state,
    reset_wallet_skip_override_cache,
    total_open_exposure_cap_fraction_for_wallet,
)


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


def _insert_counterfactual_skip(
    conn,
    *,
    trade_id: str,
    trader_address: str,
    skip_reason: str,
    counterfactual_return: float,
) -> None:
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id, market_id, question, trader_address, side, source_action,
            price_at_signal, signal_size_usd, confidence, kelly_fraction,
            real_money, skipped, skip_reason, placed_at, counterfactual_return
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            1,
            skip_reason,
            1_700_000_000,
            counterfactual_return,
        ),
    )


class WalletTrustTest(unittest.TestCase):
    def tearDown(self) -> None:
        reset_wallet_skip_override_cache()

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

    def test_negative_local_copied_history_clamps_trusted_wallet_size(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                for idx in range(20):
                    _insert_trade(
                        conn,
                        trade_id=f"trusted-bad-{idx}",
                        trader_address="0xabc",
                        skipped=False,
                        resolved_pnl_usd=-1.5,
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
                        "WALLET_LOCAL_PERFORMANCE_PENALTY_MIN_RESOLVED_COPIED_BUYS": "15",
                        "WALLET_LOCAL_PERFORMANCE_PENALTY_MAX_AVG_RETURN": "-0.10",
                        "WALLET_LOCAL_PERFORMANCE_PENALTY_SIZE_MULTIPLIER": "0.25",
                    },
                    clear=False,
                ):
                    state = get_wallet_trust_state("0xabc")

                self.assertEqual(state.tier, "trusted")
                self.assertAlmostEqual(state.resolved_copied_avg_return or 0.0, -0.15, places=6)
                self.assertAlmostEqual(state.size_multiplier, 0.25, places=6)
                self.assertAlmostEqual(state.local_performance_penalty_multiplier or 0.0, 0.25, places=6)
                self.assertIn("local copied avg return -15.0%", state.tier_note or "")
                self.assertIn("limiting size to 25%", state.tier_note or "")
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

    def test_wallet_trust_rejects_risk_adjusted_size_below_minimum(self) -> None:
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
                {"dollar_size": 10.0, "kelly_f": 0.10, "full_kelly_f": 0.20},
                trust_state,
            )

        self.assertEqual(adjusted["dollar_size"], 0.0)
        self.assertEqual(adjusted["kelly_f"], 0.0)
        self.assertEqual(adjusted["wallet_trust_effective_multiplier"], 0.0)
        self.assertIn("risk-adjusted size $0.50 < min $1.00", adjusted["wallet_trust_note"])

    def test_quality_multiplier_scales_trusted_wallet_size_within_cap(self) -> None:
        trust_state = WalletTrustState(
            wallet_address="0xabc",
            tier="trusted",
            size_multiplier=1.0,
            observed_buy_count=40,
            resolved_observed_buy_count=30,
            resolved_copied_buy_count=20,
            resolved_copied_win_rate=0.65,
            resolved_copied_avg_return=0.07,
            min_cold_start_observed_buy_count=3,
            min_observed_buy_count=8,
            min_resolved_observed_buy_count=3,
            min_resolved_copied_buy_count=15,
        )

        with patch.dict(
            os.environ,
            {
                "MIN_BET_USD": "1.00",
                "WALLET_QUALITY_SIZE_MIN_MULTIPLIER": "0.75",
                "WALLET_QUALITY_SIZE_MAX_MULTIPLIER": "1.25",
            },
            clear=False,
        ):
            high_quality = apply_wallet_trust_sizing(
                {"dollar_size": 20.0, "kelly_f": 0.10, "full_kelly_f": 0.20},
                trust_state,
                quality_score=1.0,
                max_size_usd=30.0,
            )
            low_quality = apply_wallet_trust_sizing(
                {"dollar_size": 20.0, "kelly_f": 0.10, "full_kelly_f": 0.20},
                trust_state,
                quality_score=0.0,
                max_size_usd=30.0,
            )

        self.assertEqual(high_quality["dollar_size"], 25.0)
        self.assertAlmostEqual(high_quality["wallet_quality_multiplier"], 1.25, places=6)
        self.assertAlmostEqual(high_quality["wallet_trust_effective_multiplier"], 1.25, places=6)
        self.assertAlmostEqual(high_quality["kelly_f"], 0.125, places=6)
        self.assertIn("wallet quality 1.00 -> 125%", high_quality["wallet_trust_note"])

        self.assertEqual(low_quality["dollar_size"], 15.0)
        self.assertAlmostEqual(low_quality["wallet_quality_multiplier"], 0.75, places=6)
        self.assertAlmostEqual(low_quality["wallet_trust_effective_multiplier"], 0.75, places=6)
        self.assertAlmostEqual(low_quality["kelly_f"], 0.075, places=6)
        self.assertIn("wallet quality 0.00 -> 75%", low_quality["wallet_trust_note"])

    def test_quality_multiplier_respects_hard_max_size_cap(self) -> None:
        trust_state = WalletTrustState(
            wallet_address="0xabc",
            tier="trusted",
            size_multiplier=1.0,
            observed_buy_count=40,
            resolved_observed_buy_count=30,
            resolved_copied_buy_count=20,
            resolved_copied_win_rate=0.65,
            resolved_copied_avg_return=0.07,
            min_cold_start_observed_buy_count=3,
            min_observed_buy_count=8,
            min_resolved_observed_buy_count=3,
            min_resolved_copied_buy_count=15,
        )

        with patch.dict(
            os.environ,
            {
                "MIN_BET_USD": "1.00",
                "WALLET_QUALITY_SIZE_MIN_MULTIPLIER": "0.75",
                "WALLET_QUALITY_SIZE_MAX_MULTIPLIER": "1.25",
            },
            clear=False,
        ):
            adjusted = apply_wallet_trust_sizing(
                {"dollar_size": 20.0, "kelly_f": 0.10, "full_kelly_f": 0.20},
                trust_state,
                quality_score=1.0,
                max_size_usd=22.0,
            )

        self.assertEqual(adjusted["dollar_size"], 22.0)
        self.assertAlmostEqual(adjusted["wallet_quality_multiplier"], 1.25, places=6)
        self.assertAlmostEqual(adjusted["wallet_trust_effective_multiplier"], 1.1, places=6)
        self.assertAlmostEqual(adjusted["kelly_f"], 0.11, places=6)
        self.assertIn("size scaled to 110%", adjusted["wallet_trust_note"])

    def test_nonfinite_quality_score_is_neutral_and_not_reported(self) -> None:
        trust_state = WalletTrustState(
            wallet_address="0xabc",
            tier="trusted",
            size_multiplier=1.0,
            observed_buy_count=40,
            resolved_observed_buy_count=30,
            resolved_copied_buy_count=20,
            resolved_copied_win_rate=0.65,
            resolved_copied_avg_return=0.07,
            min_cold_start_observed_buy_count=3,
            min_observed_buy_count=8,
            min_resolved_observed_buy_count=3,
            min_resolved_copied_buy_count=15,
        )

        with patch.dict(
            os.environ,
            {
                "MIN_BET_USD": "1.00",
                "WALLET_QUALITY_SIZE_MIN_MULTIPLIER": "0.75",
                "WALLET_QUALITY_SIZE_MAX_MULTIPLIER": "1.25",
            },
            clear=False,
        ):
            adjusted = apply_wallet_trust_sizing(
                {"dollar_size": 20.0, "kelly_f": 0.10, "full_kelly_f": 0.20},
                trust_state,
                quality_score=float("inf"),
            )

        self.assertEqual(adjusted["dollar_size"], 20.0)
        self.assertIsNone(adjusted["wallet_quality_score"])
        self.assertAlmostEqual(adjusted["wallet_quality_multiplier"], 1.0, places=6)
        self.assertIn("wallet quality neutral", adjusted["wallet_trust_note"])

    def test_zero_wallet_trust_multiplier_forces_no_bet(self) -> None:
        trust_state = WalletTrustState(
            wallet_address="0xabc",
            tier="cold_start",
            size_multiplier=0.0,
            observed_buy_count=0,
            resolved_observed_buy_count=0,
            resolved_copied_buy_count=0,
            resolved_copied_win_rate=None,
            resolved_copied_avg_return=None,
            min_cold_start_observed_buy_count=3,
            min_observed_buy_count=8,
            min_resolved_observed_buy_count=3,
            min_resolved_copied_buy_count=15,
        )

        adjusted = apply_wallet_trust_sizing(
            {"dollar_size": 20.0, "kelly_f": 0.10, "full_kelly_f": 0.20},
            trust_state,
        )

        self.assertEqual(adjusted["dollar_size"], 0.0)
        self.assertEqual(adjusted["kelly_f"], 0.0)
        self.assertEqual(adjusted["full_kelly_f"], 0.0)
        self.assertEqual(adjusted["wallet_trust_effective_multiplier"], 0.0)
        self.assertIn("wallet is still in cold start", adjusted["reason"])

    def test_duplicate_side_override_qualifies_wallets_with_strong_counterfactual_history(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                for idx in range(20):
                    _insert_counterfactual_skip(
                        conn,
                        trade_id=f"dup-good-{idx}",
                        trader_address="0xgood",
                        skip_reason="we already had this side of the market open, so the trade was skipped",
                        counterfactual_return=0.10,
                    )
                for idx in range(20):
                    _insert_counterfactual_skip(
                        conn,
                        trade_id=f"dup-bad-{idx}",
                        trader_address="0xbad",
                        skip_reason="we already had this side of the market open, so the trade was skipped",
                        counterfactual_return=-0.02,
                    )
                conn.commit()
                conn.close()

                with patch.dict(
                    os.environ,
                    {
                        "DUPLICATE_SIDE_OVERRIDE_MIN_SKIPS": "20",
                        "DUPLICATE_SIDE_OVERRIDE_MIN_AVG_RETURN": "0.05",
                    },
                    clear=False,
                ):
                    reset_wallet_skip_override_cache()
                    self.assertTrue(allow_duplicate_side_override("0xgood"))
                    self.assertFalse(allow_duplicate_side_override("0xbad"))
            finally:
                db.DB_PATH = original_db_path

    def test_skip_overrides_ignore_nonfinite_counterfactual_returns(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                for idx in range(20):
                    _insert_counterfactual_skip(
                        conn,
                        trade_id=f"dup-nonfinite-{idx}",
                        trader_address="0xnonfinite",
                        skip_reason="we already had this side of the market open, so the trade was skipped",
                        counterfactual_return=float("inf"),
                    )
                    _insert_counterfactual_skip(
                        conn,
                        trade_id=f"exp-nonfinite-{idx}",
                        trader_address="0xnonfinite",
                        skip_reason="total open exposure would be $280.00 on $1000.00 equity, above the 25.0% cap",
                        counterfactual_return=float("inf"),
                    )
                conn.commit()
                conn.close()

                with patch.dict(
                    os.environ,
                    {
                        "DUPLICATE_SIDE_OVERRIDE_MIN_SKIPS": "20",
                        "DUPLICATE_SIDE_OVERRIDE_MIN_AVG_RETURN": "0.05",
                        "EXPOSURE_OVERRIDE_MIN_SKIPS": "20",
                        "EXPOSURE_OVERRIDE_MIN_AVG_RETURN": "0.03",
                        "EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION": "0.30",
                    },
                    clear=False,
                ):
                    reset_wallet_skip_override_cache()
                    self.assertFalse(allow_duplicate_side_override("0xnonfinite"))
                    self.assertAlmostEqual(
                        total_open_exposure_cap_fraction_for_wallet("0xnonfinite", 0.25),
                        0.25,
                        places=6,
                    )
            finally:
                db.DB_PATH = original_db_path

    def test_total_open_exposure_override_returns_higher_cap_for_qualified_wallets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                for idx in range(20):
                    _insert_counterfactual_skip(
                        conn,
                        trade_id=f"exp-good-{idx}",
                        trader_address="0xgood",
                        skip_reason="total open exposure would be $280.00 on $1000.00 equity, above the 25.0% cap",
                        counterfactual_return=0.08,
                    )
                for idx in range(20):
                    _insert_counterfactual_skip(
                        conn,
                        trade_id=f"exp-bad-{idx}",
                        trader_address="0xbad",
                        skip_reason="total open exposure would be $280.00 on $1000.00 equity, above the 25.0% cap",
                        counterfactual_return=-0.01,
                    )
                conn.commit()
                conn.close()

                with patch.dict(
                    os.environ,
                    {
                        "EXPOSURE_OVERRIDE_MIN_SKIPS": "20",
                        "EXPOSURE_OVERRIDE_MIN_AVG_RETURN": "0.03",
                        "EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION": "0.30",
                    },
                    clear=False,
                ):
                    reset_wallet_skip_override_cache()
                    self.assertAlmostEqual(
                        total_open_exposure_cap_fraction_for_wallet("0xgood", 0.25),
                        0.30,
                        places=6,
                    )
                    self.assertAlmostEqual(
                        total_open_exposure_cap_fraction_for_wallet("0xbad", 0.25),
                        0.25,
                        places=6,
                    )
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
