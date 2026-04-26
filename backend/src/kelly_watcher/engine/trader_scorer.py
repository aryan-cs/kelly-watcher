from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

from kelly_watcher.data.db import get_conn
from kelly_watcher.engine.trade_contract import PROFITABLE_TRADE_SQL, RESOLVED_EXECUTED_ENTRY_SQL

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
TRADER_CACHE_TTL_SECONDS = 600
TRADER_CACHE_REFRESH_BATCH_SIZE = 12
REMOTE_PAGE_LIMIT = 50
REMOTE_MAX_PAGES = 8
REMOTE_CLOSED_POSITIONS_CAP = REMOTE_PAGE_LIMIT * REMOTE_MAX_PAGES
REMOTE_REQUEST_TIMEOUT_S = 10.0
REMOTE_RETRY_MAX_ATTEMPTS = 3
REMOTE_RETRY_BASE_DELAY_S = 1.0
REMOTE_PAGE_DELAY_S = 0.05
REMOTE_BACKOFF_DEFAULT_S = 120.0
_remote_backoff_until = 0.0
_refresh_cursor = 0


@dataclass
class TraderFeatures:
    win_rate: float
    n_trades: int
    consistency: float
    account_age_d: int
    volume_usd: float
    avg_size_usd: float
    diversity: int
    conviction_ratio: float
    wins: int = 0
    ties: int = 0
    realized_pnl_usd: float = 0.0
    avg_return: float = 0.0
    open_positions: int = 0
    open_value_usd: float = 0.0
    open_pnl_usd: float = 0.0


def get_trader_features(
    trader_address: str,
    observed_size_usd: float,
    force_refresh: bool = False,
    allow_remote: bool = True,
) -> TraderFeatures:
    stale_cached = _get_cached_trader_features(
        trader_address,
        observed_size_usd,
        max_age_seconds=None,
    )
    if not force_refresh:
        cached = _get_cached_trader_features(
            trader_address,
            observed_size_usd,
            max_age_seconds=TRADER_CACHE_TTL_SECONDS,
        )
        if cached:
            return cached

    if allow_remote:
        remote = _fetch_remote_trader_features(trader_address, observed_size_usd)
        if remote:
            remote = _normalize_remote_win_rate(trader_address, observed_size_usd, remote)
            _store_trader_features(trader_address, remote)
            return remote

    local = _compute_local_trader_features(trader_address, observed_size_usd)
    if local.n_trades == 0 and stale_cached:
        return stale_cached
    if not allow_remote:
        return local
    _store_trader_features(trader_address, local)
    return local


def refresh_trader_cache(wallet_addresses: list[str], force_refresh: bool = False) -> None:
    for wallet in _wallets_due_for_refresh(wallet_addresses, force_refresh=force_refresh):
        if _remote_backoff_active():
            logger.warning(
                "Trader cache refresh paused for %.1fs after data-api rate limiting",
                _remote_backoff_remaining_seconds(),
            )
            break
        wallet_address = wallet.strip().lower()
        if not wallet_address:
            continue
        try:
            get_trader_features(wallet_address, 0.0, force_refresh=force_refresh)
        except Exception as exc:
            logger.warning("Trader cache refresh failed for %s: %s", wallet_address[:10], exc)


def _get_cached_trader_features(
    trader_address: str,
    observed_size_usd: float,
    max_age_seconds: int | None,
) -> TraderFeatures | None:
    conn = get_conn()
    try:
        if max_age_seconds is None:
            row = conn.execute(
                "SELECT * FROM trader_cache WHERE trader_address=?",
                (trader_address.lower(),),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM trader_cache WHERE trader_address=? AND updated_at>?",
                (trader_address.lower(), int(time.time()) - max_age_seconds),
            ).fetchone()
    finally:
        conn.close()

    if row:
        avg_size = row["avg_size_usd"] or observed_size_usd
        return TraderFeatures(
            win_rate=row["win_rate"],
            n_trades=row["n_trades"],
            consistency=row["consistency"],
            account_age_d=row["account_age_d"],
            volume_usd=row["volume_usd"],
            avg_size_usd=avg_size,
            diversity=row["diversity"],
            conviction_ratio=(observed_size_usd / avg_size) if avg_size > 0 else 1.0,
            wins=int(row["wins"] or 0),
            ties=int(row["ties"] or 0),
            realized_pnl_usd=float(row["realized_pnl_usd"] or 0.0),
            avg_return=float(row["avg_return"] or 0.0),
            open_positions=int(row["open_positions"] or 0),
            open_value_usd=float(row["open_value_usd"] or 0.0),
            open_pnl_usd=float(row["open_pnl_usd"] or 0.0),
        )

    return None


def _fetch_remote_trader_features(
    trader_address: str,
    observed_size_usd: float,
) -> TraderFeatures | None:
    if _remote_backoff_active():
        logger.info(
            "Skipping remote trader refresh for %s during %.1fs data-api backoff",
            trader_address[:10],
            _remote_backoff_remaining_seconds(),
        )
        return None

    with httpx.Client(timeout=REMOTE_REQUEST_TIMEOUT_S, follow_redirects=True) as client:
        positions = _fetch_closed_positions(client, trader_address)
        open_positions = _fetch_open_positions(client, trader_address)
    if not positions and not open_positions:
        return None

    wins = 0
    ties = 0
    realized_pnl_total = 0.0
    returns: list[float] = []
    sizes: list[float] = []
    condition_ids: set[str] = set()
    timestamps: list[int] = []

    for row in positions:
        realized_pnl = _to_float(row.get("realizedPnl"))
        total_bought = max(_to_float(row.get("totalBought")), 0.0)
        if realized_pnl > 0:
            wins += 1
        elif abs(realized_pnl) < 1e-9:
            ties += 1
        realized_pnl_total += realized_pnl

        base = total_bought if total_bought > 0 else max(observed_size_usd, 1.0)
        returns.append(float(np.clip(realized_pnl / base, -1.0, 3.0)))
        if total_bought > 0:
            sizes.append(total_bought)

        condition_id = str(row.get("conditionId") or "").strip().lower()
        if condition_id:
            condition_ids.add(condition_id)

        timestamp = _to_int(row.get("timestamp"))
        if timestamp > 0:
            timestamps.append(timestamp)

    n_positions = len(positions)
    open_count = 0
    open_value_total = 0.0
    open_pnl_total = 0.0
    open_volume_total = 0.0

    for row in open_positions:
        current_value = max(_to_float(row.get("currentValue")), 0.0)
        cash_pnl = _to_float(row.get("cashPnl"))
        total_bought = max(_to_float(row.get("totalBought")), 0.0)
        if current_value <= 0 and total_bought <= 0:
            continue

        open_count += 1
        open_value_total += current_value if current_value > 0 else total_bought
        open_pnl_total += cash_pnl
        open_volume_total += total_bought
        if total_bought > 0:
            sizes.append(total_bought)

        condition_id = str(row.get("conditionId") or "").strip().lower()
        if condition_id:
            condition_ids.add(condition_id)

    prior = 20
    win_rate = (wins + 0.5 * ties + 0.5 * prior) / (n_positions + prior)
    std_dev = float(np.std(returns)) if len(returns) > 1 else 1.0
    avg_return = float(np.mean(returns)) if returns else 0.0
    consistency = avg_return / (std_dev + 1e-6) if returns else 0.0
    avg_size = float(np.mean(sizes)) if sizes else observed_size_usd
    total_volume = float(sum(sizes)) if sizes else open_volume_total
    age_days = int((time.time() - min(timestamps)) / 86400) if timestamps else 0

    return TraderFeatures(
        win_rate=win_rate,
        n_trades=n_positions,
        consistency=consistency,
        account_age_d=age_days,
        volume_usd=total_volume,
        avg_size_usd=avg_size,
        diversity=len(condition_ids),
        conviction_ratio=(observed_size_usd / avg_size) if avg_size > 0 else 1.0,
        wins=wins,
        ties=ties,
        realized_pnl_usd=realized_pnl_total,
        avg_return=avg_return,
        open_positions=open_count,
        open_value_usd=open_value_total,
        open_pnl_usd=open_pnl_total,
    )


def _normalize_remote_win_rate(
    trader_address: str,
    observed_size_usd: float,
    remote: TraderFeatures,
) -> TraderFeatures:
    if not _remote_win_rate_is_suspicious(remote):
        return remote

    local = _compute_local_trader_features(trader_address, observed_size_usd)
    if local.n_trades > 0:
        logger.info(
            "Remote profile WR looked capped for %s; using local resolved WR %.1f%% over %d trades",
            trader_address[:10],
            local.win_rate * 100,
            local.n_trades,
        )
        remote.win_rate = local.win_rate
        remote.wins = local.wins
        remote.ties = local.ties
        return remote

    logger.info(
        "Remote profile WR looked capped for %s with no local history; falling back to neutral WR",
        trader_address[:10],
    )
    remote.win_rate = 0.5
    remote.wins = 0
    remote.ties = 0
    return remote


def _remote_win_rate_is_suspicious(features: TraderFeatures) -> bool:
    if features.n_trades < REMOTE_CLOSED_POSITIONS_CAP:
        return False
    if features.ties != 0:
        return False
    return features.wins == features.n_trades


def _fetch_closed_positions(client: httpx.Client, trader_address: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_first_keys: set[str] = set()

    try:
        for page in range(REMOTE_MAX_PAGES):
            if _remote_backoff_active():
                break

            payload, ok = _request_data_api_json(
                client,
                f"{DATA_API}/closed-positions",
                params={
                    "user": trader_address.lower(),
                    "limit": REMOTE_PAGE_LIMIT,
                    "offset": page * REMOTE_PAGE_LIMIT,
                },
                failure_log=f"Remote trader history fetch failed for {trader_address[:10]}",
            )
            if not ok:
                break
            batch = payload if isinstance(payload, list) else payload.get("positions", [])
            if not batch:
                break

            first_key = _position_key(batch[0])
            if first_key in seen_first_keys:
                break
            seen_first_keys.add(first_key)

            added = 0
            for item in batch:
                if not isinstance(item, dict):
                    continue
                key = _position_key(item)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                rows.append(item)
                added += 1

            if len(batch) < REMOTE_PAGE_LIMIT or added == 0:
                break
            if page < REMOTE_MAX_PAGES - 1:
                time.sleep(REMOTE_PAGE_DELAY_S)
    except Exception as exc:
        logger.warning("Remote trader history fetch failed for %s: %s", trader_address[:10], exc)
        return []

    return rows


def _fetch_open_positions(client: httpx.Client, trader_address: str) -> list[dict[str, Any]]:
    if _remote_backoff_active():
        return []
    try:
        payload, ok = _request_data_api_json(
            client,
            f"{DATA_API}/positions",
            params={"user": trader_address.lower()},
            failure_log=f"Remote trader positions fetch failed for {trader_address[:10]}",
        )
        if not ok:
            return []
        rows = payload if isinstance(payload, list) else payload.get("positions", [])
        return [item for item in rows if isinstance(item, dict)]
    except Exception as exc:
        logger.warning("Remote trader positions fetch failed for %s: %s", trader_address[:10], exc)
        return []


def _request_data_api_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None,
    failure_log: str,
) -> tuple[Any | None, bool]:
    for attempt in range(REMOTE_RETRY_MAX_ATTEMPTS):
        if _remote_backoff_active():
            return None, False
        try:
            response = client.get(url, params=params)
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
            if status_code == 429:
                _arm_remote_backoff(_retry_after_seconds(exc.response))
                logger.warning("%s: %s", failure_log, exc)
                return None, False
            if status_code in {500, 502, 503, 504} and attempt < REMOTE_RETRY_MAX_ATTEMPTS - 1:
                time.sleep(REMOTE_RETRY_BASE_DELAY_S * (attempt + 1))
                continue
            logger.warning("%s: %s", failure_log, exc)
            return None, False
        except Exception as exc:
            if attempt < REMOTE_RETRY_MAX_ATTEMPTS - 1:
                time.sleep(REMOTE_RETRY_BASE_DELAY_S * (attempt + 1))
                continue
            logger.warning("%s: %s", failure_log, exc)
            return None, False
    return None, False


def _retry_after_seconds(response: httpx.Response | None) -> float:
    if response is None:
        return 0.0
    try:
        return max(float((response.headers or {}).get("Retry-After") or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _arm_remote_backoff(duration_seconds: float) -> None:
    global _remote_backoff_until
    backoff_seconds = max(duration_seconds, REMOTE_BACKOFF_DEFAULT_S)
    _remote_backoff_until = max(_remote_backoff_until, time.time() + backoff_seconds)


def _remote_backoff_active(now_ts: float | None = None) -> bool:
    return _remote_backoff_until > (time.time() if now_ts is None else now_ts)


def _remote_backoff_remaining_seconds(now_ts: float | None = None) -> float:
    return max(_remote_backoff_until - (time.time() if now_ts is None else now_ts), 0.0)


def _wallets_due_for_refresh(wallet_addresses: list[str], force_refresh: bool = False) -> list[str]:
    global _refresh_cursor

    normalized_wallets: list[str] = []
    seen_wallets: set[str] = set()
    for wallet in wallet_addresses:
        wallet_address = wallet.strip().lower()
        if not wallet_address or wallet_address in seen_wallets:
            continue
        seen_wallets.add(wallet_address)
        normalized_wallets.append(wallet_address)

    if not normalized_wallets:
        return []

    updated_at_map = _trader_cache_updated_at_map(normalized_wallets)
    freshness_cutoff = int(time.time()) - TRADER_CACHE_TTL_SECONDS
    candidates = [
        wallet
        for wallet in normalized_wallets
        if force_refresh or updated_at_map.get(wallet, 0) <= freshness_cutoff
    ]
    if not candidates:
        return []

    batch_size = min(TRADER_CACHE_REFRESH_BATCH_SIZE, len(candidates))
    if batch_size >= len(candidates):
        return candidates

    start = _refresh_cursor % len(candidates)
    rotated = candidates[start:] + candidates[:start]
    _refresh_cursor = (start + batch_size) % len(candidates)
    return rotated[:batch_size]


def _trader_cache_updated_at_map(wallet_addresses: list[str]) -> dict[str, int]:
    if not wallet_addresses:
        return {}

    conn = get_conn()
    try:
        placeholders = ",".join("?" for _ in wallet_addresses)
        rows = conn.execute(
            f"""
            SELECT trader_address, updated_at
            FROM trader_cache
            WHERE trader_address IN ({placeholders})
            """,
            tuple(wallet_addresses),
        ).fetchall()
    finally:
        conn.close()

    return {
        str(row["trader_address"] or "").strip().lower(): int(row["updated_at"] or 0)
        for row in rows
    }


def _position_key(row: dict[str, Any]) -> str:
    condition_id = str(row.get("conditionId") or "").strip().lower()
    timestamp = str(row.get("timestamp") or "").strip()
    outcome = str(row.get("outcome") or "").strip().lower()
    pnl = str(row.get("realizedPnl") or "").strip()
    return "|".join((condition_id, timestamp, outcome, pnl))


def _compute_local_trader_features(trader_address: str, observed_size_usd: float) -> TraderFeatures:
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                {PROFITABLE_TRADE_SQL} AS label,
                COALESCE(actual_entry_size_usd, signal_size_usd) AS effective_size_usd,
                placed_at,
                market_id
            FROM trade_log
            WHERE trader_address=?
              AND {RESOLVED_EXECUTED_ENTRY_SQL}
            ORDER BY placed_at DESC
            LIMIT 500
            """,
            (trader_address.lower(),),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return TraderFeatures(
            win_rate=0.5,
            n_trades=0,
            consistency=0.0,
            account_age_d=0,
            volume_usd=0.0,
            avg_size_usd=observed_size_usd,
            diversity=0,
            conviction_ratio=1.0,
            wins=0,
            ties=0,
            realized_pnl_usd=0.0,
            avg_return=0.0,
            open_positions=0,
            open_value_usd=0.0,
            open_pnl_usd=0.0,
        )

    wins = sum(1 for row in rows if row["label"] == 1)
    returns = [1.0 if row["label"] == 1 else -1.0 for row in rows]
    std_dev = float(np.std(returns)) if len(returns) > 1 else 1.0
    consistency = float(np.mean(returns)) / (std_dev + 1e-6)
    sizes = [float(row["effective_size_usd"] or observed_size_usd) for row in rows]
    avg_size = float(np.mean(sizes)) if sizes else observed_size_usd
    first_trade_ts = min(int(row["placed_at"]) for row in rows)
    age_days = int((time.time() - first_trade_ts) / 86400)
    diversity = len({row["market_id"] for row in rows})
    total_volume = float(sum(sizes))
    prior = 20
    win_rate = (wins + 0.5 * prior) / (len(rows) + prior)

    return TraderFeatures(
        win_rate=win_rate,
        n_trades=len(rows),
        consistency=consistency,
        account_age_d=age_days,
        volume_usd=total_volume,
        avg_size_usd=avg_size,
        diversity=diversity,
        conviction_ratio=(observed_size_usd / avg_size) if avg_size > 0 else 1.0,
        wins=wins,
        ties=0,
        realized_pnl_usd=0.0,
        avg_return=float(np.mean(returns)) if returns else 0.0,
        open_positions=0,
        open_value_usd=0.0,
        open_pnl_usd=0.0,
    )


def _store_trader_features(trader_address: str, features: TraderFeatures) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO trader_cache (
                trader_address,
                win_rate,
                n_trades,
                consistency,
                volume_usd,
                avg_size_usd,
                diversity,
                account_age_d,
                wins,
                ties,
                realized_pnl_usd,
                avg_return,
                open_positions,
                open_value_usd,
                open_pnl_usd,
                updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trader_address.lower(),
                features.win_rate,
                features.n_trades,
                features.consistency,
                features.volume_usd,
                features.avg_size_usd,
                features.diversity,
                features.account_age_d,
                features.wins,
                features.ties,
                features.realized_pnl_usd,
                features.avg_return,
                features.open_positions,
                features.open_value_usd,
                features.open_pnl_usd,
                int(time.time()),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _to_float(value: Any) -> float:
    try:
        numeric = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return numeric if math.isfinite(numeric) else 0.0


def _to_int(value: Any) -> int:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(numeric):
        return 0
    return int(numeric)


class TraderScorer:
    WEIGHTS = {
        "win_rate": 0.30,
        "consistency": 0.15,
        "age": 0.10,
        "conviction": 0.15,
        "diversity": 0.05,
    }

    @staticmethod
    def _score_win_rate(win_rate: float, n_trades: int) -> float:
        clipped = float(np.clip(_finite_or_default(win_rate, 0.5), 0, 1))
        safe_trades = max(_finite_or_default(n_trades, 0.0), 0.0)
        if safe_trades <= 0:
            return 0.5
        evidence_weight = safe_trades / (safe_trades + 20.0)
        shrunk = 0.5 + (clipped - 0.5) * evidence_weight
        return float(np.clip(shrunk, 0, 1))

    @staticmethod
    def _score_consistency(sharpe_like: float) -> float:
        return float(np.clip(_finite_or_default(sharpe_like, 0.0) / 3.0, 0, 1))

    @staticmethod
    def _score_age(days: int) -> float:
        safe_days = max(_finite_or_default(days, 0.0), 0.0)
        return float(np.clip(np.log1p(safe_days) / np.log1p(365), 0, 1))

    @staticmethod
    def _score_conviction(ratio: float) -> float:
        safe_ratio = float(np.clip(_finite_or_default(ratio, 1.0), -50.0, 50.0))
        return float(1 / (1 + np.exp(-2 * (safe_ratio - 1))))

    @staticmethod
    def _score_diversity(n_markets: int) -> float:
        safe_markets = max(_finite_or_default(n_markets, 0.0), 0.0)
        return float(np.clip(safe_markets / 10, 0, 1))

    def score(self, features: TraderFeatures) -> dict:
        components = {
            "win_rate": self._score_win_rate(features.win_rate, features.n_trades),
            "consistency": self._score_consistency(features.consistency),
            "age": self._score_age(features.account_age_d),
            "conviction": self._score_conviction(features.conviction_ratio),
            "diversity": self._score_diversity(features.diversity),
        }
        total_weight = sum(self.WEIGHTS.values())
        confidence = sum((self.WEIGHTS[key] / total_weight) * value for key, value in components.items())
        return {
            "score": round(float(confidence), 4),
            "components": {key: round(value, 3) for key, value in components.items()},
        }


def _finite_or_default(value: Any, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default
