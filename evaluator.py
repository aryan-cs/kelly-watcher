from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import httpx
import numpy as np

from alerter import send_alert
from beliefs import sync_belief_priors
from db import get_conn
from trade_contract import (
    EXECUTED_ENTRY_SQL,
    REALIZED_CLOSE_TS_SQL,
    RESOLVED_EXECUTED_ENTRY_SQL,
    is_fill_aware_executed_buy,
)

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


def resolve_shadow_trades() -> list[dict]:
    conn = get_conn()
    unresolved = conn.execute(
        """
        SELECT id, market_id, token_id, side, price_at_signal, signal_size_usd,
               actual_entry_price, actual_entry_shares, actual_entry_size_usd,
               remaining_entry_shares, remaining_entry_size_usd, realized_exit_pnl_usd,
               shadow_pnl_usd, actual_pnl_usd,
               real_money, skipped, source_action, exited_at
        FROM trade_log
        WHERE outcome IS NULL
          AND COALESCE(source_action, 'buy')='buy'
        """
    ).fetchall()
    conn.close()

    if not unresolved:
        return []

    resolved_rows: list[dict] = []
    market_cache: dict[str, dict | None] = {}
    with httpx.Client(timeout=10.0) as client:
        for row in unresolved:
            try:
                market = _fetch_market(client, str(row["market_id"]), market_cache)
                if not market:
                    continue

                result = _winning_outcome(market)
                close_ts = _market_close_ts(market)
                is_closed = bool(market.get("closed", False))
                if not is_closed and close_ts and close_ts > int(time.time()) and not result:
                    continue
                if not result:
                    continue

                won = str(row["side"]).strip().lower() == str(result).strip().lower()
                fill_aware = is_fill_aware_executed_buy(row)
                price = float(
                    row["actual_entry_price"] if row["actual_entry_price"] is not None else row["price_at_signal"]
                )
                size = float(
                    row["actual_entry_size_usd"] if row["actual_entry_size_usd"] is not None else row["signal_size_usd"]
                )
                unit_return = round(((1 - price) / price) if won and price > 0 else -1.0, 6)
                pnl = None
                if fill_aware:
                    existing_pnl = row["actual_pnl_usd"] if row["real_money"] == 1 else row["shadow_pnl_usd"]
                    if existing_pnl is not None and row["exited_at"] is not None:
                        pnl = round(float(existing_pnl), 2)
                    else:
                        remaining_shares = float(
                            row["remaining_entry_shares"]
                            if row["remaining_entry_shares"] is not None
                            else row["actual_entry_shares"] or 0.0
                        )
                        remaining_size = float(
                            row["remaining_entry_size_usd"]
                            if row["remaining_entry_size_usd"] is not None
                            else row["actual_entry_size_usd"] or 0.0
                        )
                        realized_exit_pnl = float(row["realized_exit_pnl_usd"] or 0.0)
                        payout = remaining_shares if won else 0.0
                        pnl = round(realized_exit_pnl + payout - remaining_size, 2)

                conn = get_conn()
                conn.execute(
                    """
                    UPDATE trade_log
                    SET outcome=?, market_resolved_outcome=?, counterfactual_return=?,
                        shadow_pnl_usd=COALESCE(?, shadow_pnl_usd),
                        actual_pnl_usd=COALESCE(?, actual_pnl_usd),
                        remaining_entry_shares=CASE
                            WHEN ? IS NOT NULL AND exited_at IS NULL THEN 0
                            ELSE remaining_entry_shares
                        END,
                        remaining_entry_size_usd=CASE
                            WHEN ? IS NOT NULL AND exited_at IS NULL THEN 0
                            ELSE remaining_entry_size_usd
                        END,
                        remaining_source_shares=CASE
                            WHEN ? IS NOT NULL AND exited_at IS NULL THEN 0
                            ELSE remaining_source_shares
                        END,
                        label_applied_at=?,
                        resolved_at=COALESCE(resolved_at, ?),
                        resolution_json=?
                    WHERE id=?
                    """,
                    (
                        1 if won else 0,
                        str(result).strip().lower(),
                        unit_return,
                        pnl if row["real_money"] == 0 and fill_aware else None,
                        pnl if row["real_money"] == 1 and fill_aware else None,
                        pnl if fill_aware else None,
                        pnl if fill_aware else None,
                        pnl if fill_aware else None,
                        int(time.time()),
                        int(time.time()),
                        json.dumps(market, separators=(",", ":"), default=str),
                        row["id"],
                    ),
                )
                if fill_aware:
                    if str(row["token_id"] or "").strip():
                        conn.execute(
                            "DELETE FROM positions WHERE market_id=? AND token_id=? AND real_money=?",
                            (row["market_id"], str(row["token_id"]).strip(), row["real_money"]),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM positions WHERE market_id=? AND LOWER(side)=? AND real_money=?",
                            (row["market_id"], str(row["side"] or "").strip().lower(), row["real_money"]),
                        )
                conn.commit()
                conn.close()

                resolved_rows.append(
                    {
                        "market_id": row["market_id"],
                        "real_money": row["real_money"],
                        "won": won,
                        "pnl": float(pnl or 0.0),
                    }
                )
            except Exception as exc:
                logger.error("Resolution check failed for %s: %s", row["market_id"][:12], exc)

    if resolved_rows:
        logger.info("Resolved %s trades", len(resolved_rows))
    sync_belief_priors()
    return resolved_rows


def compute_performance_report(mode: str = "shadow") -> dict:
    pnl_column = "shadow_pnl_usd" if mode == "shadow" else "actual_pnl_usd"
    real_money = 0 if mode == "shadow" else 1
    conn = get_conn()

    summary = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN COALESCE(source_action, 'buy')='buy' THEN 1 ELSE 0 END) AS total_signals,
            SUM(CASE WHEN {EXECUTED_ENTRY_SQL} THEN 1 ELSE 0 END) AS acted,
            SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} THEN 1 ELSE 0 END) AS resolved,
            SUM(CASE WHEN {RESOLVED_EXECUTED_ENTRY_SQL} AND {pnl_column} > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(SUM(CASE WHEN {EXECUTED_ENTRY_SQL} THEN COALESCE({pnl_column}, 0) ELSE 0 END), 2) AS total_pnl,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} THEN confidence END), 3) AS avg_confidence,
            ROUND(AVG(CASE WHEN {EXECUTED_ENTRY_SQL} THEN actual_entry_size_usd END), 2) AS avg_size
        FROM trade_log
        WHERE real_money=?
        """,
        (real_money,),
    ).fetchone()

    traders = conn.execute(
        f"""
        SELECT trader_address,
               COUNT(*) AS n,
               SUM(CASE WHEN {pnl_column} > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM({pnl_column}), 2) AS pnl
        FROM trade_log
        WHERE real_money=?
          AND {RESOLVED_EXECUTED_ENTRY_SQL}
        GROUP BY trader_address
        ORDER BY pnl DESC
        LIMIT 10
        """,
        (real_money,),
    ).fetchall()

    week_ago = int(time.time()) - 7 * 86400
    weekly = conn.execute(
        f"""
        SELECT ROUND(SUM({pnl_column}), 2) AS pnl
        FROM trade_log
        WHERE real_money=?
          AND {RESOLVED_EXECUTED_ENTRY_SQL}
          AND {REALIZED_CLOSE_TS_SQL} > ?
        """,
        (real_money, week_ago),
    ).fetchone()

    daily_rows = conn.execute(
        f"""
        SELECT strftime('%Y-%m-%d', datetime({REALIZED_CLOSE_TS_SQL}, 'unixepoch', 'localtime')) AS day,
               SUM({pnl_column}) AS day_pnl
        FROM trade_log
        WHERE real_money=?
          AND {RESOLVED_EXECUTED_ENTRY_SQL}
        GROUP BY day
        ORDER BY day
        """,
        (real_money,),
    ).fetchall()
    conn.close()

    resolved = int(summary["resolved"] or 0)
    wins = int(summary["wins"] or 0)
    day_pnls = [float(row["day_pnl"]) for row in daily_rows if row["day_pnl"] is not None]
    sharpe = float(np.mean(day_pnls) / (np.std(day_pnls) + 1e-6)) if len(day_pnls) > 1 else 0.0

    return {
        "mode": mode,
        "total_signals": int(summary["total_signals"] or 0),
        "acted": int(summary["acted"] or 0),
        "resolved": resolved,
        "win_rate": round((wins / resolved) if resolved else 0.0, 3),
        "total_pnl_usd": float(summary["total_pnl"] or 0.0),
        "weekly_pnl_usd": float(weekly["pnl"] or 0.0),
        "avg_confidence": float(summary["avg_confidence"] or 0.0),
        "avg_size_usd": float(summary["avg_size"] or 0.0),
        "sharpe": round(sharpe, 3),
        "top_traders": [dict(row) for row in traders],
        "daily_pnls": [
            {"day": row["day"], "pnl": float(row["day_pnl"] or 0.0)}
            for row in daily_rows
        ],
    }


def persist_performance_snapshot(mode: str) -> None:
    report = compute_performance_report(mode)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO perf_snapshots (
            snapshot_at, mode, n_signals, n_acted, n_resolved,
            win_rate, total_pnl_usd, avg_confidence, sharpe
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            int(time.time()),
            mode,
            report["total_signals"],
            report["acted"],
            report["resolved"],
            report["win_rate"],
            report["total_pnl_usd"],
            report["avg_confidence"],
            report["sharpe"],
        ),
    )
    conn.commit()
    conn.close()


def daily_report() -> None:
    resolve_shadow_trades()
    shadow = compute_performance_report("shadow")
    live = compute_performance_report("live")
    persist_performance_snapshot("shadow")
    persist_performance_snapshot("live")

    lines = [
        "=== Daily Performance Report ===",
        "",
        (
            f"[SHADOW] {shadow['resolved']} resolved | WR: {shadow['win_rate']:.0%} | "
            f"P&L: ${shadow['total_pnl_usd']:.2f} | 7d: ${shadow['weekly_pnl_usd']:.2f}"
        ),
        f"[SHADOW] Sharpe: {shadow['sharpe']:.2f} | Avg conf: {shadow['avg_confidence']:.3f}",
    ]

    if live["acted"] > 0:
        lines.extend(
            [
                "",
                (
                    f"[LIVE] {live['resolved']} resolved | WR: {live['win_rate']:.0%} | "
                    f"P&L: ${live['total_pnl_usd']:.2f}"
                ),
            ]
        )

    if shadow["top_traders"]:
        lines.append("")
        lines.append("Top shadow traders (by P&L):")
        for trader in shadow["top_traders"][:3]:
            lines.append(
                f"  {trader['trader_address'][:10]}... "
                f"{trader['wins']}/{trader['n']} | ${float(trader['pnl'] or 0.0):.2f}"
            )

    send_alert("\n".join(lines))


def _winning_outcome(market: dict) -> str | None:
    for token in market.get("tokens", []):
        try:
            if float(token.get("price", 0.0)) >= 0.99:
                outcome = str(token.get("outcome", "")).strip()
                if outcome:
                    return outcome
        except Exception:
            continue

    outcomes_raw = market.get("outcomes") or market.get("outcomeNames") or market.get("outcome_names")
    prices_raw = market.get("outcomePrices") or market.get("outcome_prices")
    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else list(outcomes_raw or [])
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else list(prices_raw or [])
        for outcome, price in zip(outcomes, prices):
            try:
                if float(price) >= 0.99:
                    winner = str(outcome).strip()
                    if winner:
                        return winner
            except Exception:
                continue
    except Exception:
        pass

    winner = str(market.get("winner") or market.get("resolvedOutcome") or "").strip()
    if winner:
        return winner
    return None


def _market_close_ts(market: dict) -> int:
    for key in ("endDate", "closedTime", "closeTime", "end_date"):
        value = market.get(key)
        if not value:
            continue
        try:
            return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
    return 0


def _fetch_market(client: httpx.Client, market_id: str, cache: dict[str, dict | None]) -> dict | None:
    key = market_id.lower()
    if key in cache:
        return cache[key]

    for attempt in range(3):
        try:
            response = client.get(
                f"{GAMMA_API}/markets",
                params={"condition_ids": market_id},
            )
            if response.status_code == 429:
                time.sleep(0.5 * (attempt + 1))
                continue
            response.raise_for_status()
            payload = response.json()
            markets = payload if isinstance(payload, list) else payload.get("markets", [])
            if not markets:
                cache[key] = None
                return None
            market = next(
                (
                    candidate
                    for candidate in markets
                    if str(candidate.get("conditionId", "")).lower() == key
                ),
                markets[0],
            )
            cache[key] = market
            time.sleep(0.04)
            return market
        except Exception as exc:
            if attempt == 2:
                logger.error("Resolution check failed for %s: %s", market_id[:12], exc)
                cache[key] = None
                return None
            time.sleep(0.5 * (attempt + 1))

    cache[key] = None
    return None
