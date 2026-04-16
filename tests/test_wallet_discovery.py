from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import kelly_watcher.data.db as db
import kelly_watcher.runtime.wallet_discovery as wallet_discovery
from kelly_watcher.tools.rank_copytrade_wallets import LeaderboardEntry, RankedWallet


def ranked_wallet(
    address: str,
    *,
    score: float,
    accepted: bool,
    reject_reason: str = "",
    username: str = "-",
) -> RankedWallet:
    return RankedWallet(
        address=address,
        username=username,
        style="active short-horizon",
        follow_score=score,
        accepted=accepted,
        reject_reason=reject_reason,
        leaderboard_rank=1,
        leaderboard_pnl_usd=1_000.0,
        leaderboard_volume_usd=5_000.0,
        closed_positions=20,
        win_rate=0.6,
        roi=0.12,
        realized_pnl_usd=250.0,
        recent_trades=12,
        recent_buys=6,
        avg_recent_buy_size_usd=180.0,
        large_buy_ratio=0.5,
        conviction_buy_ratio=0.5,
        copyability_score=0.7,
        last_trade_age_hours=2.0,
        median_buy_lead_hours=3.5,
        p25_buy_lead_hours=1.5,
        late_buy_ratio=0.1,
        local_resolved_copied=0,
        local_copy_avg_return=None,
        local_copy_pnl_usd=0.0,
    )


class WalletDiscoveryTest(unittest.TestCase):
    def test_refresh_wallet_discovery_candidates_persists_only_new_accepted_wallets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_runtime_db_path = wallet_discovery.DB_PATH
            temp_db_path = Path(tmpdir) / "data" / "trading.db"
            try:
                db.DB_PATH = temp_db_path
                wallet_discovery.DB_PATH = temp_db_path
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO wallet_watch_state (
                        wallet_address,
                        status,
                        updated_at
                    ) VALUES (?, 'dropped', ?)
                    """,
                    ("0xdropped", 1_700_000_000),
                )
                conn.commit()
                conn.close()

                source_batches = [
                    [
                        LeaderboardEntry("0xtracked", "tracked", 1, 1_000.0, 5_000.0, True),
                        LeaderboardEntry("0xalpha", "alpha", 2, 900.0, 4_000.0, False),
                        LeaderboardEntry("0xbeta", "beta", 3, 800.0, 3_500.0, False),
                    ],
                    [
                        LeaderboardEntry("0xbeta", "beta", 5, 850.0, 4_200.0, False),
                        LeaderboardEntry("0xdropped", "dropped", 6, 600.0, 2_500.0, False),
                        LeaderboardEntry("0xgamma", "gamma", 7, 500.0, 2_000.0, False),
                    ],
                    [],
                    [],
                    [],
                    [],
                    [],
                    [],
                ]

                analyzed = {
                    "0xalpha": ranked_wallet("0xalpha", score=0.91, accepted=True, username="alpha"),
                    "0xbeta": ranked_wallet("0xbeta", score=0.72, accepted=True, username="beta"),
                    "0xgamma": ranked_wallet(
                        "0xgamma",
                        score=0.41,
                        accepted=False,
                        reject_reason="late_buy_ratio>20%",
                        username="gamma",
                    ),
                }

                with patch(
                    "kelly_watcher.runtime.wallet_discovery.wallet_discovery_analyze_limit",
                    return_value=10,
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.wallet_discovery_candidate_limit",
                    return_value=5,
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.wallet_discovery_leaderboard_pages",
                    return_value=1,
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.wallet_discovery_leaderboard_per_page",
                    return_value=25,
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.fetch_leaderboard",
                    side_effect=source_batches,
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.load_local_copy_metrics",
                    return_value={},
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery._analyze_wallet_candidate",
                    side_effect=lambda entry, **kwargs: (analyzed[entry.address], set()),
                ):
                    summary = wallet_discovery.refresh_wallet_discovery_candidates(["0xtracked"])

                self.assertTrue(summary["ok"])
                self.assertEqual(summary["scanned_count"], 3)
                self.assertEqual(summary["accepted_count"], 2)
                self.assertEqual(summary["stored_count"], 3)

                rows = wallet_discovery.load_wallet_discovery_candidates(limit=10)
                self.assertEqual([row["wallet_address"] for row in rows], ["0xalpha", "0xbeta", "0xgamma"])
                self.assertEqual(rows[0]["source_labels"], ["leaderboard:day-pnl"])
                self.assertEqual(rows[1]["source_labels"], ["leaderboard:day-pnl", "leaderboard:day-vol"])
                self.assertEqual(rows[0]["copyability_gate_status"], "ready")
                self.assertEqual(rows[2]["copyability_gate_status"], "review_timing")
                self.assertFalse(bool(rows[2]["accepted"]))
                self.assertEqual(rows[2]["reject_reason"], "late_buy_ratio>20%")
            finally:
                wallet_discovery.DB_PATH = original_runtime_db_path
                db.DB_PATH = original_db_path

    def test_refresh_wallet_discovery_candidates_clears_cache_when_new_scan_has_no_accepts(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            original_runtime_db_path = wallet_discovery.DB_PATH
            temp_db_path = Path(tmpdir) / "data" / "trading.db"
            try:
                db.DB_PATH = temp_db_path
                wallet_discovery.DB_PATH = temp_db_path
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO wallet_discovery_candidates (
                        wallet_address,
                        username,
                        source_labels_json,
                        follow_score,
                        accepted,
                        reject_reason,
                        watch_style,
                        leaderboard_rank,
                        updated_at,
                        payload_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "0xstale",
                        "stale",
                        '["week-pnl"]',
                        0.88,
                        1,
                        "",
                        "active short-horizon",
                        1,
                        1_700_000_000,
                        '{"address":"0xstale","username":"stale","follow_score":0.88,"accepted":true}',
                    ),
                )
                conn.commit()
                conn.close()

                with patch(
                    "kelly_watcher.runtime.wallet_discovery.wallet_discovery_analyze_limit",
                    return_value=5,
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.wallet_discovery_candidate_limit",
                    return_value=5,
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.wallet_discovery_leaderboard_pages",
                    return_value=1,
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.wallet_discovery_leaderboard_per_page",
                    return_value=25,
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.fetch_leaderboard",
                    side_effect=[
                        [LeaderboardEntry("0xreject", "reject", 1, 100.0, 250.0, False)],
                        [],
                        [],
                        [],
                        [],
                        [],
                        [],
                        [],
                    ],
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.load_local_copy_metrics",
                    return_value={},
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery._analyze_wallet_candidate",
                    return_value=(
                        ranked_wallet(
                            "0xreject",
                            score=0.25,
                            accepted=False,
                            reject_reason="recent_buys<4",
                            username="reject",
                        ),
                        set(),
                    ),
                ):
                    summary = wallet_discovery.refresh_wallet_discovery_candidates([])

                self.assertTrue(summary["ok"])
                self.assertEqual(summary["accepted_count"], 0)
                self.assertEqual(summary["stored_count"], 1)
                rows = wallet_discovery.load_wallet_discovery_candidates(limit=10)
                self.assertEqual([row["wallet_address"] for row in rows], ["0xreject"])
                self.assertFalse(bool(rows[0]["accepted"]))
                self.assertEqual(rows[0]["copyability_gate_status"], "review_sample")
            finally:
                wallet_discovery.DB_PATH = original_runtime_db_path
                db.DB_PATH = original_db_path

    def test_candidate_entries_prioritize_multi_source_wallets_and_keep_later_windows(self) -> None:
        with patch(
            "kelly_watcher.runtime.wallet_discovery.fetch_leaderboard",
            side_effect=[
                [LeaderboardEntry("0xa", "a", 3, 100.0, 100.0, False)],
                [LeaderboardEntry("0xb", "b", 4, 100.0, 100.0, False)],
                [LeaderboardEntry("0xc", "c", 5, 100.0, 100.0, False)],
                [LeaderboardEntry("0xa", "a", 1, 150.0, 100.0, False)],
                [LeaderboardEntry("0xd", "d", 1, 500.0, 500.0, False)],
                [LeaderboardEntry("0xb", "b", 2, 120.0, 220.0, False)],
                [],
                [],
            ],
        ):
            with patch(
                "kelly_watcher.runtime.wallet_discovery._DISCOVERY_SOURCES",
                (
                    ("leaderboard:day-pnl", "OVERALL", "DAY", "PNL"),
                    ("leaderboard:day-vol", "OVERALL", "DAY", "VOL"),
                    ("leaderboard:week-pnl", "OVERALL", "WEEK", "PNL"),
                    ("leaderboard:week-vol", "OVERALL", "WEEK", "VOL"),
                    ("leaderboard:month-pnl", "OVERALL", "MONTH", "PNL"),
                    ("leaderboard:month-vol", "OVERALL", "MONTH", "VOL"),
                    ("leaderboard:all-pnl", "OVERALL", "ALL", "PNL"),
                    ("leaderboard:all-vol", "OVERALL", "ALL", "VOL"),
                ),
            ):
                entries, source_labels = wallet_discovery._candidate_entries(
                    client=object(),  # fetch_leaderboard is patched
                    excluded_wallets=set(),
                    pages=1,
                    per_page=25,
                    analyze_limit=3,
                )

        self.assertEqual([entry.address for entry in entries], ["0xa", "0xb", "0xd"])
        self.assertEqual(
            list(source_labels["0xa"]),
            ["leaderboard:day-pnl", "leaderboard:week-vol"],
        )
        self.assertEqual(
            list(source_labels["0xb"]),
            ["leaderboard:day-vol", "leaderboard:month-vol"],
        )
        self.assertEqual(list(source_labels["0xd"]), ["leaderboard:month-pnl"])

    def test_refresh_wallet_discovery_candidates_blocks_when_registry_is_unavailable(self) -> None:
        with patch(
            "kelly_watcher.runtime.wallet_discovery.database_integrity_state",
            return_value={"db_integrity_known": True, "db_integrity_ok": True, "db_integrity_message": ""},
        ), patch(
            "kelly_watcher.runtime.wallet_discovery.managed_wallet_registry_state",
            return_value={
                "managed_wallet_registry_available": False,
                "managed_wallet_registry_status": "unreadable",
                "managed_wallet_registry_error": "database disk image is malformed",
            },
        ):
            summary = wallet_discovery.refresh_wallet_discovery_candidates([])

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["scanned_count"], 0)
        self.assertIn("managed wallet registry is unreadable", str(summary["message"]).lower())

    def test_activity_adjacency_labels_are_added_for_managed_and_discovered_overlap(self) -> None:
        labels = wallet_discovery._apply_activity_adjacency_labels(
            {
                "0xa": ("leaderboard:day-pnl",),
                "0xb": ("leaderboard:week-pnl",),
                "0xc": ("leaderboard:month-pnl",),
            },
            {
                "0xa": {"cond-1", "cond-2"},
                "0xb": {"cond-2"},
                "0xc": {"cond-managed"},
            },
            managed_anchor_condition_ids={"cond-managed"},
            discovered_anchor_wallets=["0xa", "0xb"],
        )

        self.assertIn("adjacent:discovered-wallet", labels["0xa"])
        self.assertIn("adjacent:discovered-wallet", labels["0xb"])
        self.assertIn("adjacent:managed-wallet", labels["0xc"])


if __name__ == "__main__":
    unittest.main()
