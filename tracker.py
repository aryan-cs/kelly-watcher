from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from config import max_source_trade_age_seconds
from db import get_conn
from identity_cache import hydrate_observed_identity, resolve_username_for_wallet

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
TRADE_FETCH_MAX_ATTEMPTS = 4
RETRY_BASE_DELAY_S = 0.5


@dataclass
class TradeEvent:
    trade_id: str
    market_id: str
    question: str
    side: str
    action: str
    price: float
    shares: float
    size_usd: float
    token_id: str
    trader_name: str
    trader_address: str
    timestamp: int
    close_time: str
    snapshot: dict[str, Any] | None = field(default=None)
    raw_trade: dict[str, Any] = field(default_factory=dict)
    raw_market_metadata: dict[str, Any] = field(default_factory=dict)
    raw_orderbook: dict[str, Any] | None = field(default=None)
    source_ts_raw: str = ""
    observed_at: int = 0
    poll_started_at: int = 0
    metadata_fetched_at: int = 0
    orderbook_fetched_at: int = 0
    market_close_ts: int = 0


@dataclass
class WalletCursor:
    last_source_ts: int = 0
    last_trade_ids: set[str] = field(default_factory=set)


class PolymarketTracker:
    def __init__(self, wallet_addresses: list[str]):
        self.wallets = [address.lower() for address in wallet_addresses if address]
        self.client = httpx.Client(timeout=15.0, follow_redirects=True)
        self.seen_ids: set[str] = set()
        self.wallet_cursors = self._load_wallet_cursors()
        self.last_trade_poll_ok_at = 0
        self.consecutive_trade_poll_failures = 0

    def close(self) -> None:
        self.client.close()

    def add_wallet(self, address: str) -> None:
        wallet = address.lower()
        if wallet and wallet not in self.wallets:
            self.wallets.append(wallet)
            logger.info("Added wallet to watchlist: %s", wallet)

    def prime_identities(self) -> None:
        for wallet in self.wallets:
            resolve_username_for_wallet(wallet, client=self.client, force=True)

    def trade_feed_health(self) -> tuple[int, int]:
        return self.last_trade_poll_ok_at, self.consecutive_trade_poll_failures

    def _load_wallet_cursors(self) -> dict[str, WalletCursor]:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT wallet_address, last_source_ts, last_trade_ids_json FROM wallet_cursors"
            ).fetchall()
        finally:
            conn.close()

        cursors: dict[str, WalletCursor] = {}
        for row in rows:
            wallet = str(row["wallet_address"] or "").strip().lower()
            if not wallet:
                continue
            try:
                ids = {
                    str(value).strip()
                    for value in json.loads(row["last_trade_ids_json"] or "[]")
                    if str(value).strip()
                }
            except Exception:
                ids = set()
            cursors[wallet] = WalletCursor(
                last_source_ts=int(row["last_source_ts"] or 0),
                last_trade_ids=ids,
            )
        return cursors

    def _persist_wallet_cursor(self, wallet: str) -> None:
        cursor = self.wallet_cursors.get(wallet)
        if cursor is None:
            return
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT INTO wallet_cursors (wallet_address, last_source_ts, last_trade_ids_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                    last_source_ts=excluded.last_source_ts,
                    last_trade_ids_json=excluded.last_trade_ids_json,
                    updated_at=excluded.updated_at
                """,
                (
                    wallet,
                    int(cursor.last_source_ts),
                    json.dumps(sorted(cursor.last_trade_ids), separators=(",", ":")),
                    int(time.time()),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _advance_wallet_cursor(self, wallet: str, event: TradeEvent) -> None:
        normalized_wallet = str(wallet or "").strip().lower()
        if not normalized_wallet:
            return

        cursor = self.wallet_cursors.setdefault(normalized_wallet, WalletCursor())
        if event.timestamp > cursor.last_source_ts:
            cursor.last_source_ts = int(event.timestamp)
            cursor.last_trade_ids = {event.trade_id}
            self._persist_wallet_cursor(normalized_wallet)
            return
        if event.timestamp == cursor.last_source_ts and event.trade_id not in cursor.last_trade_ids:
            cursor.last_trade_ids.add(event.trade_id)
            self._persist_wallet_cursor(normalized_wallet)

    def _record_trade_feed_result(self, ok: bool) -> None:
        if ok:
            self.last_trade_poll_ok_at = int(time.time())
            self.consecutive_trade_poll_failures = 0
            return
        self.consecutive_trade_poll_failures += 1

    def _request_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        failure_log: str,
        suppress_404: bool = False,
    ) -> tuple[Any | None, bool]:
        for attempt in range(TRADE_FETCH_MAX_ATTEMPTS):
            try:
                response = self.client.get(url, params=params)
                if response.status_code == 404 and suppress_404:
                    return None, True
                if response.status_code == 429:
                    raise httpx.HTTPStatusError(
                        "429 Too Many Requests",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json(), True
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else 0
                if status_code in {429, 500, 502, 503, 504} and attempt < TRADE_FETCH_MAX_ATTEMPTS - 1:
                    retry_after = 0.0
                    try:
                        retry_after = float((exc.response.headers or {}).get("Retry-After") or 0.0)
                    except (TypeError, ValueError):
                        retry_after = 0.0
                    delay = max(retry_after, RETRY_BASE_DELAY_S * (attempt + 1))
                    time.sleep(delay + random.uniform(0.0, 0.25))
                    continue
                logger.error("%s: %s", failure_log, exc)
                return None, False
            except Exception as exc:
                if attempt < TRADE_FETCH_MAX_ATTEMPTS - 1:
                    time.sleep((RETRY_BASE_DELAY_S * (attempt + 1)) + random.uniform(0.0, 0.25))
                    continue
                logger.error("%s: %s", failure_log, exc)
                return None, False
        return None, False

    def get_leaderboard(self, window: str = "1w", limit: int = 50) -> list[dict]:
        payload, _ = self._request_json(
            f"{DATA_API}/leaderboard",
            params={"window": window, "limit": limit},
            failure_log="Leaderboard fetch failed",
        )
        if payload is None:
            return []
        return payload if isinstance(payload, list) else payload.get("users", [])

    def add_top_traders(self, window: str = "1w", top_n: int = 20) -> None:
        for entry in self.get_leaderboard(window=window, limit=top_n):
            address = (entry.get("address") or "").strip()
            if address:
                self.add_wallet(address)
        logger.info("Watchlist updated from leaderboard: %s wallets total", len(self.wallets))

    def get_wallet_trades(self, address: str, limit: int = 50) -> list[dict]:
        payload, ok = self._request_json(
            f"{DATA_API}/trades",
            params={"user": address, "limit": limit},
            failure_log=f"Trade fetch failed for {address[:10]}",
        )
        self._record_trade_feed_result(ok)
        if payload is None:
            return []
        return payload if isinstance(payload, list) else payload.get("trades", [])

    def get_wallet_positions(self, address: str) -> list[dict] | None:
        if not address:
            return []
        payload, ok = self._request_json(
            f"{DATA_API}/positions",
            params={"user": address},
            failure_log=f"Position fetch failed for {address[:10]}",
        )
        if payload is None and not ok:
            return None
        return payload if isinstance(payload, list) else payload.get("positions", [])

    def get_market_metadata(self, condition_id: str) -> tuple[dict[str, Any], int]:
        if not condition_id:
            return {}, 0

        payload, ok = self._request_json(
            f"{GAMMA_API}/markets",
            params={"condition_ids": condition_id},
            failure_log=f"Market metadata fetch failed for {condition_id[:12]}",
        )
        if payload is None or not ok:
            return {}, 0

        fetched_at = int(time.time())
        markets = payload if isinstance(payload, list) else payload.get("markets", [])
        for market in markets:
            if str(market.get("conditionId", "")).lower() == condition_id.lower():
                return market, fetched_at
        if markets:
            return markets[0], fetched_at
        return {}, 0

    def get_orderbook_snapshot(self, token_id: str) -> tuple[dict[str, float] | None, dict[str, Any] | None, int]:
        if not token_id:
            return None, None, 0
        book, ok = self._request_json(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            failure_log=f"Orderbook fetch failed for {token_id[:12]}",
            suppress_404=True,
        )
        if book is None:
            return None, None, 0

        fetched_at = int(time.time())
        bids = book.get("bids", []) if isinstance(book, dict) else []
        asks = book.get("asks", []) if isinstance(book, dict) else []
        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 0.0
        if not ok:
            return None, None, 0
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0,
            "bid_depth_usd": sum(float(b["size"]) * float(b["price"]) for b in bids[:5]),
            "ask_depth_usd": sum(float(a["size"]) * float(a["price"]) for a in asks[:5]),
        }, book, fetched_at

    def get_price_history(self, token_id: str, interval: str = "1h") -> list[dict]:
        if not token_id:
            return []
        payload, _ = self._request_json(
            f"{CLOB_API}/prices-history",
            params={"token_id": token_id, "interval": interval},
            failure_log=f"Price history fetch failed for {token_id[:12]}",
        )
        if payload is None:
            return []
        history = payload.get("history", []) if isinstance(payload, dict) else []
        return self._normalize_price_history(history)

    def poll(self) -> list[TradeEvent]:
        new_events: list[TradeEvent] = []
        poll_started_at = int(time.time())
        poll_seen: set[str] = set()

        for address in self.wallets:
            for raw in self.get_wallet_trades(address):
                trade_id = self._raw_trade_id(raw)
                if not trade_id or trade_id in self.seen_ids or trade_id in poll_seen:
                    continue

                event = self._parse_raw_trade(raw, address, poll_started_at)
                if event is None:
                    continue
                if not self._is_new_for_wallet(address, event):
                    continue
                if self._is_stale_event(event, poll_started_at):
                    self._advance_wallet_cursor(address, event)
                    continue
                poll_seen.add(trade_id)

                snap, raw_book, orderbook_fetched_at = self.get_orderbook_snapshot(event.token_id)
                snap = snap or {}
                if event.snapshot:
                    snap.update(event.snapshot)
                history = self.get_price_history(event.token_id, interval="1h")
                if history:
                    snap["price_history_1h"] = history

                event.snapshot = snap or None
                event.raw_orderbook = raw_book
                event.orderbook_fetched_at = orderbook_fetched_at
                new_events.append(event)
                self._advance_wallet_cursor(address, event)

        new_events.sort(key=lambda event: event.timestamp)
        return new_events

    def _is_new_for_wallet(self, wallet: str, event: TradeEvent) -> bool:
        cursor = self.wallet_cursors.get(wallet.lower())
        if cursor is None:
            return True
        if event.timestamp < cursor.last_source_ts:
            return False
        if event.timestamp == cursor.last_source_ts and event.trade_id in cursor.last_trade_ids:
            return False
        return True

    @staticmethod
    def _is_stale_event(event: TradeEvent, poll_started_at: int) -> bool:
        max_age = max_source_trade_age_seconds()
        if max_age <= 0:
            return False
        return (poll_started_at - int(event.timestamp or poll_started_at)) > max_age

    def _parse_raw_trade(self, raw: dict, address: str, poll_started_at: int) -> TradeEvent | None:
        try:
            condition_id = str(raw.get("conditionId") or raw.get("condition_id") or "").strip()
            action = str(raw.get("side") or raw.get("tradeSide") or "BUY").strip().lower()
            outcome = str(raw.get("outcome") or raw.get("title") or action).strip()
            token_id = str(
                raw.get("asset")
                or raw.get("asset_id")
                or raw.get("tokenId")
                or raw.get("token_id")
                or ""
            ).strip()
            trader_name = hydrate_observed_identity(
                address.lower(),
                str(raw.get("name") or raw.get("pseudonym") or "").strip(),
                client=self.client,
            )
            price = float(raw.get("price") or raw.get("outcomePrice") or 0.5)
            shares = float(raw.get("size") or raw.get("shares") or 0.0)
            size_usd = float(raw.get("sizeUsd") or raw.get("usdc_size") or 0.0)
            source_ts_value = raw.get("timestamp") or raw.get("createdAt") or raw.get("created_at")
            source_ts_raw = "" if source_ts_value is None else str(source_ts_value).strip()

            if shares <= 0 and size_usd > 0 and price > 0:
                shares = size_usd / price
            if size_usd <= 0 and shares > 0 and price > 0:
                size_usd = shares * price

            if not condition_id or shares <= 0 or size_usd <= 0 or not token_id:
                return None

            meta, metadata_fetched_at = self.get_market_metadata(condition_id)
            question = str(meta.get("question") or meta.get("title") or raw.get("title") or condition_id)
            close_time = str(meta.get("endDate") or meta.get("closedTime") or meta.get("closeTime") or "")
            snapshot = self._metadata_snapshot(meta)
            observed_at = int(time.time())

            return TradeEvent(
                trade_id=self._raw_trade_id(raw),
                market_id=condition_id,
                question=question,
                side=outcome.lower(),
                action=action,
                price=price,
                shares=shares,
                size_usd=size_usd,
                token_id=token_id,
                trader_name=trader_name,
                trader_address=address.lower(),
                timestamp=self._normalize_timestamp(source_ts_value),
                close_time=close_time,
                snapshot=snapshot,
                raw_trade=dict(raw),
                raw_market_metadata=dict(meta),
                source_ts_raw=source_ts_raw,
                observed_at=observed_at,
                poll_started_at=poll_started_at,
                metadata_fetched_at=metadata_fetched_at,
                market_close_ts=self._normalize_timestamp(close_time) if close_time else 0,
            )
        except Exception as exc:
            logger.warning("Failed to parse trade event: %s", exc)
            return None

    @staticmethod
    def _raw_trade_id(raw: dict) -> str:
        direct = str(raw.get("id") or raw.get("tradeID") or raw.get("trade_id") or "").strip()
        if direct:
            return direct

        tx_hash = str(raw.get("transactionHash") or raw.get("txHash") or "").strip()
        token_id = str(raw.get("asset") or raw.get("asset_id") or raw.get("tokenId") or "").strip()
        timestamp = str(raw.get("timestamp") or "").strip()
        if tx_hash and token_id:
            return f"{tx_hash}:{token_id}:{timestamp}"
        return ""

    @staticmethod
    def _normalize_timestamp(value: Any) -> int:
        if value is None:
            return int(time.time())
        if isinstance(value, (int, float)):
            ts = int(value)
            return ts // 1000 if ts > 10_000_000_000 else ts
        text = str(value).strip()
        if text.isdigit():
            ts = int(text)
            return ts // 1000 if ts > 10_000_000_000 else ts
        try:
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        except Exception:
            return int(time.time())

    @staticmethod
    def _metadata_snapshot(meta: dict) -> dict[str, float | None]:
        if not meta:
            return {}

        best_bid = float(meta.get("bestBid") or 0.0)
        best_ask = float(meta.get("bestAsk") or 0.0)
        last_trade = float(meta.get("lastTradePrice") or 0.0)
        mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else last_trade

        volume_24h = float(
            meta.get("volume24hr")
            or meta.get("volume24h")
            or meta.get("oneDayVolume")
            or 0.0
        )
        volume_7d = float(
            meta.get("volume7d")
            or meta.get("sevenDayVolume")
            or meta.get("oneWeekVolume")
            or 0.0
        )
        oi_usd = float(meta.get("openInterest") or meta.get("liquidity") or 0.0)

        top_holder_pct: float | None = None
        raw_top_holder = meta.get("topHolderPct") or meta.get("top_holder_pct")
        if raw_top_holder not in {None, ""}:
            try:
                top_holder_pct = float(raw_top_holder)
            except (TypeError, ValueError):
                top_holder_pct = None

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid if mid > 0 else 0.5,
            "volume_24h_usd": volume_24h,
            "volume_7d_avg_usd": (volume_7d / 7) if volume_7d > 0 else None,
            "oi_usd": oi_usd,
            "top_holder_pct": top_holder_pct,
        }

    @classmethod
    def _normalize_price_history(cls, history: list[dict[str, Any]]) -> list[dict[str, float]]:
        normalized: list[dict[str, float]] = []
        for row in history if isinstance(history, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                price = float(row.get("p") or row.get("price") or row.get("value"))
            except (TypeError, ValueError):
                continue
            if not (0.0 < price < 1.0):
                continue
            ts_raw = row.get("t") or row.get("timestamp") or row.get("time")
            ts = cls._normalize_timestamp(ts_raw) if ts_raw is not None else 0
            normalized.append({"p": price, "t": float(ts)})
        normalized.sort(key=lambda item: item.get("t", 0.0))
        return normalized
