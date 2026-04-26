from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from kelly_watcher.config import use_real_money
from kelly_watcher.data.db import get_conn
from kelly_watcher.engine.trade_contract import OPEN_EXECUTED_ENTRY_SQL, remaining_entry_shares_expr, remaining_entry_size_expr

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
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False, compare=False)

    def _rebuild_shadow_positions(self, conn) -> None:
        conn.execute("DELETE FROM positions WHERE real_money=0")
        rows = conn.execute(
            f"""
            SELECT
                market_id,
                LOWER(side) AS side,
                COALESCE(token_id, '') AS token_id,
                SUM({remaining_entry_size_expr()}) AS size_usd,
                SUM({remaining_entry_shares_expr()}) AS shares,
                MIN(placed_at) AS entered_at
            FROM trade_log
            WHERE real_money=0
              AND {OPEN_EXECUTED_ENTRY_SQL}
            GROUP BY market_id, COALESCE(token_id, ''), LOWER(side)
            """
        ).fetchall()

        for row in rows:
            size_usd = float(row["size_usd"] or 0.0)
            shares = float(row["shares"] or 0.0)
            if size_usd <= 0 or shares <= 0:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO positions (
                    market_id, side, size_usd, avg_price, token_id, entered_at, real_money
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    row["market_id"],
                    row["side"],
                    size_usd,
                    size_usd / shares,
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

    def load_from_db(self, *, rebuild_shadow_positions: bool = False) -> None:
        cutoff = int(time.time()) - SEEN_WINDOW
        real_money = 1 if use_real_money() else 0
        conn = get_conn()
        try:
            if real_money == 0 and rebuild_shadow_positions:
                self._rebuild_shadow_positions(conn)
            seen_rows = conn.execute(
                "SELECT trade_id FROM seen_trades WHERE seen_at > ?",
                (cutoff,),
            ).fetchall()
            position_rows = conn.execute(
                "SELECT * FROM positions WHERE real_money=?",
                (real_money,),
            ).fetchall()
        finally:
            conn.close()

        new_seen_ids = {row["trade_id"] for row in seen_rows}
        new_open_positions = {
            _position_key(row["market_id"], str(row["token_id"] or ""), row["side"]): {
                "market_id": row["market_id"],
                "side": row["side"],
                "size": float(row["size_usd"] or 0.0),
                "token_id": str(row["token_id"] or ""),
                "avg_price": float(row["avg_price"] or 0.0),
            }
            for row in position_rows
        }
        with self._lock:
            self.seen_ids = new_seen_ids
            self.open_positions = new_open_positions
        logger.info(
            "Dedup cache loaded: %s seen, %s open positions",
            len(new_seen_ids),
            len(new_open_positions),
        )

    def sync_positions_from_api(self, tracker, our_address: str) -> bool:
        if not our_address or not use_real_money():
            return False

        rows = tracker.get_wallet_positions(our_address)
        if rows is None:
            logger.warning("Live positions refresh failed; keeping last known open positions")
            return False
        self.sync_positions_from_rows(rows)
        return True

    def sync_positions_from_rows(self, rows: list[dict] | None) -> None:
        if not use_real_money():
            return
        if rows is None:
            logger.warning("Live positions sync received no rows; keeping last known open positions")
            return

        conn = get_conn()
        try:
            conn.execute("DELETE FROM positions WHERE real_money=1")
            new_open_positions: dict[str, dict[str, float | str]] = {}

            for position in rows:
                shares = _to_float(position.get("size"))
                market_id = str(position.get("market_id") or position.get("conditionId") or "").strip()
                token_id = str(
                    position.get("asset") or position.get("asset_id") or position.get("tokenId") or ""
                ).strip()
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
                new_open_positions[_position_key(market_id, token_id, side)] = {
                    "market_id": market_id,
                    "side": side,
                    "size": size_usd,
                    "token_id": token_id,
                    "avg_price": avg_price,
                }
                conn.execute(
                    """
                    INSERT OR REPLACE INTO positions (
                        market_id, side, size_usd, avg_price, token_id, entered_at, real_money
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (market_id, side, size_usd, avg_price, token_id, int(time.time()), 1),
                )

            conn.commit()
        finally:
            conn.close()
        with self._lock:
            self.open_positions = new_open_positions

    def gate(
        self,
        trade_id: str,
        market_id: str,
        side: str,
        token_id: str = "",
        *,
        allow_existing_position: bool = False,
    ) -> tuple[bool, str]:
        with self._lock:
            if trade_id in self.seen_ids:
                return False, "duplicate trade_id"
            if self._has_pending(market_id, token_id, side):
                return False, "order in-flight"
            if not allow_existing_position and self._has_position(market_id, token_id, side):
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
        with self._lock:
            key = self._find_key(market_id, token_id, side)
            return self.open_positions.get(key) if key else None

    def has_pending_position(self, market_id: str, token_id: str = "", side: str = "") -> bool:
        with self._lock:
            return self._has_pending(market_id, token_id, side)

    def mark_seen(self, trade_id: str, market_id: str, trader_id: str) -> None:
        now = int(time.time())
        with self._lock:
            self.seen_ids.add(trade_id)
        conn = get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO seen_trades VALUES (?,?,?,?)",
                (trade_id, market_id, trader_id, now),
            )
            conn.execute(
                "DELETE FROM seen_trades WHERE seen_at < ?",
                (now - SEEN_WINDOW,),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_pending(self, market_id: str, token_id: str = "", side: str = "") -> None:
        with self._lock:
            self.pending[_position_key(market_id, token_id, side)] = time.time()

    def confirm(
        self,
        market_id: str,
        side: str,
        size_usd: float,
        token_id: str,
        avg_price: float,
        real_money: bool,
        *,
        merge: bool = False,
    ) -> None:
        key = _position_key(market_id, token_id, side)
        effective_size = float(size_usd or 0.0)
        effective_avg_price = float(avg_price or 0.0)
        with self._lock:
            if merge:
                existing = self.open_positions.get(key)
                existing_size = _to_float((existing or {}).get("size"))
                existing_avg_price = _to_float((existing or {}).get("avg_price"))
                existing_shares = existing_size / existing_avg_price if existing_size > 0 and existing_avg_price > 0 else 0.0
                added_shares = effective_size / effective_avg_price if effective_size > 0 and effective_avg_price > 0 else 0.0
                total_size = existing_size + effective_size
                total_shares = existing_shares + added_shares
                if total_size > 0:
                    effective_size = total_size
                if total_size > 0 and total_shares > 0:
                    effective_avg_price = total_size / total_shares
            effective_position = {
                "market_id": market_id,
                "side": side,
                "size": effective_size,
                "token_id": token_id,
                "avg_price": effective_avg_price,
            }
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO positions (
                    market_id, side, size_usd, avg_price, token_id, entered_at, real_money
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    market_id,
                    side,
                    effective_size,
                    effective_avg_price,
                    token_id,
                    int(time.time()),
                    1 if real_money else 0,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        with self._lock:
            self.pending.pop(key, None)
            self.open_positions[key] = effective_position

    def release(self, market_id: str, token_id: str = "", side: str = "") -> None:
        with self._lock:
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
        with self._lock:
            key = self._find_key(market_id, token_id, side)
            if key is not None:
                self.pending.pop(key, None)
                self.open_positions.pop(key, None)

        conn = get_conn()
        try:
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
                with self._lock:
                    keys_to_remove = [
                        cache_key
                        for cache_key, position in self.open_positions.items()
                        if str(position.get("market_id") or "").strip().lower()
                        == str(market_id or "").strip().lower()
                    ]
                    for cache_key in keys_to_remove:
                        self.pending.pop(cache_key, None)
                        self.open_positions.pop(cache_key, None)
                conn.execute("DELETE FROM positions WHERE market_id=? AND real_money=?", (market_id, mode_flag))
            conn.commit()
        finally:
            conn.close()
