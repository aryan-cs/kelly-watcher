from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path

from market_urls import market_url_from_metadata
from runtime_paths import TRADING_DB_PATH

DB_PATH = TRADING_DB_PATH
REPAIR_BATCH_SIZE = 250
logger = logging.getLogger(__name__)
_SHARED_HOLDOUT_MESSAGE_RE = re.compile(
    r"shared holdout ll/brier:\s*([-+]?[0-9]*\.?[0-9]+)\s*/\s*([-+]?[0-9]*\.?[0-9]+).*?"
    r"incumbent ll/brier:\s*([-+]?[0-9]*\.?[0-9]+)\s*/\s*([-+]?[0-9]*\.?[0-9]+)",
    re.IGNORECASE | re.DOTALL,
)


def _resolved_db_path_text(path: Path) -> str:
    raw = os.fspath(path)
    try:
        return str(path.resolve(strict=False))
    except Exception:
        return os.path.abspath(raw)


def _preferred_journal_mode(path: Path) -> str:
    raw = os.fspath(path)
    if raw.startswith("\\\\"):
        return "DELETE"
    resolved = _resolved_db_path_text(path)
    if resolved.startswith("\\\\"):
        return "DELETE"
    return "WAL"


def _startup_heavy_maintenance_enabled(path: Path) -> bool:
    raw = os.fspath(path)
    if raw.startswith("\\\\"):
        return False
    resolved = _resolved_db_path_text(path)
    return not resolved.startswith("\\\\")


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA journal_mode={_preferred_journal_mode(DB_PATH)}")
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


def _repair_trade_log_market_urls(conn: sqlite3.Connection) -> None:
    cursor = conn.execute(
        """
        SELECT id, market_url, market_metadata_json
        FROM trade_log
        WHERE market_metadata_json IS NOT NULL
          AND market_metadata_json <> ''
        ORDER BY id ASC
        """
    )

    scanned = 0
    updated = 0
    while True:
        rows = cursor.fetchmany(REPAIR_BATCH_SIZE)
        if not rows:
            break

        batch_updates: list[tuple[str, int]] = []
        for row in rows:
            scanned += 1
            raw_meta = str(row["market_metadata_json"] or "").strip()
            if not raw_meta:
                continue
            try:
                meta = json.loads(raw_meta)
            except Exception:
                continue
            canonical_url = market_url_from_metadata(meta)
            if not canonical_url:
                continue
            existing_url = str(row["market_url"] or "").strip()
            if canonical_url == existing_url:
                continue
            batch_updates.append((canonical_url, int(row["id"])))

        if batch_updates:
            conn.executemany("UPDATE trade_log SET market_url=? WHERE id=?", batch_updates)
            updated += len(batch_updates)

        if scanned and scanned % 5000 == 0:
            logger.info(
                "Market URL repair progress: scanned=%s updated=%s",
                scanned,
                updated,
            )

    if scanned:
        logger.info(
            "Market URL repair complete: scanned=%s updated=%s",
            scanned,
            updated,
        )


def _backfill_retrain_runs_from_model_history(conn: sqlite3.Connection) -> None:
    existing_finished = {
        int(row["finished_at"])
        for row in conn.execute(
            """
            SELECT finished_at
            FROM retrain_runs
            WHERE LOWER(COALESCE(status, ''))='deployed'
            """
        ).fetchall()
    }
    rows = conn.execute(
        """
        SELECT trained_at, n_samples, brier_score, log_loss, deployed
        FROM model_history
        ORDER BY trained_at ASC
        """
    ).fetchall()
    inserts: list[tuple[int, int, str, str, int, int, int, int, float, float, str]] = []
    for row in rows:
        trained_at = int(row["trained_at"] or 0)
        if trained_at <= 0 or trained_at in existing_finished:
            continue
        inserts.append(
            (
                trained_at,
                trained_at,
                "backfill",
                "deployed",
                1,
                int(row["deployed"] or 0),
                int(row["n_samples"] or 0),
                0,
                float(row["brier_score"]),
                float(row["log_loss"]),
                "Backfilled from model_history",
            )
        )

    if inserts:
        conn.executemany(
            """
            INSERT INTO retrain_runs (
                started_at, finished_at, trigger, status, ok, deployed,
                sample_count, min_samples, brier_score, log_loss, message
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            inserts,
        )


def _parse_shared_holdout_metrics(message: str) -> tuple[float, float, float, float] | None:
    match = _SHARED_HOLDOUT_MESSAGE_RE.search(message)
    if not match:
        return None
    try:
        challenger_ll, challenger_brier, incumbent_ll, incumbent_brier = match.groups()
        return (
            float(challenger_ll),
            float(challenger_brier),
            float(incumbent_ll),
            float(incumbent_brier),
        )
    except (TypeError, ValueError):
        return None


def _backfill_retrain_run_shared_holdout_metrics(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, message
        FROM retrain_runs
        WHERE challenger_shared_log_loss IS NULL
          AND challenger_shared_brier_score IS NULL
          AND incumbent_log_loss IS NULL
          AND incumbent_brier_score IS NULL
          AND LOWER(COALESCE(message, '')) LIKE '%shared holdout ll/brier:%'
          AND LOWER(COALESCE(message, '')) LIKE '%incumbent ll/brier:%'
        """
    ).fetchall()
    updates: list[tuple[float, float, float, float, int]] = []
    for row in rows:
        parsed = _parse_shared_holdout_metrics(str(row["message"] or ""))
        if parsed is None:
            continue
        challenger_ll, challenger_brier, incumbent_ll, incumbent_brier = parsed
        updates.append(
            (
                challenger_ll,
                challenger_brier,
                incumbent_ll,
                incumbent_brier,
                int(row["id"]),
            )
        )
    if updates:
        conn.executemany(
            """
            UPDATE retrain_runs
            SET challenger_shared_log_loss=?,
                challenger_shared_brier_score=?,
                incumbent_log_loss=?,
                incumbent_brier_score=?
            WHERE id=?
            """,
            updates,
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
            market_url          TEXT,
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
            entry_fee_rate_bps  REAL NOT NULL DEFAULT 0,
            entry_fee_usd       REAL NOT NULL DEFAULT 0,
            entry_fee_shares    REAL NOT NULL DEFAULT 0,
            entry_fixed_cost_usd REAL NOT NULL DEFAULT 0,
            entry_gross_price   REAL,
            entry_gross_shares  REAL,
            entry_gross_size_usd REAL,
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
            label_applied_at    INTEGER,
            exited_at          INTEGER,
            exit_trade_id      TEXT,
            exit_price         REAL,
            exit_shares        REAL,
            exit_size_usd      REAL,
            exit_fee_rate_bps  REAL NOT NULL DEFAULT 0,
            exit_fee_usd       REAL NOT NULL DEFAULT 0,
            exit_fixed_cost_usd REAL NOT NULL DEFAULT 0,
            exit_gross_price   REAL,
            exit_gross_shares  REAL,
            exit_gross_size_usd REAL,
            exit_order_id      TEXT,
            exit_reason        TEXT,
            remaining_entry_shares REAL,
            remaining_entry_size_usd REAL,
            remaining_source_shares REAL,
            realized_exit_shares REAL NOT NULL DEFAULT 0,
            realized_exit_size_usd REAL NOT NULL DEFAULT 0,
            realized_exit_pnl_usd REAL NOT NULL DEFAULT 0,
            partial_exit_count INTEGER NOT NULL DEFAULT 0,
            resolution_fixed_cost_usd REAL NOT NULL DEFAULT 0,
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

        CREATE TABLE IF NOT EXISTS retrain_runs (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at            INTEGER NOT NULL,
            finished_at           INTEGER NOT NULL,
            trigger               TEXT NOT NULL DEFAULT '',
            status                TEXT NOT NULL DEFAULT '',
            ok                    INTEGER NOT NULL DEFAULT 0,
            deployed              INTEGER NOT NULL DEFAULT 0,
            sample_count          INTEGER NOT NULL DEFAULT 0,
            min_samples           INTEGER NOT NULL DEFAULT 0,
            brier_score           REAL,
            log_loss              REAL,
            candidate_name        TEXT,
            candidate_count       INTEGER,
            search_beats_baseline INTEGER,
            search_total_pnl      REAL,
            val_selected_trades   INTEGER,
            val_total_pnl         REAL,
            challenger_shared_log_loss    REAL,
            challenger_shared_brier_score REAL,
            incumbent_log_loss            REAL,
            incumbent_brier_score         REAL,
            message               TEXT NOT NULL DEFAULT ''
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

        CREATE TABLE IF NOT EXISTS replay_runs (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at              INTEGER NOT NULL,
            finished_at             INTEGER NOT NULL,
            label                   TEXT NOT NULL DEFAULT '',
            mode                    TEXT NOT NULL DEFAULT 'shadow',
            status                  TEXT NOT NULL DEFAULT '',
            policy_version          TEXT NOT NULL DEFAULT '',
            policy_json             TEXT NOT NULL DEFAULT '{}',
            notes                   TEXT NOT NULL DEFAULT '',
            window_start_ts         INTEGER,
            window_end_ts           INTEGER,
            initial_bankroll_usd    REAL NOT NULL DEFAULT 0,
            final_bankroll_usd      REAL NOT NULL DEFAULT 0,
            total_pnl_usd           REAL NOT NULL DEFAULT 0,
            max_drawdown_pct        REAL,
            trade_count             INTEGER NOT NULL DEFAULT 0,
            accepted_count          INTEGER NOT NULL DEFAULT 0,
            rejected_count          INTEGER NOT NULL DEFAULT 0,
            unresolved_count        INTEGER NOT NULL DEFAULT 0,
            resolved_count          INTEGER NOT NULL DEFAULT 0,
            win_rate                REAL
        );

        CREATE TABLE IF NOT EXISTS replay_trades (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_run_id           INTEGER NOT NULL,
            trade_log_id            INTEGER NOT NULL,
            trade_id                TEXT NOT NULL DEFAULT '',
            placed_at               INTEGER NOT NULL DEFAULT 0,
            market_id               TEXT NOT NULL DEFAULT '',
            trader_address          TEXT NOT NULL DEFAULT '',
            signal_mode             TEXT NOT NULL DEFAULT '',
            decision                TEXT NOT NULL DEFAULT '',
            reason                  TEXT NOT NULL DEFAULT '',
            source_status           TEXT NOT NULL DEFAULT '',
            entry_price             REAL,
            requested_size_usd      REAL,
            simulated_size_usd      REAL NOT NULL DEFAULT 0,
            return_pct              REAL,
            pnl_usd                 REAL,
            bankroll_after_usd      REAL,
            open_exposure_after_usd REAL,
            metadata_json           TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (replay_run_id) REFERENCES replay_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS segment_metrics (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_run_id    INTEGER NOT NULL,
            segment_kind     TEXT NOT NULL,
            segment_value    TEXT NOT NULL,
            trade_count      INTEGER NOT NULL DEFAULT 0,
            accepted_count   INTEGER NOT NULL DEFAULT 0,
            resolved_count   INTEGER NOT NULL DEFAULT 0,
            total_pnl_usd    REAL NOT NULL DEFAULT 0,
            win_rate         REAL,
            avg_return_pct   REAL,
            FOREIGN KEY (replay_run_id) REFERENCES replay_runs(id) ON DELETE CASCADE
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

        CREATE TABLE IF NOT EXISTS wallet_cursors (
            wallet_address    TEXT PRIMARY KEY,
            last_source_ts    INTEGER NOT NULL DEFAULT 0,
            last_trade_ids_json TEXT NOT NULL DEFAULT '[]',
            updated_at        INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wallet_watch_state (
            wallet_address           TEXT PRIMARY KEY,
            status                   TEXT NOT NULL DEFAULT 'active',
            status_reason            TEXT,
            dropped_at               INTEGER,
            reactivated_at           INTEGER,
            tracking_started_at      INTEGER NOT NULL DEFAULT 0,
            last_source_ts_at_status INTEGER NOT NULL DEFAULT 0,
            updated_at               INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wallet_policy_metrics (
            wallet_address                       TEXT PRIMARY KEY,
            total_buy_signals                    INTEGER NOT NULL DEFAULT 0,
            resolved_copied_count                INTEGER NOT NULL DEFAULT 0,
            resolved_copied_wins                 INTEGER NOT NULL DEFAULT 0,
            resolved_copied_win_rate             REAL,
            resolved_copied_avg_return           REAL,
            resolved_copied_total_pnl_usd        REAL NOT NULL DEFAULT 0,
            recent_window_seconds                INTEGER NOT NULL DEFAULT 0,
            recent_resolved_copied_count         INTEGER NOT NULL DEFAULT 0,
            recent_resolved_copied_wins          INTEGER NOT NULL DEFAULT 0,
            recent_resolved_copied_win_rate      REAL,
            recent_resolved_copied_avg_return    REAL,
            recent_resolved_copied_total_pnl_usd REAL NOT NULL DEFAULT 0,
            last_resolved_at                     INTEGER NOT NULL DEFAULT 0,
            local_quality_score                  REAL,
            local_weight                         REAL NOT NULL DEFAULT 0,
            local_drop_ready                     INTEGER NOT NULL DEFAULT 0,
            local_drop_reason                    TEXT,
            updated_at                           INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS exit_audits (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            audited_at                INTEGER NOT NULL,
            market_id                 TEXT NOT NULL DEFAULT '',
            token_id                  TEXT NOT NULL DEFAULT '',
            side                      TEXT NOT NULL DEFAULT '',
            real_money                INTEGER NOT NULL DEFAULT 0,
            trader_address            TEXT NOT NULL DEFAULT '',
            question                  TEXT NOT NULL DEFAULT '',
            strategy                  TEXT NOT NULL DEFAULT '',
            decision                  TEXT NOT NULL DEFAULT '',
            reason                    TEXT NOT NULL DEFAULT '',
            estimated_return_pct      REAL,
            loss_limit_pct            REAL,
            hard_exit_loss_pct        REAL,
            open_size_usd             REAL,
            open_shares               REAL,
            quoted_price              REAL,
            best_bid                  REAL,
            best_ask                  REAL,
            bid_depth_usd             REAL,
            ask_depth_usd             REAL,
            market_score              REAL,
            market_veto               TEXT,
            time_to_close_seconds     REAL,
            avg_entry_price           REAL,
            avg_entry_confidence      REAL,
            avg_entry_edge            REAL,
            avg_entry_market_score    REAL,
            signal_mode               TEXT,
            metadata_json             TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS trade_log_manual_edits (
            trade_log_id INTEGER PRIMARY KEY,
            entry_price  REAL,
            shares       REAL,
            size_usd     REAL,
            status       TEXT,
            updated_at   INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS position_manual_edits (
            market_id   TEXT NOT NULL,
            token_id    TEXT NOT NULL DEFAULT '',
            side        TEXT NOT NULL,
            real_money  INTEGER NOT NULL DEFAULT 0,
            entry_price REAL,
            shares      REAL,
            size_usd    REAL,
            status      TEXT,
            updated_at  INTEGER NOT NULL,
            PRIMARY KEY (market_id, token_id, side, real_money)
        );

        CREATE INDEX IF NOT EXISTS idx_seen_trades_seen_at ON seen_trades(seen_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_placed_at ON trade_log(placed_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_outcome ON trade_log(outcome);
        CREATE INDEX IF NOT EXISTS idx_trade_log_trader ON trade_log(trader_address);
        CREATE INDEX IF NOT EXISTS idx_trade_log_real_money ON trade_log(real_money);
        CREATE INDEX IF NOT EXISTS idx_trade_log_skipped ON trade_log(skipped);
        CREATE INDEX IF NOT EXISTS idx_belief_updates_applied_at ON belief_updates(applied_at);
        CREATE INDEX IF NOT EXISTS idx_wallet_watch_state_status ON wallet_watch_state(status);
        CREATE INDEX IF NOT EXISTS idx_wallet_policy_metrics_drop_ready ON wallet_policy_metrics(local_drop_ready);
        CREATE INDEX IF NOT EXISTS idx_exit_audits_audited_at ON exit_audits(audited_at DESC);
        CREATE INDEX IF NOT EXISTS idx_exit_audits_market_side ON exit_audits(market_id, token_id, side, real_money);
        CREATE INDEX IF NOT EXISTS idx_retrain_runs_finished_at ON retrain_runs(finished_at DESC);
        CREATE INDEX IF NOT EXISTS idx_replay_runs_finished_at ON replay_runs(finished_at DESC);
        CREATE INDEX IF NOT EXISTS idx_replay_trades_run_id ON replay_trades(replay_run_id);
        CREATE INDEX IF NOT EXISTS idx_replay_trades_trade_log_id ON replay_trades(trade_log_id);
        CREATE INDEX IF NOT EXISTS idx_segment_metrics_run_kind ON segment_metrics(replay_run_id, segment_kind);
        """
    )
    _ensure_table_columns(
        conn,
        "replay_runs",
        {
            "window_start_ts": "INTEGER",
            "window_end_ts": "INTEGER",
        },
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
            "market_url": "TEXT",
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
            "entry_fee_rate_bps": "REAL NOT NULL DEFAULT 0",
            "entry_fee_usd": "REAL NOT NULL DEFAULT 0",
            "entry_fee_shares": "REAL NOT NULL DEFAULT 0",
            "entry_fixed_cost_usd": "REAL NOT NULL DEFAULT 0",
            "entry_gross_price": "REAL",
            "entry_gross_shares": "REAL",
            "entry_gross_size_usd": "REAL",
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
            "label_applied_at": "INTEGER",
            "resolution_json": "TEXT",
            "exited_at": "INTEGER",
            "exit_trade_id": "TEXT",
            "exit_price": "REAL",
            "exit_shares": "REAL",
            "exit_size_usd": "REAL",
            "exit_fee_rate_bps": "REAL NOT NULL DEFAULT 0",
            "exit_fee_usd": "REAL NOT NULL DEFAULT 0",
            "exit_fixed_cost_usd": "REAL NOT NULL DEFAULT 0",
            "exit_gross_price": "REAL",
            "exit_gross_shares": "REAL",
            "exit_gross_size_usd": "REAL",
            "exit_order_id": "TEXT",
            "exit_reason": "TEXT",
            "remaining_entry_shares": "REAL",
            "remaining_entry_size_usd": "REAL",
            "remaining_source_shares": "REAL",
            "realized_exit_shares": "REAL NOT NULL DEFAULT 0",
            "realized_exit_size_usd": "REAL NOT NULL DEFAULT 0",
            "realized_exit_pnl_usd": "REAL NOT NULL DEFAULT 0",
            "partial_exit_count": "INTEGER NOT NULL DEFAULT 0",
            "resolution_fixed_cost_usd": "REAL NOT NULL DEFAULT 0",
            "f_trader_avg_size_usd": "REAL",
            "f_trader_diversity": "INTEGER",
            "f_volume_24h_usd": "REAL",
            "f_volume_7d_avg_usd": "REAL",
            "f_top_holder_pct": "REAL",
            "market_components_json": "TEXT",
            "decision_context_json": "TEXT",
        },
    )
    _ensure_table_columns(
        conn,
        "wallet_watch_state",
        {
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "status_reason": "TEXT",
            "dropped_at": "INTEGER",
            "reactivated_at": "INTEGER",
            "tracking_started_at": "INTEGER NOT NULL DEFAULT 0",
            "last_source_ts_at_status": "INTEGER NOT NULL DEFAULT 0",
            "updated_at": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_table_columns(
        conn,
        "trade_log_manual_edits",
        {
            "entry_price": "REAL",
            "shares": "REAL",
            "size_usd": "REAL",
            "status": "TEXT",
            "updated_at": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_table_columns(
        conn,
        "position_manual_edits",
        {
            "token_id": "TEXT NOT NULL DEFAULT ''",
            "real_money": "INTEGER NOT NULL DEFAULT 0",
            "entry_price": "REAL",
            "shares": "REAL",
            "size_usd": "REAL",
            "status": "TEXT",
            "updated_at": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_table_columns(
        conn,
        "retrain_runs",
        {
            "started_at": "INTEGER NOT NULL DEFAULT 0",
            "finished_at": "INTEGER NOT NULL DEFAULT 0",
            "trigger": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT ''",
            "ok": "INTEGER NOT NULL DEFAULT 0",
            "deployed": "INTEGER NOT NULL DEFAULT 0",
            "sample_count": "INTEGER NOT NULL DEFAULT 0",
            "min_samples": "INTEGER NOT NULL DEFAULT 0",
            "brier_score": "REAL",
            "log_loss": "REAL",
            "candidate_name": "TEXT",
            "candidate_count": "INTEGER",
            "search_beats_baseline": "INTEGER",
            "search_total_pnl": "REAL",
            "val_selected_trades": "INTEGER",
            "val_total_pnl": "REAL",
            "challenger_shared_log_loss": "REAL",
            "challenger_shared_brier_score": "REAL",
            "incumbent_log_loss": "REAL",
            "incumbent_brier_score": "REAL",
            "message": "TEXT NOT NULL DEFAULT ''",
        },
    )
    _ensure_positions_schema(conn)
    _backfill_retrain_runs_from_model_history(conn)
    _backfill_retrain_run_shared_holdout_metrics(conn)
    conn.commit()
    if _startup_heavy_maintenance_enabled(DB_PATH):
        try:
            _repair_trade_log_market_urls(conn)
            conn.execute(
                """
                UPDATE positions
                SET token_id = LOWER(token_id)
                WHERE token_id IS NOT NULL
                  AND token_id != LOWER(token_id)
                """
            )
            conn.execute(
                """
                UPDATE trade_log
                SET token_id = LOWER(token_id)
                WHERE token_id IS NOT NULL
                  AND token_id != LOWER(token_id)
                """
            )
            conn.execute(
                """
                UPDATE trade_log
                SET remaining_entry_shares = CASE
                        WHEN exited_at IS NOT NULL THEN 0
                        ELSE COALESCE(remaining_entry_shares, actual_entry_shares, source_shares, 0)
                    END,
                    remaining_entry_size_usd = CASE
                        WHEN exited_at IS NOT NULL THEN 0
                        ELSE COALESCE(remaining_entry_size_usd, actual_entry_size_usd, signal_size_usd, 0)
                    END,
                    remaining_source_shares = CASE
                        WHEN exited_at IS NOT NULL THEN 0
                        ELSE COALESCE(remaining_source_shares, source_shares, 0)
                    END,
                    realized_exit_shares = COALESCE(realized_exit_shares, 0),
                    realized_exit_size_usd = COALESCE(realized_exit_size_usd, 0),
                    realized_exit_pnl_usd = COALESCE(realized_exit_pnl_usd, 0),
                    partial_exit_count = COALESCE(partial_exit_count, 0)
                WHERE skipped=0
                  AND COALESCE(source_action, 'buy')='buy'
                """
            )
            conn.commit()
        except sqlite3.DatabaseError:
            conn.rollback()
            logger.exception("Heavy startup DB maintenance failed; keeping core schema changes")
    else:
        logger.info(
            "Skipping heavy startup DB maintenance for shared/network path: %s",
            DB_PATH,
        )
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
