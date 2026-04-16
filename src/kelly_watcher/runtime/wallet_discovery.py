from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from typing import Any

import httpx

from kelly_watcher.config import (
    wallet_discovery_analyze_limit,
    wallet_discovery_candidate_limit,
    wallet_discovery_leaderboard_pages,
    wallet_discovery_leaderboard_per_page,
)
from kelly_watcher.data.db import (
    DB_PATH,
    database_integrity_state,
    get_conn,
    init_db,
    load_managed_wallets,
)
from kelly_watcher.tools.rank_copytrade_wallets import (
    REQUEST_TIMEOUT_SECONDS,
    LeaderboardEntry,
    RankedWallet,
    build_ranked_wallet,
    compute_performance_metrics,
    compute_trade_timing_metrics,
    fetch_closed_positions,
    fetch_leaderboard,
    fetch_recent_trades,
    load_local_copy_metrics,
)

logger = logging.getLogger(__name__)

_DISCOVERY_SOURCES: tuple[tuple[str, str, str, str], ...] = (
    ("week-pnl", "OVERALL", "WEEK", "PNL"),
    ("week-vol", "OVERALL", "WEEK", "VOL"),
    ("month-pnl", "OVERALL", "MONTH", "PNL"),
    ("month-vol", "OVERALL", "MONTH", "VOL"),
)
_DISCOVERY_SCAN_LOCK = threading.Lock()

_TRADE_LIMIT = 40
_CLOSED_PAGE_LIMIT = 50
_CLOSED_PAGES = 4
_ACTIVITY_WINDOW_DAYS = 3
_LATE_BUY_THRESHOLD_SECONDS = 20 * 60
_BUY_SAMPLE_LIMIT = 12
_MIN_CLOSED_POSITIONS = 15
_MIN_RECENT_TRADES = 10
_MIN_RECENT_BUYS = 4
_MIN_LEAD_SAMPLES = 2
_MIN_MEDIAN_LEAD_SECONDS = 60 * 60
_MAX_MEDIAN_LEAD_SECONDS = 6 * 60 * 60
_MIN_P25_LEAD_SECONDS = 20 * 60
_MAX_LATE_BUY_RATIO = 0.20
_MAX_DAYS_SINCE_LAST_TRADE = 2
_LARGE_BUY_THRESHOLD_USD = 200.0
_MIN_LARGE_BUY_COUNT = 3
_HIGH_CONVICTION_PRICE = 0.75
_MIN_CONVICTION_BUY_RATIO = 0.30
_MIN_AVG_BUY_SIZE_USD = 125.0
_MIN_LOCAL_RESOLVED_COPIES = 3
_MIN_LOCAL_COPY_AVG_RETURN = 0.0


def _normalize_wallets(wallet_addresses: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in wallet_addresses:
        wallet = str(value or "").strip().lower()
        if not wallet or wallet in seen:
            continue
        seen.add(wallet)
        normalized.append(wallet)
    return normalized


def _merge_leaderboard_entry(existing: LeaderboardEntry, incoming: LeaderboardEntry) -> LeaderboardEntry:
    username = existing.username
    if username in {"", "-"} and incoming.username not in {"", "-"}:
        username = incoming.username

    rank_candidates = [rank for rank in (existing.rank, incoming.rank) if rank is not None and rank > 0]
    return LeaderboardEntry(
        address=existing.address,
        username=username,
        rank=min(rank_candidates) if rank_candidates else None,
        pnl_usd=max(existing.pnl_usd, incoming.pnl_usd),
        volume_usd=max(existing.volume_usd, incoming.volume_usd),
        verified=existing.verified or incoming.verified,
    )


def _dropped_wallets() -> list[str]:
    wallets: list[str] = []
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT wallet_address
            FROM wallet_watch_state
            WHERE LOWER(COALESCE(status, ''))='dropped'
            """
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        wallet = str(row["wallet_address"] or "").strip().lower()
        if wallet:
            wallets.append(wallet)
    return wallets


def _excluded_wallets(wallet_addresses: list[str] | None) -> set[str]:
    excluded = set(_normalize_wallets(wallet_addresses or []))
    try:
        excluded.update(load_managed_wallets(include_disabled=True))
    except Exception as exc:
        raise RuntimeError(f"failed to load managed wallets: {exc}") from exc
    try:
        excluded.update(_dropped_wallets())
    except Exception as exc:
        raise RuntimeError(f"failed to load dropped wallets: {exc}") from exc
    return excluded


def _candidate_entries(
    *,
    client: httpx.Client,
    excluded_wallets: set[str],
    pages: int,
    per_page: int,
    analyze_limit: int,
) -> tuple[list[LeaderboardEntry], dict[str, tuple[str, ...]]]:
    gather_limit = max(analyze_limit * 3, analyze_limit)
    ordered_wallets: list[str] = []
    entries_by_wallet: dict[str, LeaderboardEntry] = {}
    source_labels: dict[str, list[str]] = {}

    for label, category, time_period, order_by in _DISCOVERY_SOURCES:
        try:
            rows = fetch_leaderboard(
                client,
                category=category,
                time_period=time_period,
                order_by=order_by,
                per_page=per_page,
                pages=pages,
            )
        except Exception as exc:
            logger.warning(
                "Wallet discovery source %s failed: %s",
                label,
                exc,
            )
            continue

        for entry in rows:
            wallet = str(entry.address or "").strip().lower()
            if not wallet or wallet in excluded_wallets:
                continue

            labels = source_labels.setdefault(wallet, [])
            if label not in labels:
                labels.append(label)

            existing = entries_by_wallet.get(wallet)
            if existing is None:
                entries_by_wallet[wallet] = entry
                ordered_wallets.append(wallet)
            else:
                entries_by_wallet[wallet] = _merge_leaderboard_entry(existing, entry)

            if len(ordered_wallets) >= gather_limit:
                break
        if len(ordered_wallets) >= gather_limit:
            break

    selected_wallets = ordered_wallets[:analyze_limit]
    return (
        [entries_by_wallet[wallet] for wallet in selected_wallets],
        {wallet: tuple(source_labels.get(wallet, ())) for wallet in selected_wallets},
    )


def _analysis_failed_wallet(entry: LeaderboardEntry, reason: str) -> RankedWallet:
    return RankedWallet(
        address=entry.address,
        username=entry.username,
        style="error",
        follow_score=0.0,
        accepted=False,
        reject_reason=reason,
        leaderboard_rank=entry.rank,
        leaderboard_pnl_usd=entry.pnl_usd,
        leaderboard_volume_usd=entry.volume_usd,
        closed_positions=0,
        win_rate=0.0,
        roi=0.0,
        realized_pnl_usd=0.0,
        recent_trades=0,
        recent_buys=0,
        avg_recent_buy_size_usd=None,
        large_buy_ratio=None,
        conviction_buy_ratio=None,
        copyability_score=0.0,
        last_trade_age_hours=None,
        median_buy_lead_hours=None,
        p25_buy_lead_hours=None,
        late_buy_ratio=None,
        local_resolved_copied=0,
        local_copy_avg_return=None,
        local_copy_pnl_usd=0.0,
    )


def _analyze_wallet_entry(
    entry: LeaderboardEntry,
    *,
    client: httpx.Client,
    market_close_cache: dict[str, int],
    local_copy_metrics_map: dict[str, Any],
) -> RankedWallet:
    now_ts = int(time.time())
    trades = fetch_recent_trades(client, entry.address, limit=_TRADE_LIMIT)
    closed_positions = fetch_closed_positions(
        client,
        entry.address,
        page_limit=_CLOSED_PAGE_LIMIT,
        max_pages=_CLOSED_PAGES,
    )
    performance = compute_performance_metrics(closed_positions, now_ts=now_ts)
    timing = compute_trade_timing_metrics(
        client,
        trades,
        now_ts=now_ts,
        activity_window_days=_ACTIVITY_WINDOW_DAYS,
        late_buy_threshold_seconds=_LATE_BUY_THRESHOLD_SECONDS,
        large_buy_threshold_usd=_LARGE_BUY_THRESHOLD_USD,
        high_conviction_price=_HIGH_CONVICTION_PRICE,
        buy_sample_limit=_BUY_SAMPLE_LIMIT,
        market_close_cache=market_close_cache,
    )
    return build_ranked_wallet(
        entry,
        performance,
        timing,
        now_ts=now_ts,
        activity_window_days=_ACTIVITY_WINDOW_DAYS,
        min_closed_positions=_MIN_CLOSED_POSITIONS,
        min_recent_trades=_MIN_RECENT_TRADES,
        min_recent_buys=_MIN_RECENT_BUYS,
        min_lead_samples=_MIN_LEAD_SAMPLES,
        min_median_lead_seconds=_MIN_MEDIAN_LEAD_SECONDS,
        max_median_lead_seconds=_MAX_MEDIAN_LEAD_SECONDS,
        min_p25_lead_seconds=_MIN_P25_LEAD_SECONDS,
        max_late_buy_ratio=_MAX_LATE_BUY_RATIO,
        max_days_since_last_trade=_MAX_DAYS_SINCE_LAST_TRADE,
        min_avg_buy_size_usd=_MIN_AVG_BUY_SIZE_USD,
        min_large_buy_count=_MIN_LARGE_BUY_COUNT,
        min_conviction_buy_ratio=_MIN_CONVICTION_BUY_RATIO,
        large_buy_threshold_usd=_LARGE_BUY_THRESHOLD_USD,
        local_copy_metrics=local_copy_metrics_map.get(entry.address.lower()),
        min_local_resolved_copies=_MIN_LOCAL_RESOLVED_COPIES,
        min_local_copy_avg_return=_MIN_LOCAL_COPY_AVG_RETURN,
    )


def _persist_wallet_discovery_candidates(
    rows: list[RankedWallet],
    *,
    source_labels_map: dict[str, tuple[str, ...]],
    updated_at: int,
    replace_existing: bool,
) -> int:
    conn = get_conn()
    try:
        with conn:
            if replace_existing:
                wallets = [row.address for row in rows]
                if wallets:
                    placeholders = ",".join("?" for _ in wallets)
                    conn.execute(
                        f"DELETE FROM wallet_discovery_candidates WHERE wallet_address NOT IN ({placeholders})",
                        tuple(wallets),
                    )
                else:
                    conn.execute("DELETE FROM wallet_discovery_candidates")

            conn.executemany(
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
                ON CONFLICT(wallet_address) DO UPDATE SET
                    username=excluded.username,
                    source_labels_json=excluded.source_labels_json,
                    follow_score=excluded.follow_score,
                    accepted=excluded.accepted,
                    reject_reason=excluded.reject_reason,
                    watch_style=excluded.watch_style,
                    leaderboard_rank=excluded.leaderboard_rank,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                [
                    (
                        row.address,
                        row.username,
                        json.dumps(list(source_labels_map.get(row.address.lower(), ())), separators=(",", ":")),
                        float(row.follow_score),
                        1 if row.accepted else 0,
                        row.reject_reason,
                        row.style,
                        row.leaderboard_rank,
                        updated_at,
                        json.dumps(asdict(row), separators=(",", ":"), sort_keys=True),
                    )
                    for row in rows
                ],
            )
    finally:
        conn.close()
    return len(rows)


def _auto_promote_ready_wallets(
    rows: list[RankedWallet],
    *,
    source_labels_map: dict[str, tuple[str, ...]],
    started_at: int,
    finished_at: int,
) -> int:
    ready_rows = [row for row in rows if row.accepted and str(row.address or "").strip()]
    if not ready_rows:
        return 0

    wallets = _normalize_wallets([row.address for row in ready_rows])
    if not wallets:
        return 0

    promotion_payload = {
        "promotion_source": "wallet_discovery",
        "promotion_reason": "ready wallet discovered in shadow scan",
        "scan_started_at": started_at,
        "scan_finished_at": finished_at,
        "scan_wallet_count": len(rows),
        "ready_wallet_count": len(ready_rows),
        "wallets": [
            {
                "wallet_address": row.address,
                "username": row.username,
                "follow_score": float(row.follow_score),
                "leaderboard_rank": row.leaderboard_rank,
                "source_labels": list(source_labels_map.get(row.address.lower(), ())),
                "style": row.style,
                "accepted": bool(row.accepted),
            }
            for row in ready_rows
        ],
    }
    now_ts = finished_at
    metadata_json = json.dumps(promotion_payload, separators=(",", ":"), sort_keys=True)
    event_rows = [
        (
            wallet,
            "promote",
            "auto_promoted",
            "ready wallet discovered in shadow scan",
            metadata_json,
            now_ts,
        )
        for wallet in wallets
    ]

    conn = get_conn()
    try:
        with conn:
            conn.executemany(
                """
                INSERT INTO managed_wallets (
                    wallet_address,
                    tracking_enabled,
                    source,
                    added_at,
                    updated_at,
                    disabled_at,
                    disabled_reason,
                    metadata_json
                ) VALUES (?, 1, ?, ?, ?, NULL, NULL, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                    tracking_enabled=1,
                    source=excluded.source,
                    updated_at=excluded.updated_at,
                    disabled_at=NULL,
                    disabled_reason=NULL,
                    metadata_json=excluded.metadata_json
                """,
                [
                    (
                        wallet,
                        "auto_promoted",
                        now_ts,
                        now_ts,
                        metadata_json,
                    )
                    for wallet in wallets
                ],
            )
            conn.executemany(
                """
                INSERT INTO wallet_membership_events (
                    wallet_address,
                    action,
                    source,
                    reason,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                event_rows,
            )
            conn.executemany(
                """
                INSERT INTO wallet_watch_state (
                    wallet_address,
                    status,
                    status_reason,
                    dropped_at,
                    reactivated_at,
                    tracking_started_at,
                    last_source_ts_at_status,
                    updated_at
                ) VALUES (?, 'active', NULL, NULL, ?, ?, ?, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                    status='active',
                    status_reason=NULL,
                    dropped_at=NULL,
                    reactivated_at=excluded.reactivated_at,
                    tracking_started_at=CASE
                        WHEN COALESCE(wallet_watch_state.tracking_started_at, 0)=0 THEN excluded.tracking_started_at
                        ELSE wallet_watch_state.tracking_started_at
                    END,
                    last_source_ts_at_status=excluded.last_source_ts_at_status,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        wallet,
                        now_ts,
                        now_ts,
                        now_ts,
                        now_ts,
                    )
                    for wallet in wallets
                ],
            )
        return len(wallets)
    finally:
        conn.close()


def refresh_wallet_discovery_candidates(wallet_addresses: list[str] | None = None) -> dict[str, Any]:
    started_at = int(time.time())
    if not _DISCOVERY_SCAN_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "started_at": started_at,
            "finished_at": started_at,
            "scanned_count": 0,
            "accepted_count": 0,
            "stored_count": 0,
            "message": "wallet discovery scan already running",
        }

    try:
        integrity = database_integrity_state()
        if bool(integrity.get("db_integrity_known")) and not bool(integrity.get("db_integrity_ok")):
            detail = str(integrity.get("db_integrity_message") or "").splitlines()[0].strip()
            suffix = f": {detail}" if detail else ""
            return {
                "ok": False,
                "started_at": started_at,
                "finished_at": started_at,
                "scanned_count": 0,
                "accepted_count": 0,
                "promoted_count": 0,
                "stored_count": 0,
                "message": f"wallet discovery scan is unavailable because SQLite integrity check failed{suffix}.",
            }

        init_db()
        excluded = _excluded_wallets(wallet_addresses)
        local_copy_metrics_map = load_local_copy_metrics(str(DB_PATH))
        analyze_limit = wallet_discovery_analyze_limit()
        candidate_limit = wallet_discovery_candidate_limit()
        pages = wallet_discovery_leaderboard_pages()
        per_page = wallet_discovery_leaderboard_per_page()

        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
            entries, source_labels_map = _candidate_entries(
                client=client,
                excluded_wallets=excluded,
                pages=pages,
                per_page=per_page,
                analyze_limit=analyze_limit,
            )
            market_close_cache: dict[str, int] = {}
            ranked_rows: list[RankedWallet] = []
            for entry in entries:
                try:
                    ranked_rows.append(
                        _analyze_wallet_entry(
                            entry,
                            client=client,
                            market_close_cache=market_close_cache,
                            local_copy_metrics_map=local_copy_metrics_map,
                        )
                    )
                except Exception as exc:
                    ranked_rows.append(
                        _analysis_failed_wallet(entry, f"analysis_failed: {exc}")
                    )

        accepted = sorted(
            [row for row in ranked_rows if row.accepted],
            key=lambda row: row.follow_score,
            reverse=True,
        )
        stored_rows = sorted(
            ranked_rows,
            key=lambda row: (0 if row.accepted else 1, -row.follow_score, row.address),
        )[:candidate_limit]
        finished_at = int(time.time())
        replace_existing = len(ranked_rows) > 0
        stored_count = _persist_wallet_discovery_candidates(
            stored_rows,
            source_labels_map=source_labels_map,
            updated_at=finished_at,
            replace_existing=replace_existing,
        )
        promoted_count = 0
        if accepted:
            promoted_count = _auto_promote_ready_wallets(
                accepted,
                source_labels_map=source_labels_map,
                started_at=started_at,
                finished_at=finished_at,
            )
        return {
            "ok": True,
            "started_at": started_at,
            "finished_at": finished_at,
            "scanned_count": len(ranked_rows),
            "accepted_count": len(accepted),
            "promoted_count": promoted_count,
            "stored_count": stored_count,
            "message": (
                f"stored {stored_count} discovery candidate(s) ({len(accepted)} fully accepted, {promoted_count} promoted) "
                f"from {len(ranked_rows)} analyzed wallets"
                if ranked_rows
                else "wallet discovery scan found no candidate wallets to analyze"
            ),
        }
    except Exception as exc:
        logger.exception("Wallet discovery scan failed")
        finished_at = int(time.time())
        return {
            "ok": False,
            "started_at": started_at,
            "finished_at": finished_at,
            "scanned_count": 0,
            "accepted_count": 0,
            "promoted_count": 0,
            "stored_count": 0,
            "message": f"wallet discovery scan failed: {exc}",
        }
    finally:
        _DISCOVERY_SCAN_LOCK.release()


def load_wallet_discovery_candidates(*, limit: int | None = None) -> list[dict[str, Any]]:
    integrity = database_integrity_state()
    if bool(integrity.get("db_integrity_known")) and not bool(integrity.get("db_integrity_ok")):
        detail = str(integrity.get("db_integrity_message") or "").splitlines()[0].strip()
        logger.warning("Skipping discovery candidate load because SQLite integrity check failed: %s", detail)
        return []

    init_db()
    rows_limit = int(limit) if limit is not None else wallet_discovery_candidate_limit()
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                wallet_address,
                source_labels_json,
                updated_at,
                payload_json
            FROM wallet_discovery_candidates
            ORDER BY accepted DESC, follow_score DESC, updated_at DESC, wallet_address ASC
            LIMIT ?
            """,
            (max(rows_limit, 1),),
        ).fetchall()
    finally:
        conn.close()

    payloads: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        try:
            source_labels = json.loads(str(row["source_labels_json"] or "[]"))
        except json.JSONDecodeError:
            source_labels = []
        payloads.append(
            {
                **payload,
                "wallet_address": str(row["wallet_address"] or "").strip().lower(),
                "source_labels": [
                    str(label).strip()
                    for label in (source_labels if isinstance(source_labels, list) else [])
                    if str(label).strip()
                ],
                "updated_at": int(row["updated_at"] or 0),
            }
        )
    return payloads
