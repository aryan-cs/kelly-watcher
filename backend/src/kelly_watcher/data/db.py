from __future__ import annotations

import json
import logging
import os
import re
import time
import sqlite3
import threading
from pathlib import Path

from kelly_watcher.data.market_urls import market_url_from_metadata
from kelly_watcher.runtime_paths import TRADING_DB_PATH

DB_PATH = TRADING_DB_PATH
REPAIR_BATCH_SIZE = 250
VERIFIED_BACKUP_RETENTION = 5
RECOVERY_QUARANTINE_RETENTION = 5
logger = logging.getLogger(__name__)
_RUNTIME_JOURNAL_MODE_LOCK = threading.Lock()
_RUNTIME_JOURNAL_MODE_PATHS: set[str] = set()
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


def _connect_sqlite(path: Path, *, apply_runtime_pragmas: bool) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connect_timeout_s = max(float(os.getenv("SQLITE_CONNECT_TIMEOUT_SECONDS", "30") or 30), 1.0)
    except ValueError:
        connect_timeout_s = 30.0
    try:
        busy_timeout_ms = max(int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "30000") or 30000), 1000)
    except ValueError:
        busy_timeout_ms = 30000
    conn = sqlite3.connect(path, timeout=connect_timeout_s, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        if apply_runtime_pragmas:
            conn.execute("PRAGMA foreign_keys=ON")
            resolved_path = _resolved_db_path_text(path)
            if resolved_path not in _RUNTIME_JOURNAL_MODE_PATHS:
                with _RUNTIME_JOURNAL_MODE_LOCK:
                    if resolved_path not in _RUNTIME_JOURNAL_MODE_PATHS:
                        conn.execute(f"PRAGMA journal_mode={_preferred_journal_mode(path)}")
                        _RUNTIME_JOURNAL_MODE_PATHS.add(resolved_path)
        return conn
    except BaseException:
        conn.close()
        raise


def get_conn() -> sqlite3.Connection:
    return _connect_sqlite(DB_PATH, apply_runtime_pragmas=True)


def get_conn_for_path(path: Path, *, apply_runtime_pragmas: bool = False) -> sqlite3.Connection:
    return _connect_sqlite(Path(path), apply_runtime_pragmas=apply_runtime_pragmas)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_parent(path: Path) -> None:
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def rollback_safely(conn: sqlite3.Connection, *, label: str = "SQLite transaction") -> None:
    try:
        conn.rollback()
    except Exception:
        logger.debug("%s rollback skipped", label, exc_info=True)


def current_promotion_epoch_id(conn: sqlite3.Connection | None = None) -> int:
    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    assert conn is not None
    try:
        row = conn.execute(
            """
            SELECT id
            FROM replay_promotions
            WHERE LOWER(COALESCE(status, ''))='applied'
            ORDER BY applied_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        return int((row or {})["id"] or 0) if row is not None else 0
    finally:
        if owns_conn:
            conn.close()


def database_integrity_state(path: Path | None = None) -> dict[str, object]:
    target = Path(path) if path is not None else DB_PATH
    if not target.exists():
        return {
            "db_integrity_known": False,
            "db_integrity_ok": True,
            "db_integrity_message": "",
        }

    conn: sqlite3.Connection | None = None
    try:
        conn = _connect_sqlite(target, apply_runtime_pragmas=False)
        row = conn.execute("PRAGMA quick_check").fetchone()
        message = str(row[0] or "").strip() if row is not None else "unknown"
        ok = message.lower() == "ok"
        return {
            "db_integrity_known": True,
            "db_integrity_ok": ok,
            "db_integrity_message": "" if ok else message,
        }
    except sqlite3.DatabaseError as exc:
        return {
            "db_integrity_known": True,
            "db_integrity_ok": False,
            "db_integrity_message": str(exc),
        }
    finally:
        if conn is not None:
            conn.close()


def _backup_root(path: Path) -> Path:
    return path.parent / "db_backups"


def _primary_backup_path(path: Path) -> Path:
    return Path(f"{path}.bak")


def _timestamped_backup_path(path: Path) -> Path:
    backup_dir = _backup_root(path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    candidate = backup_dir / f"{path.stem}.{timestamp}{path.suffix}"
    if not candidate.exists():
        return candidate
    for attempt in range(1, 1000):
        fallback = backup_dir / f"{path.stem}.{timestamp}_{attempt}{path.suffix}"
        if not fallback.exists():
            return fallback
    return backup_dir / f"{path.stem}.{timestamp}_{time.time_ns()}{path.suffix}"


def _verified_backup_history_paths(path: Path) -> list[Path]:
    backup_dir = _backup_root(path)
    if not backup_dir.exists():
        return []
    pattern = f"{path.stem}.*{path.suffix}"
    return sorted(
        (candidate for candidate in backup_dir.glob(pattern) if candidate.is_file()),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )


def _prune_verified_backup_history(path: Path, *, keep: int = VERIFIED_BACKUP_RETENTION) -> None:
    for candidate in _verified_backup_history_paths(path)[max(int(keep or 0), 0):]:
        try:
            candidate.unlink()
        except OSError:
            logger.warning("Failed to prune old verified DB backup %s", candidate, exc_info=True)


def create_verified_backup(path: Path | None = None) -> dict[str, object]:
    source = Path(path) if path is not None else DB_PATH
    if not source.exists():
        return {
            "ok": False,
            "backup_path": "",
            "message": f"database not found at {source}",
            "created_at": 0,
        }

    integrity = database_integrity_state(source)
    if integrity.get("db_integrity_known") and not integrity.get("db_integrity_ok"):
        return {
            "ok": False,
            "backup_path": "",
            "message": "database integrity check failed; skipping verified backup",
            "created_at": 0,
        }

    primary_backup = _primary_backup_path(source)
    tmp_backup = primary_backup.with_suffix(primary_backup.suffix + ".tmp")
    if tmp_backup.exists():
        try:
            tmp_backup.unlink()
        except OSError:
            pass

    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    try:
        src_conn = _connect_sqlite(source, apply_runtime_pragmas=False)
        dst_conn = _connect_sqlite(tmp_backup, apply_runtime_pragmas=False)
        src_conn.backup(dst_conn)
    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()

    backup_integrity = database_integrity_state(tmp_backup)
    if backup_integrity.get("db_integrity_known") and not backup_integrity.get("db_integrity_ok"):
        try:
            tmp_backup.unlink()
        except OSError:
            pass
        return {
            "ok": False,
            "backup_path": "",
            "message": "verified backup integrity check failed",
            "created_at": 0,
        }
    _fsync_file(tmp_backup)

    if primary_backup.exists():
        previous_integrity = database_integrity_state(primary_backup)
        if previous_integrity.get("db_integrity_known") and previous_integrity.get("db_integrity_ok"):
            archived_backup = _timestamped_backup_path(source)
            primary_backup.replace(archived_backup)
        else:
            try:
                primary_backup.unlink()
            except OSError:
                logger.warning("Failed to remove stale DB backup %s", primary_backup, exc_info=True)

    tmp_backup.replace(primary_backup)
    _fsync_parent(primary_backup)
    _prune_verified_backup_history(source)
    return {
        "ok": True,
        "backup_path": str(primary_backup),
        "message": "verified backup created",
        "created_at": int(primary_backup.stat().st_mtime),
    }


def _backup_candidates(path: Path) -> list[Path]:
    candidates: list[Path] = []
    primary_backup = _primary_backup_path(path)
    if primary_backup.exists():
        candidates.append(primary_backup)
    candidates.extend(
        candidate
        for candidate in _verified_backup_history_paths(path)
        if candidate not in candidates
    )
    return candidates


def db_recovery_state(path: Path | None = None) -> dict[str, object]:
    target = Path(path) if path is not None else DB_PATH
    candidates = _backup_candidates(target)
    if not candidates:
        return {
            "db_recovery_state_known": False,
            "db_recovery_candidate_ready": False,
            "db_recovery_candidate_path": "",
            "db_recovery_candidate_source_path": "",
            "db_recovery_candidate_message": "",
            "db_recovery_latest_verified_backup_path": "",
            "db_recovery_latest_verified_backup_at": 0,
        }

    latest_valid_backup: Path | None = None
    latest_valid_backup_at = 0
    failure_message = ""
    for candidate in candidates:
        integrity = database_integrity_state(candidate)
        if integrity.get("db_integrity_known") and integrity.get("db_integrity_ok"):
            latest_valid_backup = candidate
            latest_valid_backup_at = int(candidate.stat().st_mtime)
            break
        if not failure_message:
            detail = str(integrity.get("db_integrity_message") or "").strip()
            failure_message = "backup integrity check failed"
            if detail:
                failure_message += f": {detail.splitlines()[0].strip()}"

    return {
        "db_recovery_state_known": True,
        "db_recovery_candidate_ready": latest_valid_backup is not None,
        "db_recovery_candidate_path": str(latest_valid_backup) if latest_valid_backup is not None else "",
        "db_recovery_candidate_source_path": str(target) if latest_valid_backup is not None else "",
        "db_recovery_candidate_message": "" if latest_valid_backup is not None else failure_message,
        "db_recovery_latest_verified_backup_path": str(latest_valid_backup) if latest_valid_backup is not None else "",
        "db_recovery_latest_verified_backup_at": latest_valid_backup_at,
    }


def _recovery_quarantine_root(path: Path) -> Path:
    return path.parent / "db_recovery_quarantine"


def _timestamped_recovery_quarantine_path(path: Path) -> Path:
    quarantine_dir = _recovery_quarantine_root(path)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    candidate = quarantine_dir / f"{path.stem}.pre_recovery.{timestamp}{path.suffix}"
    if not candidate.exists():
        return candidate
    for attempt in range(1, 1000):
        fallback = quarantine_dir / f"{path.stem}.pre_recovery.{timestamp}_{attempt}{path.suffix}"
        if not fallback.exists():
            return fallback
    return quarantine_dir / f"{path.stem}.pre_recovery.{timestamp}_{time.time_ns()}{path.suffix}"


def _recovery_quarantine_db_paths(path: Path) -> list[Path]:
    quarantine_dir = _recovery_quarantine_root(path)
    if not quarantine_dir.exists():
        return []
    pattern = f"{path.stem}.pre_recovery.*{path.suffix}"
    return sorted(
        (candidate for candidate in quarantine_dir.glob(pattern) if candidate.is_file()),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )


def _prune_recovery_quarantine(path: Path, *, keep: int = RECOVERY_QUARANTINE_RETENTION) -> None:
    for candidate in _recovery_quarantine_db_paths(path)[max(int(keep or 0), 0):]:
        for cleanup_path in (
            candidate,
            Path(f"{candidate}-wal"),
            Path(f"{candidate}-shm"),
        ):
            try:
                cleanup_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning(
                    "Failed to prune old DB recovery quarantine file %s",
                    cleanup_path,
                    exc_info=True,
                )


def recover_db_from_verified_backup(
    path: Path | None = None,
    *,
    backup_path: Path | None = None,
) -> dict[str, object]:
    target = Path(path) if path is not None else DB_PATH
    candidate = Path(backup_path) if backup_path is not None else None
    if candidate is None:
        recovery = db_recovery_state(target)
        candidate_text = str(recovery.get("db_recovery_candidate_path") or "").strip()
        candidate = Path(candidate_text) if candidate_text else None

    if candidate is None or not candidate.exists():
        return {
            "ok": False,
            "backup_path": "",
            "restored_path": str(target),
            "quarantined_path": "",
            "message": "verified backup candidate not found",
            "restored_at": 0,
        }

    candidate_integrity = database_integrity_state(candidate)
    if candidate_integrity.get("db_integrity_known") and not candidate_integrity.get("db_integrity_ok"):
        detail = str(candidate_integrity.get("db_integrity_message") or "").strip()
        message = "verified backup candidate failed integrity check"
        if detail:
            message += f": {detail.splitlines()[0].strip()}"
        return {
            "ok": False,
            "backup_path": str(candidate),
            "restored_path": str(target),
            "quarantined_path": "",
            "message": message,
            "restored_at": 0,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_restore = target.with_name(f"{target.name}.{os.getpid()}.recovering")
    for stale_path in (
        temp_restore,
        Path(f"{temp_restore}-wal"),
        Path(f"{temp_restore}-shm"),
    ):
        try:
            stale_path.unlink()
        except FileNotFoundError:
            pass

    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    try:
        src_conn = _connect_sqlite(candidate, apply_runtime_pragmas=False)
        dst_conn = _connect_sqlite(temp_restore, apply_runtime_pragmas=False)
        src_conn.backup(dst_conn)
    finally:
        if dst_conn is not None:
            dst_conn.close()
        if src_conn is not None:
            src_conn.close()

    restore_integrity = database_integrity_state(temp_restore)
    if restore_integrity.get("db_integrity_known") and not restore_integrity.get("db_integrity_ok"):
        detail = str(restore_integrity.get("db_integrity_message") or "").strip()
        try:
            temp_restore.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Failed to remove invalid DB recovery temp file %s", temp_restore, exc_info=True)
        message = "temporary DB recovery copy failed integrity check"
        if detail:
            message += f": {detail.splitlines()[0].strip()}"
        return {
            "ok": False,
            "backup_path": str(candidate),
            "restored_path": str(target),
            "quarantined_path": "",
            "message": message,
            "restored_at": 0,
        }
    _fsync_file(temp_restore)

    quarantined_path = ""
    quarantined_target: Path | None = None
    quarantined_sidecars: list[tuple[Path, Path]] = []
    sidecar_paths = (
        Path(f"{target}-wal"),
        Path(f"{target}-shm"),
    )
    try:
        if target.exists():
            quarantined_target = _timestamped_recovery_quarantine_path(target)
            target.replace(quarantined_target)
            quarantined_path = str(quarantined_target)
            for sidecar_path in sidecar_paths:
                if not sidecar_path.exists():
                    continue
                archived_sidecar = Path(f"{quarantined_target}{sidecar_path.name[len(target.name):]}")
                sidecar_path.replace(archived_sidecar)
                quarantined_sidecars.append((archived_sidecar, sidecar_path))
        temp_restore.replace(target)
        _fsync_parent(target)
    except Exception:
        if not target.exists() and quarantined_target is not None and quarantined_target.exists():
            try:
                quarantined_target.replace(target)
            except OSError:
                logger.warning("Failed to roll back quarantined DB %s", quarantined_target, exc_info=True)
            for archived_sidecar, original_sidecar in reversed(quarantined_sidecars):
                if not archived_sidecar.exists() or original_sidecar.exists():
                    continue
                try:
                    archived_sidecar.replace(original_sidecar)
                except OSError:
                    logger.warning(
                        "Failed to roll back quarantined DB sidecar %s",
                        archived_sidecar,
                        exc_info=True,
                    )
        raise
    finally:
        try:
            temp_restore.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Failed to remove DB recovery temp file %s", temp_restore, exc_info=True)

    _prune_recovery_quarantine(target)
    return {
        "ok": True,
        "backup_path": str(candidate),
        "restored_path": str(target),
        "quarantined_path": quarantined_path,
        "message": "verified backup restored",
        "restored_at": int(target.stat().st_mtime),
    }


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

    conn.execute("SAVEPOINT positions_schema_rebuild")
    try:
        conn.execute("DROP TABLE IF EXISTS positions_legacy")
        conn.execute("ALTER TABLE positions RENAME TO positions_legacy")
        pre_count = int(conn.execute("SELECT COUNT(*) FROM positions_legacy").fetchone()[0] or 0)
        conn.execute(
            """
            CREATE TABLE positions (
                market_id   TEXT NOT NULL,
                side        TEXT NOT NULL,
                size_usd    REAL NOT NULL,
                avg_price   REAL NOT NULL,
                token_id    TEXT NOT NULL,
                entered_at  INTEGER NOT NULL,
                real_money  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (market_id, token_id, side, real_money)
            )
            """
        )
        conn.execute(
            """
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
            FROM positions_legacy
            """
        )
        copied_count = int(conn.execute("SELECT changes()").fetchone()[0] or 0)
        post_count = int(conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] or 0)
        if copied_count != pre_count or post_count != pre_count:
            raise sqlite3.DatabaseError(
                "positions schema rebuild row-count mismatch: "
                f"legacy={pre_count} copied={copied_count} rebuilt={post_count}"
            )
        conn.execute("DROP TABLE positions_legacy")
        conn.execute("RELEASE SAVEPOINT positions_schema_rebuild")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT positions_schema_rebuild")
        conn.execute("RELEASE SAVEPOINT positions_schema_rebuild")
        raise


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


def init_db(path: Path | None = None, *, run_heavy_maintenance: bool | None = None) -> None:
    target = Path(path) if path is not None else DB_PATH
    conn = get_conn_for_path(target, apply_runtime_pragmas=True)
    try:
        _init_db_with_connection(conn, target, run_heavy_maintenance=run_heavy_maintenance)
    except BaseException:
        rollback_safely(conn, label="database initialization")
        raise
    finally:
        conn.close()


def _init_db_with_connection(
    conn: sqlite3.Connection,
    target: Path,
    *,
    run_heavy_maintenance: bool | None,
) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS seen_trades (
            trade_id   TEXT PRIMARY KEY,
            market_id  TEXT NOT NULL,
            trader_id  TEXT NOT NULL,
            seen_at    INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_event_queue (
            trade_id            TEXT PRIMARY KEY,
            wallet_address      TEXT NOT NULL,
            watch_tier          TEXT NOT NULL DEFAULT '',
            condition_id        TEXT NOT NULL DEFAULT '',
            token_id            TEXT NOT NULL DEFAULT '',
            source_ts           INTEGER NOT NULL DEFAULT 0,
            source_trade_json   TEXT NOT NULL DEFAULT '{}',
            status              TEXT NOT NULL DEFAULT 'pending',
            attempts            INTEGER NOT NULL DEFAULT 0,
            first_seen_at       INTEGER NOT NULL,
            observed_at         INTEGER NOT NULL,
            updated_at          INTEGER NOT NULL,
            last_error          TEXT NOT NULL DEFAULT ''
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
            segment_id          TEXT,
            policy_id           TEXT,
            policy_bundle_version INTEGER NOT NULL DEFAULT 0,
            promotion_epoch_id  INTEGER NOT NULL DEFAULT 0,
            experiment_arm      TEXT NOT NULL DEFAULT 'champion',
            expected_edge       REAL,
            expected_fill_cost_usd REAL,
            expected_exit_fee_usd REAL,
            expected_close_fixed_cost_usd REAL,
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
            resolution_checked_at INTEGER NOT NULL DEFAULT 0,
            resolution_error     TEXT NOT NULL DEFAULT '',
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
            deployed        INTEGER NOT NULL DEFAULT 0,
            training_scope  TEXT NOT NULL DEFAULT 'all_history',
            training_since_ts INTEGER NOT NULL DEFAULT 0,
            training_routed_only INTEGER NOT NULL DEFAULT 0,
            training_provenance_trusted INTEGER NOT NULL DEFAULT 0
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
            scope           TEXT NOT NULL DEFAULT 'all_history',
            since_ts        INTEGER NOT NULL DEFAULT 0,
            epoch_started_at INTEGER NOT NULL DEFAULT 0,
            epoch_source    TEXT NOT NULL DEFAULT '',
            legacy_resolved_excluded INTEGER NOT NULL DEFAULT 0,
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
            peak_open_exposure_usd  REAL NOT NULL DEFAULT 0,
            max_open_exposure_share REAL NOT NULL DEFAULT 0,
            window_end_open_exposure_usd REAL NOT NULL DEFAULT 0,
            window_end_open_exposure_share REAL NOT NULL DEFAULT 0,
            window_end_live_guard_triggered INTEGER NOT NULL DEFAULT 0,
            window_end_daily_guard_triggered INTEGER NOT NULL DEFAULT 0,
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
            accepted_size_usd REAL NOT NULL DEFAULT 0,
            resolved_count   INTEGER NOT NULL DEFAULT 0,
            resolved_size_usd REAL NOT NULL DEFAULT 0,
            total_pnl_usd    REAL NOT NULL DEFAULT 0,
            win_rate         REAL,
            avg_return_pct   REAL,
            FOREIGN KEY (replay_run_id) REFERENCES replay_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS replay_search_runs (
            id                           INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at                   INTEGER NOT NULL,
            finished_at                  INTEGER NOT NULL,
            request_token                TEXT NOT NULL DEFAULT '',
            trigger                      TEXT NOT NULL DEFAULT '',
            label_prefix                 TEXT NOT NULL DEFAULT '',
            status                       TEXT NOT NULL DEFAULT '',
            status_message               TEXT NOT NULL DEFAULT '',
            base_policy_json             TEXT NOT NULL DEFAULT '{}',
            grid_json                    TEXT NOT NULL DEFAULT '{}',
            constraints_json             TEXT NOT NULL DEFAULT '{}',
            notes                        TEXT NOT NULL DEFAULT '',
            window_days                  INTEGER NOT NULL DEFAULT 0,
            window_count                 INTEGER NOT NULL DEFAULT 1,
            drawdown_penalty             REAL NOT NULL DEFAULT 0,
            window_stddev_penalty        REAL NOT NULL DEFAULT 0,
            worst_window_penalty         REAL NOT NULL DEFAULT 0,
            pause_guard_penalty          REAL NOT NULL DEFAULT 0,
            daily_guard_window_penalty   REAL NOT NULL DEFAULT 0,
            live_guard_window_penalty    REAL NOT NULL DEFAULT 0,
            daily_guard_restart_window_penalty REAL NOT NULL DEFAULT 0,
            live_guard_restart_window_penalty REAL NOT NULL DEFAULT 0,
            open_exposure_penalty        REAL NOT NULL DEFAULT 0,
            window_end_open_exposure_penalty REAL NOT NULL DEFAULT 0,
            avg_window_end_open_exposure_penalty REAL NOT NULL DEFAULT 0,
            carry_window_penalty         REAL NOT NULL DEFAULT 0,
            carry_restart_window_penalty REAL NOT NULL DEFAULT 0,
            resolved_share_penalty       REAL NOT NULL DEFAULT 0,
            resolved_size_share_penalty  REAL NOT NULL DEFAULT 0,
            worst_window_resolved_share_penalty REAL NOT NULL DEFAULT 0,
            worst_window_resolved_size_share_penalty REAL NOT NULL DEFAULT 0,
            mode_resolved_share_penalty  REAL NOT NULL DEFAULT 0,
            mode_resolved_size_share_penalty REAL NOT NULL DEFAULT 0,
            mode_worst_window_resolved_share_penalty REAL NOT NULL DEFAULT 0,
            mode_worst_window_resolved_size_share_penalty REAL NOT NULL DEFAULT 0,
            mode_active_window_accepted_share_penalty REAL NOT NULL DEFAULT 0,
            mode_active_window_accepted_size_share_penalty REAL NOT NULL DEFAULT 0,
            worst_active_window_accepted_penalty REAL NOT NULL DEFAULT 0,
            worst_active_window_accepted_size_penalty REAL NOT NULL DEFAULT 0,
            mode_worst_active_window_accepted_penalty REAL NOT NULL DEFAULT 0,
            mode_worst_active_window_accepted_size_penalty REAL NOT NULL DEFAULT 0,
            mode_loss_penalty            REAL NOT NULL DEFAULT 0,
            mode_inactivity_penalty      REAL NOT NULL DEFAULT 0,
            mode_accepted_window_count_penalty REAL NOT NULL DEFAULT 0,
            mode_accepted_window_share_penalty REAL NOT NULL DEFAULT 0,
            mode_non_accepting_active_window_streak_penalty REAL NOT NULL DEFAULT 0,
            mode_non_accepting_active_window_episode_penalty REAL NOT NULL DEFAULT 0,
            mode_accepting_window_accepted_share_penalty REAL NOT NULL DEFAULT 0,
            mode_accepting_window_accepted_size_share_penalty REAL NOT NULL DEFAULT 0,
            mode_top_two_accepting_window_accepted_share_penalty REAL NOT NULL DEFAULT 0,
            mode_top_two_accepting_window_accepted_size_share_penalty REAL NOT NULL DEFAULT 0,
            mode_accepting_window_accepted_concentration_index_penalty REAL NOT NULL DEFAULT 0,
            mode_accepting_window_accepted_size_concentration_index_penalty REAL NOT NULL DEFAULT 0,
            window_inactivity_penalty    REAL NOT NULL DEFAULT 0,
            accepted_window_count_penalty REAL NOT NULL DEFAULT 0,
            accepted_window_share_penalty REAL NOT NULL DEFAULT 0,
            non_accepting_active_window_streak_penalty REAL NOT NULL DEFAULT 0,
            non_accepting_active_window_episode_penalty REAL NOT NULL DEFAULT 0,
            accepting_window_accepted_share_penalty REAL NOT NULL DEFAULT 0,
            accepting_window_accepted_size_share_penalty REAL NOT NULL DEFAULT 0,
            top_two_accepting_window_accepted_share_penalty REAL NOT NULL DEFAULT 0,
            top_two_accepting_window_accepted_size_share_penalty REAL NOT NULL DEFAULT 0,
            accepting_window_accepted_concentration_index_penalty REAL NOT NULL DEFAULT 0,
            accepting_window_accepted_size_concentration_index_penalty REAL NOT NULL DEFAULT 0,
            wallet_count_penalty         REAL NOT NULL DEFAULT 0,
            market_count_penalty         REAL NOT NULL DEFAULT 0,
            entry_price_band_count_penalty REAL NOT NULL DEFAULT 0,
            time_to_close_band_count_penalty REAL NOT NULL DEFAULT 0,
            wallet_concentration_penalty REAL NOT NULL DEFAULT 0,
            market_concentration_penalty REAL NOT NULL DEFAULT 0,
            entry_price_band_concentration_penalty REAL NOT NULL DEFAULT 0,
            time_to_close_band_concentration_penalty REAL NOT NULL DEFAULT 0,
            wallet_size_concentration_penalty REAL NOT NULL DEFAULT 0,
            market_size_concentration_penalty REAL NOT NULL DEFAULT 0,
            entry_price_band_size_concentration_penalty REAL NOT NULL DEFAULT 0,
            time_to_close_band_size_concentration_penalty REAL NOT NULL DEFAULT 0,
            candidate_count              INTEGER NOT NULL DEFAULT 0,
            feasible_count               INTEGER NOT NULL DEFAULT 0,
            rejected_count               INTEGER NOT NULL DEFAULT 0,
            current_candidate_score      REAL,
            current_candidate_feasible   INTEGER NOT NULL DEFAULT 0,
            current_candidate_total_pnl_usd REAL,
            current_candidate_max_drawdown_pct REAL,
            current_candidate_constraint_failures_json TEXT NOT NULL DEFAULT '[]',
            current_candidate_result_json TEXT NOT NULL DEFAULT '{}',
            best_vs_current_pnl_usd      REAL,
            best_vs_current_score        REAL,
            best_feasible_candidate_index INTEGER,
            best_feasible_score          REAL,
            best_feasible_total_pnl_usd  REAL,
            best_feasible_max_drawdown_pct REAL
        );

        CREATE TABLE IF NOT EXISTS replay_search_candidates (
            id                           INTEGER PRIMARY KEY AUTOINCREMENT,
            replay_search_run_id         INTEGER NOT NULL,
            candidate_index              INTEGER NOT NULL,
            score                        REAL NOT NULL DEFAULT 0,
            feasible                     INTEGER NOT NULL DEFAULT 0,
            is_current_policy            INTEGER NOT NULL DEFAULT 0,
            constraint_failures_json     TEXT NOT NULL DEFAULT '[]',
            overrides_json               TEXT NOT NULL DEFAULT '{}',
            policy_json                  TEXT NOT NULL DEFAULT '{}',
            config_json                  TEXT NOT NULL DEFAULT '{}',
            result_json                  TEXT NOT NULL DEFAULT '{}',
            total_pnl_usd                REAL NOT NULL DEFAULT 0,
            max_drawdown_pct             REAL,
            accepted_count               INTEGER NOT NULL DEFAULT 0,
            resolved_count               INTEGER NOT NULL DEFAULT 0,
            win_rate                     REAL,
            positive_window_count        INTEGER NOT NULL DEFAULT 0,
            negative_window_count        INTEGER NOT NULL DEFAULT 0,
            worst_window_pnl_usd         REAL,
            worst_window_drawdown_pct    REAL,
            window_pnl_stddev_usd        REAL,
            FOREIGN KEY (replay_search_run_id) REFERENCES replay_search_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS replay_promotions (
            id                           INTEGER PRIMARY KEY AUTOINCREMENT,
            requested_at                 INTEGER NOT NULL,
            finished_at                  INTEGER NOT NULL DEFAULT 0,
            applied_at                   INTEGER NOT NULL DEFAULT 0,
            trigger                      TEXT NOT NULL DEFAULT '',
            scope                        TEXT NOT NULL DEFAULT 'shadow_only',
            source_mode                  TEXT NOT NULL DEFAULT '',
            status                       TEXT NOT NULL DEFAULT '',
            reason                       TEXT NOT NULL DEFAULT '',
            replay_search_run_id         INTEGER,
            replay_search_candidate_id   INTEGER,
            config_json                  TEXT NOT NULL DEFAULT '{}',
            previous_config_json         TEXT NOT NULL DEFAULT '{}',
            updated_keys_json            TEXT NOT NULL DEFAULT '[]',
            candidate_result_json        TEXT NOT NULL DEFAULT '{}',
            score                        REAL,
            score_delta                  REAL,
            total_pnl_usd                REAL,
            pnl_delta_usd                REAL,
            shadow_resolved_count        INTEGER NOT NULL DEFAULT 0,
            shadow_resolved_since_previous INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (replay_search_run_id) REFERENCES replay_search_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (replay_search_candidate_id) REFERENCES replay_search_candidates(id) ON DELETE SET NULL
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
        CREATE INDEX IF NOT EXISTS idx_source_event_queue_status_ts ON source_event_queue(status, source_ts DESC);
        CREATE INDEX IF NOT EXISTS idx_source_event_queue_status_tier_ts ON source_event_queue(status, watch_tier, source_ts DESC);
        CREATE INDEX IF NOT EXISTS idx_source_event_queue_wallet_status ON source_event_queue(wallet_address, status);
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
        CREATE INDEX IF NOT EXISTS idx_replay_search_runs_finished_at ON replay_search_runs(finished_at DESC);
        CREATE INDEX IF NOT EXISTS idx_replay_search_candidates_run_id ON replay_search_candidates(replay_search_run_id);
        CREATE INDEX IF NOT EXISTS idx_replay_promotions_applied_at ON replay_promotions(applied_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_replay_promotions_run_id ON replay_promotions(replay_search_run_id);
        """
    )
    _ensure_table_columns(
        conn,
        "perf_snapshots",
        {
            "scope": "TEXT NOT NULL DEFAULT 'all_history'",
            "since_ts": "INTEGER NOT NULL DEFAULT 0",
            "epoch_started_at": "INTEGER NOT NULL DEFAULT 0",
            "epoch_source": "TEXT NOT NULL DEFAULT ''",
            "legacy_resolved_excluded": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_table_columns(
        conn,
        "model_history",
        {
            "training_scope": "TEXT NOT NULL DEFAULT 'all_history'",
            "training_since_ts": "INTEGER NOT NULL DEFAULT 0",
            "training_routed_only": "INTEGER NOT NULL DEFAULT 0",
            "training_provenance_trusted": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_table_columns(
        conn,
        "replay_runs",
        {
            "window_start_ts": "INTEGER",
            "window_end_ts": "INTEGER",
            "peak_open_exposure_usd": "REAL NOT NULL DEFAULT 0",
            "max_open_exposure_share": "REAL NOT NULL DEFAULT 0",
            "window_end_open_exposure_usd": "REAL NOT NULL DEFAULT 0",
            "window_end_open_exposure_share": "REAL NOT NULL DEFAULT 0",
            "window_end_live_guard_triggered": "INTEGER NOT NULL DEFAULT 0",
            "window_end_daily_guard_triggered": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_table_columns(
        conn,
        "segment_metrics",
        {
            "accepted_size_usd": "REAL NOT NULL DEFAULT 0",
            "resolved_size_usd": "REAL NOT NULL DEFAULT 0",
        },
    )
    _ensure_table_columns(
        conn,
        "replay_search_runs",
        {
            "request_token": "TEXT NOT NULL DEFAULT ''",
            "trigger": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT ''",
            "status_message": "TEXT NOT NULL DEFAULT ''",
            "base_policy_json": "TEXT NOT NULL DEFAULT '{}'",
            "grid_json": "TEXT NOT NULL DEFAULT '{}'",
            "constraints_json": "TEXT NOT NULL DEFAULT '{}'",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "window_days": "INTEGER NOT NULL DEFAULT 0",
            "window_count": "INTEGER NOT NULL DEFAULT 1",
            "drawdown_penalty": "REAL NOT NULL DEFAULT 0",
            "window_stddev_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_window_penalty": "REAL NOT NULL DEFAULT 0",
            "pause_guard_penalty": "REAL NOT NULL DEFAULT 0",
            "daily_guard_window_penalty": "REAL NOT NULL DEFAULT 0",
            "live_guard_window_penalty": "REAL NOT NULL DEFAULT 0",
            "daily_guard_restart_window_penalty": "REAL NOT NULL DEFAULT 0",
            "live_guard_restart_window_penalty": "REAL NOT NULL DEFAULT 0",
            "open_exposure_penalty": "REAL NOT NULL DEFAULT 0",
            "window_end_open_exposure_penalty": "REAL NOT NULL DEFAULT 0",
            "avg_window_end_open_exposure_penalty": "REAL NOT NULL DEFAULT 0",
            "carry_window_penalty": "REAL NOT NULL DEFAULT 0",
            "carry_restart_window_penalty": "REAL NOT NULL DEFAULT 0",
            "resolved_share_penalty": "REAL NOT NULL DEFAULT 0",
            "resolved_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_window_resolved_share_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_window_resolved_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_resolved_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_resolved_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_worst_window_resolved_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_worst_window_resolved_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_active_window_accepted_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_active_window_accepted_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_active_window_accepted_penalty": "REAL NOT NULL DEFAULT 0",
            "worst_active_window_accepted_size_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_worst_active_window_accepted_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_worst_active_window_accepted_size_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_loss_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_inactivity_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_accepted_window_count_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_accepted_window_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_non_accepting_active_window_streak_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_non_accepting_active_window_episode_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_accepting_window_accepted_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_accepting_window_accepted_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_top_two_accepting_window_accepted_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_top_two_accepting_window_accepted_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_accepting_window_accepted_concentration_index_penalty": "REAL NOT NULL DEFAULT 0",
            "mode_accepting_window_accepted_size_concentration_index_penalty": "REAL NOT NULL DEFAULT 0",
            "window_inactivity_penalty": "REAL NOT NULL DEFAULT 0",
            "accepted_window_count_penalty": "REAL NOT NULL DEFAULT 0",
            "accepted_window_share_penalty": "REAL NOT NULL DEFAULT 0",
            "non_accepting_active_window_streak_penalty": "REAL NOT NULL DEFAULT 0",
            "non_accepting_active_window_episode_penalty": "REAL NOT NULL DEFAULT 0",
            "accepting_window_accepted_share_penalty": "REAL NOT NULL DEFAULT 0",
            "accepting_window_accepted_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "top_two_accepting_window_accepted_share_penalty": "REAL NOT NULL DEFAULT 0",
            "top_two_accepting_window_accepted_size_share_penalty": "REAL NOT NULL DEFAULT 0",
            "accepting_window_accepted_concentration_index_penalty": "REAL NOT NULL DEFAULT 0",
            "accepting_window_accepted_size_concentration_index_penalty": "REAL NOT NULL DEFAULT 0",
            "wallet_count_penalty": "REAL NOT NULL DEFAULT 0",
            "market_count_penalty": "REAL NOT NULL DEFAULT 0",
            "entry_price_band_count_penalty": "REAL NOT NULL DEFAULT 0",
            "time_to_close_band_count_penalty": "REAL NOT NULL DEFAULT 0",
            "wallet_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "market_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "entry_price_band_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "time_to_close_band_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "wallet_size_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "market_size_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "entry_price_band_size_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "time_to_close_band_size_concentration_penalty": "REAL NOT NULL DEFAULT 0",
            "candidate_count": "INTEGER NOT NULL DEFAULT 0",
            "feasible_count": "INTEGER NOT NULL DEFAULT 0",
            "rejected_count": "INTEGER NOT NULL DEFAULT 0",
            "current_candidate_score": "REAL",
            "current_candidate_feasible": "INTEGER NOT NULL DEFAULT 0",
            "current_candidate_total_pnl_usd": "REAL",
            "current_candidate_max_drawdown_pct": "REAL",
            "current_candidate_constraint_failures_json": "TEXT NOT NULL DEFAULT '[]'",
            "current_candidate_result_json": "TEXT NOT NULL DEFAULT '{}'",
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
    _ensure_table_columns(
        conn,
        "replay_promotions",
        {
            "requested_at": "INTEGER NOT NULL DEFAULT 0",
            "finished_at": "INTEGER NOT NULL DEFAULT 0",
            "applied_at": "INTEGER NOT NULL DEFAULT 0",
            "trigger": "TEXT NOT NULL DEFAULT ''",
            "scope": "TEXT NOT NULL DEFAULT 'shadow_only'",
            "source_mode": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT ''",
            "reason": "TEXT NOT NULL DEFAULT ''",
            "replay_search_run_id": "INTEGER",
            "replay_search_candidate_id": "INTEGER",
            "config_json": "TEXT NOT NULL DEFAULT '{}'",
            "previous_config_json": "TEXT NOT NULL DEFAULT '{}'",
            "updated_keys_json": "TEXT NOT NULL DEFAULT '[]'",
            "candidate_result_json": "TEXT NOT NULL DEFAULT '{}'",
            "score": "REAL",
            "score_delta": "REAL",
            "total_pnl_usd": "REAL",
            "pnl_delta_usd": "REAL",
            "shadow_resolved_count": "INTEGER NOT NULL DEFAULT 0",
            "shadow_resolved_since_previous": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_search_runs_request_token ON replay_search_runs(request_token)")
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
            "trade_id": "TEXT NOT NULL DEFAULT ''",
            "market_id": "TEXT NOT NULL DEFAULT ''",
            "question": "TEXT",
            "trader_address": "TEXT NOT NULL DEFAULT ''",
            "side": "TEXT NOT NULL DEFAULT ''",
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
            "price_at_signal": "REAL NOT NULL DEFAULT 0",
            "signal_size_usd": "REAL NOT NULL DEFAULT 0",
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
            "segment_id": "TEXT",
            "policy_id": "TEXT",
            "policy_bundle_version": "INTEGER NOT NULL DEFAULT 0",
            "promotion_epoch_id": "INTEGER NOT NULL DEFAULT 0",
            "experiment_arm": "TEXT NOT NULL DEFAULT 'champion'",
            "expected_edge": "REAL",
            "expected_fill_cost_usd": "REAL",
            "expected_exit_fee_usd": "REAL",
            "expected_close_fixed_cost_usd": "REAL",
            "confidence": "REAL NOT NULL DEFAULT 0",
            "raw_confidence": "REAL",
            "kelly_fraction": "REAL NOT NULL DEFAULT 0",
            "signal_mode": "TEXT",
            "belief_prior": "REAL",
            "belief_blend": "REAL",
            "belief_evidence": "INTEGER",
            "trader_score": "REAL",
            "market_score": "REAL",
            "market_veto": "TEXT",
            "real_money": "INTEGER NOT NULL DEFAULT 0",
            "order_id": "TEXT",
            "skipped": "INTEGER NOT NULL DEFAULT 0",
            "skip_reason": "TEXT",
            "placed_at": "INTEGER NOT NULL DEFAULT 0",
            "resolved_at": "INTEGER",
            "market_resolved_outcome": "TEXT",
            "counterfactual_return": "REAL",
            "resolution_checked_at": "INTEGER NOT NULL DEFAULT 0",
            "resolution_error": "TEXT NOT NULL DEFAULT ''",
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
            "outcome": "INTEGER",
            "shadow_pnl_usd": "REAL",
            "actual_pnl_usd": "REAL",
            "f_trader_win_rate": "REAL",
            "f_trader_n_trades": "INTEGER",
            "f_conviction_ratio": "REAL",
            "f_trader_volume_usd": "REAL",
            "f_trader_avg_size_usd": "REAL",
            "f_account_age_days": "INTEGER",
            "f_consistency": "REAL",
            "f_trader_diversity": "INTEGER",
            "f_days_to_res": "REAL",
            "f_price": "REAL",
            "f_spread_pct": "REAL",
            "f_momentum_1h": "REAL",
            "f_volume_24h_usd": "REAL",
            "f_volume_7d_avg_usd": "REAL",
            "f_volume_trend": "REAL",
            "f_oi_usd": "REAL",
            "f_top_holder_pct": "REAL",
            "f_bid_depth_usd": "REAL",
            "f_ask_depth_usd": "REAL",
            "market_components_json": "TEXT",
            "decision_context_json": "TEXT",
        },
    )
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_trade_log_placed_at ON trade_log(placed_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_outcome ON trade_log(outcome);
        CREATE INDEX IF NOT EXISTS idx_trade_log_trader ON trade_log(trader_address);
        CREATE INDEX IF NOT EXISTS idx_trade_log_real_money ON trade_log(real_money);
        CREATE INDEX IF NOT EXISTS idx_trade_log_skipped ON trade_log(skipped);
        CREATE INDEX IF NOT EXISTS idx_trade_log_real_trader_placed ON trade_log(real_money, trader_address, placed_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_real_market_position ON trade_log(real_money, market_id, token_id, side);
        CREATE INDEX IF NOT EXISTS idx_trade_log_trader_action_skipped ON trade_log(trader_address, source_action, skipped);
        CREATE INDEX IF NOT EXISTS idx_trade_log_resolution_due ON trade_log(real_money, outcome, source_action, resolution_checked_at, market_close_ts, placed_at);
        """
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
    heavy_maintenance_enabled = (
        _startup_heavy_maintenance_enabled(target)
        if run_heavy_maintenance is None
        else bool(run_heavy_maintenance)
    )
    if heavy_maintenance_enabled:
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
                SET experiment_arm = LOWER(TRIM(experiment_arm))
                WHERE experiment_arm IS NOT NULL
                  AND TRIM(experiment_arm) <> ''
                  AND experiment_arm != LOWER(TRIM(experiment_arm))
                """
            )
            conn.execute(
                """
                UPDATE trade_log
                SET experiment_arm = 'champion'
                WHERE COALESCE(TRIM(experiment_arm), '') = ''
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
            rollback_safely(conn, label="heavy startup DB maintenance")
            logger.exception("Heavy startup DB maintenance failed; keeping core schema changes")
    else:
        logger.info(
            "Skipping heavy startup DB maintenance for shared/network path: %s",
            target,
        )


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
