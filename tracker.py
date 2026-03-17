from __future__ import annotations

import logging
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from identity_cache import hydrate_observed_identity, resolve_username_for_wallet

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


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


class PolymarketTracker:
    def __init__(self, wallet_addresses: list[str]):
        self.wallets = [address.lower() for address in wallet_addresses if address]
        self.client = httpx.Client(timeout=15.0, follow_redirects=True)
        self.seen_ids: set[str] = set()

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

    def get_leaderboard(self, window: str = "1w", limit: int = 50) -> list[dict]:
        try:
            response = self.client.get(
                f"{DATA_API}/leaderboard",
                params={"window": window, "limit": limit},
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else payload.get("users", [])
        except Exception as exc:
            logger.error("Leaderboard fetch failed: %s", exc)
            return []

    def add_top_traders(self, window: str = "1w", top_n: int = 20) -> None:
        for entry in self.get_leaderboard(window=window, limit=top_n):
            address = (entry.get("address") or "").strip()
            if address:
                self.add_wallet(address)
        logger.info("Watchlist updated from leaderboard: %s wallets total", len(self.wallets))

    def get_wallet_trades(self, address: str, limit: int = 50) -> list[dict]:
        try:
            response = self.client.get(
                f"{DATA_API}/trades",
                params={"user": address, "limit": limit},
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else payload.get("trades", [])
        except Exception as exc:
            logger.error("Trade fetch failed for %s: %s", address[:10], exc)
            return []

    def get_wallet_positions(self, address: str) -> list[dict] | None:
        if not address:
            return []
        try:
            response = self.client.get(
                f"{DATA_API}/positions",
                params={"user": address},
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else payload.get("positions", [])
        except Exception as exc:
            logger.error("Position fetch failed for %s: %s", address[:10], exc)
            return None

    def get_market_metadata(self, condition_id: str) -> tuple[dict[str, Any], int]:
        if not condition_id:
            return {}, 0

        try:
            response = self.client.get(
                f"{GAMMA_API}/markets",
                params={"condition_ids": condition_id},
            )
            response.raise_for_status()
            fetched_at = int(time.time())
            payload = response.json()
            markets = payload if isinstance(payload, list) else payload.get("markets", [])
            for market in markets:
                if str(market.get("conditionId", "")).lower() == condition_id.lower():
                    return market, fetched_at
            if markets:
                return markets[0], fetched_at
        except Exception as exc:
            logger.debug("Market metadata fetch failed for %s: %s", condition_id[:12], exc)

        logger.error("Market metadata fetch failed for %s", condition_id[:12])
        return {}, 0

    def get_orderbook_snapshot(self, token_id: str) -> tuple[dict[str, float] | None, dict[str, Any] | None, int]:
        if not token_id:
            return None, None, 0
        try:
            response = self.client.get(f"{CLOB_API}/book", params={"token_id": token_id})
            response.raise_for_status()
            book = response.json()
            fetched_at = int(time.time())
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            best_bid = float(bids[0]["price"]) if bids else 0.01
            best_ask = float(asks[0]["price"]) if asks else 0.99
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid": (best_bid + best_ask) / 2,
                "bid_depth_usd": sum(float(b["size"]) * float(b["price"]) for b in bids[:5]),
                "ask_depth_usd": sum(float(a["size"]) * float(a["price"]) for a in asks[:5]),
            }, book, fetched_at
        except Exception as exc:
            message = str(exc)
            if "404" in message:
                logger.debug("No orderbook for token %s", token_id[:12])
            else:
                logger.error("Orderbook fetch failed for %s: %s", token_id[:12], exc)
            return None, None, 0

    def get_price_history(self, token_id: str, interval: str = "1h") -> list[dict]:
        if not token_id:
            return []
        try:
            response = self.client.get(
                f"{CLOB_API}/prices-history",
                params={"token_id": token_id, "interval": interval},
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("history", []) if isinstance(payload, dict) else []
        except Exception as exc:
            logger.warning("Price history fetch failed for %s: %s", token_id[:12], exc)
            return []

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
                poll_seen.add(trade_id)

                snap, raw_book, orderbook_fetched_at = self.get_orderbook_snapshot(event.token_id)
                snap = snap or {}
                if event.snapshot:
                    snap.update(event.snapshot)

                event.snapshot = snap or None
                event.raw_orderbook = raw_book
                event.orderbook_fetched_at = orderbook_fetched_at
                new_events.append(event)

        new_events.sort(key=lambda event: event.timestamp)
        return new_events

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
    def _metadata_snapshot(meta: dict) -> dict[str, float]:
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
        top_holder_pct = float(meta.get("topHolderPct") or meta.get("top_holder_pct") or 0.1)

        price_history_1h = []
        raw_prices = meta.get("outcomePrices")
        if isinstance(raw_prices, str):
            try:
                prices = json.loads(raw_prices)
                if prices:
                    price_history_1h = [{"p": float(prices[0])}]
            except Exception:
                price_history_1h = []

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid if mid > 0 else 0.5,
            "volume_24h_usd": volume_24h,
            "volume_7d_avg_usd": (volume_7d / 7) if volume_7d > 0 else 0.0,
            "oi_usd": oi_usd,
            "top_holder_pct": top_holder_pct,
            "price_history_1h": price_history_1h,
        }
