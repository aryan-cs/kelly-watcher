from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from config import use_real_money
from db import get_conn

logger = logging.getLogger(__name__)

PENDING_TIMEOUT = 30
SEEN_WINDOW = 86400


def _token_key(market_id: str, token_id: str) -> str:
    return f"{str(market_id or '').strip().lower()}::token::{str(token_id or '').strip()}"


def _side_key(market_id: str, side: str) -> str:
    return f"{str(market_id or '').strip().lower()}::side::{str(side or '').strip().lower()}"


def _position_key(market_id: str, token_id: str, side: str) -> str:
    normalized_token = str(token_id or "").strip()
    if normalized_token:
        return _token_key(market_id, normalized_token)
    return _side_key(market_id, side)


def _to_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class DedupeCache:
    seen_ids: set[str] = field(default_factory=set)
    open_positions: dict[str, dict[str, float | str]] = field(default_factory=dict)
    pending: dict[str, float] = field(default_factory=dict)

    def _rebuild_shadow_positions(self, conn) -> None:
        conn.execute("DELETE FROM positions WHERE real_money=0")
        rows = conn.execute(
            """
            SELECT
                market_id,
                LOWER(side) AS side,
                COALESCE(token_id, '') AS token_id,
                SUM(COALESCE(actual_entry_size_usd, signal_size_usd)) AS size_usd,
                CASE
                    WHEN SUM(COALESCE(actual_entry_size_usd, signal_size_usd)) > 0
                        THEN SUM(
                            COALESCE(actual_entry_size_usd, signal_size_usd)
                            * COALESCE(actual_entry_price, price_at_signal)
                        ) / SUM(COALESCE(actual_entry_size_usd, signal_size_usd))
                    ELSE 0
                END AS avg_price,
                MIN(placed_at) AS entered_at
            FROM trade_log
            WHERE real_money=0
              AND skipped=0
              AND outcome IS NULL
              AND exited_at IS NULL
              AND COALESCE(source_action, 'buy')='buy'
            GROUP BY market_id, COALESCE(token_id, ''), LOWER(side)
            """
        ).fetchall()

        for row in rows:
            conn.execute(
                "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
                (
                    row["market_id"],
                    row["side"],
                    float(row["size_usd"] or 0.0),
                    float(row["avg_price"] or 0.0),
                    str(row["token_id"] or ""),
                    int(row["entered_at"] or time.time()),
                    0,
                ),
            )

    def _find_key(
        self,
        market_id: str,
        token_id: str,
        side: str,
        source: dict[str, object] | None = None,
    ) -> str | None:
        collection = source if source is not None else self.open_positions
        normalized_token = str(token_id or "").strip()
        normalized_side = str(side or "").strip().lower()

        if normalized_token:
            token_key = _token_key(market_id, normalized_token)
            if token_key in collection:
                return token_key

        if normalized_side:
            side_key = _side_key(market_id, normalized_side)
            if side_key in collection:
                return side_key

        return None

    def load_from_db(self) -> None:
        cutoff = int(time.time()) - SEEN_WINDOW
        real_money = 1 if use_real_money() else 0
        conn = get_conn()
        if real_money == 0:
            self._rebuild_shadow_positions(conn)
        seen_rows = conn.execute(
            "SELECT trade_id FROM seen_trades WHERE seen_at > ?",
            (cutoff,),
        ).fetchall()
        position_rows = conn.execute(
            "SELECT * FROM positions WHERE real_money=?",
            (real_money,),
        ).fetchall()
        conn.close()

        self.seen_ids = {row["trade_id"] for row in seen_rows}
        self.open_positions = {
            _position_key(row["market_id"], str(row["token_id"] or ""), row["side"]): {
                "market_id": row["market_id"],
                "side": row["side"],
                "size": float(row["size_usd"] or 0.0),
                "token_id": str(row["token_id"] or ""),
                "avg_price": float(row["avg_price"] or 0.0),
            }
            for row in position_rows
        }
        logger.info(
            "Dedup cache loaded: %s seen, %s open positions",
            len(self.seen_ids),
            len(self.open_positions),
        )

    def sync_positions_from_api(self, tracker, our_address: str) -> None:
        if not our_address or not use_real_money():
            return

        rows = tracker.get_wallet_positions(our_address)
        if rows is None:
            logger.warning("Live positions refresh failed; keeping last known open positions")
            return
        self.sync_positions_from_rows(rows)

    def sync_positions_from_rows(self, rows: list[dict] | None) -> None:
        if not use_real_money():
            return
        if rows is None:
            logger.warning("Live positions sync received no rows; keeping last known open positions")
            return

        conn = get_conn()
        conn.execute("DELETE FROM positions WHERE real_money=1")
        self.open_positions = {}

        for position in rows:
            shares = _to_float(position.get("size"))
            market_id = str(position.get("market_id") or position.get("conditionId") or "").strip()
            token_id = str(position.get("asset") or position.get("asset_id") or position.get("tokenId") or "").strip()
            side = str(position.get("outcome") or position.get("title") or "unknown").strip().lower()
            avg_price = _to_float(position.get("avgPrice") or position.get("averagePrice") or 0.0)
            total_bought = max(_to_float(position.get("totalBought")), 0.0)
            initial_value = max(_to_float(position.get("initialValue")), 0.0)
            current_value = max(_to_float(position.get("currentValue")), 0.0)
            size_usd = total_bought or initial_value
            if size_usd <= 0 and shares > 0 and avg_price > 0:
                size_usd = shares * avg_price
            if size_usd <= 0:
                size_usd = current_value
            if shares <= 0 and size_usd > 0 and avg_price > 0:
                shares = size_usd / avg_price

            if shares <= 0 or size_usd <= 0:
                continue
            if not market_id:
                continue
            self.open_positions[_position_key(market_id, token_id, side)] = {
                "market_id": market_id,
                "side": side,
                "size": size_usd,
                "token_id": token_id,
                "avg_price": avg_price,
            }
            conn.execute(
                "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
                (market_id, side, size_usd, avg_price, token_id, int(time.time()), 1),
            )

        conn.commit()
        conn.close()

    def gate(self, trade_id: str, market_id: str, side: str, token_id: str = "") -> tuple[bool, str]:
        if trade_id in self.seen_ids:
            return False, "duplicate trade_id"
        if self._has_pending(market_id, token_id, side):
            return False, "order in-flight"
        if self._has_position(market_id, token_id, side):
            return False, "position already open"
        return True, "ok"

    def _has_pending(self, market_id: str, token_id: str, side: str) -> bool:
        key = self._find_key(market_id, token_id, side, self.pending)
        if key is None:
            key = _position_key(market_id, token_id, side)
        ts = self.pending.get(key)
        if ts is None:
            return False
        if time.time() - ts > PENDING_TIMEOUT:
            self.pending.pop(key, None)
            return False
        return True

    def _has_position(self, market_id: str, token_id: str, side: str) -> bool:
        return self._find_key(market_id, token_id, side) is not None

    def get_position(self, market_id: str, token_id: str = "", side: str = "") -> dict[str, float | str] | None:
        key = self._find_key(market_id, token_id, side)
        return self.open_positions.get(key) if key else None

    def mark_seen(self, trade_id: str, market_id: str, trader_id: str) -> None:
        now = int(time.time())
        self.seen_ids.add(trade_id)
        conn = get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO seen_trades VALUES (?,?,?,?)",
            (trade_id, market_id, trader_id, now),
        )
        conn.execute(
            "DELETE FROM seen_trades WHERE seen_at < ?",
            (now - SEEN_WINDOW,),
        )
        conn.commit()
        conn.close()

    def mark_pending(self, market_id: str, token_id: str = "", side: str = "") -> None:
        self.pending[_position_key(market_id, token_id, side)] = time.time()

    def confirm(
        self,
        market_id: str,
        side: str,
        size_usd: float,
        token_id: str,
        avg_price: float,
        real_money: bool,
    ) -> None:
        key = _position_key(market_id, token_id, side)
        self.pending.pop(key, None)
        self.open_positions[key] = {
            "market_id": market_id,
            "side": side,
            "size": size_usd,
            "token_id": token_id,
            "avg_price": avg_price,
        }
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
            (market_id, side, size_usd, avg_price, token_id, int(time.time()), 1 if real_money else 0),
        )
        conn.commit()
        conn.close()

    def release(self, market_id: str, token_id: str = "", side: str = "") -> None:
        key = self._find_key(market_id, token_id, side, self.pending)
        if key is None:
            key = _position_key(market_id, token_id, side)
        self.pending.pop(key, None)

    def clear_position(
        self,
        market_id: str,
        token_id: str = "",
        side: str = "",
        real_money: bool | None = None,
    ) -> None:
        mode_flag = 1 if (use_real_money() if real_money is None else real_money) else 0
        key = self._find_key(market_id, token_id, side)
        if key is not None:
            self.pending.pop(key, None)
            self.open_positions.pop(key, None)

        conn = get_conn()
        normalized_token = str(token_id or "").strip()
        normalized_side = str(side or "").strip().lower()
        if normalized_token:
            conn.execute(
                "DELETE FROM positions WHERE market_id=? AND token_id=? AND real_money=?",
                (market_id, normalized_token, mode_flag),
            )
        elif normalized_side:
            conn.execute(
                "DELETE FROM positions WHERE market_id=? AND LOWER(side)=? AND real_money=?",
                (market_id, normalized_side, mode_flag),
            )
        else:
            keys_to_remove = [
                cache_key
                for cache_key, position in self.open_positions.items()
                if str(position.get("market_id") or "").strip().lower() == str(market_id or "").strip().lower()
            ]
            for cache_key in keys_to_remove:
                self.pending.pop(cache_key, None)
                self.open_positions.pop(cache_key, None)
            conn.execute("DELETE FROM positions WHERE market_id=? AND real_money=?", (market_id, mode_flag))
        conn.commit()
        conn.close()
