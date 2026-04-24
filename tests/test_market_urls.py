from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import kelly_watcher.data.db as db
from kelly_watcher.data.market_urls import market_url_from_metadata


class MarketUrlsTest(unittest.TestCase):
    def test_startup_heavy_maintenance_enabled_for_local_paths(self) -> None:
        self.assertTrue(db._startup_heavy_maintenance_enabled(Path("data/trading.db")))

    def test_startup_heavy_maintenance_disabled_for_windows_unc_paths(self) -> None:
        self.assertFalse(db._startup_heavy_maintenance_enabled(Path(r"\\server\share\trading.db")))

    def test_preferred_journal_mode_uses_wal_for_local_paths(self) -> None:
        self.assertEqual(db._preferred_journal_mode(Path("data/trading.db")), "WAL")

    def test_preferred_journal_mode_uses_delete_for_windows_unc_paths(self) -> None:
        self.assertEqual(
            db._preferred_journal_mode(Path(r"\\server\share\trading.db")),
            "DELETE",
        )

    def test_locked_operational_error_helper_matches_sqlite_lock_messages(self) -> None:
        self.assertTrue(db._is_locked_operational_error(sqlite3.OperationalError("database is locked")))
        self.assertTrue(db._is_locked_operational_error(sqlite3.OperationalError("database table is locked: trade_log")))
        self.assertFalse(db._is_locked_operational_error(sqlite3.OperationalError("no such table: trade_log")))

    def test_connect_sqlite_applies_busy_timeout_and_retrying_connection(self) -> None:
        with TemporaryDirectory() as tmpdir:
            conn = db.get_conn_for_path(Path(tmpdir) / "trading.db", apply_runtime_pragmas=True)
            try:
                self.assertIsInstance(conn, db._RetryingConnection)
                busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                self.assertEqual(int(busy_timeout), db.SQLITE_BUSY_TIMEOUT_MS)
            finally:
                conn.close()

    def test_market_url_from_metadata_uses_direct_polymarket_url_when_present(self) -> None:
        meta = {
            "slug": "wta-cirstea-mertens-2026-03-21",
            "marketUrl": "https://polymarket.com/event/wta-cirstea-mertens-2026-03-21",
            "events": [{"slug": "wta-cirstea-mertens-2026-03-21"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/event/wta-cirstea-mertens-2026-03-21",
        )

    def test_market_url_from_metadata_uses_market_slug_for_documented_event_route(self) -> None:
        meta = {
            "slug": "xrp-updown-15m-1773963000",
            "events": [{"slug": "xrp-updown-15m-1773963000"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/event/xrp-updown-15m-1773963000",
        )

    def test_market_url_from_metadata_does_not_guess_sports_route(self) -> None:
        meta = {
            "slug": "wta-cirstea-mertens-2026-03-21",
            "events": [{"slug": "wta-cirstea-mertens-2026-03-21"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/event/wta-cirstea-mertens-2026-03-21",
        )

    def test_market_url_from_metadata_prefers_market_slug_over_event_slug(self) -> None:
        meta = {
            "slug": "bra-fla-cre-2026-03-19-btts",
            "events": [{"slug": "bra-fla-cre-2026-03-19-more-markets"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/event/bra-fla-cre-2026-03-19-btts",
        )

    def test_market_url_from_metadata_prefers_direct_mls_event_route_over_outcome_slug(self) -> None:
        meta = {
            "slug": "mls-aus-laf-2026-03-21-laf",
            "sportsMarketType": "moneyline",
            "events": [{"slug": "mls-aus-laf-2026-03-21"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/sports/mls/mls-aus-laf-2026-03-21",
        )

    def test_market_url_from_metadata_prefers_direct_mls_event_route_for_other_moneyline_market(self) -> None:
        meta = {
            "slug": "mls-skc-col-2026-03-21-col",
            "sportsMarketType": "moneyline",
            "events": [{"slug": "mls-skc-col-2026-03-21"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/sports/mls/mls-skc-col-2026-03-21",
        )

    def test_market_url_from_metadata_falls_back_to_event_slug_when_market_slug_missing(self) -> None:
        meta = {
            "events": [{"slug": "ethereum-above-on-march-22"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/event/ethereum-above-on-march-22",
        )

    def test_init_db_repairs_existing_trade_log_market_urls(self) -> None:
        with TemporaryDirectory() as tmpdir:
            original_db_path = db.DB_PATH
            try:
                db.DB_PATH = Path(tmpdir) / "data" / "trading.db"
                db.init_db()
                conn = db.get_conn()
                conn.execute(
                    """
                    INSERT INTO trade_log (
                        trade_id, market_id, question, market_url, trader_address, side,
                        source_action, price_at_signal, signal_size_usd, confidence,
                        kelly_fraction, real_money, skipped, placed_at, market_metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "trade-1",
                        "market-1",
                        "Counter-Strike: Favbet vs ESC Gaming - Map 2 Winner",
                        "https://polymarket.com/event/cs2-fav-esc1-2026-03-19-game2",
                        "0xabc",
                        "yes",
                        "buy",
                        0.5,
                        10.0,
                        0.7,
                        0.1,
                        0,
                        0,
                        1_700_000_000,
                        json.dumps(
                            {
                                "slug": "cs2-fav-esc1-2026-03-19-game2",
                                "events": [{"slug": "cs2-fav-esc1-2026-03-19"}],
                            }
                        ),
                    ),
                )
                conn.commit()
                conn.close()

                db.init_db()

                conn = db.get_conn()
                row = conn.execute(
                    "SELECT market_url FROM trade_log WHERE trade_id=?",
                    ("trade-1",),
                ).fetchone()
                conn.close()

                self.assertEqual(
                    row["market_url"],
                    "https://polymarket.com/event/cs2-fav-esc1-2026-03-19-game2",
                )
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
