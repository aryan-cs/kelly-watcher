from __future__ import annotations

import json
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from alerter import send_alert
from beliefs import invalidate_belief_cache, sync_belief_priors
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
SPORTS_PAGE_BASE = "https://polymarket.com/sports"
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">(.*?)</script>'
)
_TEAM_WIN_QUESTION_RE = re.compile(
    r"^will\s+(.+?)\s+win(?:\s+on\s+\d{4}-\d{2}-\d{2})?\??$",
    re.IGNORECASE,
)
_DRAW_QUESTION_RE = re.compile(r"^will\s+.+?\s+end in a draw\??$", re.IGNORECASE)
_SPREAD_QUESTION_RE = re.compile(r"^spread:\s+(.+?)\s+\(([-+]?\d+(?:\.\d+)?)\)\s*$", re.IGNORECASE)
_SPORTS_ROUTE_ALIASES = {
    "atp": ("tennis", "atp"),
    "blast": ("esports", "blast"),
    "bun": ("bundesliga", "bun"),
    "cfb": ("cfb",),
    "cbb": ("cbb",),
    "cs": ("esports", "cs"),
    "epl": ("epl",),
    "esl": ("esports", "esl"),
    "esports": ("esports",),
    "f1": ("f1",),
    "ipl": ("ipl",),
    "iem": ("esports", "iem"),
    "laliga": ("laliga",),
    "ligue1": ("ligue1",),
    "nba": ("nba",),
    "nfl": ("nfl",),
    "nhl": ("nhl",),
    "mlb": ("mlb",),
    "mls": ("mls",),
    "ncaa": ("cbb", "cfb"),
    "pgl": ("esports", "pgl"),
    "seriea": ("seriea",),
    "uecl": ("uecl",),
    "uel": ("uel",),
    "ucl": ("ucl",),
    "ufc": ("ufc",),
    "wnba": ("wnba",),
    "wta": ("tennis", "wta"),
}
PREMATURE_RESOLUTION_WHERE_SQL = """
outcome IS NOT NULL
AND resolution_json IS NOT NULL
AND (
    COALESCE(json_extract(resolution_json, '$.closed'), 0) IN (0, 'false', '0')
    OR (
        json_extract(resolution_json, '$.source') = 'sports_page'
        AND COALESCE(json_extract(resolution_json, '$.ended'), 0) IN (0, 'false', '0')
        AND (
            json_extract(resolution_json, '$.finishedTimestamp') IS NULL
            OR json_extract(resolution_json, '$.finishedTimestamp') = ''
        )
        AND COALESCE(
            json_extract(resolution_json, '$.period'),
            json_extract(resolution_json, '$.score.period'),
            ''
        ) NOT IN ('FT', 'FINAL', 'VFT', 'FINISHED', 'COMPLETE', 'COMPLETED', 'ENDED')
    )
)
"""


def resolve_shadow_trades() -> list[dict]:
    conn = get_conn()
    unresolved = conn.execute(
        """
        SELECT id, trade_id, market_id, question, market_url, market_metadata_json,
               token_id, side, price_at_signal, signal_size_usd,
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
    sports_page_cache: dict[str, dict[str, Any] | None] = {}
    with httpx.Client(timeout=10.0) as client:
        for row in unresolved:
            try:
                now_ts = int(time.time())
                result: str | None = None
                resolution_payload: dict[str, Any] | None = None

                sports_snapshot = _fetch_sports_page_snapshot(client, row, sports_page_cache)
                if sports_snapshot is not None:
                    result = _resolve_from_sports_page(row, sports_snapshot)
                    if result is not None:
                        resolution_payload = _sports_resolution_payload(row, sports_snapshot)

                if result is None:
                    market = _fetch_market(client, str(row["market_id"]), market_cache)
                    if market and _market_is_closed(market):
                        result = _winning_outcome(market)
                        if result is None:
                            logger.info(
                                "Closed market %s is missing an explicit winner after sports-page fallback",
                                str(row["market_id"])[:12],
                            )
                        else:
                            resolution_payload = market

                if result is None:
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
                        json.dumps(resolution_payload, separators=(",", ":"), default=str),
                        row["id"],
                    ),
                )
                token_id_str = str(row["token_id"] or "").strip().lower()
                side_str = str(row["side"] or "").strip().lower()
                if token_id_str:
                    deleted = conn.execute(
                        "DELETE FROM positions WHERE market_id=? AND LOWER(token_id)=? AND real_money=?",
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
                        "trade_id": row["trade_id"],
                        "market_id": row["market_id"],
                        "question": row["question"],
                        "market_url": row["market_url"],
                        "side": row["side"],
                        "real_money": row["real_money"],
                        "executed": bool(fill_aware),
                        "won": won,
                        "pnl": float(pnl or 0.0),
                        "market_resolved_outcome": str(result).strip().lower(),
                    }
                )
            except Exception as exc:
                logger.error("Resolution check failed for %s: %s", row["market_id"][:12], exc)

    if resolved_rows:
        logger.info("Resolved %s trades", len(resolved_rows))
    sync_belief_priors()
    return resolved_rows


def _fetch_sports_page_snapshot(
    client: httpx.Client,
    row,
    cache: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    meta = _row_market_metadata(row)
    event_slug = _sports_event_slug(row, meta)
    if not event_slug:
        return None

    cache_key = event_slug.lower()
    if cache_key in cache:
        return cache[cache_key]

    for league_slug in _sports_route_candidates(event_slug, meta):
        url = f"{SPORTS_PAGE_BASE}/{league_slug}/{event_slug}"
        try:
            response = client.get(url, follow_redirects=True)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            match = _NEXT_DATA_RE.search(response.text)
            if match is None:
                continue
            payload = json.loads(match.group(1))
            page_props = payload.get("props", {}).get("pageProps", {})
            if not isinstance(page_props, dict):
                continue
            if page_props.get("statusCode") == 404:
                continue
            event = page_props.get("event", {})
            if not isinstance(event, dict):
                continue
            cache[cache_key] = page_props
            return page_props
        except Exception as exc:
            logger.debug("Sports page fetch failed for %s via %s: %s", cache_key, league_slug, exc)

    cache[cache_key] = None
    return None


def _resolve_from_sports_page(row, snapshot: dict[str, Any]) -> str | None:
    if not _sports_snapshot_is_ended(snapshot):
        return None

    market = _sports_snapshot_market(snapshot, str(row["market_id"] or ""))
    if market is not None:
        outcome = _resolve_sports_market(row, market, snapshot)
        if outcome is not None:
            return outcome

    return _resolve_basic_sports_question(row, snapshot)


def _sports_resolution_payload(row, snapshot: dict[str, Any]) -> dict[str, Any]:
    market = _sports_snapshot_market(snapshot, str(row["market_id"] or ""))
    event = snapshot.get("event", {})
    if not isinstance(event, dict):
        event = {}
    score = event.get("score", snapshot.get("score"))
    period = event.get("period") or snapshot.get("period")
    if not period and isinstance(score, dict):
        period = score.get("period") or score.get("status")
    finished_timestamp = event.get("finishedTimestamp") or snapshot.get("finishedTimestamp")
    payload: dict[str, Any] = {
        "source": "sports_page",
        "closed": True,
        "ended": _sports_snapshot_is_ended(snapshot),
        "score": score,
        "period": period,
        "finishedTimestamp": finished_timestamp,
        "canonicalUrl": snapshot.get("canonicalUrl"),
    }

    event_slug = str(event.get("slug") or "").strip()
    if event_slug:
        payload["eventSlug"] = event_slug

    teams = _snapshot_teams(snapshot, market)
    if teams:
        payload["teams"] = teams

    if isinstance(market, dict):
        payload["market"] = {
            "conditionId": market.get("conditionId"),
            "question": market.get("question"),
            "slug": market.get("slug"),
            "closed": market.get("closed"),
            "acceptingOrders": market.get("acceptingOrders"),
            "sportsMarketType": market.get("sportsMarketType"),
            "line": market.get("line"),
            "outcomes": _parse_text_list(market.get("outcomes")),
            "outcomePrices": _parse_text_list(market.get("outcomePrices")),
        }

    return payload


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

    invalidate_belief_cache()
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


def _row_market_metadata(row) -> dict[str, Any] | None:
    raw = row["market_metadata_json"]
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _sports_event_slug(row, meta: dict[str, Any] | None) -> str | None:
    if meta is not None:
        events = meta.get("events", [])
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                slug = str(event.get("slug") or "").strip()
                if slug:
                    return slug
        slug = str(meta.get("slug") or meta.get("marketSlug") or "").strip()
        if slug:
            return slug
        nested_event = meta.get("event")
        if isinstance(nested_event, dict):
            slug = str(nested_event.get("slug") or nested_event.get("marketSlug") or "").strip()
            if slug:
                return slug

    market_url = str(row["market_url"] or "").strip()
    if "/event/" not in market_url:
        return None
    slug = market_url.rsplit("/event/", 1)[-1].strip().strip("/")
    return slug or None


def _sports_route_candidates(event_slug: str, meta: dict[str, Any] | None) -> list[str]:
    candidates: list[str] = []

    def _add(value: str | None) -> None:
        text = str(value or "").strip().lower()
        if not text or text in candidates:
            return
        candidates.append(text)

    prefix = event_slug.split("-", 1)[0].strip().lower()
    for route in _SPORTS_ROUTE_ALIASES.get(prefix, ()):
        _add(route)

    if meta is not None:
        for key in ("sportsMarketType", "seriesSlug", "leagueSlug", "sportSlug"):
            value = str(meta.get(key) or "").strip().lower()
            if value:
                _add(value)

    _add(prefix)
    return candidates


def _sports_snapshot_is_ended(snapshot: dict[str, Any]) -> bool:
    event = snapshot.get("event")
    if not isinstance(event, dict):
        event = {}

    for source in (event, snapshot):
        if not isinstance(source, dict):
            continue
        if _truthy_market_flag(source.get("ended", False)):
            return True
        if str(source.get("finishedTimestamp") or "").strip():
            return True
        period = str(
            source.get("period") or source.get("status") or source.get("gameStatus") or ""
        ).strip().upper()
        if period in {"FT", "FINAL", "VFT", "FINISHED", "COMPLETE", "COMPLETED", "ENDED"}:
            return True

    score_obj = event.get("score") or snapshot.get("score") or {}
    if isinstance(score_obj, dict):
        status = str(score_obj.get("status") or score_obj.get("period") or "").strip().upper()
        if status in {"FT", "FINAL", "VFT", "FINISHED", "COMPLETE", "COMPLETED", "ENDED"}:
            return True

    return False


def _sports_snapshot_market(snapshot: dict[str, Any], market_id: str) -> dict[str, Any] | None:
    event = snapshot.get("event", {})
    if not isinstance(event, dict):
        return None
    target = str(market_id or "").strip().lower()
    if not target:
        return None
    markets = event.get("markets", [])
    if not isinstance(markets, list):
        return None
    for market in markets:
        if not isinstance(market, dict):
            continue
        if str(market.get("conditionId") or "").strip().lower() == target:
            return market
    return None


def _resolve_sports_market(row, market: dict[str, Any], snapshot: dict[str, Any]) -> str | None:
    outcomes = _parse_text_list(market.get("outcomes"))
    teams = _snapshot_teams(snapshot, market)
    if len(teams) < 2:
        return None

    market_type = str(market.get("sportsMarketType") or "").strip().lower()
    if market_type in {"", "moneyline", "child_moneyline"}:
        return _resolve_basic_question_outcome(str(row["question"] or ""), outcomes, teams)
    if market_type == "totals":
        return _resolve_total_outcome(outcomes, teams, market.get("line"))
    if market_type == "spreads":
        return _resolve_spread_outcome(str(row["question"] or ""), outcomes, teams)
    if market_type == "both_teams_to_score":
        return _yes_no_outcome(outcomes, teams[0]["score"] > 0 and teams[1]["score"] > 0)
    return None


def _resolve_basic_sports_question(row, snapshot: dict[str, Any]) -> str | None:
    teams = _snapshot_teams(snapshot, None)
    if len(teams) < 2:
        return None
    return _resolve_basic_question_outcome(str(row["question"] or ""), [], teams)


def _snapshot_teams(snapshot: dict[str, Any], market: dict[str, Any] | None) -> list[dict[str, Any]]:
    for source in (market, snapshot.get("event")):
        if not isinstance(source, dict):
            continue
        teams = _coerce_teams(source.get("teams"))
        if teams:
            return teams

    event = snapshot.get("event", {})
    if not isinstance(event, dict):
        return []
    markets = event.get("markets", [])
    if not isinstance(markets, list):
        return []
    for market_entry in markets:
        if not isinstance(market_entry, dict):
            continue
        teams = _coerce_teams(market_entry.get("teams"))
        if teams:
            return teams

    score_obj = event.get("score") or snapshot.get("score")
    if isinstance(score_obj, dict):
        teams = _teams_from_score_obj(score_obj)
        if teams:
            return teams
    return []


def _teams_from_score_obj(score_obj: dict[str, Any]) -> list[dict[str, Any]]:
    teams: list[dict[str, Any]] = []
    for key in ("homeTeam", "awayTeam", "home", "away", "team1", "team2"):
        entry = score_obj.get(key)
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("teamName") or "").strip()
        score = _coerce_score(entry.get("score") or entry.get("goals") or entry.get("points"))
        if name and score is not None:
            teams.append({"name": name, "score": score})
    return teams[:2] if len(teams) >= 2 else []


def _coerce_teams(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    teams: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        score = _coerce_score(entry.get("score"))
        if score is None:
            continue
        teams.append({"name": name, "score": score, "hostStatus": entry.get("hostStatus")})
    return teams[:2]


def _coerce_score(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_text_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except Exception:
            return [text]
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [text]


def _resolve_basic_question_outcome(
    question: str,
    outcomes: list[str],
    teams: list[dict[str, Any]],
) -> str | None:
    winner, loser, draw = _winner_and_loser(teams)
    if draw:
        matched_draw = _match_outcome_name("draw", outcomes)
        if matched_draw is not None:
            return matched_draw
        if _DRAW_QUESTION_RE.match(question):
            return _yes_no_outcome(outcomes, True)
        return None

    matched_winner = _match_outcome_name(winner, outcomes)
    if matched_winner is not None:
        return matched_winner

    if _DRAW_QUESTION_RE.match(question):
        return _yes_no_outcome(outcomes, False)

    win_question = _TEAM_WIN_QUESTION_RE.match(question.strip())
    if win_question:
        target_team = win_question.group(1).strip()
        return _yes_no_outcome(outcomes, _same_name(target_team, winner))

    if outcomes:
        return None

    return winner


def _resolve_total_outcome(outcomes: list[str], teams: list[dict[str, Any]], line: object) -> str | None:
    try:
        threshold = float(line)
    except (TypeError, ValueError):
        return None
    total = teams[0]["score"] + teams[1]["score"]
    if abs(total - threshold) <= 1e-9:
        return None
    return _match_outcome_name("over", outcomes) if total > threshold else _match_outcome_name("under", outcomes)


def _resolve_spread_outcome(question: str, outcomes: list[str], teams: list[dict[str, Any]]) -> str | None:
    match = _SPREAD_QUESTION_RE.match(question.strip())
    if match is None:
        return None
    named_team = match.group(1).strip()
    try:
        line = float(match.group(2))
    except (TypeError, ValueError):
        return None

    named_entry = _find_team(teams, named_team)
    if named_entry is None:
        return None
    other_entry = next((team for team in teams if not _same_name(team["name"], named_team)), None)
    if other_entry is None:
        return None

    named_adjusted = named_entry["score"] + line
    if abs(named_adjusted - other_entry["score"]) <= 1e-9:
        return None
    winner_name = named_entry["name"] if named_adjusted > other_entry["score"] else other_entry["name"]
    return _match_outcome_name(winner_name, outcomes)


def _winner_and_loser(teams: list[dict[str, Any]]) -> tuple[str, str, bool]:
    if teams[0]["score"] > teams[1]["score"]:
        return teams[0]["name"], teams[1]["name"], False
    if teams[1]["score"] > teams[0]["score"]:
        return teams[1]["name"], teams[0]["name"], False
    return teams[0]["name"], teams[1]["name"], True


def _yes_no_outcome(outcomes: list[str], yes: bool) -> str | None:
    if not outcomes:
        return "yes" if yes else "no"
    target = "yes" if yes else "no"
    return _match_outcome_name(target, outcomes)


def _match_outcome_name(target: str, outcomes: list[str]) -> str | None:
    normalized_target = _normalize_text(target)
    for outcome in outcomes:
        if _normalize_text(outcome) == normalized_target:
            return outcome
    return None


def _find_team(teams: list[dict[str, Any]], target: str) -> dict[str, Any] | None:
    for team in teams:
        if _same_name(team["name"], target):
            return team
    return None


def _same_name(left: str, right: str) -> bool:
    return _normalize_text(left) == _normalize_text(right)


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


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
