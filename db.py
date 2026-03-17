from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path("data/trading.db")


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_table_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, ddl_type in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")


def _ensure_positions_schema(conn: sqlite3.Connection) -> None:
    info = conn.execute("PRAGMA table_info(positions)").fetchall()
    if not info:
        return

    pk_columns = [row["name"] for row in sorted(info, key=lambda row: int(row["pk"] or 0)) if int(row["pk"] or 0) > 0]
    if pk_columns == ["market_id", "token_id", "side", "real_money"]:
        return

    conn.executescript(
        """
        DROP TABLE IF EXISTS positions_legacy;
        ALTER TABLE positions RENAME TO positions_legacy;
        CREATE TABLE positions (
            market_id   TEXT NOT NULL,
            side        TEXT NOT NULL,
            size_usd    REAL NOT NULL,
            avg_price   REAL NOT NULL,
            token_id    TEXT NOT NULL,
            entered_at  INTEGER NOT NULL,
            real_money  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (market_id, token_id, side, real_money)
        );
        INSERT OR REPLACE INTO positions (
            market_id, side, size_usd, avg_price, token_id, entered_at, real_money
        )
        SELECT
            market_id,
            side,
            size_usd,
            avg_price,
            COALESCE(token_id, ''),
            entered_at,
            COALESCE(real_money, 0)
        FROM positions_legacy;
        DROP TABLE positions_legacy;
        """
    )


def init_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS seen_trades (
            trade_id   TEXT PRIMARY KEY,
            market_id  TEXT NOT NULL,
            trader_id  TEXT NOT NULL,
            seen_at    INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trade_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id            TEXT NOT NULL,
            market_id           TEXT NOT NULL,
            question            TEXT,
            trader_address      TEXT NOT NULL,
            trader_name         TEXT,
            side                TEXT NOT NULL,
            token_id            TEXT,
            source_action       TEXT,
            source_ts           INTEGER,
            source_ts_raw       TEXT,
            observed_at         INTEGER,
            poll_started_at     INTEGER,
            market_close_ts     INTEGER,
            metadata_fetched_at INTEGER,
            orderbook_fetched_at INTEGER,
            source_latency_s    REAL,
            observation_latency_s REAL,
            processing_latency_s REAL,
            source_shares       REAL,
            source_amount_usd   REAL,
            source_trade_json   TEXT,
            market_metadata_json TEXT,
            orderbook_json      TEXT,
            snapshot_json       TEXT,
            price_at_signal     REAL NOT NULL,
            signal_size_usd     REAL NOT NULL,
            actual_entry_price  REAL,
            actual_entry_shares REAL,
            actual_entry_size_usd REAL,
            confidence          REAL NOT NULL,
            raw_confidence      REAL,
            kelly_fraction      REAL NOT NULL,
            signal_mode         TEXT,
            belief_prior        REAL,
            belief_blend        REAL,
            belief_evidence     INTEGER,
            trader_score        REAL,
            market_score        REAL,
            market_veto         TEXT,
            real_money          INTEGER NOT NULL DEFAULT 0,
            order_id            TEXT,
            skipped             INTEGER NOT NULL DEFAULT 0,
            skip_reason         TEXT,
            placed_at           INTEGER NOT NULL,
            resolved_at         INTEGER,
            exited_at          INTEGER,
            exit_trade_id      TEXT,
            exit_price         REAL,
            exit_shares        REAL,
            exit_size_usd      REAL,
            exit_order_id      TEXT,
            exit_reason        TEXT,
            outcome             INTEGER,
            market_resolved_outcome TEXT,
            counterfactual_return REAL,
            shadow_pnl_usd      REAL,
            actual_pnl_usd      REAL,
            resolution_json     TEXT,
            f_trader_win_rate   REAL,
            f_trader_n_trades   INTEGER,
            f_conviction_ratio  REAL,
            f_trader_volume_usd REAL,
            f_trader_avg_size_usd REAL,
            f_account_age_days  INTEGER,
            f_consistency       REAL,
            f_trader_diversity  INTEGER,
            f_days_to_res       REAL,
            f_price             REAL,
            f_spread_pct        REAL,
            f_momentum_1h       REAL,
            f_volume_24h_usd    REAL,
            f_volume_7d_avg_usd REAL,
            f_volume_trend      REAL,
            f_oi_usd            REAL,
            f_top_holder_pct    REAL,
            f_bid_depth_usd     REAL,
            f_ask_depth_usd     REAL,
            market_components_json TEXT,
            decision_context_json TEXT
        );

        CREATE TABLE IF NOT EXISTS positions (
            market_id   TEXT NOT NULL,
            side        TEXT NOT NULL,
            size_usd    REAL NOT NULL,
            avg_price   REAL NOT NULL,
            token_id    TEXT NOT NULL,
            entered_at  INTEGER NOT NULL,
            real_money  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (market_id, token_id, side, real_money)
        );

        CREATE TABLE IF NOT EXISTS trader_cache (
            trader_address TEXT PRIMARY KEY,
            win_rate       REAL NOT NULL,
            n_trades       INTEGER NOT NULL,
            consistency    REAL NOT NULL,
            volume_usd     REAL NOT NULL,
            avg_size_usd   REAL NOT NULL,
            diversity      INTEGER NOT NULL,
            account_age_d  INTEGER NOT NULL,
            wins           INTEGER NOT NULL DEFAULT 0,
            ties           INTEGER NOT NULL DEFAULT 0,
            realized_pnl_usd REAL NOT NULL DEFAULT 0,
            avg_return     REAL NOT NULL DEFAULT 0,
            open_positions INTEGER NOT NULL DEFAULT 0,
            open_value_usd REAL NOT NULL DEFAULT 0,
            open_pnl_usd   REAL NOT NULL DEFAULT 0,
            updated_at     INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS model_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            trained_at      INTEGER NOT NULL,
            n_samples       INTEGER NOT NULL,
            brier_score     REAL NOT NULL,
            log_loss        REAL NOT NULL,
            feature_cols    TEXT NOT NULL,
            model_path      TEXT NOT NULL,
            deployed        INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS perf_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at     INTEGER NOT NULL,
            mode            TEXT NOT NULL,
            n_signals       INTEGER NOT NULL,
            n_acted         INTEGER NOT NULL,
            n_resolved      INTEGER NOT NULL,
            win_rate        REAL,
            total_pnl_usd   REAL,
            avg_confidence  REAL,
            sharpe          REAL
        );

        CREATE TABLE IF NOT EXISTS belief_priors (
            feature_name TEXT NOT NULL,
            bucket       TEXT NOT NULL,
            wins         REAL NOT NULL DEFAULT 0,
            losses       REAL NOT NULL DEFAULT 0,
            updated_at   INTEGER NOT NULL,
            PRIMARY KEY (feature_name, bucket)
        );

        CREATE TABLE IF NOT EXISTS belief_updates (
            trade_log_id INTEGER PRIMARY KEY,
            applied_at   INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_seen_trades_seen_at ON seen_trades(seen_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_placed_at ON trade_log(placed_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_outcome ON trade_log(outcome);
        CREATE INDEX IF NOT EXISTS idx_trade_log_trader ON trade_log(trader_address);
        CREATE INDEX IF NOT EXISTS idx_trade_log_real_money ON trade_log(real_money);
        CREATE INDEX IF NOT EXISTS idx_trade_log_skipped ON trade_log(skipped);
        CREATE INDEX IF NOT EXISTS idx_belief_updates_applied_at ON belief_updates(applied_at);
        """
    )
    _ensure_table_columns(
        conn,
        "trader_cache",
        {
            "wins": "INTEGER NOT NULL DEFAULT 0",
            "ties": "INTEGER NOT NULL DEFAULT 0",
            "realized_pnl_usd": "REAL NOT NULL DEFAULT 0",
            "avg_return": "REAL NOT NULL DEFAULT 0",
            "open_positions": "INTEGER NOT NULL DEFAULT 0",
            "open_value_usd": "REAL NOT NULL DEFAULT 0",
            "open_pnl_usd": "REAL NOT NULL DEFAULT 0",
        },
    )
    _ensure_table_columns(
        conn,
        "trade_log",
        {
            "trader_name": "TEXT",
            "token_id": "TEXT",
            "source_action": "TEXT",
            "source_ts": "INTEGER",
            "source_ts_raw": "TEXT",
            "observed_at": "INTEGER",
            "poll_started_at": "INTEGER",
            "market_close_ts": "INTEGER",
            "metadata_fetched_at": "INTEGER",
            "orderbook_fetched_at": "INTEGER",
            "source_latency_s": "REAL",
            "observation_latency_s": "REAL",
            "processing_latency_s": "REAL",
            "source_shares": "REAL",
            "source_amount_usd": "REAL",
            "source_trade_json": "TEXT",
            "market_metadata_json": "TEXT",
            "orderbook_json": "TEXT",
            "snapshot_json": "TEXT",
            "actual_entry_price": "REAL",
            "actual_entry_shares": "REAL",
            "actual_entry_size_usd": "REAL",
            "raw_confidence": "REAL",
            "signal_mode": "TEXT",
            "belief_prior": "REAL",
            "belief_blend": "REAL",
            "belief_evidence": "INTEGER",
            "trader_score": "REAL",
            "market_score": "REAL",
            "market_veto": "TEXT",
            "market_resolved_outcome": "TEXT",
            "counterfactual_return": "REAL",
            "resolution_json": "TEXT",
            "exited_at": "INTEGER",
            "exit_trade_id": "TEXT",
            "exit_price": "REAL",
            "exit_shares": "REAL",
            "exit_size_usd": "REAL",
            "exit_order_id": "TEXT",
            "exit_reason": "TEXT",
            "f_trader_avg_size_usd": "REAL",
            "f_trader_diversity": "INTEGER",
            "f_volume_24h_usd": "REAL",
            "f_volume_7d_avg_usd": "REAL",
            "f_top_holder_pct": "REAL",
            "market_components_json": "TEXT",
            "decision_context_json": "TEXT",
        },
    )
    _ensure_positions_schema(conn)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
