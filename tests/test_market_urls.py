from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db
from market_urls import market_url_from_metadata


class MarketUrlsTest(unittest.TestCase):
    def test_market_url_from_metadata_uses_sports_route_for_esports_child_market(self) -> None:
        meta = {
            "slug": "cs2-fav-esc1-2026-03-19-game2",
            "events": [{"slug": "cs2-fav-esc1-2026-03-19"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/sports/counter-strike/cs2-fav-esc1-2026-03-19",
        )

    def test_market_url_from_metadata_keeps_single_slug_markets_flat(self) -> None:
        meta = {
            "slug": "xrp-updown-15m-1773963000",
            "events": [{"slug": "xrp-updown-15m-1773963000"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/event/xrp-updown-15m-1773963000",
        )

    def test_market_url_from_metadata_uses_sports_route_for_sports_child_market(self) -> None:
        meta = {
            "slug": "ere-fey-aja-2026-03-22-aja",
            "events": [{"slug": "ere-fey-aja-2026-03-22"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/sports/ere/ere-fey-aja-2026-03-22",
        )

    def test_market_url_from_metadata_normalizes_more_markets_event_slug(self) -> None:
        meta = {
            "slug": "bra-fla-cre-2026-03-19-btts",
            "events": [{"slug": "bra-fla-cre-2026-03-19-more-markets"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/sports/bra/bra-fla-cre-2026-03-19",
        )

    def test_market_url_from_metadata_keeps_nested_event_route_for_non_sports_child_market(self) -> None:
        meta = {
            "slug": "ethereum-above-2100-on-march-22",
            "events": [{"slug": "ethereum-above-on-march-22"}],
        }

        self.assertEqual(
            market_url_from_metadata(meta),
            "https://polymarket.com/event/ethereum-above-on-march-22/ethereum-above-2100-on-march-22",
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
                    "https://polymarket.com/sports/counter-strike/cs2-fav-esc1-2026-03-19",
                )
            finally:
                db.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
