from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from replay import ReplayPolicy, policy_to_config_payload, run_replay
from runtime_paths import TRADING_DB_PATH


def _load_payload(*, file_path: str, inline_json: str) -> dict[str, Any] | None:
    if file_path:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    if inline_json:
        return json.loads(inline_json)
    return None


def _load_base_policy(args: argparse.Namespace) -> ReplayPolicy:
    payload = _load_payload(file_path=args.base_policy_file, inline_json=args.base_policy_json)
    return ReplayPolicy.from_payload(payload)


def _load_grid(args: argparse.Namespace) -> dict[str, list[Any]]:
    payload = _load_payload(file_path=args.grid_file, inline_json=args.grid_json)
    if payload is None:
        raise ValueError("A grid payload is required via --grid-file or --grid-json")
    if not isinstance(payload, dict):
        raise ValueError("Grid payload must be a JSON object")

    base_keys = ReplayPolicy.default().as_dict().keys()
    grid: dict[str, list[Any]] = {}
    for key, value in payload.items():
        if key not in base_keys:
            raise ValueError(f"Unknown replay policy key in grid: {key}")
        if isinstance(value, list):
            values = value
        else:
            values = [value]
        if not values:
            raise ValueError(f"Grid key {key} must have at least one value")
        grid[str(key)] = values
    if not grid:
        raise ValueError("Grid payload must include at least one varying key")
    return grid


def _iter_policy_overrides(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    return [
        dict(zip(keys, combo, strict=True))
        for combo in itertools.product(*(grid[key] for key in keys))
    ]


def _score_result(
    result: dict[str, Any],
    *,
    initial_bankroll_usd: float,
    drawdown_penalty: float,
    window_stddev_penalty: float,
    worst_window_penalty: float,
) -> float:
    pnl = float(result.get("total_pnl_usd") or 0.0)
    max_drawdown_pct = float(result.get("max_drawdown_pct") or 0.0)
    window_pnl_stddev_usd = float(result.get("window_pnl_stddev_usd") or 0.0)
    worst_window_pnl_usd = float(result.get("worst_window_pnl_usd") or 0.0)
    worst_window_loss_usd = max(-worst_window_pnl_usd, 0.0)
    return (
        pnl
        - (initial_bankroll_usd * drawdown_penalty * max_drawdown_pct)
        - (window_stddev_penalty * window_pnl_stddev_usd)
        - (worst_window_penalty * worst_window_loss_usd)
    )


def _constraint_failures(
    result: dict[str, Any],
    *,
    min_accepted_count: int,
    min_resolved_count: int,
    min_win_rate: float,
    max_drawdown_pct: float,
    min_worst_window_pnl_usd: float,
    max_worst_window_drawdown_pct: float,
) -> list[str]:
    failures: list[str] = []
    accepted_count = int(result.get("accepted_count") or 0)
    resolved_count = int(result.get("resolved_count") or 0)
    raw_win_rate = result.get("win_rate")
    win_rate = float(raw_win_rate) if raw_win_rate is not None else None
    drawdown_pct = float(result.get("max_drawdown_pct") or 0.0)
    worst_window_pnl_usd = float(result.get("worst_window_pnl_usd") or 0.0)
    worst_window_drawdown_pct = float(result.get("worst_window_drawdown_pct") or 0.0)

    if accepted_count < max(min_accepted_count, 0):
        failures.append("accepted_count")
    if resolved_count < max(min_resolved_count, 0):
        failures.append("resolved_count")
    if min_win_rate > 0 and (win_rate is None or win_rate < min_win_rate):
        failures.append("win_rate")
    if max_drawdown_pct > 0 and drawdown_pct > max_drawdown_pct:
        failures.append("max_drawdown_pct")
    if worst_window_pnl_usd < min_worst_window_pnl_usd:
        failures.append("worst_window_pnl_usd")
    if max_worst_window_drawdown_pct > 0 and worst_window_drawdown_pct > max_worst_window_drawdown_pct:
        failures.append("worst_window_drawdown_pct")
    return failures


def _compact_override_summary(payload: dict[str, Any]) -> str:
    if not payload:
        return "default"
    parts = [f"{key}={payload[key]}" for key in sorted(payload)]
    return ", ".join(parts)


def _print_ranked_summary(results: list[dict[str, Any]], *, top: int, title: str) -> None:
    print(title, file=sys.stderr)
    for index, row in enumerate(results[:top], start=1):
        failures = row.get("constraint_failures") or []
        feasibility_suffix = "" if not failures else f" | reject {','.join(str(value) for value in failures)}"
        window_count = int(row["result"].get("window_count") or 0)
        window_suffix = ""
        if window_count > 1:
            positive_window_count = int(row["result"].get("positive_window_count") or 0)
            worst_window_pnl_usd = float(row["result"].get("worst_window_pnl_usd") or 0.0)
            window_suffix = f" | windows {positive_window_count}/{window_count}+ | worst {worst_window_pnl_usd:+.2f}"
        print(
            "  "
            f"{index}. score {row['score']:+.2f} | pnl {row['result']['total_pnl_usd']:+.2f} | "
            f"dd {float(row['result'].get('max_drawdown_pct') or 0.0) * 100:.1f}% | "
            f"acc {int(row['result'].get('accepted_count') or 0)} | "
            f"win {float(row['result'].get('win_rate') or 0.0) * 100:.1f}% | "
            f"{_compact_override_summary(row['overrides'])}{window_suffix}{feasibility_suffix}",
            file=sys.stderr,
        )


def _evaluate_candidate(
    *,
    policy: ReplayPolicy,
    db_path: Path | None,
    label: str,
    notes: str,
    windows: list[tuple[int | None, int | None]],
) -> dict[str, Any]:
    if len(windows) == 1 and windows[0] == (None, None):
        return run_replay(
            policy=policy,
            db_path=db_path,
            label=label,
            notes=notes,
        )

    window_results: list[dict[str, Any]] = []
    for window_index, (start_ts, end_ts) in enumerate(windows, start=1):
        window_results.append(
            run_replay(
                policy=policy,
                db_path=db_path,
                label=f"{label}-w{window_index:02d}",
                notes=notes,
                start_ts=start_ts,
                end_ts=end_ts,
            )
        )
    return _aggregate_window_results(
        window_results,
        initial_bankroll_usd=policy.initial_bankroll_usd,
    )


def _resolve_db_path(raw_path: str) -> Path | None:
    return Path(raw_path) if raw_path else Path(TRADING_DB_PATH)


def _latest_trade_ts(*, db_path: Path | None, mode: str) -> int:
    target_path = db_path or Path(TRADING_DB_PATH)
    conn = sqlite3.connect(str(target_path))
    try:
        row = conn.execute(
            """
            SELECT MAX(placed_at)
            FROM trade_log
            WHERE COALESCE(source_action, 'buy')='buy'
              AND real_money=?
            """,
            (1 if mode == "live" else 0,),
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        raise ValueError("No replayable trades found for the selected mode")
    return int(row[0])


def _build_time_windows(
    *,
    db_path: Path | None,
    mode: str,
    window_days: int,
    window_count: int,
) -> list[tuple[int | None, int | None]]:
    if window_days <= 0 or window_count <= 1:
        return [(None, None)]

    window_seconds = max(window_days, 1) * 86400
    latest_ts = _latest_trade_ts(db_path=db_path, mode=mode)
    windows: list[tuple[int, int]] = []
    end_ts = latest_ts + 1
    for _ in range(max(window_count, 1)):
        start_ts = max(0, end_ts - window_seconds)
        windows.append((start_ts, end_ts))
        end_ts = start_ts
    windows.reverse()
    return windows


def _aggregate_window_results(
    window_results: list[dict[str, Any]],
    *,
    initial_bankroll_usd: float,
) -> dict[str, Any]:
    pnl_values = [float(row.get("total_pnl_usd") or 0.0) for row in window_results]
    drawdown_values = [float(row.get("max_drawdown_pct") or 0.0) for row in window_results]
    total_pnl = sum(float(row.get("total_pnl_usd") or 0.0) for row in window_results)
    accepted_count = sum(int(row.get("accepted_count") or 0) for row in window_results)
    resolved_count = sum(int(row.get("resolved_count") or 0) for row in window_results)
    rejected_count = sum(int(row.get("rejected_count") or 0) for row in window_results)
    unresolved_count = sum(int(row.get("unresolved_count") or 0) for row in window_results)
    trade_count = sum(int(row.get("trade_count") or 0) for row in window_results)
    weighted_wins = sum(
        float(row.get("win_rate") or 0.0) * int(row.get("resolved_count") or 0)
        for row in window_results
    )
    max_drawdown_pct = max(drawdown_values, default=0.0)
    positive_window_count = sum(1 for pnl in pnl_values if pnl > 0)
    negative_window_count = sum(1 for pnl in pnl_values if pnl < 0)
    window_avg_pnl_usd = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0
    window_pnl_stddev_usd = (
        math.sqrt(sum((value - window_avg_pnl_usd) ** 2 for value in pnl_values) / len(pnl_values))
        if pnl_values
        else 0.0
    )
    return {
        "window_count": len(window_results),
        "window_results": window_results,
        "initial_bankroll_usd": initial_bankroll_usd,
        "final_bankroll_usd": round(initial_bankroll_usd + total_pnl, 6),
        "total_pnl_usd": round(total_pnl, 6),
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "trade_count": trade_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "unresolved_count": unresolved_count,
        "resolved_count": resolved_count,
        "win_rate": round(weighted_wins / resolved_count, 6) if resolved_count else None,
        "positive_window_count": positive_window_count,
        "negative_window_count": negative_window_count,
        "window_avg_pnl_usd": round(window_avg_pnl_usd, 6),
        "window_pnl_stddev_usd": round(window_pnl_stddev_usd, 6),
        "worst_window_pnl_usd": round(min(pnl_values, default=0.0), 6),
        "best_window_pnl_usd": round(max(pnl_values, default=0.0), 6),
        "worst_window_drawdown_pct": round(max(drawdown_values, default=0.0), 6),
    }


def _ensure_table_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl_type in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")


def _ensure_search_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS replay_search_runs (
            id                            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at                    INTEGER NOT NULL,
            finished_at                   INTEGER NOT NULL,
            label_prefix                  TEXT NOT NULL DEFAULT '',
            status                        TEXT NOT NULL DEFAULT '',
            base_policy_json              TEXT NOT NULL DEFAULT '{}',
            grid_json                     TEXT NOT NULL DEFAULT '{}',
            constraints_json              TEXT NOT NULL DEFAULT '{}',
            notes                         TEXT NOT NULL DEFAULT '',
            window_days                   INTEGER NOT NULL DEFAULT 0,
            window_count                  INTEGER NOT NULL DEFAULT 1,
            drawdown_penalty              REAL NOT NULL DEFAULT 0,
            window_stddev_penalty         REAL NOT NULL DEFAULT 0,
            worst_window_penalty          REAL NOT NULL DEFAULT 0,
            candidate_count               INTEGER NOT NULL DEFAULT 0,
            feasible_count                INTEGER NOT NULL DEFAULT 0,
            rejected_count                INTEGER NOT NULL DEFAULT 0,
            current_candidate_score       REAL,
            current_candidate_feasible    INTEGER NOT NULL DEFAULT 0,
            current_candidate_total_pnl_usd REAL,
            current_candidate_max_drawdown_pct REAL,
            best_vs_current_pnl_usd       REAL,
            best_vs_current_score         REAL,
            best_feasible_candidate_index INTEGER,
            best_feasible_score           REAL,
            best_feasible_total_pnl_usd   REAL,
            best_feasible_max_drawdown_pct REAL
        );

        CREATE TABLE IF NOT EXISTS replay_search_candidates (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_search_run_id      INTEGER NOT NULL,
            candidate_index           INTEGER NOT NULL,
            score                     REAL NOT NULL DEFAULT 0,
            feasible                  INTEGER NOT NULL DEFAULT 0,
            constraint_failures_json  TEXT NOT NULL DEFAULT '[]',
            overrides_json            TEXT NOT NULL DEFAULT '{}',
            policy_json               TEXT NOT NULL DEFAULT '{}',
            config_json               TEXT NOT NULL DEFAULT '{}',
            result_json               TEXT NOT NULL DEFAULT '{}',
            total_pnl_usd             REAL NOT NULL DEFAULT 0,
            max_drawdown_pct          REAL,
            accepted_count            INTEGER NOT NULL DEFAULT 0,
            resolved_count            INTEGER NOT NULL DEFAULT 0,
            win_rate                  REAL,
            positive_window_count     INTEGER NOT NULL DEFAULT 0,
            negative_window_count     INTEGER NOT NULL DEFAULT 0,
            worst_window_pnl_usd      REAL,
            worst_window_drawdown_pct REAL,
            window_pnl_stddev_usd     REAL,
            FOREIGN KEY (replay_search_run_id) REFERENCES replay_search_runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_replay_search_runs_finished_at ON replay_search_runs(finished_at DESC);
        CREATE INDEX IF NOT EXISTS idx_replay_search_candidates_run_id ON replay_search_candidates(replay_search_run_id);
        """
    )
    _ensure_table_columns(
        conn,
        "replay_search_runs",
        {
            "status": "TEXT NOT NULL DEFAULT ''",
            "base_policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "grid_json": "TEXT NOT NULL DEFAULT '{}'",
            "constraints_json": "TEXT NOT NULL DEFAULT '{}'",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "window_days": "INTEGER NOT NULL DEFAULT 0",
            "window_count": "INTEGER NOT NULL DEFAULT 1",
            "drawdown_penalty": "REAL NOT NULL DEFAULT 0",
            "window_stddev_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_window_penalty": "REAL NOT NULL DEFAULT 0",
            "candidate_count": "INTEGER NOT NULL DEFAULT 0",
            "feasible_count": "INTEGER NOT NULL DEFAULT 0",
            "rejected_count": "INTEGER NOT NULL DEFAULT 0",
            "current_candidate_score": "REAL",
            "current_candidate_feasible": "INTEGER NOT NULL DEFAULT 0",
            "current_candidate_total_pnl_usd": "REAL",
            "current_candidate_max_drawdown_pct": "REAL",
            "best_vs_current_pnl_usd": "REAL",
            "best_vs_current_score": "REAL",
            "best_feasible_candidate_index": "INTEGER",
            "best_feasible_score": "REAL",
            "best_feasible_total_pnl_usd": "REAL",
            "best_feasible_max_drawdown_pct": "REAL",
        },
    )
    _ensure_table_columns(
        conn,
        "replay_search_candidates",
        {
            "feasible": "INTEGER NOT NULL DEFAULT 0",
            "is_current_policy": "INTEGER NOT NULL DEFAULT 0",
            "constraint_failures_json": "TEXT NOT NULL DEFAULT '[]'",
            "overrides_json": "TEXT NOT NULL DEFAULT '{}'",
            "policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "config_json": "TEXT NOT NULL DEFAULT '{}'",
            "result_json": "TEXT NOT NULL DEFAULT '{}'",
            "total_pnl_usd": "REAL NOT NULL DEFAULT 0",
            "max_drawdown_pct": "REAL",
            "accepted_count": "INTEGER NOT NULL DEFAULT 0",
            "resolved_count": "INTEGER NOT NULL DEFAULT 0",
            "win_rate": "REAL",
            "positive_window_count": "INTEGER NOT NULL DEFAULT 0",
            "negative_window_count": "INTEGER NOT NULL DEFAULT 0",
            "worst_window_pnl_usd": "REAL",
            "worst_window_drawdown_pct": "REAL",
            "window_pnl_stddev_usd": "REAL",
        },
    )


def _persist_search_results(
    *,
    db_path: Path | None,
    started_at: int,
    finished_at: int,
    label_prefix: str,
    notes: str,
    base_policy: ReplayPolicy,
    grid: dict[str, list[Any]],
    constraints: dict[str, Any],
    drawdown_penalty: float,
    window_stddev_penalty: float,
    worst_window_penalty: float,
    window_days: int,
    window_count: int,
    current_candidate: dict[str, Any] | None,
    persist_current_candidate: bool,
    ranked: list[dict[str, Any]],
    feasible: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> int:
    target_path = db_path or Path(TRADING_DB_PATH)
    conn = sqlite3.connect(str(target_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        _ensure_search_schema(conn)
        best_feasible = feasible[0] if feasible else None
        cursor = conn.execute(
            """
            INSERT INTO replay_search_runs (
                started_at, finished_at, label_prefix, status, base_policy_json, grid_json,
                constraints_json, notes, window_days, window_count, drawdown_penalty,
                window_stddev_penalty, worst_window_penalty, candidate_count, feasible_count,
                rejected_count, current_candidate_score, current_candidate_feasible,
                current_candidate_total_pnl_usd, current_candidate_max_drawdown_pct,
                best_vs_current_pnl_usd, best_vs_current_score,
                best_feasible_candidate_index, best_feasible_score,
                best_feasible_total_pnl_usd, best_feasible_max_drawdown_pct
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                started_at,
                finished_at,
                label_prefix,
                "completed",
                json.dumps(base_policy.as_dict(), sort_keys=True, separators=(",", ":")),
                json.dumps(grid, sort_keys=True, separators=(",", ":"), default=str),
                json.dumps(constraints, sort_keys=True, separators=(",", ":"), default=str),
                notes,
                window_days,
                window_count,
                drawdown_penalty,
                window_stddev_penalty,
                worst_window_penalty,
                len(ranked),
                len(feasible),
                len(rejected),
                float(current_candidate["score"]) if current_candidate else None,
                0 if current_candidate and current_candidate["constraint_failures"] else 1 if current_candidate else 0,
                float(current_candidate["result"].get("total_pnl_usd") or 0.0) if current_candidate else None,
                float(current_candidate["result"].get("max_drawdown_pct") or 0.0) if current_candidate else None,
                (
                    float(best_feasible["result"].get("total_pnl_usd") or 0.0)
                    - float(current_candidate["result"].get("total_pnl_usd") or 0.0)
                ) if best_feasible and current_candidate else None,
                (
                    float(best_feasible["score"]) - float(current_candidate["score"])
                ) if best_feasible and current_candidate else None,
                int(best_feasible["index"]) if best_feasible else None,
                float(best_feasible["score"]) if best_feasible else None,
                float(best_feasible["result"].get("total_pnl_usd") or 0.0) if best_feasible else None,
                float(best_feasible["result"].get("max_drawdown_pct") or 0.0) if best_feasible else None,
            ),
        )
        search_run_id = int(cursor.lastrowid)
        inserts = []
        if current_candidate and persist_current_candidate:
            current_result = current_candidate["result"]
            inserts.append(
                (
                    search_run_id,
                    0,
                    float(current_candidate["score"]),
                    0 if current_candidate["constraint_failures"] else 1,
                    1,
                    json.dumps(current_candidate["constraint_failures"], separators=(",", ":"), default=str),
                    json.dumps({}, separators=(",", ":"), default=str),
                    json.dumps(current_candidate["policy"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(current_candidate["config"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(current_result, sort_keys=True, separators=(",", ":"), default=str),
                    float(current_result.get("total_pnl_usd") or 0.0),
                    float(current_result.get("max_drawdown_pct") or 0.0),
                    int(current_result.get("accepted_count") or 0),
                    int(current_result.get("resolved_count") or 0),
                    float(current_result.get("win_rate") or 0.0) if current_result.get("win_rate") is not None else None,
                    int(current_result.get("positive_window_count") or 0),
                    int(current_result.get("negative_window_count") or 0),
                    float(current_result.get("worst_window_pnl_usd") or 0.0) if current_result.get("worst_window_pnl_usd") is not None else None,
                    float(current_result.get("worst_window_drawdown_pct") or 0.0) if current_result.get("worst_window_drawdown_pct") is not None else None,
                    float(current_result.get("window_pnl_stddev_usd") or 0.0) if current_result.get("window_pnl_stddev_usd") is not None else None,
                )
            )
        for row in ranked:
            result = row["result"]
            inserts.append(
                (
                    search_run_id,
                    int(row["index"]),
                    float(row["score"]),
                    0 if row["constraint_failures"] else 1,
                    0,
                    json.dumps(row["constraint_failures"], separators=(",", ":"), default=str),
                    json.dumps(row["overrides"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(row["policy"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(row["config"], sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(result, sort_keys=True, separators=(",", ":"), default=str),
                    float(result.get("total_pnl_usd") or 0.0),
                    float(result.get("max_drawdown_pct") or 0.0),
                    int(result.get("accepted_count") or 0),
                    int(result.get("resolved_count") or 0),
                    float(result.get("win_rate") or 0.0) if result.get("win_rate") is not None else None,
                    int(result.get("positive_window_count") or 0),
                    int(result.get("negative_window_count") or 0),
                    float(result.get("worst_window_pnl_usd") or 0.0) if result.get("worst_window_pnl_usd") is not None else None,
                    float(result.get("worst_window_drawdown_pct") or 0.0) if result.get("worst_window_drawdown_pct") is not None else None,
                    float(result.get("window_pnl_stddev_usd") or 0.0) if result.get("window_pnl_stddev_usd") is not None else None,
                )
            )
        if inserts:
            conn.executemany(
                """
                INSERT INTO replay_search_candidates (
                    replay_search_run_id, candidate_index, score, feasible, is_current_policy,
                    constraint_failures_json, overrides_json, policy_json, config_json, result_json,
                    total_pnl_usd, max_drawdown_pct, accepted_count, resolved_count,
                    win_rate, positive_window_count, negative_window_count,
                    worst_window_pnl_usd, worst_window_drawdown_pct, window_pnl_stddev_usd
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                inserts,
            )
        conn.commit()
        return search_run_id
    finally:
        conn.close()


def main() -> None:
    started_at = int(time.time())
    parser = argparse.ArgumentParser(description="Run a replay policy sweep over a parameter grid.")
    parser.add_argument("--db", default="", help="Path to a trading.db snapshot. Defaults to the runtime DB.")
    parser.add_argument("--label-prefix", default="sweep", help="Label prefix stored with each replay run.")
    parser.add_argument("--notes", default="", help="Optional notes stored with each replay run.")
    parser.add_argument("--base-policy-file", default="", help="JSON file with base replay policy overrides.")
    parser.add_argument("--base-policy-json", default="", help="Inline JSON payload with base replay policy overrides.")
    parser.add_argument("--grid-file", default="", help="JSON file describing the parameter grid to sweep.")
    parser.add_argument("--grid-json", default="", help="Inline JSON object describing the parameter grid to sweep.")
    parser.add_argument("--top", type=int, default=10, help="How many ranked candidates to print in the stderr summary.")
    parser.add_argument(
        "--drawdown-penalty",
        type=float,
        default=1.0,
        help="Penalty multiplier applied to max drawdown in bankroll-dollar terms when ranking candidates.",
    )
    parser.add_argument("--window-stddev-penalty", type=float, default=0.0, help="Penalty per dollar of cross-window P&L standard deviation.")
    parser.add_argument("--worst-window-penalty", type=float, default=0.0, help="Penalty per dollar of worst-window loss magnitude.")
    parser.add_argument("--max-combos", type=int, default=256, help="Safety cap on total grid combinations.")
    parser.add_argument("--window-days", type=int, default=0, help="Replay over rolling windows of this many days instead of the full history.")
    parser.add_argument("--window-count", type=int, default=1, help="How many most-recent rolling windows to evaluate when --window-days is set.")
    parser.add_argument("--min-positive-windows", type=int, default=0, help="Minimum count of positive-P&L windows required for feasibility.")
    parser.add_argument("--min-accepted-count", type=int, default=0, help="Minimum accepted trades required for a candidate to be feasible.")
    parser.add_argument("--min-resolved-count", type=int, default=0, help="Minimum resolved trades required for a candidate to be feasible.")
    parser.add_argument("--min-win-rate", type=float, default=0.0, help="Minimum replay win rate required for a candidate to be feasible.")
    parser.add_argument("--max-drawdown-pct", type=float, default=0.0, help="Maximum replay drawdown allowed for a candidate to be feasible.")
    parser.add_argument("--min-worst-window-pnl-usd", type=float, default=-1_000_000_000.0, help="Minimum allowed P&L for the worst replay window.")
    parser.add_argument("--max-worst-window-drawdown-pct", type=float, default=0.0, help="Maximum allowed drawdown for the worst replay window.")
    args = parser.parse_args()

    base_policy = _load_base_policy(args)
    grid = _load_grid(args)
    overrides_list = _iter_policy_overrides(grid)
    if len(overrides_list) > max(args.max_combos, 1):
        raise ValueError(f"Grid expands to {len(overrides_list)} combinations, above --max-combos={args.max_combos}")

    db_path = _resolve_db_path(args.db)
    windows = _build_time_windows(
        db_path=db_path,
        mode=base_policy.mode,
        window_days=max(args.window_days, 0),
        window_count=max(args.window_count, 1),
    )
    current_result = _evaluate_candidate(
        policy=base_policy,
        db_path=db_path,
        label=f"{args.label_prefix}-current",
        notes=args.notes,
        windows=windows,
    )
    current_constraint_failures = _constraint_failures(
        current_result,
        min_accepted_count=args.min_accepted_count,
        min_resolved_count=args.min_resolved_count,
        min_win_rate=max(args.min_win_rate, 0.0),
        max_drawdown_pct=max(args.max_drawdown_pct, 0.0),
        min_worst_window_pnl_usd=args.min_worst_window_pnl_usd,
        max_worst_window_drawdown_pct=max(args.max_worst_window_drawdown_pct, 0.0),
    )
    if int(current_result.get("positive_window_count") or 0) < max(args.min_positive_windows, 0):
        current_constraint_failures.append("positive_window_count")
    current_candidate = {
        "index": 0,
        "score": round(
            _score_result(
                current_result,
                initial_bankroll_usd=base_policy.initial_bankroll_usd,
                drawdown_penalty=max(args.drawdown_penalty, 0.0),
                window_stddev_penalty=max(args.window_stddev_penalty, 0.0),
                worst_window_penalty=max(args.worst_window_penalty, 0.0),
            ),
            6,
        ),
        "overrides": {},
        "policy": base_policy.as_dict(),
        "config": policy_to_config_payload(base_policy),
        "result": current_result,
        "constraint_failures": current_constraint_failures,
        "is_current_policy": True,
        "policy_version": base_policy.version(),
    }
    candidates: list[dict[str, Any]] = []
    for index, overrides in enumerate(overrides_list, start=1):
        policy_payload = base_policy.as_dict()
        policy_payload.update(overrides)
        policy = ReplayPolicy.from_payload(policy_payload)
        policy_version = policy.version()
        result = current_result if policy_version == current_candidate["policy_version"] else _evaluate_candidate(
            policy=policy,
            db_path=db_path,
            label=f"{args.label_prefix}-{index:03d}",
            notes=args.notes,
            windows=windows,
        )
        score = _score_result(
            result,
            initial_bankroll_usd=policy.initial_bankroll_usd,
            drawdown_penalty=max(args.drawdown_penalty, 0.0),
            window_stddev_penalty=max(args.window_stddev_penalty, 0.0),
            worst_window_penalty=max(args.worst_window_penalty, 0.0),
        )
        constraint_failures = _constraint_failures(
            result,
            min_accepted_count=args.min_accepted_count,
            min_resolved_count=args.min_resolved_count,
            min_win_rate=max(args.min_win_rate, 0.0),
            max_drawdown_pct=max(args.max_drawdown_pct, 0.0),
            min_worst_window_pnl_usd=args.min_worst_window_pnl_usd,
            max_worst_window_drawdown_pct=max(args.max_worst_window_drawdown_pct, 0.0),
        )
        if int(result.get("positive_window_count") or 0) < max(args.min_positive_windows, 0):
            constraint_failures.append("positive_window_count")
        candidates.append(
            {
                "index": index,
                "score": round(score, 6),
                "overrides": overrides,
                "policy": policy.as_dict(),
                "config": policy_to_config_payload(policy),
                "result": result,
                "constraint_failures": constraint_failures,
                "is_current_policy": False,
                "policy_version": policy_version,
            }
        )

    current_matches_grid = any(row["policy_version"] == current_candidate["policy_version"] for row in candidates)
    ranked = sorted(
        candidates,
        key=lambda row: (
            float(row["score"]),
            float(row["result"].get("total_pnl_usd") or 0.0),
            -float(row["result"].get("max_drawdown_pct") or 0.0),
            float(row["result"].get("win_rate") or 0.0),
        ),
        reverse=True,
    )
    feasible = [row for row in ranked if not row["constraint_failures"]]
    rejected = [row for row in ranked if row["constraint_failures"]]
    constraints = {
        "min_accepted_count": max(args.min_accepted_count, 0),
        "min_resolved_count": max(args.min_resolved_count, 0),
        "min_win_rate": max(args.min_win_rate, 0.0),
        "max_drawdown_pct": max(args.max_drawdown_pct, 0.0),
        "min_positive_windows": max(args.min_positive_windows, 0),
        "min_worst_window_pnl_usd": args.min_worst_window_pnl_usd,
        "max_worst_window_drawdown_pct": max(args.max_worst_window_drawdown_pct, 0.0),
    }
    finished_at = int(time.time())
    search_run_id = _persist_search_results(
        db_path=db_path,
        started_at=started_at,
        finished_at=finished_at,
        label_prefix=args.label_prefix,
        notes=args.notes,
        base_policy=base_policy,
        grid=grid,
        constraints=constraints,
        drawdown_penalty=max(args.drawdown_penalty, 0.0),
        window_stddev_penalty=max(args.window_stddev_penalty, 0.0),
        worst_window_penalty=max(args.worst_window_penalty, 0.0),
        window_days=max(args.window_days, 0),
        window_count=max(args.window_count, 1),
        current_candidate=current_candidate,
        persist_current_candidate=not current_matches_grid,
        ranked=ranked,
        feasible=feasible,
        rejected=rejected,
    )
    print(
        json.dumps(
            {
                "search_run_id": search_run_id,
                "base_policy": base_policy.as_dict(),
                "grid": grid,
                "windows": [{"start_ts": start_ts, "end_ts": end_ts} for start_ts, end_ts in windows],
                "drawdown_penalty": max(args.drawdown_penalty, 0.0),
                "window_stddev_penalty": max(args.window_stddev_penalty, 0.0),
                "worst_window_penalty": max(args.worst_window_penalty, 0.0),
                "constraints": constraints,
                "candidate_count": len(ranked),
                "feasible_count": len(feasible),
                "rejected_count": len(rejected),
                "current_candidate_matches_grid": current_matches_grid,
                "current_candidate": current_candidate,
                "best_feasible_config": feasible[0]["config"] if feasible else None,
                "best_vs_current_pnl_usd": (
                    float(feasible[0]["result"].get("total_pnl_usd") or 0.0)
                    - float(current_candidate["result"].get("total_pnl_usd") or 0.0)
                ) if feasible else None,
                "best_vs_current_score": (
                    float(feasible[0]["score"]) - float(current_candidate["score"])
                ) if feasible else None,
                "best_feasible": feasible[0] if feasible else None,
                "ranked": ranked,
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(file=sys.stderr)
    _print_ranked_summary(feasible if feasible else ranked, top=max(args.top, 1), title="Replay sweep top candidates:")
    if rejected:
        print(file=sys.stderr)
        _print_ranked_summary(rejected, top=min(max(args.top, 1), len(rejected)), title="Replay sweep rejected candidates:")


if __name__ == "__main__":
    main()
