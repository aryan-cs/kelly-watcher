from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from config import (
    discovery_poll_interval_multiplier,
    hot_wallet_count,
    wallet_inactivity_limit_seconds,
    wallet_slow_drop_max_tracking_age_seconds,
    wallet_performance_drop_max_avg_return,
    wallet_performance_drop_max_win_rate,
    wallet_performance_drop_min_trades,
    warm_poll_interval_multiplier,
    warm_wallet_count,
)
from db import get_conn


@dataclass(frozen=True)
class RankedWallet:
    wallet: str
    follow_score: float
    last_source_ts: int
    cache_updated_at: int


@dataclass(frozen=True)
class WatchTierSnapshot:
    hot: tuple[str, ...]
    warm: tuple[str, ...]
    discovery: tuple[str, ...]
    dropped: tuple[str, ...]
    ranked: tuple[RankedWallet, ...]
    refreshed_at: int


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize_wallets(wallet_addresses: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for wallet in wallet_addresses:
        address = str(wallet or "").strip().lower()
        if not address or address in seen:
            continue
        seen.add(address)
        normalized.append(address)
    return normalized


def _format_duration_label(seconds: float) -> str:
    if not math.isfinite(seconds):
        return "unlimited"
    rounded = int(max(seconds, 0))
    if rounded % 604800 == 0 and rounded >= 604800:
        return f"{rounded // 604800}w"
    if rounded % 86400 == 0 and rounded >= 86400:
        return f"{rounded // 86400}d"
    if rounded % 3600 == 0 and rounded >= 3600:
        return f"{rounded // 3600}h"
    if rounded % 60 == 0 and rounded >= 60:
        return f"{rounded // 60}m"
    return f"{rounded}s"


def _score_wallet(
    *,
    win_rate: float,
    n_trades: int,
    avg_return: float,
    realized_pnl_usd: float,
    volume_usd: float,
    avg_size_usd: float,
    open_positions: int,
    last_source_ts: int,
    cache_updated_at: int,
    now_ts: int,
) -> float:
    shrunk_win_rate = ((max(n_trades, 0) * win_rate) + (20 * 0.5)) / (max(n_trades, 0) + 20)
    win_score = _clip((shrunk_win_rate - 0.45) / 0.25)
    sample_score = _clip(math.log1p(max(n_trades, 0)) / math.log1p(80))
    return_score = _clip((avg_return + 0.05) / 0.20)
    pnl_score = _clip(math.log1p(max(realized_pnl_usd, 0.0)) / math.log1p(5_000.0))
    volume_score = _clip(math.log1p(max(volume_usd, 0.0)) / math.log1p(50_000.0))
    size_score = _clip(math.log1p(max(avg_size_usd, 0.0)) / math.log1p(250.0))

    if last_source_ts > 0:
        activity_age_hours = max(now_ts - last_source_ts, 0) / 3600.0
        activity_score = _clip(1.0 - (activity_age_hours / 72.0))
    elif cache_updated_at > 0:
        cache_age_hours = max(now_ts - cache_updated_at, 0) / 3600.0
        activity_score = 0.35 if cache_age_hours <= 24 else 0.15 if cache_age_hours <= 72 else 0.0
    else:
        activity_score = 0.0

    open_score = _clip(open_positions / 3.0)
    freshness_penalty = 0.0
    if cache_updated_at > 0 and (now_ts - cache_updated_at) > 86400:
        freshness_penalty = 0.10

    quality_score = (
        0.35 * win_score
        + 0.17 * return_score
        + 0.18 * sample_score
        + 0.10 * pnl_score
        + 0.10 * volume_score
        + 0.10 * size_score
    )
    composite = (
        0.70 * quality_score
        + 0.25 * activity_score
        + 0.05 * open_score
    ) - freshness_penalty
    return round(_clip(composite), 4)


def _wallet_status_rows(wallet_addresses: list[str]) -> dict[str, dict[str, int | str | None]]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return {}

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                wallet_address,
                status,
                status_reason,
                dropped_at,
                reactivated_at,
                tracking_started_at,
                last_source_ts_at_status,
                updated_at
            FROM wallet_watch_state
            WHERE wallet_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    return {
        str(row["wallet_address"] or "").strip().lower(): {
            "status": str(row["status"] or "active").strip().lower(),
            "status_reason": row["status_reason"],
            "dropped_at": int(row["dropped_at"] or 0),
            "reactivated_at": int(row["reactivated_at"] or 0),
            "tracking_started_at": int(row["tracking_started_at"] or 0),
            "last_source_ts_at_status": int(row["last_source_ts_at_status"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }
        for row in rows
    }


def _wallet_cursor_map(wallet_addresses: list[str]) -> dict[str, int]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return {}

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT wallet_address, last_source_ts
            FROM wallet_cursors
            WHERE wallet_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    return {
        str(row["wallet_address"] or "").strip().lower(): int(row["last_source_ts"] or 0)
        for row in rows
    }


def _drop_wallets(wallet_updates: list[tuple[str, str, int, int]]) -> None:
    if not wallet_updates:
        return

    now_ts = int(time.time())
    conn = get_conn()
    try:
        conn.executemany(
            """
            INSERT INTO wallet_watch_state (
                wallet_address,
                status,
                status_reason,
                dropped_at,
                last_source_ts_at_status,
                updated_at
            ) VALUES (?, 'dropped', ?, ?, ?, ?)
            ON CONFLICT(wallet_address) DO UPDATE SET
                status='dropped',
                status_reason=excluded.status_reason,
                dropped_at=excluded.dropped_at,
                last_source_ts_at_status=excluded.last_source_ts_at_status,
                updated_at=excluded.updated_at
            """,
            [
                (wallet, reason, dropped_at, last_source_ts, now_ts)
                for wallet, reason, dropped_at, last_source_ts in wallet_updates
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_tracking_started(wallet_addresses: list[str]) -> None:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return

    now_ts = int(time.time())
    placeholders = ",".join("?" for _ in wallets)
    cursor_map = _wallet_cursor_map(wallets)
    conn = get_conn()
    try:
        existing_rows = conn.execute(
            f"""
            SELECT
                wallet_address,
                tracking_started_at,
                reactivated_at,
                updated_at
            FROM wallet_watch_state
            WHERE wallet_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
        existing = {
            str(row["wallet_address"] or "").strip().lower(): row
            for row in existing_rows
        }

        to_insert = [
            (
                wallet,
                int(cursor_map.get(wallet, 0)) or now_ts,
                now_ts,
            )
            for wallet in wallets
            if wallet not in existing
        ]
        to_update: list[tuple[int, int, str]] = []
        for wallet in wallets:
            row = existing.get(wallet)
            if row is None:
                continue
            tracking_started_at = int(row["tracking_started_at"] or 0)
            if tracking_started_at > 0:
                continue
            anchor = int(cursor_map.get(wallet, 0)) or int(row["reactivated_at"] or 0) or int(row["updated_at"] or 0) or now_ts
            updated_at = int(row["updated_at"] or 0) or anchor
            to_update.append((anchor, updated_at, wallet))

        if to_insert:
            conn.executemany(
                """
                INSERT INTO wallet_watch_state (
                    wallet_address,
                    status,
                    tracking_started_at,
                    updated_at
                ) VALUES (?, 'active', ?, ?)
                ON CONFLICT(wallet_address) DO NOTHING
                """,
                to_insert,
            )
        if to_update:
            conn.executemany(
                """
                UPDATE wallet_watch_state
                SET tracking_started_at=?,
                    updated_at=?
                WHERE wallet_address=?
                  AND COALESCE(tracking_started_at, 0)=0
                """,
                to_update,
            )
        if to_insert or to_update:
            conn.commit()
    finally:
        conn.close()


def _auto_drop_underperforming_wallets(wallet_addresses: list[str]) -> None:
    minimum_trades = wallet_performance_drop_min_trades()
    if minimum_trades <= 0:
        return

    max_win_rate = wallet_performance_drop_max_win_rate()
    max_avg_return = wallet_performance_drop_max_avg_return()
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return

    status_rows = _wallet_status_rows(wallets)
    cursor_map = _wallet_cursor_map(wallets)
    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        trader_rows = conn.execute(
            f"""
            SELECT trader_address, win_rate, n_trades, avg_return
            FROM trader_cache
            WHERE trader_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    trader_map = {
        str(row["trader_address"] or "").strip().lower(): row
        for row in trader_rows
    }
    now_ts = int(time.time())
    to_drop: list[tuple[str, str, int, int]] = []

    for wallet in wallets:
        status_row = status_rows.get(wallet, {})
        if status_row.get("status") == "dropped":
            continue

        row = trader_map.get(wallet)
        if row is None:
            continue

        n_trades = int(row["n_trades"] or 0)
        win_rate = float(row["win_rate"] or 0.0)
        avg_return = float(row["avg_return"] or 0.0)
        if n_trades < minimum_trades or win_rate > max_win_rate or avg_return > max_avg_return:
            continue

        reactivated_at = int(status_row.get("reactivated_at") or 0)
        last_source_ts = int(cursor_map.get(wallet, 0))
        if reactivated_at > 0 and last_source_ts <= reactivated_at:
            continue

        reason = (
            f"poor_perf {n_trades}t {win_rate * 100.0:.1f}%wr {avg_return * 100.0:.1f}%ret"
        )
        to_drop.append((wallet, reason, now_ts, last_source_ts))

    _drop_wallets(to_drop)


def reactivate_wallet(wallet_address: str) -> bool:
    wallet = str(wallet_address or "").strip().lower()
    if not wallet:
        return False

    now_ts = int(time.time())
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO wallet_watch_state (
                wallet_address,
                status,
                status_reason,
                dropped_at,
                reactivated_at,
                tracking_started_at,
                updated_at
            ) VALUES (?, 'active', NULL, NULL, ?, ?, ?)
            ON CONFLICT(wallet_address) DO UPDATE SET
                status='active',
                status_reason=NULL,
                dropped_at=NULL,
                reactivated_at=excluded.reactivated_at,
                tracking_started_at=excluded.tracking_started_at,
                updated_at=excluded.updated_at
            """,
            (wallet, now_ts, now_ts, now_ts),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _auto_drop_inactive_wallets(wallet_addresses: list[str]) -> None:
    inactivity_limit = wallet_inactivity_limit_seconds()
    if not math.isfinite(inactivity_limit):
        return

    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return

    status_rows = _wallet_status_rows(wallets)
    cursor_map = _wallet_cursor_map(wallets)
    now_ts = int(time.time())
    reason = f"inactive>{_format_duration_label(inactivity_limit)}"
    to_drop: list[tuple[str, str, int, int]] = []

    for wallet in wallets:
        status_row = status_rows.get(wallet, {})
        if status_row.get("status") == "dropped":
            continue

        last_source_ts = int(cursor_map.get(wallet, 0))
        reactivated_at = int(status_row.get("reactivated_at") or 0)
        tracking_started_at = int(status_row.get("tracking_started_at") or 0)
        inactivity_anchor = max(last_source_ts, reactivated_at, tracking_started_at)
        if inactivity_anchor <= 0:
            continue
        if (now_ts - inactivity_anchor) < inactivity_limit:
            continue
        to_drop.append((wallet, reason, now_ts, last_source_ts))

    _drop_wallets(to_drop)


def _slow_wallet_drop_updates(
    discovery_wallets: tuple[str, ...],
    status_rows: dict[str, dict[str, int | str | None]],
) -> list[tuple[str, str, int, int]]:
    max_tracking_age = wallet_slow_drop_max_tracking_age_seconds()
    if not math.isfinite(max_tracking_age) or not discovery_wallets:
        return []

    cursor_map = _wallet_cursor_map(list(discovery_wallets))
    now_ts = int(time.time())
    reason = f"slow>{_format_duration_label(max_tracking_age)}"
    to_drop: list[tuple[str, str, int, int]] = []

    for wallet in discovery_wallets:
        status_row = status_rows.get(wallet, {})
        if status_row.get("status") == "dropped":
            continue
        tracking_started_at = int(status_row.get("tracking_started_at") or 0)
        if tracking_started_at <= 0:
            continue
        if (now_ts - tracking_started_at) < max_tracking_age:
            continue
        to_drop.append((wallet, reason, now_ts, int(cursor_map.get(wallet, 0))))

    return to_drop


def _load_watch_metrics(wallet_addresses: list[str]) -> list[RankedWallet]:
    wallets = _normalize_wallets(wallet_addresses)
    if not wallets:
        return []

    placeholders = ",".join("?" for _ in wallets)
    conn = get_conn()
    try:
        trader_rows = conn.execute(
            f"""
            SELECT
                trader_address,
                win_rate,
                n_trades,
                avg_return,
                realized_pnl_usd,
                volume_usd,
                avg_size_usd,
                open_positions,
                updated_at
            FROM trader_cache
            WHERE trader_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
        cursor_rows = conn.execute(
            f"""
            SELECT wallet_address, last_source_ts
            FROM wallet_cursors
            WHERE wallet_address IN ({placeholders})
            """,
            tuple(wallets),
        ).fetchall()
    finally:
        conn.close()

    trader_map = {
        str(row["trader_address"] or "").strip().lower(): row
        for row in trader_rows
    }
    cursor_map = {
        str(row["wallet_address"] or "").strip().lower(): int(row["last_source_ts"] or 0)
        for row in cursor_rows
    }
    now_ts = int(time.time())
    ranked: list[RankedWallet] = []
    order_index = {wallet: index for index, wallet in enumerate(wallets)}

    for wallet in wallets:
        row = trader_map.get(wallet)
        win_rate = float(row["win_rate"] or 0.5) if row else 0.5
        n_trades = int(row["n_trades"] or 0) if row else 0
        avg_return = float(row["avg_return"] or 0.0) if row else 0.0
        realized_pnl_usd = float(row["realized_pnl_usd"] or 0.0) if row else 0.0
        volume_usd = float(row["volume_usd"] or 0.0) if row else 0.0
        avg_size_usd = float(row["avg_size_usd"] or 0.0) if row else 0.0
        open_positions = int(row["open_positions"] or 0) if row else 0
        cache_updated_at = int(row["updated_at"] or 0) if row else 0
        last_source_ts = int(cursor_map.get(wallet, 0))
        score = _score_wallet(
            win_rate=win_rate,
            n_trades=n_trades,
            avg_return=avg_return,
            realized_pnl_usd=realized_pnl_usd,
            volume_usd=volume_usd,
            avg_size_usd=avg_size_usd,
            open_positions=open_positions,
            last_source_ts=last_source_ts,
            cache_updated_at=cache_updated_at,
            now_ts=now_ts,
        )
        ranked.append(
            RankedWallet(
                wallet=wallet,
                follow_score=score,
                last_source_ts=last_source_ts,
                cache_updated_at=cache_updated_at,
            )
        )

    ranked.sort(
        key=lambda row: (
            -row.follow_score,
            -row.last_source_ts,
            -row.cache_updated_at,
            order_index[row.wallet],
        )
    )
    return ranked


class WatchlistManager:
    def __init__(self, wallet_addresses: list[str]):
        self.wallets = _normalize_wallets(wallet_addresses)
        self._lock = threading.Lock()
        self._loop_count = 0
        self._snapshot = self._build_snapshot()

    def _build_snapshot(self) -> WatchTierSnapshot:
        _ensure_tracking_started(self.wallets)
        _auto_drop_inactive_wallets(self.wallets)
        _auto_drop_underperforming_wallets(self.wallets)
        status_rows = _wallet_status_rows(self.wallets)
        dropped_wallets = tuple(
            wallet for wallet in self.wallets if status_rows.get(wallet, {}).get("status") == "dropped"
        )
        active_wallets = [wallet for wallet in self.wallets if wallet not in dropped_wallets]
        ranked = _load_watch_metrics(active_wallets)
        hot_count = min(len(ranked), hot_wallet_count())
        remaining = max(len(ranked) - hot_count, 0)
        warm_count = min(remaining, warm_wallet_count())
        hot = tuple(row.wallet for row in ranked[:hot_count])
        warm = tuple(row.wallet for row in ranked[hot_count:hot_count + warm_count])
        discovery = tuple(row.wallet for row in ranked[hot_count + warm_count:])

        slow_drop_updates = _slow_wallet_drop_updates(discovery, status_rows)
        if slow_drop_updates:
            _drop_wallets(slow_drop_updates)
            status_rows = _wallet_status_rows(self.wallets)
            dropped_wallets = tuple(
                wallet for wallet in self.wallets if status_rows.get(wallet, {}).get("status") == "dropped"
            )
            active_wallets = [wallet for wallet in self.wallets if wallet not in dropped_wallets]
            ranked = _load_watch_metrics(active_wallets)
            hot_count = min(len(ranked), hot_wallet_count())
            remaining = max(len(ranked) - hot_count, 0)
            warm_count = min(remaining, warm_wallet_count())
            hot = tuple(row.wallet for row in ranked[:hot_count])
            warm = tuple(row.wallet for row in ranked[hot_count:hot_count + warm_count])
            discovery = tuple(row.wallet for row in ranked[hot_count + warm_count:])

        return WatchTierSnapshot(
            hot=hot,
            warm=warm,
            discovery=discovery,
            dropped=dropped_wallets,
            ranked=tuple(ranked),
            refreshed_at=int(time.time()),
        )

    def refresh(self) -> WatchTierSnapshot:
        snapshot = self._build_snapshot()
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def startup_wallets(self) -> list[str]:
        with self._lock:
            snapshot = self._snapshot
            wallets = list(snapshot.hot)
            wallets.extend(snapshot.warm)
        return _normalize_wallets(wallets)

    def active_wallets(self) -> list[str]:
        with self._lock:
            snapshot = self._snapshot
            wallets = list(snapshot.hot)
            wallets.extend(snapshot.warm)
            wallets.extend(snapshot.discovery)
        return _normalize_wallets(wallets)

    def wallets_for_poll(self) -> list[str]:
        with self._lock:
            self._loop_count += 1
            snapshot = self._snapshot
            due: list[str] = list(snapshot.hot)
            if snapshot.warm and (self._loop_count % warm_poll_interval_multiplier()) == 0:
                due.extend(snapshot.warm)
            if snapshot.discovery and (self._loop_count % discovery_poll_interval_multiplier()) == 0:
                due.extend(snapshot.discovery)
            if not due:
                due = list(snapshot.hot or snapshot.warm or snapshot.discovery)
        return _normalize_wallets(due)

    def state_fields(self) -> dict[str, int]:
        with self._lock:
            snapshot = self._snapshot
        tracked_count = len(snapshot.hot) + len(snapshot.warm) + len(snapshot.discovery)
        return {
            "tracked_wallet_count": tracked_count,
            "dropped_wallet_count": len(snapshot.dropped),
            "hot_wallet_count": len(snapshot.hot),
            "warm_wallet_count": len(snapshot.warm),
            "discovery_wallet_count": len(snapshot.discovery),
            "watch_tier_refreshed_at": snapshot.refreshed_at,
        }
