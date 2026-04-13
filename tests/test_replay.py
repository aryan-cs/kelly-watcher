from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db
from replay import ReplayPolicy, run_replay


def _insert_trade(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    market_id: str,
    trader_address: str,
    signal_mode: str,
    confidence: float,
    price_at_signal: float,
    placed_at: int,
    resolved_at: int | None = None,
    skipped: bool = False,
    actual_entry_price: float | None = None,
    actual_entry_size_usd: float | None = None,
    shadow_pnl_usd: float | None = None,
    counterfactual_return: float | None = None,
    signal_payload: dict | None = None,
    real_money: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO trade_log (
            trade_id, market_id, question, trader_address, side, token_id, source_action,
            price_at_signal, signal_size_usd, actual_entry_price, actual_entry_shares,
            actual_entry_size_usd, confidence, kelly_fraction, signal_mode, real_money,
            skipped, skip_reason, placed_at, resolved_at, counterfactual_return, shadow_pnl_usd,
            decision_context_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            trade_id,
            market_id,
            f"Question for {trade_id}",
            trader_address,
            "yes",
            f"token-{trade_id}",
            "buy",
            price_at_signal,
            actual_entry_size_usd if actual_entry_size_usd is not None else 100.0,
            actual_entry_price,
            (actual_entry_size_usd / actual_entry_price) if actual_entry_size_usd and actual_entry_price else None,
            actual_entry_size_usd,
            confidence,
            0.1,
            signal_mode,
            real_money,
            1 if skipped else 0,
            "counterfactual only" if skipped else None,
            placed_at,
            resolved_at,
            counterfactual_return,
            shadow_pnl_usd,
            json.dumps({"signal": signal_payload or {}}, separators=(",", ":")),
        ),
    )


class ReplayTest(unittest.TestCase):
    def test_replay_policy_normalizes_segment_filters(self) -> None:
        policy = ReplayPolicy.from_payload(
            {
                "allowed_entry_price_bands": [">=0.70", "0.60-0.69", ">=0.70"],
                "allowed_time_to_close_bands": "2h-12h,<=5m",
                "heuristic_min_time_to_close_seconds": "15m",
                "model_min_time_to_close_seconds": 3600,
            }
        )

        self.assertEqual(policy.allowed_entry_price_bands, ("0.60-0.69", ">=0.70"))
        self.assertEqual(policy.allowed_time_to_close_bands, ("<=5m", "2h-12h"))
        self.assertEqual(policy.heuristic_min_time_to_close_seconds, 900)
        self.assertEqual(policy.model_min_time_to_close_seconds, 3600)

        with self.assertRaisesRegex(ValueError, "Unknown allowed_entry_price_bands values"):
            ReplayPolicy.from_payload({"allowed_entry_price_bands": ["bad-band"]})
        with self.assertRaisesRegex(ValueError, "heuristic_min_time_to_close_seconds"):
            ReplayPolicy.from_payload({"heuristic_min_time_to_close_seconds": "-5m"})

    def test_run_replay_persists_summary_and_trade_decisions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="heur-pass",
                    market_id="market-1",
                    trader_address="0xaaa",
                    signal_mode="heuristic",
                    confidence=0.70,
                    price_at_signal=0.68,
                    actual_entry_price=0.68,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=20.0,
                    placed_at=1_700_000_000,
                    resolved_at=1_700_000_100,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.85},
                        "min_confidence": 0.55,
                    },
                )
                _insert_trade(
                    conn,
                    trade_id="heur-band-reject",
                    market_id="market-2",
                    trader_address="0xbbb",
                    signal_mode="heuristic",
                    confidence=0.75,
                    price_at_signal=0.80,
                    actual_entry_price=0.80,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=25.0,
                    placed_at=1_700_000_010,
                    resolved_at=1_700_000_110,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.90},
                        "min_confidence": 0.55,
                    },
                )
                _insert_trade(
                    conn,
                    trade_id="model-edge-reject",
                    market_id="market-3",
                    trader_address="0xccc",
                    signal_mode="model",
                    confidence=0.60,
                    price_at_signal=0.58,
                    actual_entry_price=0.58,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=50.0,
                    placed_at=1_700_000_020,
                    resolved_at=1_700_000_120,
                    signal_payload={
                        "mode": "xgboost",
                        "edge": 0.02,
                    },
                )
                conn.commit()
                conn.close()

                result = run_replay(
                    policy=ReplayPolicy.from_payload(
                        {
                            "initial_bankroll_usd": 1000.0,
                            "min_confidence": 0.55,
                            "min_bet_usd": 1.0,
                            "heuristic_min_entry_price": 0.65,
                            "heuristic_max_entry_price": 0.75,
                            "model_edge_mid_confidence": 0.55,
                            "model_edge_high_confidence": 0.65,
                            "model_edge_mid_threshold": 0.05,
                            "model_edge_high_threshold": 0.05,
                            "max_bet_fraction": 0.10,
                            "max_total_open_exposure_fraction": 1.0,
                            "max_market_exposure_fraction": 1.0,
                            "max_trader_exposure_fraction": 1.0,
                        }
                    ),
                    db_path=test_db_path,
                    label="unit-test",
                )

                self.assertEqual(result["trade_count"], 3)
                self.assertEqual(result["accepted_count"], 1)
                self.assertEqual(result["rejected_count"], 2)
                self.assertAlmostEqual(result["final_bankroll_usd"], 1011.548, places=3)
                self.assertEqual(result["reject_reason_summary"]["heuristic_entry_band"], 1)
                self.assertEqual(result["reject_reason_summary"]["model_edge_below_threshold"], 1)
                self.assertEqual(result["signal_mode_summary"]["heuristic"]["accepted_count"], 1)
                self.assertEqual(result["signal_mode_summary"]["heuristic"]["win_count"], 1)
                self.assertEqual(result["signal_mode_summary"]["xgboost"]["accepted_count"], 0)
                self.assertEqual(result["trader_concentration"]["trader_count"], 1)
                self.assertEqual(result["trader_concentration"]["top_accepted_trader_address"], "0xaaa")
                self.assertEqual(result["trader_concentration"]["top_accepted_count"], 1)
                self.assertEqual(result["trader_concentration"]["top_accepted_share"], 1.0)
                self.assertEqual(result["trader_concentration"]["top_abs_pnl_trader_address"], "0xaaa")
                self.assertEqual(result["trader_concentration"]["top_abs_pnl_share"], 1.0)
                self.assertEqual(result["entry_price_band_concentration"]["entry_price_band_count"], 1)
                self.assertEqual(result["entry_price_band_concentration"]["top_accepted_entry_price_band"], "0.60-0.69")
                self.assertEqual(result["entry_price_band_concentration"]["top_accepted_share"], 1.0)
                self.assertEqual(result["time_to_close_band_concentration"]["time_to_close_band_count"], 1)
                self.assertEqual(result["time_to_close_band_concentration"]["top_accepted_time_to_close_band"], "<=5m")
                self.assertEqual(result["time_to_close_band_concentration"]["top_abs_pnl_share"], 1.0)

                conn = sqlite3.connect(str(test_db_path))
                conn.row_factory = sqlite3.Row
                run_row = conn.execute("SELECT * FROM replay_runs").fetchone()
                trade_rows = conn.execute(
                    "SELECT trade_id, decision, reason FROM replay_trades ORDER BY trade_log_id ASC"
                ).fetchall()
                mode_segment = conn.execute(
                    """
                    SELECT segment_value, accepted_count, total_pnl_usd
                    FROM segment_metrics
                    WHERE segment_kind='signal_mode'
                    ORDER BY segment_value ASC
                    """
                ).fetchall()
                horizon_segment = conn.execute(
                    """
                    SELECT segment_value, accepted_count, total_pnl_usd
                    FROM segment_metrics
                    WHERE segment_kind='time_to_close_band'
                    ORDER BY segment_value ASC
                    """
                ).fetchall()
                conn.close()

                self.assertEqual(run_row["label"], "unit-test")
                self.assertEqual(run_row["accepted_count"], 1)
                self.assertEqual(
                    [(row["trade_id"], row["decision"], row["reason"]) for row in trade_rows],
                    [
                        ("heur-pass", "accept", "accepted"),
                        ("heur-band-reject", "reject", "heuristic_entry_band"),
                        ("model-edge-reject", "reject", "model_edge_below_threshold"),
                    ],
                )
                self.assertIn(("heuristic", 1, 11.548), [(row["segment_value"], row["accepted_count"], round(float(row["total_pnl_usd"]), 3)) for row in mode_segment])
                self.assertIn(("<=5m", 1, 11.548), [(row["segment_value"], row["accepted_count"], round(float(row["total_pnl_usd"]), 3)) for row in horizon_segment])
                self.assertEqual(result["segment_leaders"]["signal_mode"]["best"]["segment_value"], "heuristic")
                self.assertEqual(result["segment_leaders"]["time_to_close_band"]["best"]["segment_value"], "<=5m")
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_can_accept_skipped_counterfactual_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="skip-counterfactual",
                    market_id="market-skip",
                    trader_address="0xskip",
                    signal_mode="heuristic",
                    confidence=0.50,
                    price_at_signal=0.66,
                    placed_at=1_700_000_000,
                    resolved_at=1_700_000_100,
                    skipped=True,
                    counterfactual_return=0.50,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.82},
                        "min_confidence": 0.45,
                    },
                )
                conn.commit()
                conn.close()

                result = run_replay(
                    policy=ReplayPolicy.from_payload(
                        {
                            "initial_bankroll_usd": 1000.0,
                            "min_confidence": 0.45,
                            "min_bet_usd": 1.0,
                            "heuristic_min_entry_price": 0.65,
                            "heuristic_max_entry_price": 0.75,
                            "model_edge_mid_confidence": 0.55,
                            "model_edge_high_confidence": 0.65,
                            "model_edge_mid_threshold": 0.05,
                            "model_edge_high_threshold": 0.05,
                            "max_bet_fraction": 0.10,
                            "max_total_open_exposure_fraction": 1.0,
                            "max_market_exposure_fraction": 1.0,
                            "max_trader_exposure_fraction": 1.0,
                        }
                    ),
                    db_path=test_db_path,
                )

                self.assertEqual(result["accepted_count"], 1)
                self.assertAlmostEqual(result["final_bankroll_usd"], 1015.075, places=3)
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_reports_trader_concentration(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="trader-a-1",
                    market_id="market-a-1",
                    trader_address="0xaaa",
                    signal_mode="heuristic",
                    confidence=0.70,
                    price_at_signal=0.68,
                    actual_entry_price=0.68,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=10.0,
                    placed_at=1_700_010_000,
                    resolved_at=1_700_010_100,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.85},
                        "min_confidence": 0.55,
                    },
                )
                _insert_trade(
                    conn,
                    trade_id="trader-a-2",
                    market_id="market-a-2",
                    trader_address="0xaaa",
                    signal_mode="heuristic",
                    confidence=0.72,
                    price_at_signal=0.69,
                    actual_entry_price=0.69,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=8.0,
                    placed_at=1_700_010_200,
                    resolved_at=1_700_010_300,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.86},
                        "min_confidence": 0.55,
                    },
                )
                _insert_trade(
                    conn,
                    trade_id="trader-b-1",
                    market_id="market-b-1",
                    trader_address="0xbbb",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.70,
                    actual_entry_price=0.70,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=-30.0,
                    placed_at=1_700_010_400,
                    resolved_at=1_700_010_500,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.88},
                        "min_confidence": 0.55,
                    },
                )
                conn.commit()
                conn.close()

                result = run_replay(
                    policy=ReplayPolicy.from_payload(
                        {
                            "initial_bankroll_usd": 1000.0,
                            "min_confidence": 0.55,
                            "min_bet_usd": 1.0,
                            "heuristic_min_entry_price": 0.65,
                            "heuristic_max_entry_price": 0.75,
                            "model_edge_mid_confidence": 0.55,
                            "model_edge_high_confidence": 0.65,
                            "model_edge_mid_threshold": 0.05,
                            "model_edge_high_threshold": 0.05,
                            "max_bet_fraction": 0.10,
                            "max_total_open_exposure_fraction": 1.0,
                            "max_market_exposure_fraction": 1.0,
                            "max_trader_exposure_fraction": 1.0,
                        }
                    ),
                    db_path=test_db_path,
                    label="trader-concentration",
                )

                self.assertEqual(result["accepted_count"], 3)
                self.assertEqual(result["trader_concentration"]["trader_count"], 2)
                self.assertEqual(result["trader_concentration"]["top_accepted_trader_address"], "0xaaa")
                self.assertEqual(result["trader_concentration"]["top_accepted_count"], 2)
                self.assertAlmostEqual(result["trader_concentration"]["top_accepted_share"], 2 / 3, places=6)
                self.assertGreater(result["trader_concentration"]["top_accepted_total_pnl_usd"], 0.0)
                self.assertEqual(result["trader_concentration"]["top_abs_pnl_trader_address"], "0xbbb")
                self.assertGreater(result["trader_concentration"]["top_abs_pnl_usd"], abs(result["trader_concentration"]["top_accepted_total_pnl_usd"]))
                self.assertGreater(result["trader_concentration"]["top_abs_pnl_share"], 0.5)
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_reports_market_concentration(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="market-a-1",
                    market_id="shared-market",
                    trader_address="0xaaa",
                    signal_mode="heuristic",
                    confidence=0.70,
                    price_at_signal=0.68,
                    actual_entry_price=0.68,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=12.0,
                    placed_at=1_700_020_000,
                    resolved_at=1_700_020_100,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.85},
                        "min_confidence": 0.55,
                    },
                )
                _insert_trade(
                    conn,
                    trade_id="market-a-2",
                    market_id="shared-market",
                    trader_address="0xbbb",
                    signal_mode="heuristic",
                    confidence=0.72,
                    price_at_signal=0.69,
                    actual_entry_price=0.69,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=10.0,
                    placed_at=1_700_020_200,
                    resolved_at=1_700_020_300,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.86},
                        "min_confidence": 0.55,
                    },
                )
                _insert_trade(
                    conn,
                    trade_id="market-b-1",
                    market_id="single-market",
                    trader_address="0xccc",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.70,
                    actual_entry_price=0.70,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=-35.0,
                    placed_at=1_700_020_400,
                    resolved_at=1_700_020_500,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.88},
                        "min_confidence": 0.55,
                    },
                )
                conn.commit()
                conn.close()

                result = run_replay(
                    policy=ReplayPolicy.from_payload(
                        {
                            "initial_bankroll_usd": 1000.0,
                            "min_confidence": 0.55,
                            "min_bet_usd": 1.0,
                            "heuristic_min_entry_price": 0.65,
                            "heuristic_max_entry_price": 0.75,
                            "model_edge_mid_confidence": 0.55,
                            "model_edge_high_confidence": 0.65,
                            "model_edge_mid_threshold": 0.05,
                            "model_edge_high_threshold": 0.05,
                            "max_bet_fraction": 0.10,
                            "max_total_open_exposure_fraction": 1.0,
                            "max_market_exposure_fraction": 1.0,
                            "max_trader_exposure_fraction": 1.0,
                        }
                    ),
                    db_path=test_db_path,
                    label="market-concentration",
                )

                self.assertEqual(result["accepted_count"], 3)
                self.assertEqual(result["market_concentration"]["market_count"], 2)
                self.assertEqual(result["market_concentration"]["top_accepted_market_id"], "shared-market")
                self.assertEqual(result["market_concentration"]["top_accepted_count"], 2)
                self.assertAlmostEqual(result["market_concentration"]["top_accepted_share"], 2 / 3, places=6)
                self.assertGreater(result["market_concentration"]["top_accepted_total_pnl_usd"], 0.0)
                self.assertEqual(result["market_concentration"]["top_abs_pnl_market_id"], "single-market")
                self.assertGreater(result["market_concentration"]["top_abs_pnl_usd"], abs(result["market_concentration"]["top_accepted_total_pnl_usd"]))
                self.assertGreater(result["market_concentration"]["top_abs_pnl_share"], 0.5)
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_can_filter_by_time_window(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="early",
                    market_id="market-early",
                    trader_address="0xearly",
                    signal_mode="heuristic",
                    confidence=0.70,
                    price_at_signal=0.68,
                    actual_entry_price=0.68,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=20.0,
                    placed_at=1_700_000_000,
                    resolved_at=1_700_000_100,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.85}},
                )
                _insert_trade(
                    conn,
                    trade_id="late",
                    market_id="market-late",
                    trader_address="0xlate",
                    signal_mode="heuristic",
                    confidence=0.70,
                    price_at_signal=0.68,
                    actual_entry_price=0.68,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=30.0,
                    placed_at=1_700_100_000,
                    resolved_at=1_700_100_100,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.85}},
                )
                conn.commit()
                conn.close()

                result = run_replay(
                    policy=ReplayPolicy.from_payload(
                        {
                            "initial_bankroll_usd": 1000.0,
                            "min_confidence": 0.55,
                            "min_bet_usd": 1.0,
                            "heuristic_min_entry_price": 0.65,
                            "heuristic_max_entry_price": 0.75,
                            "model_edge_mid_confidence": 0.55,
                            "model_edge_high_confidence": 0.65,
                            "model_edge_mid_threshold": 0.05,
                            "model_edge_high_threshold": 0.05,
                            "max_bet_fraction": 0.10,
                            "max_total_open_exposure_fraction": 1.0,
                            "max_market_exposure_fraction": 1.0,
                            "max_trader_exposure_fraction": 1.0,
                        }
                    ),
                    db_path=test_db_path,
                    start_ts=1_700_050_000,
                    end_ts=1_700_200_000,
                )

                self.assertEqual(result["trade_count"], 1)
                self.assertEqual(result["accepted_count"], 1)
                self.assertEqual(result["window_start_ts"], 1_700_050_000)
                self.assertEqual(result["window_end_ts"], 1_700_200_000)
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_can_filter_by_entry_band_and_horizon_band(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="accept-band-horizon",
                    market_id="market-a",
                    trader_address="0xaaa",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.72,
                    actual_entry_price=0.72,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=18.0,
                    placed_at=1_700_000_000,
                    resolved_at=1_700_010_800,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.82}},
                )
                _insert_trade(
                    conn,
                    trade_id="reject-entry-band",
                    market_id="market-b",
                    trader_address="0xbbb",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.64,
                    actual_entry_price=0.64,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=18.0,
                    placed_at=1_700_000_010,
                    resolved_at=1_700_010_810,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.82}},
                )
                _insert_trade(
                    conn,
                    trade_id="reject-horizon-band",
                    market_id="market-c",
                    trader_address="0xccc",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.72,
                    actual_entry_price=0.72,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=18.0,
                    placed_at=1_700_000_020,
                    resolved_at=1_700_000_200,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.82}},
                )
                conn.commit()
                conn.close()

                result = run_replay(
                    policy=ReplayPolicy.from_payload(
                        {
                            "initial_bankroll_usd": 1000.0,
                            "min_confidence": 0.55,
                            "min_bet_usd": 1.0,
                            "heuristic_min_entry_price": 0.60,
                            "heuristic_max_entry_price": 0.80,
                            "model_edge_mid_confidence": 0.55,
                            "model_edge_high_confidence": 0.65,
                            "model_edge_mid_threshold": 0.05,
                            "model_edge_high_threshold": 0.05,
                            "max_bet_fraction": 0.10,
                            "max_total_open_exposure_fraction": 1.0,
                            "max_market_exposure_fraction": 1.0,
                            "max_trader_exposure_fraction": 1.0,
                            "allowed_entry_price_bands": [">=0.70"],
                            "allowed_time_to_close_bands": ["2h-12h"],
                        }
                    ),
                    db_path=test_db_path,
                )

                self.assertEqual(result["accepted_count"], 1)
                self.assertEqual(result["rejected_count"], 2)

                conn = sqlite3.connect(str(test_db_path))
                rows = conn.execute(
                    "SELECT trade_id, decision, reason FROM replay_trades ORDER BY trade_log_id ASC"
                ).fetchall()
                conn.close()

                self.assertEqual(
                    rows,
                    [
                        ("accept-band-horizon", "accept", "accepted"),
                        ("reject-entry-band", "reject", "entry_price_band_filter"),
                        ("reject-horizon-band", "reject", "time_to_close_band_filter"),
                    ],
                )
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_can_filter_by_mode_specific_horizon_controls(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="heur-short",
                    market_id="market-h",
                    trader_address="0xaaa",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.72,
                    actual_entry_price=0.72,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=18.0,
                    placed_at=1_700_000_000,
                    resolved_at=1_700_000_900,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.82}},
                )
                _insert_trade(
                    conn,
                    trade_id="model-short",
                    market_id="market-m",
                    trader_address="0xbbb",
                    signal_mode="xgboost",
                    confidence=0.74,
                    price_at_signal=0.72,
                    actual_entry_price=0.72,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=18.0,
                    placed_at=1_700_000_010,
                    resolved_at=1_700_000_910,
                    signal_payload={"mode": "xgboost", "edge": 0.10},
                )
                conn.commit()
                conn.close()

                result = run_replay(
                    policy=ReplayPolicy.from_payload(
                        {
                            "initial_bankroll_usd": 1000.0,
                            "min_confidence": 0.55,
                            "min_bet_usd": 1.0,
                            "heuristic_min_entry_price": 0.60,
                            "heuristic_max_entry_price": 0.80,
                            "heuristic_min_time_to_close_seconds": 1800,
                            "model_edge_mid_confidence": 0.55,
                            "model_edge_high_confidence": 0.65,
                            "model_edge_mid_threshold": 0.05,
                            "model_edge_high_threshold": 0.05,
                            "model_min_time_to_close_seconds": 0,
                            "max_bet_fraction": 0.10,
                            "max_total_open_exposure_fraction": 1.0,
                            "max_market_exposure_fraction": 1.0,
                            "max_trader_exposure_fraction": 1.0,
                        }
                    ),
                    db_path=test_db_path,
                )

                self.assertEqual(result["accepted_count"], 1)
                self.assertEqual(result["rejected_count"], 1)

                conn = sqlite3.connect(str(test_db_path))
                rows = conn.execute(
                    "SELECT trade_id, decision, reason FROM replay_trades ORDER BY trade_log_id ASC"
                ).fetchall()
                conn.close()

                self.assertEqual(
                    rows,
                    [
                        ("heur-short", "reject", "heuristic_time_to_close_filter"),
                        ("model-short", "accept", "accepted"),
                    ],
                )
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_applies_daily_loss_guard_and_resets_next_day(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                first_ts = 1_700_000_000
                next_day_ts = first_ts + 90_000
                _insert_trade(
                    conn,
                    trade_id="loss-day-one",
                    market_id="market-loss",
                    trader_address="0xloss",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.70,
                    actual_entry_price=0.70,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=-60.0,
                    placed_at=first_ts,
                    resolved_at=first_ts + 60,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.85}},
                )
                _insert_trade(
                    conn,
                    trade_id="blocked-same-day",
                    market_id="market-blocked",
                    trader_address="0xblock",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.70,
                    actual_entry_price=0.70,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=30.0,
                    placed_at=first_ts + 120,
                    resolved_at=first_ts + 180,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.85}},
                )
                _insert_trade(
                    conn,
                    trade_id="reset-next-day",
                    market_id="market-reset",
                    trader_address="0xreset",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.70,
                    actual_entry_price=0.70,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=30.0,
                    placed_at=next_day_ts,
                    resolved_at=next_day_ts + 60,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.85}},
                )
                conn.commit()
                conn.close()

                result = run_replay(
                    policy=ReplayPolicy.from_payload(
                        {
                            "initial_bankroll_usd": 1000.0,
                            "min_confidence": 0.55,
                            "min_bet_usd": 1.0,
                            "heuristic_min_entry_price": 0.65,
                            "heuristic_max_entry_price": 0.75,
                            "model_edge_mid_confidence": 0.55,
                            "model_edge_high_confidence": 0.65,
                            "model_edge_mid_threshold": 0.05,
                            "model_edge_high_threshold": 0.05,
                            "max_bet_fraction": 0.10,
                            "max_total_open_exposure_fraction": 1.0,
                            "max_market_exposure_fraction": 1.0,
                            "max_trader_exposure_fraction": 1.0,
                            "max_daily_loss_pct": 0.01,
                            "max_live_drawdown_pct": 0.0,
                        }
                    ),
                    db_path=test_db_path,
                )

                self.assertEqual(result["accepted_count"], 2)
                self.assertEqual(result["rejected_count"], 1)

                conn = sqlite3.connect(str(test_db_path))
                rows = conn.execute(
                    "SELECT trade_id, decision, reason FROM replay_trades ORDER BY trade_log_id ASC"
                ).fetchall()
                conn.close()

                self.assertEqual(
                    rows,
                    [
                        ("loss-day-one", "accept", "accepted"),
                        ("blocked-same-day", "reject", "daily_loss_guard"),
                        ("reset-next-day", "accept", "accepted"),
                    ],
                )
                self.assertEqual(result["reject_reason_summary"]["daily_loss_guard"], 1)
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_applies_live_drawdown_guard_in_live_mode(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                first_ts = 1_700_000_000
                _insert_trade(
                    conn,
                    trade_id="live-loss",
                    market_id="market-live-loss",
                    trader_address="0xlive1",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.70,
                    actual_entry_price=0.70,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=-60.0,
                    placed_at=first_ts,
                    resolved_at=first_ts + 60,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.85}},
                    real_money=1,
                )
                _insert_trade(
                    conn,
                    trade_id="live-blocked",
                    market_id="market-live-blocked",
                    trader_address="0xlive2",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.70,
                    actual_entry_price=0.70,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=30.0,
                    placed_at=first_ts + 120,
                    resolved_at=first_ts + 180,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.85}},
                    real_money=1,
                )
                conn.commit()
                conn.close()

                result = run_replay(
                    policy=ReplayPolicy.from_payload(
                        {
                            "mode": "live",
                            "initial_bankroll_usd": 1000.0,
                            "min_confidence": 0.55,
                            "min_bet_usd": 1.0,
                            "heuristic_min_entry_price": 0.65,
                            "heuristic_max_entry_price": 0.75,
                            "model_edge_mid_confidence": 0.55,
                            "model_edge_high_confidence": 0.65,
                            "model_edge_mid_threshold": 0.05,
                            "model_edge_high_threshold": 0.05,
                            "max_bet_fraction": 0.10,
                            "max_total_open_exposure_fraction": 1.0,
                            "max_market_exposure_fraction": 1.0,
                            "max_trader_exposure_fraction": 1.0,
                            "max_daily_loss_pct": 0.0,
                            "max_live_drawdown_pct": 0.01,
                        }
                    ),
                    db_path=test_db_path,
                )

                self.assertEqual(result["accepted_count"], 1)
                self.assertEqual(result["rejected_count"], 1)

                conn = sqlite3.connect(str(test_db_path))
                rows = conn.execute(
                    "SELECT trade_id, decision, reason FROM replay_trades ORDER BY trade_log_id ASC"
                ).fetchall()
                conn.close()

                self.assertEqual(
                    rows,
                    [
                        ("live-loss", "accept", "accepted"),
                        ("live-blocked", "reject", "live_drawdown_guard"),
                    ],
                )
                self.assertEqual(result["reject_reason_summary"]["live_drawdown_guard"], 1)
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
