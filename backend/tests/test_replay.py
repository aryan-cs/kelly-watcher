from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.data.db as db
from kelly_watcher.research.replay import ReplayPolicy, policy_to_config_payload, run_replay


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
    market_close_ts: int | None = None,
    resolved_at: int | None = None,
    skipped: bool = False,
    actual_entry_price: float | None = None,
    actual_entry_size_usd: float | None = None,
    actual_pnl_usd: float | None = None,
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
            skipped, skip_reason, placed_at, resolved_at, counterfactual_return, actual_pnl_usd, shadow_pnl_usd,
            market_close_ts, decision_context_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            actual_pnl_usd,
            shadow_pnl_usd,
            market_close_ts,
            json.dumps({"signal": signal_payload or {}}, separators=(",", ":")),
        ),
    )


class ReplayTest(unittest.TestCase):
    def test_default_replay_policy_uses_global_runtime_filters(self) -> None:
        with (
            patch(
                "kelly_watcher.research.replay.allowed_entry_price_bands",
                return_value=("0.60-0.69", ">=0.70"),
            ),
            patch(
                "kelly_watcher.research.replay.allowed_time_to_close_bands",
                return_value=("15m-1h", "1h-2h"),
            ),
        ):
            policy = ReplayPolicy.default()

        self.assertEqual(policy.allowed_entry_price_bands, ("0.60-0.69", ">=0.70"))
        self.assertEqual(policy.allowed_time_to_close_bands, ("15m-1h", "1h-2h"))

    def test_replay_policy_config_payload_excludes_artifact_owned_base_edge_threshold(self) -> None:
        payload = policy_to_config_payload(ReplayPolicy.from_payload({"edge_threshold": 0.05}))

        self.assertNotIn("MODEL_BASE_EDGE_THRESHOLD", payload)
        self.assertIn("MODEL_EDGE_MID_THRESHOLD", payload)

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
                            "model_edge_mid_confidence": 0.65,
                            "model_edge_high_confidence": 0.65,
                            "edge_threshold": 0.03,
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
                self.assertGreater(result["peak_open_exposure_usd"], 0.0)
                self.assertGreater(result["max_open_exposure_share"], 0.0)

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
                self.assertGreater(run_row["peak_open_exposure_usd"], 0.0)
                self.assertGreater(run_row["max_open_exposure_share"], 0.0)
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

    def test_run_replay_keeps_unresolved_accepts_as_open_exposure(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="resolved-pass",
                    market_id="market-resolved",
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
                    trade_id="open-pass",
                    market_id="market-open",
                    trader_address="0xbbb",
                    signal_mode="heuristic",
                    confidence=0.72,
                    price_at_signal=0.69,
                    actual_entry_price=0.69,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=None,
                    placed_at=1_700_000_010,
                    resolved_at=None,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.86},
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
                    label="open-exposure",
                )

                self.assertEqual(result["accepted_count"], 2)
                self.assertEqual(result["resolved_count"], 1)
                self.assertEqual(result["unresolved_count"], 1)
                self.assertLess(result["final_bankroll_usd"], result["initial_bankroll_usd"])
                self.assertGreater(result["window_end_open_exposure_usd"], 0.0)
                self.assertGreater(result["window_end_open_exposure_share"], 0.0)
                self.assertAlmostEqual(
                    result["window_end_open_exposure_usd"],
                    result["final_equity_usd"] - result["final_bankroll_usd"],
                    places=6,
                )

                conn = sqlite3.connect(str(test_db_path))
                conn.row_factory = sqlite3.Row
                run_row = conn.execute("SELECT * FROM replay_runs").fetchone()
                trade_rows = conn.execute(
                    "SELECT trade_id, decision, pnl_usd, simulated_size_usd FROM replay_trades ORDER BY trade_log_id ASC"
                ).fetchall()
                segment_rows = conn.execute(
                    """
                    SELECT segment_value, accepted_size_usd, resolved_size_usd
                    FROM segment_metrics
                    WHERE segment_kind='signal_mode'
                    ORDER BY segment_value ASC
                    """
                ).fetchall()
                conn.close()

                accepted_size_usd = sum(float(row["simulated_size_usd"]) for row in trade_rows)
                resolved_size_usd = sum(
                    float(row["simulated_size_usd"])
                    for row in trade_rows
                    if row["pnl_usd"] is not None
                )

                self.assertAlmostEqual(result["accepted_size_usd"], accepted_size_usd)
                self.assertAlmostEqual(result["resolved_size_usd"], resolved_size_usd)
                self.assertAlmostEqual(
                    result["signal_mode_summary"]["heuristic"]["accepted_size_usd"],
                    accepted_size_usd,
                )
                self.assertAlmostEqual(
                    result["signal_mode_summary"]["heuristic"]["resolved_size_usd"],
                    resolved_size_usd,
                )
                self.assertAlmostEqual(float(run_row["window_end_open_exposure_usd"]), result["window_end_open_exposure_usd"])
                self.assertAlmostEqual(float(run_row["window_end_open_exposure_share"]), result["window_end_open_exposure_share"])

                self.assertEqual(
                    [(row["trade_id"], row["decision"], row["pnl_usd"] is None) for row in trade_rows],
                    [
                        ("resolved-pass", "accept", False),
                        ("open-pass", "accept", True),
                    ],
                )
                heuristic_segment = next(
                    row for row in segment_rows if row["segment_value"] == "heuristic"
                )
                self.assertAlmostEqual(float(heuristic_segment["accepted_size_usd"]), accepted_size_usd)
                self.assertAlmostEqual(float(heuristic_segment["resolved_size_usd"]), resolved_size_usd)
            finally:
                db.DB_PATH = original_db_path

    def test_live_replay_does_not_use_shadow_pnl_when_actual_pnl_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="live-shadow-fallback",
                    market_id="market-live",
                    trader_address="0xaaa",
                    signal_mode="heuristic",
                    confidence=0.70,
                    price_at_signal=0.68,
                    actual_entry_price=0.68,
                    actual_entry_size_usd=100.0,
                    actual_pnl_usd=None,
                    shadow_pnl_usd=50.0,
                    placed_at=1_700_000_000,
                    resolved_at=1_700_000_100,
                    real_money=1,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.85},
                        "min_confidence": 0.55,
                    },
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
                        }
                    ),
                    db_path=test_db_path,
                    label="live-no-shadow-fallback",
                )

                self.assertEqual(result["accepted_count"], 1)
                self.assertEqual(result["resolved_count"], 0)
                self.assertEqual(result["unresolved_count"], 1)
                self.assertEqual(result["total_pnl_usd"], 0.0)
                self.assertGreater(result["window_end_open_exposure_usd"], 0.0)
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_respects_window_end_for_resolution_and_pnl(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="resolved-in-window",
                    market_id="market-window-a",
                    trader_address="0xaaa",
                    signal_mode="heuristic",
                    confidence=0.70,
                    price_at_signal=0.68,
                    actual_entry_price=0.68,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=20.0,
                    placed_at=1_700_000_000,
                    resolved_at=1_700_000_050,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.85},
                        "min_confidence": 0.55,
                    },
                )
                _insert_trade(
                    conn,
                    trade_id="carry-past-window",
                    market_id="market-window-b",
                    trader_address="0xbbb",
                    signal_mode="heuristic",
                    confidence=0.72,
                    price_at_signal=0.69,
                    actual_entry_price=0.69,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=30.0,
                    placed_at=1_700_000_010,
                    resolved_at=1_700_000_200,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.86},
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
                    label="window-boundary",
                    start_ts=1_700_000_000,
                    end_ts=1_700_000_100,
                )

                conn = sqlite3.connect(str(test_db_path))
                conn.row_factory = sqlite3.Row
                run_row = conn.execute("SELECT * FROM replay_runs").fetchone()
                trade_rows = conn.execute(
                    "SELECT trade_id, pnl_usd, metadata_json FROM replay_trades ORDER BY trade_log_id ASC"
                ).fetchall()
                conn.close()

                realized_pnl = sum(float(row["pnl_usd"] or 0.0) for row in trade_rows if row["pnl_usd"] is not None)
                carried_trade = next(row for row in trade_rows if row["trade_id"] == "carry-past-window")
                carried_metadata = json.loads(carried_trade["metadata_json"])

                self.assertEqual(result["accepted_count"], 2)
                self.assertEqual(result["resolved_count"], 1)
                self.assertEqual(result["unresolved_count"], 1)
                self.assertAlmostEqual(result["total_pnl_usd"], realized_pnl)
                self.assertAlmostEqual(float(run_row["total_pnl_usd"]), realized_pnl)
                self.assertLess(result["final_bankroll_usd"], result["initial_bankroll_usd"])
                self.assertGreater(result["final_equity_usd"], result["final_bankroll_usd"])
                self.assertGreater(result["window_end_open_exposure_usd"], 0.0)
                self.assertGreater(result["window_end_open_exposure_share"], 0.0)
                self.assertAlmostEqual(
                    result["window_end_open_exposure_usd"],
                    result["final_equity_usd"] - result["final_bankroll_usd"],
                    places=6,
                )
                self.assertAlmostEqual(float(run_row["window_end_open_exposure_usd"]), result["window_end_open_exposure_usd"])
                self.assertAlmostEqual(float(run_row["window_end_open_exposure_share"]), result["window_end_open_exposure_share"])
                self.assertIsNone(carried_trade["pnl_usd"])
                self.assertTrue(bool(carried_metadata["window_carried"]))
                self.assertEqual(int(carried_metadata["eventual_close_ts"]), 1_700_000_200)
                self.assertAlmostEqual(float(carried_metadata["eventual_return_pct"]), 0.3, places=6)
                self.assertGreater(float(carried_metadata["eventual_pnl_usd"]), 0.0)
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_carries_unresolved_position_into_next_window(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="carry-forward",
                    market_id="market-carry",
                    trader_address="0xcarry",
                    signal_mode="heuristic",
                    confidence=0.72,
                    price_at_signal=0.69,
                    actual_entry_price=0.69,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=30.0,
                    placed_at=1_700_000_010,
                    resolved_at=1_700_000_250,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.86},
                        "min_confidence": 0.55,
                    },
                )
                conn.commit()
                conn.close()

                policy = ReplayPolicy.from_payload(
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
                )
                first_window = run_replay(
                    policy=policy,
                    db_path=test_db_path,
                    label="carry-w1",
                    start_ts=1_700_000_000,
                    end_ts=1_700_000_200,
                )
                second_window = run_replay(
                    policy=policy,
                    db_path=test_db_path,
                    label="carry-w2",
                    start_ts=1_700_000_200,
                    end_ts=1_700_000_300,
                    initial_state=first_window["continuity_state"],
                )

                self.assertEqual(first_window["accepted_count"], 1)
                self.assertEqual(first_window["resolved_count"], 0)
                self.assertEqual(first_window["unresolved_count"], 1)
                self.assertGreater(first_window["window_end_open_exposure_usd"], 0.0)
                self.assertEqual(first_window["window_end_signal_mode_exposure"]["heuristic"]["open_count"], 1)
                carried_size_usd = float(first_window["continuity_state"]["open_positions"][0]["size_usd"])
                self.assertAlmostEqual(
                    first_window["window_end_signal_mode_exposure"]["heuristic"]["open_size_usd"],
                    carried_size_usd,
                    places=6,
                )
                self.assertEqual(len(first_window["continuity_state"]["open_positions"]), 1)
                carried_pnl_usd = float(first_window["continuity_state"]["open_positions"][0]["pnl_usd"])

                self.assertEqual(second_window["accepted_count"], 0)
                self.assertEqual(second_window["resolved_count"], 1)
                self.assertEqual(second_window["unresolved_count"], 0)
                self.assertAlmostEqual(second_window["initial_bankroll_usd"], 1000.0, places=6)
                self.assertAlmostEqual(second_window["total_pnl_usd"], carried_pnl_usd, places=6)
                self.assertAlmostEqual(second_window["final_equity_usd"], 1000.0 + carried_pnl_usd, places=6)
                self.assertAlmostEqual(second_window["final_bankroll_usd"], 1000.0 + carried_pnl_usd, places=6)
                self.assertAlmostEqual(second_window["resolved_size_usd"], carried_size_usd, places=6)
                self.assertAlmostEqual(second_window["win_rate"], 1.0, places=6)
                self.assertEqual(second_window["signal_mode_summary"]["heuristic"]["accepted_count"], 0)
                self.assertEqual(second_window["signal_mode_summary"]["heuristic"]["resolved_count"], 1)
                self.assertAlmostEqual(
                    second_window["signal_mode_summary"]["heuristic"]["resolved_size_usd"],
                    carried_size_usd,
                    places=6,
                )
                self.assertAlmostEqual(
                    second_window["signal_mode_summary"]["heuristic"]["total_pnl_usd"],
                    carried_pnl_usd,
                    places=6,
                )
                self.assertEqual(second_window["window_end_open_exposure_usd"], 0.0)
                self.assertEqual(second_window["window_end_signal_mode_exposure"], {})
                self.assertEqual(second_window["continuity_state"]["open_positions"], [])
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_carry_state_can_block_next_window_entry(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                test_db_path = Path(tmpdir) / "data" / "trading.db"
                db.DB_PATH = test_db_path
                db.init_db()

                conn = db.get_conn()
                _insert_trade(
                    conn,
                    trade_id="carry-blocker",
                    market_id="market-blocker",
                    trader_address="0xcarry",
                    signal_mode="heuristic",
                    confidence=0.72,
                    price_at_signal=0.69,
                    actual_entry_price=0.69,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=20.0,
                    placed_at=1_700_000_010,
                    resolved_at=1_700_000_400,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.86},
                        "min_confidence": 0.55,
                    },
                )
                _insert_trade(
                    conn,
                    trade_id="blocked-by-carry",
                    market_id="market-next",
                    trader_address="0xnext",
                    signal_mode="heuristic",
                    confidence=0.72,
                    price_at_signal=0.69,
                    actual_entry_price=0.69,
                    actual_entry_size_usd=100.0,
                    shadow_pnl_usd=25.0,
                    placed_at=1_700_000_220,
                    resolved_at=1_700_000_320,
                    signal_payload={
                        "mode": "heuristic",
                        "market": {"score": 0.86},
                        "min_confidence": 0.55,
                    },
                )
                conn.commit()
                conn.close()

                policy = ReplayPolicy.from_payload(
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
                        "max_total_open_exposure_fraction": 0.10,
                        "max_market_exposure_fraction": 1.0,
                        "max_trader_exposure_fraction": 1.0,
                    }
                )
                first_window = run_replay(
                    policy=policy,
                    db_path=test_db_path,
                    label="carry-cap-w1",
                    start_ts=1_700_000_000,
                    end_ts=1_700_000_200,
                )
                second_window = run_replay(
                    policy=policy,
                    db_path=test_db_path,
                    label="carry-cap-w2",
                    start_ts=1_700_000_200,
                    end_ts=1_700_000_350,
                    initial_state=first_window["continuity_state"],
                )

                conn = sqlite3.connect(str(test_db_path))
                trade_rows = conn.execute(
                    """
                    SELECT trade_id, decision, reason
                    FROM replay_trades
                    WHERE replay_run_id=?
                    ORDER BY trade_log_id ASC
                    """,
                    (second_window["run_id"],),
                ).fetchall()
                conn.close()

                self.assertEqual(first_window["accepted_count"], 1)
                self.assertGreater(first_window["window_end_open_exposure_usd"], 0.0)
                self.assertEqual(second_window["accepted_count"], 0)
                self.assertEqual(second_window["rejected_count"], 1)
                self.assertEqual(
                    trade_rows,
                    [("blocked-by-carry", "reject", "total_exposure_cap")],
                )
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
                    confidence=0.56,
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
                    confidence=0.57,
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
                    confidence=0.90,
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
                self.assertEqual(result["trader_concentration"]["top_size_trader_address"], "0xbbb")
                self.assertGreater(result["trader_concentration"]["top_size_usd"], 0.0)
                self.assertGreater(result["trader_concentration"]["top_size_share"], 0.5)
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
                    market_close_ts=1_700_010_800,
                    resolved_at=1_700_000_200,
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
                    market_close_ts=1_700_010_810,
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
                    market_close_ts=1_700_000_200,
                    resolved_at=1_700_010_820,
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
                run_row = conn.execute(
                    "SELECT window_end_live_guard_triggered, window_end_daily_guard_triggered FROM replay_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
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
                run_row = conn.execute(
                    "SELECT window_end_live_guard_triggered, window_end_daily_guard_triggered FROM replay_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
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
                    actual_pnl_usd=-60.0,
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
                    actual_pnl_usd=30.0,
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
                run_row = conn.execute(
                    "SELECT window_end_live_guard_triggered, window_end_daily_guard_triggered FROM replay_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
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
                self.assertEqual(result["window_end_daily_guard_triggered"], 0)
                self.assertEqual(int(run_row[0]), 0)
                self.assertEqual(int(run_row[1]), 0)
            finally:
                db.DB_PATH = original_db_path

    def test_run_replay_records_window_end_daily_guard_when_day_ends_locked(self) -> None:
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
                    trade_id="daily-loss",
                    market_id="market-daily-loss",
                    trader_address="0xdaily1",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.70,
                    actual_entry_price=0.70,
                    actual_entry_size_usd=100.0,
                    actual_pnl_usd=-60.0,
                    shadow_pnl_usd=-60.0,
                    placed_at=first_ts,
                    resolved_at=first_ts + 60,
                    signal_payload={"mode": "heuristic", "market": {"score": 0.85}},
                )
                _insert_trade(
                    conn,
                    trade_id="daily-blocked",
                    market_id="market-daily-blocked",
                    trader_address="0xdaily2",
                    signal_mode="heuristic",
                    confidence=0.74,
                    price_at_signal=0.70,
                    actual_entry_price=0.70,
                    actual_entry_size_usd=100.0,
                    actual_pnl_usd=30.0,
                    shadow_pnl_usd=30.0,
                    placed_at=first_ts + 120,
                    resolved_at=first_ts + 180,
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

                self.assertEqual(result["accepted_count"], 1)
                self.assertEqual(result["rejected_count"], 1)

                conn = sqlite3.connect(str(test_db_path))
                rows = conn.execute(
                    "SELECT trade_id, decision, reason FROM replay_trades ORDER BY trade_log_id ASC"
                ).fetchall()
                run_row = conn.execute(
                    "SELECT window_end_daily_guard_triggered FROM replay_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                conn.close()

                self.assertEqual(
                    rows,
                    [
                        ("daily-loss", "accept", "accepted"),
                        ("daily-blocked", "reject", "daily_loss_guard"),
                    ],
                )
                self.assertEqual(result["reject_reason_summary"]["daily_loss_guard"], 1)
                self.assertEqual(result["window_end_daily_guard_triggered"], 1)
                self.assertEqual(int(run_row[0]), 1)
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
                    actual_pnl_usd=-60.0,
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
                    actual_pnl_usd=30.0,
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
                run_row = conn.execute(
                    "SELECT window_end_live_guard_triggered FROM replay_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                conn.close()

                self.assertEqual(
                    rows,
                    [
                        ("live-loss", "accept", "accepted"),
                        ("live-blocked", "reject", "live_drawdown_guard"),
                    ],
                )
                self.assertEqual(result["reject_reason_summary"]["live_drawdown_guard"], 1)
                self.assertEqual(result["window_end_live_guard_triggered"], 1)
                self.assertEqual(int(run_row[0]), 1)
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
