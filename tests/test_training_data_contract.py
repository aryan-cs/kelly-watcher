from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.research.auto_retrain as auto_retrain
import kelly_watcher.data.db as db
import kelly_watcher.research.train as train
from kelly_watcher.engine.economic_model import COUNTERFACTUAL_SAMPLE_WEIGHT, EXECUTED_SAMPLE_WEIGHT, transform_return_target


class TrainingDataContractTest(unittest.TestCase):
    def test_init_db_backfills_model_history_training_provenance_columns(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                try:
                    columns = {
                        str(row["name"])
                        for row in conn.execute("PRAGMA table_info(model_history)").fetchall()
                    }
                finally:
                    conn.close()
            finally:
                db.DB_PATH = original_db_path

        self.assertIn("training_scope", columns)
        self.assertIn("training_since_ts", columns)
        self.assertIn("training_routed_only", columns)
        self.assertIn("training_provenance_trusted", columns)

    def test_load_training_data_orders_by_label_time(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        entry_gross_price, entry_gross_shares, entry_gross_size_usd,
                        shadow_pnl_usd, resolved_at, label_applied_at, counterfactual_return
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "labeled-late",
                            "market-1",
                            "Placed first but labeled later",
                            "0xaaa",
                            "yes",
                            "buy",
                            0.40,
                            10.0,
                            0.71,
                            0.10,
                            0,
                            0,
                            None,
                            1_700_000_000,
                            0.40,
                            25.0,
                            10.0,
                            0.40,
                            25.0,
                            10.0,
                            5.0,
                            1_700_000_300,
                            1_700_000_300,
                            1.5,
                        ),
                        (
                            "labeled-early",
                            "market-2",
                            "Placed later but labeled earlier",
                            "0xbbb",
                            "yes",
                            "buy",
                            0.45,
                            12.0,
                            0.72,
                            0.09,
                            0,
                            0,
                            None,
                            1_700_000_100,
                            0.45,
                            26.0,
                            12.0,
                            0.45,
                            26.0,
                            12.0,
                            3.0,
                            1_700_000_200,
                            1_700_000_200,
                            0.25,
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                df = train.load_training_data()

                self.assertEqual(list(df["trade_id"]), ["labeled-early", "labeled-late"])
            finally:
                db.DB_PATH = original_db_path

    def test_load_training_data_excludes_fee_blind_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        entry_gross_price, entry_gross_shares, entry_gross_size_usd,
                        shadow_pnl_usd, resolved_at, label_applied_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "fee-aware-executed",
                        "market-1",
                        "Fee-aware executed row",
                        "0xaaa",
                        "yes",
                        "buy",
                        0.40,
                        10.0,
                        0.71,
                        0.10,
                        0,
                        0,
                        1_700_000_000,
                        0.41,
                        24.0,
                        10.0,
                        0.40,
                        25.0,
                        10.0,
                        5.0,
                        1_700_000_100,
                        1_700_000_100,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        shadow_pnl_usd, resolved_at, label_applied_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "fee-blind-executed",
                        "market-2",
                        "Legacy executed row",
                        "0xbbb",
                        "yes",
                        "buy",
                        0.42,
                        10.0,
                        0.72,
                        0.09,
                        0,
                        0,
                        1_700_000_010,
                        0.42,
                        23.8,
                        10.0,
                        4.0,
                        1_700_000_110,
                        1_700_000_110,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        resolved_at, label_applied_at, counterfactual_return, snapshot_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "fee-aware-skip",
                        "market-3",
                        "Fee-aware skipped row",
                        "0xccc",
                        "yes",
                        "buy",
                        0.45,
                        12.0,
                        0.59,
                        0.08,
                        0,
                        1,
                        "signal confidence was 59.0%, below the 60.0% minimum",
                        1_700_000_020,
                        1_700_000_120,
                        1_700_000_120,
                        1.222222,
                        json.dumps({"fee_rate_bps": 0}),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        resolved_at, label_applied_at, counterfactual_return
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "fee-blind-skip",
                        "market-4",
                        "Legacy skipped row",
                        "0xddd",
                        "yes",
                        "buy",
                        0.46,
                        12.0,
                        0.58,
                        0.07,
                        0,
                        1,
                        "signal confidence was 58.0%, below the 60.0% minimum",
                        1_700_000_030,
                        1_700_000_130,
                        1_700_000_130,
                        1.173913,
                    ),
                )
                conn.commit()
                conn.close()

                df = train.load_training_data()

                self.assertEqual(list(df["trade_id"]), ["fee-aware-executed", "fee-aware-skip"])
            finally:
                db.DB_PATH = original_db_path

    def test_load_training_data_includes_trainable_skips_but_excludes_policy_skips(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        entry_gross_price, entry_gross_shares, entry_gross_size_usd,
                        shadow_pnl_usd, resolved_at, label_applied_at, counterfactual_return
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "accepted-win",
                        "market-1",
                        "Accepted trade",
                        "0xaaa",
                        "yes",
                        "buy",
                        0.40,
                        10.0,
                        0.71,
                        0.10,
                        0,
                        0,
                        None,
                        1_700_000_000,
                        0.40,
                        25.0,
                        10.0,
                        0.40,
                        25.0,
                        10.0,
                        5.0,
                        1_700_000_100,
                        1_700_000_100,
                        1.5,
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        resolved_at, label_applied_at, counterfactual_return, snapshot_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "skip-trainable-win",
                            "market-2",
                            "Skipped confidence reject that would have won",
                            "0xbbb",
                            "yes",
                            "buy",
                            0.45,
                            12.0,
                            0.59,
                            0.08,
                            0,
                            1,
                            "signal confidence was 59.0%, below the 60.0% minimum",
                            1_700_000_010,
                            1_700_000_110,
                            1_700_000_110,
                            1.222222,
                            json.dumps({"fee_rate_bps": 0}),
                        ),
                        (
                            "skip-trainable-loss",
                            "market-3",
                            "Skipped confidence reject that would have lost",
                            "0xccc",
                            "yes",
                            "buy",
                            0.53,
                            8.0,
                            0.58,
                            0.05,
                            0,
                            1,
                            "confidence was 58.0%, below the 60.0% minimum needed to place a trade",
                            1_700_000_020,
                            1_700_000_120,
                            1_700_000_120,
                            -1.0,
                            json.dumps({"fee_rate_bps": 0}),
                        ),
                        (
                            "skip-policy-win",
                            "market-4",
                            "Skipped policy reject that should stay out of training",
                            "0xddd",
                            "yes",
                            "buy",
                            0.35,
                            6.0,
                            0.66,
                            0.07,
                            0,
                            1,
                            "too close to resolution, less than 45 seconds remained to place the trade",
                            1_700_000_030,
                            1_700_000_130,
                            1_700_000_130,
                            1.857143,
                            json.dumps({"fee_rate_bps": 0}),
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                df = train.load_training_data()

                self.assertEqual(
                    list(df["trade_id"]),
                    ["accepted-win", "skip-trainable-win", "skip-trainable-loss"],
                )
                returns = {row.trade_id: float(row.economic_return) for row in df.itertuples(index=False)}
                self.assertAlmostEqual(returns["accepted-win"], 0.5, places=6)
                self.assertAlmostEqual(returns["skip-trainable-win"], 1.222222, places=6)
                self.assertAlmostEqual(returns["skip-trainable-loss"], -1.0, places=6)

                labels = {row.trade_id: float(row.label) for row in df.itertuples(index=False)}
                self.assertAlmostEqual(labels["accepted-win"], transform_return_target(0.5), places=6)
                self.assertAlmostEqual(labels["skip-trainable-win"], transform_return_target(1.222222), places=6)
                self.assertAlmostEqual(labels["skip-trainable-loss"], transform_return_target(-1.0), places=6)

                outcomes = {row.trade_id: int(row.outcome_label) for row in df.itertuples(index=False)}
                self.assertEqual(outcomes["accepted-win"], 1)
                self.assertEqual(outcomes["skip-trainable-win"], 1)
                self.assertEqual(outcomes["skip-trainable-loss"], 0)

                weights = {row.trade_id: float(row.sample_weight) for row in df.itertuples(index=False)}
                self.assertAlmostEqual(weights["accepted-win"], EXECUTED_SAMPLE_WEIGHT, places=6)
                self.assertAlmostEqual(weights["skip-trainable-win"], COUNTERFACTUAL_SAMPLE_WEIGHT, places=6)
                self.assertAlmostEqual(weights["skip-trainable-loss"], COUNTERFACTUAL_SAMPLE_WEIGHT, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_load_training_data_caps_total_counterfactual_weight(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        entry_gross_price, entry_gross_shares, entry_gross_size_usd,
                        shadow_pnl_usd, resolved_at, label_applied_at, counterfactual_return
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "accepted-win",
                        "market-1",
                        "Accepted trade",
                        "0xaaa",
                        "yes",
                        "buy",
                        0.40,
                        10.0,
                        0.71,
                        0.10,
                        0,
                        0,
                        None,
                        1_700_000_000,
                        0.40,
                        25.0,
                        10.0,
                        0.40,
                        25.0,
                        10.0,
                        5.0,
                        1_700_000_100,
                        1_700_000_100,
                        1.5,
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        resolved_at, label_applied_at, counterfactual_return, snapshot_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            f"skip-{idx}",
                            f"market-s-{idx}",
                            "Skipped confidence reject",
                            f"0x{idx:03x}",
                            "yes",
                            "buy",
                            0.45,
                            12.0,
                            0.59,
                            0.08,
                            0,
                            1,
                            "signal confidence was 59.0%, below the 60.0% minimum",
                            1_700_000_010 + idx,
                            1_700_000_110 + idx,
                            1_700_000_110 + idx,
                            1.222222,
                            json.dumps({"fee_rate_bps": 0}),
                        )
                        for idx in range(8)
                    ],
                )
                conn.commit()
                conn.close()

                df = train.load_training_data()
                executed_total = float(df.loc[df["skipped"] == 0, "sample_weight"].sum())
                counterfactual_total = float(df.loc[df["skipped"] == 1, "sample_weight"].sum())

                self.assertAlmostEqual(executed_total, 1.0, places=6)
                self.assertAlmostEqual(counterfactual_total, 1.0, places=6)
            finally:
                db.DB_PATH = original_db_path

    def test_load_training_data_can_scope_to_post_epoch_routed_samples(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        entry_gross_price, entry_gross_shares, entry_gross_size_usd,
                        shadow_pnl_usd, resolved_at, label_applied_at, counterfactual_return, segment_id, snapshot_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "pre-epoch-routed",
                            "market-1",
                            "Pre-epoch routed sample",
                            "0xaaa",
                            "yes",
                            "buy",
                            0.40,
                            10.0,
                            0.71,
                            0.10,
                            0,
                            0,
                            None,
                            1_700_000_000,
                            0.40,
                            25.0,
                            10.0,
                            0.40,
                            25.0,
                            10.0,
                            5.0,
                            1_700_000_100,
                            1_700_000_100,
                            1.5,
                            "hot_short",
                            json.dumps({"fee_rate_bps": 0}),
                        ),
                        (
                            "post-epoch-unassigned",
                            "market-2",
                            "Post-epoch unassigned sample",
                            "0xbbb",
                            "yes",
                            "buy",
                            0.43,
                            11.0,
                            0.69,
                            0.09,
                            0,
                            0,
                            None,
                            1_700_000_150,
                            0.43,
                            25.581395,
                            11.0,
                            0.43,
                            25.581395,
                            11.0,
                            4.0,
                            1_700_000_210,
                            1_700_000_210,
                            0.9,
                            "",
                            json.dumps({"fee_rate_bps": 0}),
                        ),
                        (
                            "post-epoch-routed",
                            "market-3",
                            "Post-epoch routed sample",
                            "0xccc",
                            "yes",
                            "buy",
                            0.45,
                            12.0,
                            0.72,
                            0.08,
                            0,
                            0,
                            None,
                            1_700_000_200,
                            0.45,
                            26.666667,
                            12.0,
                            0.45,
                            26.666667,
                            12.0,
                            6.0,
                            1_700_000_300,
                            1_700_000_300,
                            1.2,
                            "warm_mid",
                            json.dumps({"fee_rate_bps": 0}),
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                df = train.load_training_data(since_ts=1_700_000_200, routed_only=True)

                self.assertEqual(list(df["trade_id"]), ["post-epoch-routed"])
            finally:
                db.DB_PATH = original_db_path

    def test_early_retrain_counts_trainable_skipped_labels_only(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO model_history (
                        trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (2_000, 250, 0.2, 0.6, "[]", "model.joblib", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        resolved_at, label_applied_at, counterfactual_return, snapshot_json, segment_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "eligible-new-skip",
                            "market-2",
                            "Eligible skipped label",
                            "0xdef",
                            "yes",
                            "buy",
                            0.45,
                            12.0,
                            0.59,
                            0.08,
                            0,
                            1,
                            "signal confidence was 59.0%, below the 60.0% minimum",
                            1_000,
                            1_500,
                            3_000,
                            1.222222,
                            json.dumps({"fee_rate_bps": 0}),
                            "hot_short",
                        ),
                        (
                            "ineligible-new-skip",
                            "market-3",
                            "Ineligible skipped label",
                            "0x987",
                            "yes",
                            "buy",
                            0.35,
                            8.0,
                            0.67,
                            0.07,
                            0,
                            1,
                            "too close to resolution, less than 45 seconds remained to place the trade",
                            1_001,
                            1_600,
                            3_100,
                            1.857143,
                            json.dumps({"fee_rate_bps": 0}),
                            "warm_mid",
                        ),
                        (
                            "fee-blind-new-skip",
                            "market-4",
                            "Eligible but fee-blind skipped label",
                            "0x654",
                            "yes",
                            "buy",
                            0.44,
                            11.0,
                            0.58,
                            0.08,
                            0,
                            1,
                            "signal confidence was 58.0%, below the 60.0% minimum",
                            1_002,
                            1_700,
                            3_200,
                            1.272727,
                            None,
                            "discovery_long",
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                with patch(
                    "kelly_watcher.research.auto_retrain.read_shadow_evidence_epoch",
                    return_value={"shadow_evidence_epoch_started_at": 3_000},
                ), patch("kelly_watcher.research.auto_retrain.retrain_min_new_labels", return_value=1):
                    self.assertTrue(auto_retrain.should_retrain_early(None))
                with patch(
                    "kelly_watcher.research.auto_retrain.read_shadow_evidence_epoch",
                    return_value={"shadow_evidence_epoch_started_at": 3_000},
                ), patch("kelly_watcher.research.auto_retrain.retrain_min_new_labels", return_value=2):
                    self.assertFalse(auto_retrain.should_retrain_early(None))
            finally:
                db.DB_PATH = original_db_path

    def test_early_retrain_counts_only_post_epoch_routed_labels(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO model_history (
                        trained_at, n_samples, brier_score, log_loss, feature_cols, model_path, deployed
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (2_000, 250, 0.2, 0.6, "[]", "model.joblib", 1),
                )
                conn.executemany(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, trader_address, side, source_action,
                        price_at_signal, signal_size_usd, confidence, kelly_fraction,
                        real_money, skipped, skip_reason, placed_at,
                        actual_entry_price, actual_entry_shares, actual_entry_size_usd,
                        entry_gross_price, entry_gross_shares, entry_gross_size_usd,
                        shadow_pnl_usd, resolved_at, label_applied_at, counterfactual_return, segment_id, snapshot_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            "eligible-routed",
                            "market-1",
                            "Eligible routed label",
                            "0xaaa",
                            "yes",
                            "buy",
                            0.45,
                            12.0,
                            0.72,
                            0.08,
                            0,
                            0,
                            None,
                            1_000,
                            0.45,
                            26.666667,
                            12.0,
                            0.45,
                            26.666667,
                            12.0,
                            2.5,
                            1_500,
                            3_000,
                            0.25,
                            "hot_short",
                            json.dumps({"fee_rate_bps": 0}),
                        ),
                        (
                            "post-epoch-unassigned",
                            "market-2",
                            "Post-epoch unassigned label",
                            "0xbbb",
                            "yes",
                            "buy",
                            0.44,
                            11.0,
                            0.70,
                            0.07,
                            0,
                            0,
                            None,
                            1_001,
                            0.44,
                            25.0,
                            11.0,
                            0.44,
                            25.0,
                            11.0,
                            1.5,
                            1_600,
                            3_100,
                            0.136364,
                            "",
                            json.dumps({"fee_rate_bps": 0}),
                        ),
                        (
                            "pre-epoch-routed",
                            "market-3",
                            "Pre-epoch routed label",
                            "0xccc",
                            "yes",
                            "buy",
                            0.43,
                            10.0,
                            0.69,
                            0.06,
                            0,
                            0,
                            None,
                            900,
                            0.43,
                            23.255814,
                            10.0,
                            0.43,
                            23.255814,
                            10.0,
                            1.0,
                            1_400,
                            1_900,
                            0.1,
                            "warm_mid",
                            json.dumps({"fee_rate_bps": 0}),
                        ),
                    ],
                )
                conn.commit()
                conn.close()

                with patch(
                    "kelly_watcher.research.auto_retrain.read_shadow_evidence_epoch",
                    return_value={"shadow_evidence_epoch_started_at": 3_000},
                ), patch("kelly_watcher.research.auto_retrain.retrain_min_new_labels", return_value=1):
                    self.assertTrue(auto_retrain.should_retrain_early(None))
                with patch(
                    "kelly_watcher.research.auto_retrain.read_shadow_evidence_epoch",
                    return_value={"shadow_evidence_epoch_started_at": 3_000},
                ), patch("kelly_watcher.research.auto_retrain.retrain_min_new_labels", return_value=2):
                    self.assertFalse(auto_retrain.should_retrain_early(None))
            finally:
                db.DB_PATH = original_db_path

    def test_early_retrain_returns_false_without_active_epoch(self) -> None:
        with patch(
            "kelly_watcher.research.auto_retrain.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 0},
        ), patch("kelly_watcher.research.auto_retrain.load_training_data") as load_mock:
            self.assertFalse(auto_retrain.should_retrain_early(None))

        load_mock.assert_not_called()

    def test_direct_train_skips_without_active_epoch(self) -> None:
        with patch(
            "kelly_watcher.research.train.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 0},
        ), patch("kelly_watcher.research.train.load_training_data") as load_mock:
            metrics = train.train()

        self.assertTrue(metrics["skipped"])
        self.assertEqual(metrics["n_samples"], 0)
        self.assertIn("current evidence window is not active yet", metrics["reason"])
        self.assertEqual(metrics["training_scope"], "all_history")
        self.assertFalse(metrics["training_provenance_trusted"])
        load_mock.assert_not_called()

    def test_direct_train_scopes_default_load_to_active_epoch_and_routed_rows(self) -> None:
        with patch(
            "kelly_watcher.research.train.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 3_000},
        ), patch("kelly_watcher.research.train.load_training_data", return_value=[] ) as load_mock:
            metrics = train.train()

        self.assertTrue(metrics["skipped"])
        self.assertEqual(metrics["training_scope"], "current_evidence_window")
        self.assertEqual(metrics["training_since_ts"], 3_000)
        self.assertTrue(metrics["training_routed_only"])
        self.assertTrue(metrics["training_provenance_trusted"])
        load_mock.assert_called_once_with(since_ts=3_000, routed_only=True)

    def test_direct_train_scopes_default_load_to_latest_promotion_within_epoch(self) -> None:
        with patch(
            "kelly_watcher.research.train.read_shadow_evidence_epoch",
            return_value={"shadow_evidence_epoch_started_at": 3_000},
        ), patch(
            "kelly_watcher.research.train._latest_applied_replay_promotion_at",
            return_value=3_500,
        ), patch("kelly_watcher.research.train.load_training_data", return_value=[] ) as load_mock:
            metrics = train.train()

        self.assertTrue(metrics["skipped"])
        self.assertEqual(metrics["training_scope"], "current_evidence_window")
        self.assertEqual(metrics["training_since_ts"], 3_500)
        self.assertTrue(metrics["training_routed_only"])
        self.assertTrue(metrics["training_provenance_trusted"])
        load_mock.assert_called_once_with(since_ts=3_500, routed_only=True)


if __name__ == "__main__":
    unittest.main()
