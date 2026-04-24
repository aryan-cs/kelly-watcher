from __future__ import annotations

import json
import logging
import hashlib
import random
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import httpx

from kelly_watcher.config import max_source_trade_age_seconds
from kelly_watcher.data.db import get_conn, init_db
from kelly_watcher.data.identity_cache import hydrate_observed_identity, resolve_username_for_wallet

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
TRADE_FETCH_MAX_ATTEMPTS = 2
AUX_FETCH_MAX_ATTEMPTS = 2
RETRY_BASE_DELAY_S = 0.35
TRADE_REQUEST_TIMEOUT_S = 3.0
POSITIONS_REQUEST_TIMEOUT_S = 4.0
METADATA_REQUEST_TIMEOUT_S = 4.0
ORDERBOOK_REQUEST_TIMEOUT_S = 3.0
PRICE_HISTORY_REQUEST_TIMEOUT_S = 4.0
WALLET_TRADE_FETCH_WORKERS = 6
ENRICHMENT_FETCH_WORKERS = 6
WALLET_TRADE_FETCH_MAX_PAGES = 50
MARKET_METADATA_CACHE_TTL_S = 300
ORDERBOOK_CACHE_TTL_S = 2
PRICE_HISTORY_CACHE_TTL_S = 60
INTRADAY_MARKET_METADATA_CACHE_TTL_S = 30


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
    watch_tier: str = ""


@dataclass
class WalletCursor:
    last_source_ts: int = 0
    last_trade_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class RawTradeCandidate:
    wallet: str
    trade_id: str
    timestamp: int
    condition_id: str
    token_id: str
    raw: dict[str, Any]
    watch_tier: str = ""
    first_seen_at: int = 0
    observed_at: int = 0


@dataclass(frozen=True)
class SourceEventIngestionResult:
    fetched: int = 0
    queued: int = 0
    malformed: int = 0
    duplicate: int = 0


class PolymarketTracker:
    def __init__(
        self,
        wallet_addresses: list[str],
        activity_callback: Callable[[], None] | None = None,
    ):
        self.wallets = [address.lower() for address in wallet_addresses if address]
        self.client = httpx.Client(timeout=15.0, follow_redirects=True)
        self.activity_callback = activity_callback
        self.seen_ids: set[str] = set()
        self.wallet_cursors = self._load_wallet_cursors()
        self.last_trade_poll_ok_at = 0
        self.consecutive_trade_poll_failures = 0
        self._market_metadata_cache: dict[str, tuple[float, tuple[dict[str, Any], int]]] = {}
        self._orderbook_cache: dict[str, tuple[float, tuple[dict[str, float] | None, dict[str, Any] | None, int]]] = {}
        self._price_history_cache: dict[tuple[str, str], tuple[float, list[dict[str, float]]]] = {}
        self._dirty_wallet_cursors: set[str] = set()

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _new_http_client() -> httpx.Client:
        return httpx.Client(timeout=15.0, follow_redirects=True)

    def add_wallet(self, address: str) -> None:
        wallet = address.lower()
        if wallet and wallet not in self.wallets:
            self.wallets.append(wallet)
            logger.info("Added wallet to watchlist: %s", wallet)

    def prime_identities(self, wallet_addresses: list[str] | None = None) -> None:
        targets = self.wallets if wallet_addresses is None else wallet_addresses
        with self._new_http_client() as client:
            for wallet in targets:
                self._touch_activity()
                resolve_username_for_wallet(wallet, client=client, force=True)

    def trade_feed_health(self) -> tuple[int, int]:
        return self.last_trade_poll_ok_at, self.consecutive_trade_poll_failures

    def _touch_activity(self) -> None:
        if self.activity_callback is None:
            return
        try:
            self.activity_callback()
        except Exception:
            logger.debug("Tracker activity callback failed", exc_info=True)

    def _load_wallet_cursors(self) -> dict[str, WalletCursor]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    CAST(wallet_address AS BLOB) AS wallet_address_blob,
                    last_source_ts,
                    CAST(last_trade_ids_json AS BLOB) AS last_trade_ids_blob
                FROM wallet_cursors
                """
            ).fetchall()
        except sqlite3.DatabaseError:
            return {}
        finally:
            conn.close()

        cursors: dict[str, WalletCursor] = {}
        for row in rows:
            wallet_raw = row["wallet_address_blob"]
            if isinstance(wallet_raw, bytes):
                wallet = wallet_raw.decode("utf-8", errors="ignore").strip().lower()
            else:
                wallet = str(wallet_raw or "").strip().lower()
            if not wallet:
                continue
            try:
                ids_payload = row["last_trade_ids_blob"]
                if isinstance(ids_payload, bytes):
                    ids_payload = ids_payload.decode("utf-8", errors="ignore")
                ids = {
                    str(value).strip()
                    for value in json.loads(ids_payload or "[]")
                    if str(value).strip()
                }
            except Exception:
                logger.warning("Skipping malformed wallet cursor ids for %s", wallet)
                ids = set()
            cursors[wallet] = WalletCursor(
                last_source_ts=int(row["last_source_ts"] or 0),
                last_trade_ids=ids,
            )
        return cursors

    def _persist_wallet_cursors(self, wallets: set[str] | list[str] | tuple[str, ...]) -> None:
        normalized_wallets = sorted(
            {
                str(wallet or "").strip().lower()
                for wallet in wallets
                if str(wallet or "").strip()
            }
        )
        if not normalized_wallets:
            return

        rows: list[tuple[str, int, str, int]] = []
        now_ts = int(time.time())
        for wallet in normalized_wallets:
            cursor = self.wallet_cursors.get(wallet)
            if cursor is None:
                continue
            rows.append(
                (
                    wallet,
                    int(cursor.last_source_ts),
                    json.dumps(sorted(cursor.last_trade_ids), separators=(",", ":")),
                    now_ts,
                )
            )
        if not rows:
            return

        conn = get_conn()
        try:
            conn.executemany(
                """
                INSERT INTO wallet_cursors (wallet_address, last_source_ts, last_trade_ids_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                    last_source_ts=excluded.last_source_ts,
                    last_trade_ids_json=excluded.last_trade_ids_json,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def _flush_dirty_wallet_cursors(self) -> None:
        dirty = set(self._dirty_wallet_cursors)
        if not dirty:
            return
        self._persist_wallet_cursors(dirty)
        self._dirty_wallet_cursors.difference_update(dirty)

    def _advance_wallet_cursor_state(self, wallet: str, timestamp: int, trade_id: str) -> None:
        normalized_wallet = str(wallet or "").strip().lower()
        trade_id_text = str(trade_id or "").strip()
        source_ts = int(timestamp or 0)
        if not normalized_wallet or not trade_id_text or source_ts <= 0:
            return

        cursor = self.wallet_cursors.setdefault(normalized_wallet, WalletCursor())
        if source_ts > cursor.last_source_ts:
            cursor.last_source_ts = source_ts
            cursor.last_trade_ids = {trade_id_text}
            self._dirty_wallet_cursors.add(normalized_wallet)
            return
        if source_ts == cursor.last_source_ts and trade_id_text not in cursor.last_trade_ids:
            cursor.last_trade_ids.add(trade_id_text)
            self._dirty_wallet_cursors.add(normalized_wallet)

    def _advance_wallet_cursor(self, wallet: str, event: TradeEvent) -> None:
        self._advance_wallet_cursor_state(wallet, event.timestamp, event.trade_id)

    def _stage_source_rows(self, rows: list[tuple[str, str, str, str, str, int, str, str, int, int, int, str]]) -> None:
        if not rows:
            return
        sql = """
            INSERT INTO source_event_queue (
                trade_id, wallet_address, watch_tier, condition_id, token_id,
                source_ts, source_trade_json, status, attempts, first_seen_at,
                observed_at, updated_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(trade_id) DO UPDATE SET
                wallet_address=excluded.wallet_address,
                watch_tier=excluded.watch_tier,
                condition_id=excluded.condition_id,
                token_id=excluded.token_id,
                source_ts=excluded.source_ts,
                source_trade_json=excluded.source_trade_json,
                observed_at=excluded.observed_at,
                updated_at=excluded.updated_at,
                status=CASE
                    WHEN source_event_queue.status='processed' THEN source_event_queue.status
                    ELSE excluded.status
                END,
                last_error=CASE
                    WHEN source_event_queue.status='processed' THEN source_event_queue.last_error
                    ELSE excluded.last_error
                END
            """
        conn = get_conn()
        try:
            conn.executemany(sql, rows)
            conn.commit()
        except sqlite3.OperationalError as exc:
            conn.close()
            if "source_event_queue" not in str(exc):
                raise
            init_db()
            conn = get_conn()
            conn.executemany(sql, rows)
            conn.commit()
        finally:
            conn.close()

    def stage_source_events(
        self,
        wallet_addresses: list[str] | None = None,
        *,
        trade_limit: int = 50,
        watch_tier: str = "",
    ) -> SourceEventIngestionResult:
        targets = self.wallets if wallet_addresses is None else wallet_addresses
        normalized_targets = [str(address or "").strip().lower() for address in targets if str(address or "").strip()]
        if not normalized_targets:
            return SourceEventIngestionResult()

        poll_started_at = int(time.time())
        wallet_trades = self._fetch_wallet_trades_batch(normalized_targets, limit=trade_limit)
        poll_seen: set[str] = set()
        rows_to_stage: list[tuple[str, str, str, str, str, int, str, str, int, int, int, str]] = []
        cursor_advances: list[tuple[str, int, str]] = []
        fetched = 0
        malformed = 0
        duplicate = 0

        for address in normalized_targets:
            self._touch_activity()
            cursor = self.wallet_cursors.get(address)
            rows = sorted(
                wallet_trades.get(address, []),
                key=self._raw_trade_timestamp,
                reverse=True,
            )
            fetched += len(rows)
            for raw in rows:
                trade_id = self._raw_trade_id(raw)
                missing_trade_id = False
                if not trade_id:
                    raw_fingerprint = json.dumps(dict(raw), separators=(",", ":"), sort_keys=True, default=str)
                    trade_id = f"malformed:{address}:{hashlib.sha256(raw_fingerprint.encode('utf-8')).hexdigest()[:24]}"
                    missing_trade_id = True
                    logger.warning("Recording malformed source trade without a stable trade id for wallet %s", address[:10])
                if trade_id in self.seen_ids or trade_id in poll_seen:
                    duplicate += 1
                    continue

                source_ts = self._raw_trade_timestamp(raw)
                if cursor is not None and source_ts > 0 and source_ts < cursor.last_source_ts:
                    break
                if cursor is not None and source_ts == cursor.last_source_ts and trade_id in cursor.last_trade_ids:
                    duplicate += 1
                    continue

                condition_id = str(raw.get("conditionId") or raw.get("condition_id") or "").strip().lower()
                token_id = str(
                    raw.get("asset")
                    or raw.get("asset_id")
                    or raw.get("tokenId")
                    or raw.get("token_id")
                    or ""
                ).strip()
                error = ""
                status = "pending"
                if missing_trade_id:
                    error = "missing stable trade id"
                elif source_ts <= 0:
                    error = "missing source timestamp"
                elif not condition_id:
                    error = "missing condition id"
                elif not token_id:
                    error = "missing token id"
                if error:
                    malformed += 1
                    status = "malformed"

                poll_seen.add(trade_id)
                observed_at = int(time.time())
                rows_to_stage.append(
                    (
                        trade_id,
                        address,
                        str(watch_tier or ""),
                        condition_id,
                        token_id,
                        int(source_ts or 0),
                        json.dumps(dict(raw), separators=(",", ":"), sort_keys=True),
                        status,
                        poll_started_at,
                        observed_at,
                        observed_at,
                        error,
                    )
                )
                if source_ts > 0:
                    cursor_advances.append((address, int(source_ts), trade_id))

        self._stage_source_rows(rows_to_stage)
        for wallet, source_ts, trade_id in cursor_advances:
            self._advance_wallet_cursor_state(wallet, source_ts, trade_id)
        self._flush_dirty_wallet_cursors()
        return SourceEventIngestionResult(
            fetched=fetched,
            queued=len(rows_to_stage),
            malformed=malformed,
            duplicate=duplicate,
        )

    def source_queue_counts(self) -> dict[str, int]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM source_event_queue
                GROUP BY status
                """
            ).fetchall()
        finally:
            conn.close()
        return {str(row["status"] or ""): int(row["n"] or 0) for row in rows}

    def _claim_source_queue_rows(self, *, limit: int | None = None) -> list[sqlite3.Row]:
        now_ts = int(time.time())
        stale_processing_cutoff = now_ts - 300
        limit_clause = ""
        params: list[Any] = [stale_processing_cutoff]
        if limit is not None and int(limit) > 0:
            limit_clause = " LIMIT ?"
            params.append(int(limit))

        conn = get_conn()
        try:
            rows = conn.execute(
                f"""
                SELECT *
                FROM source_event_queue
                WHERE status='pending'
                   OR (status='failed' AND attempts < 5)
                   OR (status='processing' AND updated_at < ?)
                ORDER BY source_ts DESC, first_seen_at ASC
                {limit_clause}
                """,
                params,
            ).fetchall()
            if rows:
                conn.executemany(
                    """
                    UPDATE source_event_queue
                    SET status='processing',
                        attempts=attempts + 1,
                        updated_at=?,
                        last_error=''
                    WHERE trade_id=?
                    """,
                    [(now_ts, str(row["trade_id"])) for row in rows],
                )
                conn.commit()
            return list(rows)
        finally:
            conn.close()

    def load_queued_events(self, *, limit: int | None = None) -> list[TradeEvent]:
        rows = self._claim_source_queue_rows(limit=limit)
        if not rows:
            return []

        candidates: list[RawTradeCandidate] = []
        for row in rows:
            trade_id = str(row["trade_id"] or "").strip()
            try:
                raw_payload = json.loads(str(row["source_trade_json"] or "{}"))
            except json.JSONDecodeError as exc:
                self.mark_source_event_failed(trade_id, f"malformed source json: {exc}", terminal=True)
                continue
            if not isinstance(raw_payload, dict):
                self.mark_source_event_failed(trade_id, "source json was not an object", terminal=True)
                continue
            candidates.append(
                RawTradeCandidate(
                    wallet=str(row["wallet_address"] or "").strip().lower(),
                    trade_id=trade_id,
                    timestamp=int(row["source_ts"] or 0),
                    condition_id=str(row["condition_id"] or "").strip().lower(),
                    token_id=str(row["token_id"] or "").strip(),
                    raw=dict(raw_payload),
                    watch_tier=str(row["watch_tier"] or ""),
                    first_seen_at=int(row["first_seen_at"] or 0),
                    observed_at=int(row["observed_at"] or 0),
                )
            )

        metadata_by_condition = self._fetch_market_metadata_batch(
            [candidate.condition_id for candidate in candidates]
        )
        orderbooks_by_token = self._fetch_orderbook_snapshots_batch(
            [candidate.token_id for candidate in candidates]
        )

        events: list[TradeEvent] = []
        for candidate in candidates:
            meta, metadata_fetched_at = metadata_by_condition.get(candidate.condition_id, ({}, 0))
            event = self._parse_raw_trade(
                candidate.raw,
                candidate.wallet,
                candidate.first_seen_at or int(time.time()),
                market_meta=meta,
                metadata_fetched_at=metadata_fetched_at,
                watch_tier=candidate.watch_tier,
            )
            if event is None:
                self.mark_source_event_failed(candidate.trade_id, "source event could not be parsed", terminal=True)
                continue

            snap, raw_book, orderbook_fetched_at = orderbooks_by_token.get(
                candidate.token_id,
                (None, None, 0),
            )
            merged_snapshot = dict(snap or {})
            if event.snapshot:
                merged_snapshot.update(event.snapshot)

            event.snapshot = merged_snapshot or None
            event.raw_orderbook = raw_book
            if candidate.observed_at > 0:
                event.observed_at = candidate.observed_at
            event.orderbook_fetched_at = orderbook_fetched_at
            event.watch_tier = candidate.watch_tier or event.watch_tier or ""
            events.append(event)

        events.sort(key=lambda event: event.timestamp)
        return events

    def mark_source_event_processed(self, trade_id: str) -> None:
        normalized = str(trade_id or "").strip()
        if not normalized:
            return
        conn = get_conn()
        try:
            conn.execute(
                """
                UPDATE source_event_queue
                SET status='processed',
                    updated_at=?,
                    last_error=''
                WHERE trade_id=?
                """,
                (int(time.time()), normalized),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_source_event_failed(self, trade_id: str, error: str, *, terminal: bool = False) -> None:
        normalized = str(trade_id or "").strip()
        if not normalized:
            return
        conn = get_conn()
        try:
            conn.execute(
                """
                UPDATE source_event_queue
                SET status=?,
                    updated_at=?,
                    last_error=?
                WHERE trade_id=?
                """,
                ("malformed" if terminal else "failed", int(time.time()), str(error or "")[:500], normalized),
            )
            conn.commit()
        finally:
            conn.close()

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
        timeout_s: float | None = None,
        max_attempts: int = TRADE_FETCH_MAX_ATTEMPTS,
    ) -> tuple[Any | None, bool]:
        attempt_count = max(1, int(max_attempts or 1))
        for attempt in range(attempt_count):
            try:
                self._touch_activity()
                request_kwargs: dict[str, Any] = {"params": params}
                if timeout_s is not None:
                    request_kwargs["timeout"] = timeout_s
                client = getattr(self, "client", None)
                if client is None:
                    with self._new_http_client() as client:
                        response = client.get(url, **request_kwargs)
                else:
                    response = client.get(url, **request_kwargs)
                if response.status_code == 404 and suppress_404:
                    self._touch_activity()
                    return None, True
                if response.status_code == 429:
                    raise httpx.HTTPStatusError(
                        "429 Too Many Requests",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                self._touch_activity()
                return response.json(), True
            except httpx.HTTPStatusError as exc:
                self._touch_activity()
                status_code = exc.response.status_code if exc.response is not None else 0
                if status_code in {429, 500, 502, 503, 504} and attempt < attempt_count - 1:
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
                self._touch_activity()
                if attempt < attempt_count - 1:
                    time.sleep((RETRY_BASE_DELAY_S * (attempt + 1)) + random.uniform(0.0, 0.25))
                    continue
                logger.error("%s: %s", failure_log, exc)
                return None, False
        return None, False

    def _cache_get(self, cache_name: str, key: Any, ttl_seconds: int) -> Any | None:
        cache = getattr(self, cache_name, None)
        if cache is None:
            cache = {}
            setattr(self, cache_name, cache)
        entry = cache.get(key)
        if not entry:
            return None
        cached_at, value = entry
        if (time.time() - float(cached_at)) <= ttl_seconds:
            return value
        cache.pop(key, None)
        return None

    def _cache_put(self, cache_name: str, key: Any, value: Any) -> Any:
        cache = getattr(self, cache_name, None)
        if cache is None:
            cache = {}
            setattr(self, cache_name, cache)
        cache[key] = (time.time(), value)
        return value

    @staticmethod
    def _metadata_cache_ttl_s(meta: dict[str, Any] | None) -> int:
        if not isinstance(meta, dict):
            return MARKET_METADATA_CACHE_TTL_S

        close_time = str(meta.get("endDate") or meta.get("closedTime") or "").strip()
        if not close_time:
            return MARKET_METADATA_CACHE_TTL_S

        try:
            close_ts = datetime.fromisoformat(close_time.replace("Z", "+00:00")).timestamp()
        except Exception:
            return MARKET_METADATA_CACHE_TTL_S

        if max(close_ts - time.time(), 0.0) < 86400:
            return INTRADAY_MARKET_METADATA_CACHE_TTL_S
        return MARKET_METADATA_CACHE_TTL_S

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

    def get_wallet_trades(
        self,
        address: str,
        limit: int = 50,
        cursor: WalletCursor | None = None,
    ) -> list[dict]:
        page_limit = max(1, min(int(limit or 50), 100))
        rows: list[dict] = []
        seen_ids: set[str] = set()
        reached_cursor = False

        for page in range(WALLET_TRADE_FETCH_MAX_PAGES):
            params: dict[str, Any] = {"user": address, "limit": page_limit}
            if page > 0:
                params["offset"] = page * page_limit
            payload, ok = self._request_json(
                f"{DATA_API}/trades",
                params=params,
                failure_log=f"Trade fetch failed for {address[:10]}",
                timeout_s=TRADE_REQUEST_TIMEOUT_S,
                max_attempts=TRADE_FETCH_MAX_ATTEMPTS,
            )
            self._record_trade_feed_result(ok)
            if payload is None:
                break

            batch = payload if isinstance(payload, list) else payload.get("trades", [])
            if not isinstance(batch, list) or not batch:
                break

            added = 0
            for raw in batch:
                if not isinstance(raw, dict):
                    continue
                trade_id = self._raw_trade_id(raw)
                source_ts = self._raw_trade_timestamp(raw)
                if cursor is not None and source_ts > 0:
                    if source_ts < cursor.last_source_ts:
                        reached_cursor = True
                        break
                    if source_ts == cursor.last_source_ts and trade_id in cursor.last_trade_ids:
                        reached_cursor = True
                        break
                if trade_id and trade_id in seen_ids:
                    continue
                if trade_id:
                    seen_ids.add(trade_id)
                rows.append(raw)
                added += 1

            if reached_cursor or len(batch) < page_limit or added == 0:
                break

        return rows

    def get_wallet_positions(self, address: str) -> list[dict] | None:
        if not address:
            return []
        payload, ok = self._request_json(
            f"{DATA_API}/positions",
            params={"user": address},
            failure_log=f"Position fetch failed for {address[:10]}",
            timeout_s=POSITIONS_REQUEST_TIMEOUT_S,
            max_attempts=AUX_FETCH_MAX_ATTEMPTS,
        )
        if payload is None and not ok:
            return None
        return payload if isinstance(payload, list) else payload.get("positions", [])

    def get_market_metadata(self, condition_id: str) -> tuple[dict[str, Any], int]:
        if not condition_id:
            return {}, 0

        cache_key = str(condition_id).strip().lower()
        cache_entry = self._market_metadata_cache.get(cache_key)
        if cache_entry:
            cached_at, cached_value = cache_entry
            meta, _ = cached_value
            ttl_seconds = self._metadata_cache_ttl_s(meta)
            if (time.time() - float(cached_at)) <= ttl_seconds:
                return cached_value
            self._market_metadata_cache.pop(cache_key, None)

        payload, ok = self._request_json(
            f"{GAMMA_API}/markets",
            params={"condition_ids": condition_id},
            failure_log=f"Market metadata fetch failed for {condition_id[:12]}",
            timeout_s=METADATA_REQUEST_TIMEOUT_S,
            max_attempts=AUX_FETCH_MAX_ATTEMPTS,
        )
        if payload is None or not ok:
            return {}, 0

        fetched_at = int(time.time())
        markets = payload if isinstance(payload, list) else payload.get("markets", [])
        for market in markets:
            if str(market.get("conditionId", "")).lower() == condition_id.lower():
                return self._cache_put("_market_metadata_cache", cache_key, (market, fetched_at))
        if markets:
            return self._cache_put("_market_metadata_cache", cache_key, (markets[0], fetched_at))
        return {}, 0

    def get_orderbook_snapshot(self, token_id: str) -> tuple[dict[str, float] | None, dict[str, Any] | None, int]:
        if not token_id:
            return None, None, 0

        cache_key = str(token_id).strip()
        cached = self._cache_get("_orderbook_cache", cache_key, ORDERBOOK_CACHE_TTL_S)
        if cached is not None:
            return cached
        book, ok = self._request_json(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            failure_log=f"Orderbook fetch failed for {token_id[:12]}",
            suppress_404=True,
            timeout_s=ORDERBOOK_REQUEST_TIMEOUT_S,
            max_attempts=AUX_FETCH_MAX_ATTEMPTS,
        )
        if book is None:
            return self._cache_put("_orderbook_cache", cache_key, (None, None, 0))

        fetched_at = int(time.time())
        bids = book.get("bids", []) if isinstance(book, dict) else []
        asks = book.get("asks", []) if isinstance(book, dict) else []
        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 0.0
        if not ok:
            return self._cache_put("_orderbook_cache", cache_key, (None, None, 0))
        return self._cache_put("_orderbook_cache", cache_key, ({
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0,
            "bid_depth_usd": sum(float(b["size"]) * float(b["price"]) for b in bids[:5]),
            "ask_depth_usd": sum(float(a["size"]) * float(a["price"]) for a in asks[:5]),
        }, book, fetched_at))

    def get_price_history(self, token_id: str, interval: str = "1h") -> list[dict]:
        if not token_id:
            return []
        cache_key = (str(token_id).strip(), str(interval).strip())
        cached = self._cache_get("_price_history_cache", cache_key, PRICE_HISTORY_CACHE_TTL_S)
        if cached is not None:
            return cached
        payload, _ = self._request_json(
            f"{CLOB_API}/prices-history",
            params={"token_id": token_id, "interval": interval},
            failure_log=f"Price history fetch failed for {token_id[:12]}",
            timeout_s=PRICE_HISTORY_REQUEST_TIMEOUT_S,
            max_attempts=AUX_FETCH_MAX_ATTEMPTS,
        )
        if payload is None:
            return self._cache_put("_price_history_cache", cache_key, [])
        history = payload.get("history", []) if isinstance(payload, dict) else []
        return self._cache_put("_price_history_cache", cache_key, self._normalize_price_history(history))

    def _fetch_wallet_trades_batch(
        self,
        wallet_addresses: list[str],
        *,
        limit: int = 50,
    ) -> dict[str, list[dict]]:
        def fetch_for_wallet(wallet: str) -> list[dict]:
            try:
                return self.get_wallet_trades(wallet, limit=limit, cursor=self.wallet_cursors.get(wallet))
            except TypeError:
                # Some tests monkeypatch get_wallet_trades with the old two-arg
                # shape. Keep that compatibility while production uses cursors.
                return self.get_wallet_trades(wallet, limit=limit)

        targets = [str(address or "").strip().lower() for address in wallet_addresses if str(address or "").strip()]
        if not targets:
            return {}
        if len(targets) == 1:
            wallet = targets[0]
            return {wallet: fetch_for_wallet(wallet)}

        results: dict[str, list[dict]] = {}
        worker_count = min(WALLET_TRADE_FETCH_WORKERS, len(targets))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            future_map = {
                pool.submit(fetch_for_wallet, wallet): wallet
                for wallet in targets
            }
            for future in as_completed(future_map):
                wallet = future_map[future]
                try:
                    results[wallet] = future.result()
                except Exception as exc:
                    logger.error("Trade fetch worker failed for %s: %s", wallet[:10], exc)
                    results[wallet] = []
        return results

    def _fetch_market_metadata_batch(
        self,
        condition_ids: list[str],
    ) -> dict[str, tuple[dict[str, Any], int]]:
        normalized = sorted({str(condition_id or "").strip().lower() for condition_id in condition_ids if str(condition_id or "").strip()})
        if not normalized:
            return {}
        if len(normalized) == 1:
            condition_id = normalized[0]
            return {condition_id: self.get_market_metadata(condition_id)}

        results: dict[str, tuple[dict[str, Any], int]] = {}
        worker_count = min(ENRICHMENT_FETCH_WORKERS, len(normalized))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            future_map = {
                pool.submit(self.get_market_metadata, condition_id): condition_id
                for condition_id in normalized
            }
            for future in as_completed(future_map):
                condition_id = future_map[future]
                try:
                    results[condition_id] = future.result()
                except Exception as exc:
                    logger.error("Metadata worker failed for %s: %s", condition_id[:12], exc)
                    results[condition_id] = ({}, 0)
        return results

    def _fetch_orderbook_snapshots_batch(
        self,
        token_ids: list[str],
    ) -> dict[str, tuple[dict[str, float] | None, dict[str, Any] | None, int]]:
        normalized = sorted({str(token_id or "").strip() for token_id in token_ids if str(token_id or "").strip()})
        if not normalized:
            return {}
        if len(normalized) == 1:
            token_id = normalized[0]
            return {token_id: self.get_orderbook_snapshot(token_id)}

        results: dict[str, tuple[dict[str, float] | None, dict[str, Any] | None, int]] = {}
        worker_count = min(ENRICHMENT_FETCH_WORKERS, len(normalized))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            future_map = {
                pool.submit(self.get_orderbook_snapshot, token_id): token_id
                for token_id in normalized
            }
            for future in as_completed(future_map):
                token_id = future_map[future]
                try:
                    results[token_id] = future.result()
                except Exception as exc:
                    logger.error("Orderbook worker failed for %s: %s", token_id[:12], exc)
                    results[token_id] = (None, None, 0)
        return results

    def poll(
        self,
        wallet_addresses: list[str] | None = None,
        *,
        trade_limit: int = 50,
        watch_tier: str = "",
    ) -> list[TradeEvent]:
        self.stage_source_events(
            wallet_addresses,
            trade_limit=trade_limit,
            watch_tier=watch_tier,
        )
        return self.load_queued_events()

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

    @staticmethod
    def _is_stale_source_timestamp(source_ts: int, poll_started_at: int) -> bool:
        max_age = max_source_trade_age_seconds()
        if max_age <= 0:
            return False
        return (poll_started_at - int(source_ts or poll_started_at)) > max_age

    def _parse_raw_trade(
        self,
        raw: dict,
        address: str,
        poll_started_at: int,
        *,
        market_meta: dict[str, Any] | None = None,
        metadata_fetched_at: int = 0,
        watch_tier: str = "",
    ) -> TradeEvent | None:
        try:
            condition_id = str(raw.get("conditionId") or raw.get("condition_id") or "").strip()
            action = str(raw.get("side") or raw.get("tradeSide") or "BUY").strip().lower()
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
                allow_network=False,
            )
            shares = self._optional_float(raw.get("size"))
            if shares is None:
                shares = self._optional_float(raw.get("shares"))
            size_usd = self._optional_float(raw.get("sizeUsd"))
            if size_usd is None:
                size_usd = self._optional_float(raw.get("usdc_size"))
            price = self._parse_trade_price(raw, shares=shares, size_usd=size_usd)
            source_ts_value = raw.get("timestamp") or raw.get("createdAt") or raw.get("created_at")
            source_ts_raw = "" if source_ts_value is None else str(source_ts_value).strip()
            source_ts = self._normalize_timestamp(source_ts_value)

            if (shares is None or shares <= 0) and size_usd is not None and size_usd > 0 and price is not None:
                shares = size_usd / price
            if (size_usd is None or size_usd <= 0) and shares is not None and shares > 0 and price is not None:
                size_usd = shares * price

            if (
                not condition_id
                or not token_id
                or price is None
                or shares is None
                or shares <= 0
                or size_usd is None
                or size_usd <= 0
                or source_ts <= 0
            ):
                return None

            meta = dict(market_meta or {})
            fetched_at = int(metadata_fetched_at or 0)
            if not meta:
                meta, fetched_at = self.get_market_metadata(condition_id)
            outcome = self._resolve_outcome_name(raw, meta, token_id)
            if not outcome:
                return None
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
                timestamp=source_ts,
                close_time=close_time,
                snapshot=snapshot,
                raw_trade=dict(raw),
                raw_market_metadata=dict(meta),
                source_ts_raw=source_ts_raw,
                observed_at=observed_at,
                poll_started_at=poll_started_at,
                metadata_fetched_at=fetched_at,
                market_close_ts=self._normalize_timestamp(close_time) if close_time else 0,
                watch_tier=str(watch_tier or "").strip().lower(),
            )
        except Exception as exc:
            logger.warning("Failed to parse trade event: %s", exc)
            return None

    @staticmethod
    def _raw_trade_timestamp(raw: dict[str, Any]) -> int:
        source_ts_value = raw.get("timestamp") or raw.get("createdAt") or raw.get("created_at")
        return PolymarketTracker._normalize_timestamp(source_ts_value)

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
            return 0
        if isinstance(value, (int, float)):
            ts = int(value)
            return ts // 1000 if ts > 10_000_000_000 else ts
        text = str(value).strip()
        try:
            ts = int(float(text))
            return ts // 1000 if ts > 10_000_000_000 else ts
        except (TypeError, ValueError):
            pass
        try:
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        except Exception:
            return 0

    @staticmethod
    def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = mapping.get(key)
            if value not in {None, ""}:
                return value
        return None

    @staticmethod
    def _optional_float(
        value: Any,
        *,
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> float | None:
        if value in {None, ""}:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if min_value is not None and numeric < min_value:
            return None
        if max_value is not None and numeric > max_value:
            return None
        return numeric

    @classmethod
    def _parse_trade_price(
        cls,
        raw: dict[str, Any],
        *,
        shares: float | None,
        size_usd: float | None,
    ) -> float | None:
        for key in ("price", "outcomePrice"):
            price = cls._optional_float(raw.get(key), min_value=0.0, max_value=1.0)
            if price is not None and 0.0 < price < 1.0:
                return price

        if shares is not None and shares > 0 and size_usd is not None and size_usd > 0:
            derived = size_usd / shares
            if 0.0 < derived < 1.0:
                return derived
        return None

    @staticmethod
    def _parse_meta_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return parsed
            if "," in text:
                return [part.strip() for part in text.split(",") if part.strip()]
            return [text]
        return []

    @classmethod
    def _resolve_outcome_name(
        cls,
        raw: dict[str, Any],
        meta: dict[str, Any],
        token_id: str,
    ) -> str:
        for key in ("outcome", "outcomeName", "outcome_name"):
            direct = str(raw.get(key) or "").strip()
            if direct:
                return direct

        outcomes = [
            str(value).strip()
            for value in cls._parse_meta_list(
                cls._first_present(meta, "outcomes", "outcomeNames", "outcome_names")
            )
            if str(value).strip()
        ]

        outcome_index_raw = raw.get("outcomeIndex")
        if outcome_index_raw is None:
            outcome_index_raw = raw.get("outcome_index")
        if outcomes and outcome_index_raw not in {None, ""}:
            try:
                outcome_index = int(float(outcome_index_raw))
            except (TypeError, ValueError):
                outcome_index = -1
            if 0 <= outcome_index < len(outcomes):
                return outcomes[outcome_index]

        normalized_token_id = str(token_id or "").strip()
        if outcomes and normalized_token_id:
            token_ids = [
                str(value).strip()
                for value in cls._parse_meta_list(
                    cls._first_present(meta, "clobTokenIds", "clobTokenIDs", "tokenIds", "token_ids")
                )
                if str(value).strip()
            ]
            if len(token_ids) == len(outcomes):
                for mapped_token_id, outcome in zip(token_ids, outcomes):
                    if mapped_token_id == normalized_token_id:
                        return outcome

            tokens = meta.get("tokens")
            if isinstance(tokens, list):
                for item in tokens:
                    if not isinstance(item, dict):
                        continue
                    candidate_token = str(
                        item.get("token_id") or item.get("tokenId") or item.get("clobTokenId") or ""
                    ).strip()
                    if candidate_token != normalized_token_id:
                        continue
                    candidate_outcome = str(
                        item.get("outcome") or item.get("name") or item.get("title") or ""
                    ).strip()
                    if candidate_outcome:
                        return candidate_outcome
        return ""

    @staticmethod
    def _metadata_snapshot(meta: dict) -> dict[str, float | None]:
        if not meta:
            return {}

        best_bid = PolymarketTracker._optional_float(meta.get("bestBid"), min_value=0.0) or 0.0
        best_ask = PolymarketTracker._optional_float(meta.get("bestAsk"), min_value=0.0) or 0.0
        last_trade = PolymarketTracker._optional_float(meta.get("lastTradePrice"), min_value=0.0) or 0.0
        mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else last_trade

        volume_24h = PolymarketTracker._optional_float(
            PolymarketTracker._first_present(meta, "volume24hr", "volume24h", "oneDayVolume"),
            min_value=0.0,
        )
        volume_7d = PolymarketTracker._optional_float(
            PolymarketTracker._first_present(meta, "volume7d", "sevenDayVolume", "oneWeekVolume"),
            min_value=0.0,
        )
        oi_usd = PolymarketTracker._optional_float(
            PolymarketTracker._first_present(meta, "openInterest", "liquidity"),
            min_value=0.0,
        )

        raw_top_holder = PolymarketTracker._first_present(meta, "topHolderPct", "top_holder_pct")
        top_holder_pct = PolymarketTracker._optional_float(raw_top_holder, min_value=0.0, max_value=1.0)

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid if mid > 0 else 0.0,
            "volume_24h_usd": volume_24h,
            "volume_7d_avg_usd": (volume_7d / 7) if volume_7d is not None and volume_7d > 0 else None,
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
            if ts <= 0:
                continue
            normalized.append({"p": price, "t": float(ts)})
        normalized.sort(key=lambda item: item.get("t", 0.0))
        return normalized
