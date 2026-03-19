from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

from alerter import send_alert
from beliefs import sync_belief_priors
from db import DB_PATH, get_conn
from trade_contract import (
    EXECUTED_ENTRY_SQL,
    OPEN_EXECUTED_ENTRY_SQL,
    REALIZED_CLOSE_TS_SQL,
    RESOLVED_EXECUTED_ENTRY_SQL,
    is_fill_aware_executed_buy,
    remaining_entry_shares_expr,
    remaining_entry_size_expr,
)

logger = logging.getLogger(__name__)

CLOB_API = "https://clob.polymarket.com"
PREMATURE_RESOLUTION_WHERE_SQL = """
outcome IS NOT NULL
AND resolution_json IS NOT NULL
AND COALESCE(json_extract(resolution_json, '$.closed'), 0) IN (0, 'false', '0')
"""


def resolve_shadow_trades() -> list[dict]:
    conn = get_conn()
    unresolved = conn.execute(
        """
        SELECT id, market_id, token_id, side, price_at_signal, signal_size_usd,
               actual_entry_price, actual_entry_shares, actual_entry_size_usd,
               remaining_entry_shares, remaining_entry_size_usd, realized_exit_pnl_usd,
               shadow_pnl_usd, actual_pnl_usd,
               real_money, skipped, source_action, exited_at, source_shares
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

                now_ts = int(time.time())
                if not _market_is_closed(market):
                    continue
                result = _winning_outcome(market)
                if result is None:
                    logger.info(
                        "Skipping closed market %s without explicit Polymarket winner",
                        str(row["market_id"])[:12],
                    )
                    continue

                won = str(row["side"]).strip().lower() == str(result).strip().lower()
                fill_aware = is_fill_aware_executed_buy(row)
                price = float(
                    row["actual_entry_price"] if row["actual_entry_price"] is not None else row["price_at_signal"]
                )
                if won and price > 0:
                    unit_return = round((1.0 - price) / price, 6)
                elif price > 0:
                    unit_return = round(-price / price, 6)
                else:
                    unit_return = -1.0
                pnl = None
                if fill_aware:
                    existing_pnl = row["actual_pnl_usd"] if row["real_money"] == 1 else row["shadow_pnl_usd"]
                    if existing_pnl is not None and row["exited_at"] is not None:
                        pnl = round(float(existing_pnl), 2)
                    else:
                        remaining_shares = float(
                            row["remaining_entry_shares"]
                            if row["remaining_entry_shares"] is not None
                            else row["actual_entry_shares"]
                            if row["actual_entry_shares"] is not None
                            else row["source_shares"] or 0.0
                        )
                        remaining_size = float(
                            row["remaining_entry_size_usd"]
                            if row["remaining_entry_size_usd"] is not None
                            else row["actual_entry_size_usd"]
                            if row["actual_entry_size_usd"] is not None
                            else row["signal_size_usd"] or 0.0
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
                        now_ts,
                        now_ts,
                        json.dumps(market, separators=(",", ":"), default=str),
                        row["id"],
                    ),
                )
                token_id_str = str(row["token_id"] or "").strip()
                side_str = str(row["side"] or "").strip().lower()
                if token_id_str:
                    deleted = conn.execute(
                        "DELETE FROM positions WHERE market_id=? AND token_id=? AND real_money=?",
                        (row["market_id"], token_id_str, row["real_money"]),
                    ).rowcount
                    if deleted == 0:
                        conn.execute(
                            "DELETE FROM positions WHERE market_id=? AND LOWER(side)=? AND real_money=?",
                            (row["market_id"], side_str, row["real_money"]),
                        )
                else:
                    conn.execute(
                        "DELETE FROM positions WHERE market_id=? AND LOWER(side)=? AND real_money=?",
                        (row["market_id"], side_str, row["real_money"]),
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


def cleanup_premature_resolutions(backup_path: Path | None = None) -> dict[str, int | str]:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_target = backup_path or DB_PATH.with_name(f"{DB_PATH.stem}.premature_cleanup_{timestamp}.bak")
    backup_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB_PATH, backup_target)

    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT
            id,
            real_money,
            skipped,
            actual_entry_price,
            actual_entry_shares,
            actual_entry_size_usd,
            source_shares,
            realized_exit_shares,
            realized_exit_size_usd,
            exited_at
        FROM trade_log
        WHERE {PREMATURE_RESOLUTION_WHERE_SQL}
        ORDER BY id
        """
    ).fetchall()

    if not rows:
        conn.close()
        return {
            "backup_path": str(backup_target),
            "rows_cleaned": 0,
            "skipped_rows_cleaned": 0,
            "open_positions_reopened": 0,
            "exited_rows_preserved": 0,
            "belief_rows_reapplied": 0,
        }

    skipped_rows_cleaned = 0
    open_positions_reopened = 0
    exited_rows_preserved = 0
    for row in rows:
        fill_aware = is_fill_aware_executed_buy(row)
        exited_at = row["exited_at"]
        restored_resolved_at = int(exited_at) if exited_at is not None else None

        conn.execute(
            """
            UPDATE trade_log
            SET outcome=NULL,
                market_resolved_outcome=NULL,
                counterfactual_return=NULL,
                label_applied_at=NULL,
                resolution_json=NULL,
                resolved_at=?
            WHERE id=?
            """,
            (restored_resolved_at, int(row["id"])),
        )

        if bool(row["skipped"]):
            skipped_rows_cleaned += 1
            continue

        if fill_aware and exited_at is None:
            actual_shares = max(float(row["actual_entry_shares"] or 0.0), 0.0)
            actual_size = max(float(row["actual_entry_size_usd"] or 0.0), 0.0)
            source_shares = max(float(row["source_shares"] or 0.0), 0.0)
            realized_exit_shares = max(float(row["realized_exit_shares"] or 0.0), 0.0)
            realized_exit_size = max(float(row["realized_exit_size_usd"] or 0.0), 0.0)

            remaining_shares = max(round(actual_shares - realized_exit_shares, 6), 0.0)
            remaining_size = max(round(actual_size - realized_exit_size, 6), 0.0)
            if actual_shares > 1e-9:
                remaining_source = max(round(source_shares * (remaining_shares / actual_shares), 6), 0.0)
            else:
                remaining_source = source_shares

            conn.execute(
                """
                UPDATE trade_log
                SET shadow_pnl_usd=NULL,
                    actual_pnl_usd=NULL,
                    remaining_entry_shares=?,
                    remaining_entry_size_usd=?,
                    remaining_source_shares=?
                WHERE id=?
                """,
                (
                    remaining_shares,
                    remaining_size,
                    remaining_source,
                    int(row["id"]),
                ),
            )
            open_positions_reopened += 1
        elif fill_aware and exited_at is not None:
            exited_rows_preserved += 1

    _rebuild_shadow_positions(conn)
    conn.execute("DELETE FROM belief_updates")
    conn.execute("DELETE FROM belief_priors")
    conn.commit()
    conn.close()

    belief_rows_reapplied = sync_belief_priors()
    logger.info(
        "Cleaned %s premature resolutions (%s skipped, %s reopened, %s exited preserved)",
        len(rows),
        skipped_rows_cleaned,
        open_positions_reopened,
        exited_rows_preserved,
    )
    return {
        "backup_path": str(backup_target),
        "rows_cleaned": len(rows),
        "skipped_rows_cleaned": skipped_rows_cleaned,
        "open_positions_reopened": open_positions_reopened,
        "exited_rows_preserved": exited_rows_preserved,
        "belief_rows_reapplied": belief_rows_reapplied,
    }


def _winning_outcome(market: dict) -> str | None:
    winner_tokens: list[str] = []
    for token in market.get("tokens", []):
        if not isinstance(token, dict):
            continue
        if not _truthy_market_flag(token.get("winner", False)):
            continue
        outcome = str(token.get("outcome", "")).strip()
        if outcome:
            winner_tokens.append(outcome)

    if len(winner_tokens) == 1:
        return winner_tokens[0]
    if len(winner_tokens) > 1:
        logger.warning("Closed market reported multiple winning tokens: %s", winner_tokens)
        return None

    winner = str(market.get("winner") or market.get("resolvedOutcome") or "").strip()
    if winner:
        return winner
    return None


def _market_is_closed(market: dict) -> bool:
    return _truthy_market_flag(market.get("closed", False))


def _truthy_market_flag(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _fetch_market(client: httpx.Client, market_id: str, cache: dict[str, dict | None]) -> dict | None:
    key = market_id.lower()
    if key in cache:
        return cache[key]

    for attempt in range(3):
        try:
            response = client.get(f"{CLOB_API}/markets/{market_id}")
            if response.status_code == 429:
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code == 404:
                cache[key] = None
                return None
            response.raise_for_status()
            market = response.json()
            if not isinstance(market, dict):
                cache[key] = None
                return None
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


def _rebuild_shadow_positions(conn) -> None:
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
            "INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
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
