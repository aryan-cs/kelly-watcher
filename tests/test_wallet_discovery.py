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
                    "kelly_watcher.runtime.wallet_discovery._analyze_wallet_entry",
                    side_effect=lambda entry, **kwargs: analyzed[entry.address],
                ):
                    summary = wallet_discovery.refresh_wallet_discovery_candidates(["0xtracked"])

                self.assertTrue(summary["ok"])
                self.assertEqual(summary["scanned_count"], 3)
                self.assertEqual(summary["accepted_count"], 2)
                self.assertEqual(summary["stored_count"], 3)

                rows = wallet_discovery.load_wallet_discovery_candidates(limit=10)
                self.assertEqual([row["wallet_address"] for row in rows], ["0xalpha", "0xbeta", "0xgamma"])
                self.assertEqual(rows[0]["source_labels"], ["week-pnl"])
                self.assertEqual(rows[1]["source_labels"], ["week-pnl", "week-vol"])
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
                    ],
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery.load_local_copy_metrics",
                    return_value={},
                ), patch(
                    "kelly_watcher.runtime.wallet_discovery._analyze_wallet_entry",
                    return_value=ranked_wallet(
                        "0xreject",
                        score=0.25,
                        accepted=False,
                        reject_reason="recent_buys<4",
                        username="reject",
                    ),
                ):
                    summary = wallet_discovery.refresh_wallet_discovery_candidates([])

                self.assertTrue(summary["ok"])
                self.assertEqual(summary["accepted_count"], 0)
                self.assertEqual(summary["stored_count"], 1)
                rows = wallet_discovery.load_wallet_discovery_candidates(limit=10)
                self.assertEqual([row["wallet_address"] for row in rows], ["0xreject"])
                self.assertFalse(bool(rows[0]["accepted"]))
            finally:
                wallet_discovery.DB_PATH = original_runtime_db_path
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
