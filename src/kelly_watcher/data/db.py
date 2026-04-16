from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import time
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

from kelly_watcher.data.market_urls import market_url_from_metadata
from kelly_watcher.runtime_paths import TRADING_DB_PATH

DB_PATH = TRADING_DB_PATH
REPAIR_BATCH_SIZE = 250
VERIFIED_BACKUP_RETENTION = 5
RECOVERY_QUARANTINE_RETENTION = 5
RECOVERY_QUARANTINE_UNCOMPRESSED_RETENTION = 1
TRADE_LOG_ARCHIVE_SCHEMA = "trade_log_archive"
_ARCHIVE_OPEN_SIZE_EPSILON = 1e-9
logger = logging.getLogger(__name__)
_RUNTIME_SETTINGS_CACHE: dict[str, str] | None = None
_RUNTIME_SETTINGS_CACHE_AT = 0.0
_RUNTIME_SETTINGS_CACHE_TTL_SECONDS = 1.0
_RUNTIME_SETTINGS_CACHE_LOCK = threading.Lock()
_BACKUP_TIMESTAMP_RE = re.compile(r"(\d{8}_\d{6})")
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
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if apply_runtime_pragmas:
        conn.execute(f"PRAGMA journal_mode={_preferred_journal_mode(path)}")
        conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    return _connect_sqlite(DB_PATH, apply_runtime_pragmas=True)


def get_conn_for_path(path: Path, *, apply_runtime_pragmas: bool = False) -> sqlite3.Connection:
    return _connect_sqlite(Path(path), apply_runtime_pragmas=apply_runtime_pragmas)


def trade_log_archive_db_path(path: Path | None = None) -> Path:
    target = Path(path) if path is not None else DB_PATH
    return target.parent / "archive" / f"{target.stem}_archive{target.suffix}"


def _file_size_metrics(path: Path) -> tuple[int, int]:
    try:
        if not path.exists() or not path.is_file():
            return 0, 0
        stat_result = path.stat()
        logical_size = int(stat_result.st_size)
        allocated_blocks = int(getattr(stat_result, "st_blocks", 0) or 0)
        allocated_size = allocated_blocks * 512 if allocated_blocks > 0 else logical_size
        return logical_size, max(allocated_size, logical_size)
    except OSError:
        return 0, 0


def _quote_identifier(name: str) -> str:
    return f'"{str(name).replace(chr(34), chr(34) * 2)}"'


def _table_exists(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _table_info(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> list[sqlite3.Row]:
    return conn.execute(f"PRAGMA {schema}.table_info({_quote_identifier(table)})").fetchall()


def _is_attached_schema(conn: sqlite3.Connection, schema: str) -> bool:
    rows = conn.execute("PRAGMA database_list").fetchall()
    return any(str(row[1] or "").strip() == schema for row in rows)


def _column_definition_from_info(row: sqlite3.Row) -> str:
    name = _quote_identifier(str(row["name"] or "").strip())
    pieces: list[str] = [name]
    suffix = _column_ddl_suffix_from_info(row)
    if suffix:
        pieces.append(suffix)
    return " ".join(pieces)


def _column_ddl_suffix_from_info(row: sqlite3.Row) -> str:
    pieces: list[str] = []
    declared_type = str(row["type"] or "").strip()
    if declared_type:
        pieces.append(declared_type)
    if int(row["pk"] or 0) > 0:
        pieces.append("PRIMARY KEY")
    elif int(row["notnull"] or 0) > 0:
        pieces.append("NOT NULL")
    default_value = row["dflt_value"]
    if default_value is not None:
        pieces.append(f"DEFAULT {default_value}")
    return " ".join(pieces)


def _ensure_table_columns_in_schema(
    conn: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
    *,
    schema: str = "main",
) -> None:
    existing = {
        str(row["name"] or "").strip()
        for row in _table_info(conn, table, schema=schema)
    }
    for name, ddl_type in columns.items():
        if name in existing:
            continue
        conn.execute(
            f"ALTER TABLE {schema}.{_quote_identifier(table)} "
            f"ADD COLUMN {_quote_identifier(name)} {ddl_type}"
        )


def _ensure_trade_log_archive_attached(
    conn: sqlite3.Connection,
    target: Path,
    *,
    create_if_missing: bool,
) -> Path:
    archive_path = trade_log_archive_db_path(target)
    if not archive_path.exists() and not create_if_missing:
        return archive_path
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if not _is_attached_schema(conn, TRADE_LOG_ARCHIVE_SCHEMA):
        conn.execute(f"ATTACH DATABASE ? AS {TRADE_LOG_ARCHIVE_SCHEMA}", (str(archive_path),))
    return archive_path


def _ensure_trade_log_archive_schema(conn: sqlite3.Connection, target: Path) -> Path:
    archive_path = _ensure_trade_log_archive_attached(conn, target, create_if_missing=True)
    if not _table_exists(conn, "trade_log", schema="main"):
        return archive_path

    main_columns = _table_info(conn, "trade_log", schema="main")
    if not _table_exists(conn, "trade_log", schema=TRADE_LOG_ARCHIVE_SCHEMA):
        column_sql = ",\n            ".join(_column_definition_from_info(row) for row in main_columns)
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TRADE_LOG_ARCHIVE_SCHEMA}.trade_log (
                {column_sql}
            )
            """
        )

    archive_column_ddls = {
        str(row["name"] or "").strip(): _column_ddl_suffix_from_info(row)
        for row in main_columns
        if str(row["name"] or "").strip()
    }
    _ensure_table_columns_in_schema(
        conn,
        "trade_log",
        archive_column_ddls,
        schema=TRADE_LOG_ARCHIVE_SCHEMA,
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {TRADE_LOG_ARCHIVE_SCHEMA}.idx_trade_log_archive_placed_at "
        "ON trade_log(placed_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {TRADE_LOG_ARCHIVE_SCHEMA}.idx_trade_log_archive_resolved_at "
        "ON trade_log(resolved_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {TRADE_LOG_ARCHIVE_SCHEMA}.idx_trade_log_archive_real_money "
        "ON trade_log(real_money)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {TRADE_LOG_ARCHIVE_SCHEMA}.idx_trade_log_archive_trader "
        "ON trade_log(trader_address)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {TRADE_LOG_ARCHIVE_SCHEMA}.idx_trade_log_archive_segment "
        "ON trade_log(segment_id)"
    )
    return archive_path


def _trade_log_select_list(
    available_columns: set[str],
    all_columns: list[str],
    *,
    table_expr: str,
) -> str:
    select_parts: list[str] = []
    for column in all_columns:
        quoted = _quote_identifier(column)
        if column in available_columns:
            select_parts.append(f"{table_expr}.{quoted} AS {quoted}")
        else:
            select_parts.append(f"NULL AS {quoted}")
    return ", ".join(select_parts)


def _attach_trade_log_archive_read_view(conn: sqlite3.Connection, target: Path) -> bool:
    try:
        archive_path = _ensure_trade_log_archive_attached(conn, target, create_if_missing=False)
        if not archive_path.exists() or not _table_exists(conn, "trade_log", schema=TRADE_LOG_ARCHIVE_SCHEMA):
            return False
        main_columns = [str(row["name"] or "").strip() for row in _table_info(conn, "trade_log", schema="main")]
        if not main_columns:
            return False
        archive_columns = {
            str(row["name"] or "").strip()
            for row in _table_info(conn, "trade_log", schema=TRADE_LOG_ARCHIVE_SCHEMA)
        }
        conn.execute("DROP VIEW IF EXISTS temp.trade_log")
        conn.execute(
            f"""
            CREATE TEMP VIEW trade_log AS
            SELECT {_trade_log_select_list(set(main_columns), main_columns, table_expr='main.trade_log')}
            FROM main.trade_log
            UNION ALL
            SELECT {_trade_log_select_list(archive_columns, main_columns, table_expr=f'{TRADE_LOG_ARCHIVE_SCHEMA}.trade_log')}
            FROM {TRADE_LOG_ARCHIVE_SCHEMA}.trade_log
            """
        )
        return True
    except sqlite3.DatabaseError as exc:
        logger.warning("Failed to attach trade_log archive read view: %s", exc)
        return False


def get_trade_log_read_conn(
    path: Path | None = None,
    *,
    apply_runtime_pragmas: bool = False,
    include_archive: bool = True,
) -> sqlite3.Connection:
    target = Path(path) if path is not None else DB_PATH
    conn = get_conn_for_path(target, apply_runtime_pragmas=apply_runtime_pragmas)
    if include_archive:
        _attach_trade_log_archive_read_view(conn, target)
    return conn


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


def _normalize_wallet_addresses(wallet_addresses: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for wallet in wallet_addresses or []:
        address = str(wallet or "").strip().lower()
        if not address or address in seen:
            continue
        seen.add(address)
        normalized.append(address)
    return normalized


def _read_env_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _parse_env_items(path: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for raw_line in _read_env_lines(path):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = str(key or "").strip().upper()
        if not normalized_key:
            continue
        items.append((normalized_key, str(value or "").strip()))
    return items


def _write_env_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not lines:
        path.write_text("", encoding="utf-8")
        return
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _runtime_settings_table_exists(conn: sqlite3.Connection) -> bool:
    return _table_exists(conn, "runtime_settings", schema="main")


def _ensure_runtime_settings_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL DEFAULT '',
            updated_at  INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def _invalidate_runtime_settings_cache() -> None:
    global _RUNTIME_SETTINGS_CACHE, _RUNTIME_SETTINGS_CACHE_AT
    with _RUNTIME_SETTINGS_CACHE_LOCK:
        _RUNTIME_SETTINGS_CACHE = None
        _RUNTIME_SETTINGS_CACHE_AT = 0.0


def load_runtime_settings(
    *,
    force: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, str]:
    global _RUNTIME_SETTINGS_CACHE, _RUNTIME_SETTINGS_CACHE_AT

    if conn is None:
        now = time.time()
        with _RUNTIME_SETTINGS_CACHE_LOCK:
            if (
                not force
                and _RUNTIME_SETTINGS_CACHE is not None
                and (now - _RUNTIME_SETTINGS_CACHE_AT) < _RUNTIME_SETTINGS_CACHE_TTL_SECONDS
            ):
                return dict(_RUNTIME_SETTINGS_CACHE)

    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    assert conn is not None
    try:
        _ensure_runtime_settings_table(conn)
        rows = conn.execute(
            "SELECT key, value FROM runtime_settings ORDER BY key ASC"
        ).fetchall()
        payload = {
            str(row["key"] or "").strip().upper(): str(row["value"] or "").strip()
            for row in rows
            if str(row["key"] or "").strip()
        }
    finally:
        if owns_conn:
            conn.close()

    if owns_conn:
        with _RUNTIME_SETTINGS_CACHE_LOCK:
            _RUNTIME_SETTINGS_CACHE = dict(payload)
            _RUNTIME_SETTINGS_CACHE_AT = time.time()
    return payload


def get_runtime_setting(
    key: str,
    *,
    conn: sqlite3.Connection | None = None,
    force: bool = False,
) -> str | None:
    normalized_key = str(key or "").strip().upper()
    if not normalized_key:
        return None
    return load_runtime_settings(force=force, conn=conn).get(normalized_key)


def set_runtime_setting(
    key: str,
    value: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    normalized_key = str(key or "").strip().upper()
    if not normalized_key:
        raise ValueError("Runtime setting key cannot be blank")
    text_value = str(value or "").strip()

    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    assert conn is not None
    try:
        _ensure_runtime_settings_table(conn)
        conn.execute(
            """
            INSERT INTO runtime_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (normalized_key, text_value, int(time.time())),
        )
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
    _invalidate_runtime_settings_cache()


def delete_runtime_setting(
    key: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    normalized_key = str(key or "").strip().upper()
    if not normalized_key:
        return
    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    assert conn is not None
    try:
        _ensure_runtime_settings_table(conn)
        conn.execute("DELETE FROM runtime_settings WHERE key=?", (normalized_key,))
        conn.commit()
    finally:
        if owns_conn:
            conn.close()
    _invalidate_runtime_settings_cache()


def bootstrap_runtime_settings_from_env(
    env_path: Path,
    *,
    env_only_keys: set[str] | frozenset[str],
    bootstrap_env_keys: set[str] | frozenset[str] | None = None,
) -> dict[str, object]:
    path = Path(env_path)
    if not path.exists():
        return {"imported_keys": [], "imported_wallets": 0, "stripped_keys": []}

    bootstrap_keys = {str(key or "").strip().upper() for key in (bootstrap_env_keys or set()) if str(key or "").strip()}
    env_only = {str(key or "").strip().upper() for key in env_only_keys if str(key or "").strip()}
    original_lines = _read_env_lines(path)
    if not original_lines:
        return {"imported_keys": [], "imported_wallets": 0, "stripped_keys": []}

    migratable: dict[str, str] = {}
    bootstrap_wallets: list[str] = []
    retained_lines: list[str] = []
    stripped_keys: list[str] = []

    for raw_line in original_lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            retained_lines.append(raw_line)
            continue

        raw_key, raw_value = raw_line.split("=", 1)
        key = str(raw_key or "").strip().upper()
        value = str(raw_value or "").strip()
        if not key:
            retained_lines.append(raw_line)
            continue
        if key in env_only:
            retained_lines.append(raw_line)
            continue
        if key in bootstrap_keys:
            bootstrap_wallets = _normalize_wallet_addresses(value.split(","))
            stripped_keys.append(key)
            continue
        migratable[key] = value
        stripped_keys.append(key)

    imported_wallets = 0
    imported_keys: list[str] = []
    if migratable or bootstrap_wallets:
        init_db()
    for key, value in sorted(migratable.items()):
        set_runtime_setting(key, value)
        imported_keys.append(key)
    if bootstrap_wallets:
        imported_wallets = import_managed_wallets_from_env(bootstrap_wallets)

    if stripped_keys:
        while retained_lines and not retained_lines[-1].strip():
            retained_lines.pop()
        _write_env_lines(path, retained_lines)

    return {
        "imported_keys": imported_keys,
        "imported_wallets": imported_wallets,
        "stripped_keys": sorted(stripped_keys),
    }


def load_managed_wallets(
    *,
    include_disabled: bool = False,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    assert conn is not None
    try:
        if not _table_exists(conn, "managed_wallets"):
            return []
        if include_disabled:
            rows = conn.execute(
                """
                SELECT wallet_address
                FROM managed_wallets
                ORDER BY tracking_enabled DESC, added_at ASC, wallet_address ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT wallet_address
                FROM managed_wallets
                WHERE COALESCE(tracking_enabled, 1)=1
                ORDER BY added_at ASC, wallet_address ASC
                """
            ).fetchall()
        return [str(row["wallet_address"] or "").strip().lower() for row in rows if str(row["wallet_address"] or "").strip()]
    finally:
        if owns_conn:
            conn.close()


def load_managed_wallet_registry_rows(
    *,
    include_disabled: bool = False,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, object]]:
    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    assert conn is not None
    try:
        if not _table_exists(conn, "managed_wallets"):
            return []
        query = """
            SELECT wallet_address,
                   tracking_enabled,
                   source,
                   added_at,
                   updated_at,
                   disabled_at,
                   disabled_reason,
                   metadata_json
            FROM managed_wallets
        """
        params: tuple[object, ...] = ()
        if not include_disabled:
            query += " WHERE COALESCE(tracking_enabled, 1)=1"
        query += " ORDER BY tracking_enabled DESC, added_at ASC, wallet_address ASC"
        rows = conn.execute(query, params).fetchall()
        registry_rows: list[dict[str, object]] = []
        for row in rows:
            wallet = str(row["wallet_address"] or "").strip().lower()
            if not wallet:
                continue
            metadata_json = str(row["metadata_json"] or "{}")
            try:
                metadata = json.loads(metadata_json)
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            registry_rows.append(
                {
                    "wallet_address": wallet,
                    "tracking_enabled": bool(int(row["tracking_enabled"] or 0)),
                    "source": str(row["source"] or "").strip(),
                    "added_at": int(row["added_at"] or 0),
                    "updated_at": int(row["updated_at"] or 0),
                    "disabled_at": (int(row["disabled_at"]) if row["disabled_at"] is not None else None),
                    "disabled_reason": str(row["disabled_reason"] or "").strip(),
                    "metadata": metadata,
                }
            )
        return registry_rows
    finally:
        if owns_conn:
            conn.close()


def managed_wallet_registry_updated_at(conn: sqlite3.Connection | None = None) -> int:
    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    assert conn is not None
    try:
        if not _table_exists(conn, "managed_wallets"):
            return 0
        row = conn.execute("SELECT MAX(updated_at) AS updated_at FROM managed_wallets").fetchone()
        if row is None:
            return 0
        return int(row["updated_at"] or 0)
    finally:
        if owns_conn:
            conn.close()


def managed_wallet_registry_state(conn: sqlite3.Connection | None = None) -> dict[str, object]:
    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    assert conn is not None
    try:
        active_wallets = load_managed_wallets(conn=conn)
        all_wallets = load_managed_wallets(include_disabled=True, conn=conn)
        return {
            "managed_wallets": active_wallets,
            "managed_wallet_count": len(active_wallets),
            "managed_wallet_total_count": len(all_wallets),
            "managed_wallet_registry_updated_at": managed_wallet_registry_updated_at(conn=conn),
        }
    finally:
        if owns_conn:
            conn.close()


def load_wallet_promotion_state(
    wallet_addresses: list[str] | tuple[str, ...] | set[str] | None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, dict[str, object]]:
    wallets = _normalize_wallet_addresses(wallet_addresses)
    if not wallets:
        return {}

    owns_conn = conn is None
    if owns_conn:
        conn = get_conn()
    assert conn is not None

    placeholders = ",".join("?" for _ in wallets)
    states: dict[str, dict[str, object]] = {
        wallet: {
            "wallet_address": wallet,
            "managed_source": "",
            "managed_added_at": 0,
            "managed_updated_at": 0,
            "tracking_started_at": 0,
            "reactivated_at": 0,
            "event_action": "",
            "event_source": "",
            "event_reason": "",
            "event_created_at": 0,
            "event_payload": {},
            "promoted_at": 0,
            "baseline_at": 0,
            "boundary_action": "",
            "boundary_source": "",
            "boundary_reason": "",
            "boundary_at": 0,
            "boundary_payload": {},
            "promotion_source": "",
            "promotion_reason": "",
            "promotion_payload": {},
            "is_auto_promoted": False,
        }
        for wallet in wallets
    }

    try:
        if _table_exists(conn, "managed_wallets"):
            managed_rows = conn.execute(
                f"""
                SELECT wallet_address, source, added_at, updated_at, metadata_json
                FROM managed_wallets
                WHERE wallet_address IN ({placeholders})
                """,
                tuple(wallets),
            ).fetchall()
            for row in managed_rows:
                wallet = str(row["wallet_address"] or "").strip().lower()
                if not wallet or wallet not in states:
                    continue
                metadata_json = str(row["metadata_json"] or "{}")
                try:
                    metadata = json.loads(metadata_json)
                except json.JSONDecodeError:
                    metadata = {}
                if not isinstance(metadata, dict):
                    metadata = {}
                state = states[wallet]
                state["managed_source"] = str(row["source"] or "").strip()
                state["managed_added_at"] = int(row["added_at"] or 0)
                state["managed_updated_at"] = int(row["updated_at"] or 0)
                if str(row["source"] or "").strip().lower() == "auto_promoted":
                    state["is_auto_promoted"] = True
                    state["promotion_payload"] = metadata
                    state["promotion_source"] = str(metadata.get("promotion_source") or row["source"] or "").strip()
                    state["promotion_reason"] = str(metadata.get("promotion_reason") or "").strip()
                    state["promoted_at"] = int(metadata.get("promoted_at") or row["added_at"] or row["updated_at"] or 0)

        if _table_exists(conn, "wallet_watch_state"):
            watch_rows = conn.execute(
                f"""
                SELECT wallet_address, tracking_started_at, reactivated_at
                FROM wallet_watch_state
                WHERE wallet_address IN ({placeholders})
                """,
                tuple(wallets),
            ).fetchall()
            for row in watch_rows:
                wallet = str(row["wallet_address"] or "").strip().lower()
                if not wallet or wallet not in states:
                    continue
                state = states[wallet]
                state["tracking_started_at"] = int(row["tracking_started_at"] or 0)
                state["reactivated_at"] = int(row["reactivated_at"] or 0)

        if _table_exists(conn, "wallet_membership_events"):
            event_rows = conn.execute(
                f"""
                SELECT wallet_address, action, source, reason, payload_json, created_at, id
                FROM wallet_membership_events
                WHERE wallet_address IN ({placeholders})
                  AND (
                    LOWER(COALESCE(action, ''))='promote'
                    OR LOWER(COALESCE(action, ''))='reactivate'
                    OR LOWER(COALESCE(action, ''))='restore'
                    OR LOWER(COALESCE(source, ''))='auto_promoted'
                  )
                ORDER BY wallet_address ASC, created_at DESC, id DESC
                """,
                tuple(wallets),
            ).fetchall()
            seen_promotions: set[str] = set()
            seen_boundaries: set[str] = set()
            for row in event_rows:
                wallet = str(row["wallet_address"] or "").strip().lower()
                if not wallet or wallet not in states:
                    continue
                payload_json = str(row["payload_json"] or "{}")
                try:
                    payload = json.loads(payload_json)
                except json.JSONDecodeError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                action = str(row["action"] or "").strip().lower()
                source = str(row["source"] or "").strip().lower()
                state = states[wallet]
                if action in {"reactivate", "restore"} and wallet not in seen_boundaries:
                    seen_boundaries.add(wallet)
                    state["boundary_action"] = str(row["action"] or "").strip()
                    state["boundary_source"] = str(row["source"] or "").strip()
                    state["boundary_reason"] = str(row["reason"] or "").strip()
                    state["boundary_at"] = int(
                        payload.get("baseline_at")
                        or payload.get("reactivated_at")
                        or row["created_at"]
                        or 0
                    )
                    state["boundary_payload"] = payload
                if (
                    action == "promote"
                    or source == "auto_promoted"
                ) and wallet not in seen_promotions:
                    seen_promotions.add(wallet)
                    state["event_action"] = str(row["action"] or "").strip()
                    state["event_source"] = str(row["source"] or "").strip()
                    state["event_reason"] = str(row["reason"] or "").strip()
                    state["event_created_at"] = int(row["created_at"] or 0)
                    state["event_payload"] = payload
                    state["promotion_payload"] = payload or state["promotion_payload"]
                    state["promotion_source"] = str(
                        payload.get("promotion_source") or row["source"] or state["promotion_source"] or ""
                    ).strip()
                    state["promotion_reason"] = str(
                        row["reason"] or payload.get("promotion_reason") or state["promotion_reason"] or ""
                    ).strip()
                    state["promoted_at"] = int(
                        payload.get("promoted_at") or row["created_at"] or state["promoted_at"] or 0
                    )
                    state["is_auto_promoted"] = True

        for wallet, state in states.items():
            if not bool(state.get("is_auto_promoted")):
                continue
            promoted_at = int(state.get("promoted_at") or 0)
            tracking_started_at = int(state.get("tracking_started_at") or 0)
            reactivated_at = int(state.get("reactivated_at") or 0)
            boundary_at = int(state.get("boundary_at") or 0)
            if reactivated_at > promoted_at:
                boundary_at = max(boundary_at, reactivated_at)
            if tracking_started_at > promoted_at:
                boundary_at = max(boundary_at, tracking_started_at)
            if boundary_at > promoted_at:
                baseline_at = boundary_at
            elif promoted_at > 0:
                baseline_at = promoted_at
            else:
                baseline_at = tracking_started_at
            state["baseline_at"] = baseline_at
            if not state.get("promotion_source"):
                state["promotion_source"] = str(state.get("managed_source") or "").strip()
        return states
    finally:
        if owns_conn:
            conn.close()


def _wallet_membership_event_rows(
    wallets: list[str],
    *,
    action: str,
    source: str,
    reason: str = "",
    payload: dict[str, Any] | None = None,
    created_at: int,
) -> list[tuple[str, str, str, str, str, int]]:
    payload_json = json.dumps(payload or {}, separators=(",", ":"), sort_keys=True)
    return [
        (
            wallet,
            action,
            source,
            reason,
            payload_json,
            created_at,
        )
        for wallet in wallets
    ]


def upsert_managed_wallets(
    wallet_addresses: list[str] | tuple[str, ...] | set[str] | None,
    *,
    source: str,
    action: str,
    reason: str = "",
    payload: dict[str, Any] | None = None,
    tracking_enabled: bool = True,
) -> int:
    wallets = _normalize_wallet_addresses(wallet_addresses)
    if not wallets:
        return 0

    now_ts = int(time.time())
    metadata_json = json.dumps(payload or {}, separators=(",", ":"), sort_keys=True)
    event_rows = _wallet_membership_event_rows(
        wallets,
        action=action,
        source=source,
        reason=reason,
        payload=payload,
        created_at=now_ts,
    )
    watch_state_rows = [
        (
            wallet,
            now_ts,
            now_ts,
        )
        for wallet in wallets
        if tracking_enabled
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
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                    tracking_enabled=excluded.tracking_enabled,
                    source=excluded.source,
                    updated_at=excluded.updated_at,
                    disabled_at=CASE WHEN excluded.tracking_enabled=1 THEN NULL ELSE managed_wallets.disabled_at END,
                    disabled_reason=CASE WHEN excluded.tracking_enabled=1 THEN NULL ELSE managed_wallets.disabled_reason END,
                    metadata_json=excluded.metadata_json
                """,
                [
                    (
                        wallet,
                        1 if tracking_enabled else 0,
                        source,
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
            if watch_state_rows:
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
                    if tracking_enabled
                ],
            )
        return len(wallets)
    finally:
        conn.close()


def import_managed_wallets_from_env(wallet_addresses: list[str] | tuple[str, ...] | set[str] | None) -> int:
    return upsert_managed_wallets(
        wallet_addresses,
        source="seed_env",
        action="import",
        reason="one-time bootstrap from WATCHED_WALLETS",
        tracking_enabled=True,
    )


def restore_managed_wallets_from_snapshot(
    wallet_addresses: list[str] | tuple[str, ...] | set[str] | None,
    *,
    source: str = "shadow_reset",
) -> int:
    return upsert_managed_wallets(
        wallet_addresses,
        source=source,
        action="restore",
        reason="shadow reset registry restore",
        tracking_enabled=True,
    )


def restore_managed_wallet_registry_records(
    wallet_records: list[dict[str, object]] | tuple[dict[str, object], ...] | None,
    *,
    source: str = "shadow_reset",
) -> int:
    normalized_records: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_record in wallet_records or ():
        if not isinstance(raw_record, dict):
            continue
        wallet = str(raw_record.get("wallet_address") or "").strip().lower()
        if not wallet or wallet in seen:
            continue
        seen.add(wallet)
        metadata = raw_record.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        tracking_enabled = bool(raw_record.get("tracking_enabled", True))
        disabled_at_raw = raw_record.get("disabled_at")
        try:
            disabled_at = int(disabled_at_raw) if disabled_at_raw is not None else None
        except (TypeError, ValueError):
            disabled_at = None
        normalized_records.append(
            {
                "wallet_address": wallet,
                "tracking_enabled": tracking_enabled,
                "source": str(raw_record.get("source") or "seed_env").strip() or "seed_env",
                "disabled_at": disabled_at if not tracking_enabled else None,
                "disabled_reason": str(raw_record.get("disabled_reason") or "").strip(),
                "metadata": metadata,
            }
        )
    if not normalized_records:
        return 0

    now_ts = int(time.time())
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet_address) DO UPDATE SET
                    tracking_enabled=excluded.tracking_enabled,
                    source=excluded.source,
                    updated_at=excluded.updated_at,
                    disabled_at=excluded.disabled_at,
                    disabled_reason=excluded.disabled_reason,
                    metadata_json=excluded.metadata_json
                """,
                [
                    (
                        str(record["wallet_address"]),
                        1 if bool(record["tracking_enabled"]) else 0,
                        str(record["source"]),
                        now_ts,
                        now_ts,
                        record["disabled_at"],
                        str(record["disabled_reason"]),
                        json.dumps(record["metadata"], separators=(",", ":"), sort_keys=True),
                    )
                    for record in normalized_records
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
                [
                    (
                        str(record["wallet_address"]),
                        "restore",
                        source,
                        "shadow reset registry restore",
                        json.dumps(
                            {
                                "baseline_at": now_ts,
                                "restored_source": str(record["source"]),
                                "tracking_enabled": bool(record["tracking_enabled"]),
                                "metadata": record["metadata"],
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        now_ts,
                    )
                    for record in normalized_records
                ],
            )
            active_records = [record for record in normalized_records if bool(record["tracking_enabled"])]
            if active_records:
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
                            str(record["wallet_address"]),
                            now_ts,
                            now_ts,
                            now_ts,
                            now_ts,
                        )
                        for record in active_records
                    ],
                )
        return len(normalized_records)
    finally:
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


def _compressed_backup_history_path(path: Path) -> Path:
    candidate = Path(f"{path}.gz")
    if not candidate.exists():
        return candidate
    for attempt in range(1, 1000):
        fallback = Path(f"{path}.{attempt}.gz")
        if not fallback.exists():
            return fallback
    return Path(f"{path}.{time.time_ns()}.gz")


def _is_compressed_backup_path(path: Path) -> bool:
    return str(path.name).lower().endswith(".gz")


def _filename_timestamp(path: Path) -> float:
    match = _BACKUP_TIMESTAMP_RE.search(str(path.name))
    if not match:
        return 0.0
    try:
        return time.mktime(time.strptime(match.group(1), "%Y%m%d_%H%M%S"))
    except ValueError:
        return 0.0


def _path_recency_sort_key(path: Path) -> tuple[float, float, str]:
    try:
        mtime = float(path.stat().st_mtime)
    except OSError:
        mtime = 0.0
    return (_filename_timestamp(path), mtime, str(path))


def _verified_backup_history_paths(path: Path) -> list[Path]:
    backup_dir = _backup_root(path)
    if not backup_dir.exists():
        return []
    return sorted(
        (
            candidate
            for candidate in backup_dir.iterdir()
            if candidate.is_file()
            and str(candidate.name).startswith(f"{path.stem}.")
            and (
                str(candidate.name).endswith(path.suffix)
                or str(candidate.name).endswith(f"{path.suffix}.gz")
            )
        ),
        key=_path_recency_sort_key,
        reverse=True,
    )


def _compress_backup_file(source: Path) -> Path:
    target = _compressed_backup_history_path(source)
    with source.open("rb") as input_handle, gzip.open(target, "wb", compresslevel=6) as output_handle:
        shutil.copyfileobj(input_handle, output_handle)
    source.unlink()
    return target


def _compress_verified_backup_history(path: Path) -> None:
    for candidate in _verified_backup_history_paths(path):
        if _is_compressed_backup_path(candidate):
            continue
        try:
            _compress_backup_file(candidate)
        except OSError:
            logger.warning("Failed to compress verified DB backup history %s", candidate, exc_info=True)


def _prune_verified_backup_history(path: Path, *, keep: int = VERIFIED_BACKUP_RETENTION) -> None:
    for candidate in _verified_backup_history_paths(path)[max(int(keep or 0), 0):]:
        try:
            candidate.unlink()
        except OSError:
            logger.warning("Failed to prune old verified DB backup %s", candidate, exc_info=True)


def _prepared_backup_candidate(candidate: Path) -> tuple[Path, Path | None]:
    if not _is_compressed_backup_path(candidate):
        return candidate, None

    suffix = candidate.with_suffix("").suffix or ".db"
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{candidate.stem}.",
        suffix=suffix,
        dir=str(candidate.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with gzip.open(candidate, "rb") as input_handle, temp_path.open("wb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return temp_path, temp_path


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

    if primary_backup.exists():
        previous_integrity = database_integrity_state(primary_backup)
        if previous_integrity.get("db_integrity_known") and previous_integrity.get("db_integrity_ok"):
            archived_backup = _timestamped_backup_path(source)
            primary_backup.replace(archived_backup)
            try:
                archived_backup = _compress_backup_file(archived_backup)
            except OSError:
                logger.warning("Failed to compress rotated verified DB backup %s", archived_backup, exc_info=True)
        else:
            try:
                primary_backup.unlink()
            except OSError:
                logger.warning("Failed to remove stale DB backup %s", primary_backup, exc_info=True)

    tmp_backup.replace(primary_backup)
    _compress_verified_backup_history(source)
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


def _backup_candidate_kind(path: Path, candidate: Path) -> str:
    return "primary_backup" if candidate == _primary_backup_path(path) else "backup_history"


def _backup_candidate_inventory_entry(
    path: Path,
    candidate: Path,
    *,
    ready: bool,
    message: str,
) -> dict[str, object]:
    try:
        candidate_mtime = int(candidate.stat().st_mtime)
    except OSError:
        candidate_mtime = 0
    return {
        "path": str(candidate),
        "kind": _backup_candidate_kind(path, candidate),
        "compressed": _is_compressed_backup_path(candidate),
        "ready": bool(ready),
        "selected": False,
        "mtime": candidate_mtime,
        "message": str(message or "").strip(),
    }


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
            "db_recovery_inventory": [],
            "db_recovery_inventory_count": 0,
        }

    latest_valid_backup: Path | None = None
    latest_valid_backup_at = 0
    failure_message = ""
    inventory: list[dict[str, object]] = []
    for candidate in candidates:
        prepared_candidate, cleanup_candidate = _prepared_backup_candidate(candidate)
        try:
            integrity = database_integrity_state(prepared_candidate)
            candidate_ready = bool(integrity.get("db_integrity_known")) and bool(integrity.get("db_integrity_ok"))
            detail = str(integrity.get("db_integrity_message") or "").strip()
            inventory.append(
                _backup_candidate_inventory_entry(
                    target,
                    candidate,
                    ready=candidate_ready,
                    message="" if candidate_ready else detail.splitlines()[0].strip(),
                )
            )
            if candidate_ready:
                latest_valid_backup = candidate
                latest_valid_backup_at = int(candidate.stat().st_mtime)
                break
            if not failure_message:
                failure_message = "backup integrity check failed"
                if detail:
                    failure_message += f": {detail.splitlines()[0].strip()}"
        finally:
            if cleanup_candidate is not None:
                try:
                    cleanup_candidate.unlink()
                except OSError:
                    pass

    if latest_valid_backup is not None:
        selected_path = str(latest_valid_backup)
        for entry in inventory:
            if str(entry.get("path") or "").strip() == selected_path:
                entry["selected"] = True
                break

    return {
        "db_recovery_state_known": True,
        "db_recovery_candidate_ready": latest_valid_backup is not None,
        "db_recovery_candidate_path": str(latest_valid_backup) if latest_valid_backup is not None else "",
        "db_recovery_candidate_source_path": str(target) if latest_valid_backup is not None else "",
        "db_recovery_candidate_message": "" if latest_valid_backup is not None else failure_message,
        "db_recovery_latest_verified_backup_path": str(latest_valid_backup) if latest_valid_backup is not None else "",
        "db_recovery_latest_verified_backup_at": latest_valid_backup_at,
        "db_recovery_inventory": inventory,
        "db_recovery_inventory_count": len(inventory),
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
    raw_pattern = f"{path.stem}.pre_recovery.*{path.suffix}"
    compressed_pattern = f"{path.stem}.pre_recovery.*{path.suffix}.gz"
    families: dict[Path, tuple[float, float, str]] = {}
    for candidate in list(quarantine_dir.glob(raw_pattern)) + list(quarantine_dir.glob(compressed_pattern)):
        if not candidate.is_file():
            continue
        base_candidate = _recovery_quarantine_base_path(candidate)
        family_mtime = _recovery_quarantine_family_mtime(base_candidate)
        family_sort_key = (_filename_timestamp(base_candidate), family_mtime, str(base_candidate))
        existing_sort_key = families.get(base_candidate)
        if existing_sort_key is None or family_sort_key > existing_sort_key:
            families[base_candidate] = family_sort_key
    return [
        candidate
        for candidate, _ in sorted(
            families.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def _recovery_quarantine_base_path(path: Path) -> Path:
    text = str(path)
    if text.endswith(".gz"):
        return Path(text[:-3])
    return path


def _recovery_quarantine_family_paths(path: Path) -> tuple[Path, ...]:
    base = _recovery_quarantine_base_path(path)
    return (
        base,
        Path(f"{base}.gz"),
        Path(f"{base}-wal"),
        Path(f"{base}-wal.gz"),
        Path(f"{base}-shm"),
        Path(f"{base}-shm.gz"),
    )


def _recovery_quarantine_family_mtime(path: Path) -> float:
    latest_mtime = 0.0
    for candidate in _recovery_quarantine_family_paths(path):
        try:
            if candidate.exists() and candidate.is_file():
                latest_mtime = max(latest_mtime, float(candidate.stat().st_mtime))
        except OSError:
            continue
    return latest_mtime


def _compress_recovery_quarantine_history(
    path: Path,
    *,
    keep_uncompressed: int | None = None,
) -> None:
    raw_keep = max(
        int(
            RECOVERY_QUARANTINE_UNCOMPRESSED_RETENTION
            if keep_uncompressed is None
            else keep_uncompressed or 0
        ),
        0,
    )
    for candidate in _recovery_quarantine_db_paths(path)[raw_keep:]:
        for raw_path in (candidate, Path(f"{candidate}-wal"), Path(f"{candidate}-shm")):
            if not raw_path.exists() or not raw_path.is_file():
                continue
            compressed_path = Path(f"{raw_path}.gz")
            if compressed_path.exists():
                try:
                    raw_path.unlink()
                except OSError:
                    logger.warning(
                        "Failed to remove already-compressed DB recovery quarantine file %s",
                        raw_path,
                        exc_info=True,
                    )
                continue
            try:
                with raw_path.open("rb") as input_handle, gzip.open(compressed_path, "wb", compresslevel=6) as output_handle:
                    shutil.copyfileobj(input_handle, output_handle)
                raw_path.unlink()
            except OSError:
                logger.warning(
                    "Failed to compress DB recovery quarantine file %s",
                    raw_path,
                    exc_info=True,
                )


def _prune_recovery_quarantine(path: Path, *, keep: int | None = None) -> None:
    retention = max(int(RECOVERY_QUARANTINE_RETENTION if keep is None else keep or 0), 0)
    for candidate in _recovery_quarantine_db_paths(path)[retention:]:
        for cleanup_path in _recovery_quarantine_family_paths(candidate):
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

    prepared_candidate, cleanup_candidate = _prepared_backup_candidate(candidate)
    try:
        candidate_integrity = database_integrity_state(prepared_candidate)
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
            src_conn = _connect_sqlite(prepared_candidate, apply_runtime_pragmas=False)
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

        _compress_recovery_quarantine_history(target)
        _prune_recovery_quarantine(target)
        return {
            "ok": True,
            "backup_path": str(candidate),
            "restored_path": str(target),
            "quarantined_path": quarantined_path,
            "message": "verified backup restored",
            "restored_at": int(target.stat().st_mtime),
        }
    finally:
        if cleanup_candidate is not None:
            try:
                cleanup_candidate.unlink()
            except OSError:
                pass


def archive_old_trade_log_rows(
    path: Path | None = None,
    *,
    cutoff_ts: int,
    preserve_since_ts: int = 0,
    batch_size: int = 10_000,
    vacuum: bool = True,
) -> dict[str, object]:
    target = Path(path) if path is not None else DB_PATH
    archive_path = trade_log_archive_db_path(target)
    cutoff = max(int(cutoff_ts or 0), 0)
    preserve_since = max(int(preserve_since_ts or 0), 0)
    limit = max(int(batch_size or 0), 1)
    result: dict[str, object] = {
        "ok": True,
        "db_path": str(target),
        "archive_path": str(archive_path),
        "candidate_count": 0,
        "archived_count": 0,
        "deleted_count": 0,
        "preserve_since_ts": preserve_since,
        "cutoff_ts": cutoff,
        "vacuumed": False,
        "message": "",
    }

    if cutoff <= 0:
        result["message"] = "archive cutoff timestamp must be positive"
        return result
    if not target.exists():
        result["message"] = "database not found"
        return result

    integrity = database_integrity_state(target)
    if integrity.get("db_integrity_known") and not integrity.get("db_integrity_ok"):
        result["ok"] = False
        result["message"] = "database integrity check failed; skipping archive maintenance"
        return result

    conn: sqlite3.Connection | None = None
    try:
        conn = get_conn_for_path(target, apply_runtime_pragmas=True)
        archive_path = _ensure_trade_log_archive_schema(conn, target)
        main_columns = [str(row["name"] or "").strip() for row in _table_info(conn, "trade_log", schema="main")]
        column_sql = ", ".join(_quote_identifier(column) for column in main_columns if column)
        if not column_sql:
            result["message"] = "trade_log schema unavailable"
            return result

        candidate_where, params = _trade_log_archive_candidate_where(
            cutoff_ts=cutoff,
            preserve_since_ts=preserve_since,
        )

        conn.execute("DROP TABLE IF EXISTS temp.trade_log_archive_candidates")
        conn.execute(
            f"""
            CREATE TEMP TABLE trade_log_archive_candidates AS
            SELECT id
            FROM main.trade_log
            WHERE {" AND ".join(candidate_where)}
            ORDER BY COALESCE(resolved_at, exited_at, label_applied_at, placed_at, 0) ASC, id ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        candidate_row = conn.execute(
            "SELECT COUNT(*) AS n FROM temp.trade_log_archive_candidates"
        ).fetchone()
        candidate_count = max(int((candidate_row or {})["n"] or 0), 0) if candidate_row is not None else 0
        result["candidate_count"] = candidate_count
        if candidate_count <= 0:
            result["message"] = "no eligible trade_log rows to archive"
            return result

        with conn:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {TRADE_LOG_ARCHIVE_SCHEMA}.trade_log ({column_sql})
                SELECT {column_sql}
                FROM main.trade_log
                WHERE id IN (SELECT id FROM temp.trade_log_archive_candidates)
                """
            )
            archived_row = conn.execute(
                f"""
                SELECT COUNT(*) AS n
                FROM {TRADE_LOG_ARCHIVE_SCHEMA}.trade_log
                WHERE id IN (SELECT id FROM temp.trade_log_archive_candidates)
                """
            ).fetchone()
            archived_count = max(int((archived_row or {})["n"] or 0), 0) if archived_row is not None else 0
            delete_cursor = conn.execute(
                f"""
                DELETE FROM main.trade_log
                WHERE id IN (SELECT id FROM temp.trade_log_archive_candidates)
                  AND id IN (SELECT id FROM {TRADE_LOG_ARCHIVE_SCHEMA}.trade_log)
                """
            )
            deleted_count = max(int(delete_cursor.rowcount or 0), 0)

        result["archived_count"] = archived_count
        result["deleted_count"] = deleted_count
        result["message"] = (
            f"archived {deleted_count} trade_log row(s) to {archive_path}"
            if deleted_count > 0
            else "no trade_log rows were deleted from the active database"
        )
    except sqlite3.DatabaseError as exc:
        logger.exception("Trade log archive maintenance failed")
        result["ok"] = False
        result["message"] = str(exc)
        return result
    finally:
        if conn is not None:
            try:
                conn.execute("DROP TABLE IF EXISTS temp.trade_log_archive_candidates")
            except sqlite3.DatabaseError:
                pass
            conn.close()

    if bool(result.get("deleted_count")) and vacuum:
        vacuum_conn: sqlite3.Connection | None = None
        try:
            vacuum_conn = get_conn_for_path(target, apply_runtime_pragmas=True)
            vacuum_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            vacuum_conn.execute("VACUUM")
            result["vacuumed"] = True
        except sqlite3.DatabaseError:
            logger.exception("Active DB vacuum after trade log archive failed")
        finally:
            if vacuum_conn is not None:
                vacuum_conn.close()

    return result


def _trade_log_archive_candidate_where(
    *,
    cutoff_ts: int,
    preserve_since_ts: int = 0,
) -> tuple[list[str], list[object]]:
    cutoff = max(int(cutoff_ts or 0), 0)
    preserve_since = max(int(preserve_since_ts or 0), 0)
    where = [
        "(skipped = 1 OR resolved_at IS NOT NULL OR exited_at IS NOT NULL OR outcome IS NOT NULL OR shadow_pnl_usd IS NOT NULL OR actual_pnl_usd IS NOT NULL)",
        "COALESCE(remaining_entry_shares, 0) <= ?",
        "COALESCE(remaining_entry_size_usd, 0) <= ?",
        "COALESCE(resolved_at, exited_at, label_applied_at, placed_at, 0) > 0",
        "COALESCE(resolved_at, exited_at, label_applied_at, placed_at, 0) < ?",
    ]
    params: list[object] = [
        _ARCHIVE_OPEN_SIZE_EPSILON,
        _ARCHIVE_OPEN_SIZE_EPSILON,
        cutoff,
    ]
    if preserve_since > 0:
        where.append("COALESCE(resolved_at, exited_at, label_applied_at, placed_at, 0) < ?")
        params.append(preserve_since)
    return where, params


def trade_log_archive_state(
    path: Path | None = None,
    *,
    cutoff_ts: int = 0,
    preserve_since_ts: int = 0,
) -> dict[str, object]:
    target = Path(path) if path is not None else DB_PATH
    archive_path = trade_log_archive_db_path(target)
    active_db_size, active_db_allocated = _file_size_metrics(target)
    archive_db_size, archive_db_allocated = _file_size_metrics(archive_path)
    cutoff = max(int(cutoff_ts or 0), 0)
    preserve_since = max(int(preserve_since_ts or 0), 0)
    state: dict[str, object] = {
        "trade_log_archive_state_known": True,
        "trade_log_archive_status": "checking",
        "trade_log_archive_db_path": str(target),
        "trade_log_archive_archive_path": str(archive_path),
        "trade_log_archive_archive_exists": archive_path.exists(),
        "trade_log_archive_active_db_size_bytes": active_db_size,
        "trade_log_archive_active_db_allocated_bytes": active_db_allocated,
        "trade_log_archive_archive_db_size_bytes": archive_db_size,
        "trade_log_archive_archive_db_allocated_bytes": archive_db_allocated,
        "trade_log_archive_active_row_count": 0,
        "trade_log_archive_archive_row_count": 0,
        "trade_log_archive_eligible_row_count": 0,
        "trade_log_archive_cutoff_ts": cutoff,
        "trade_log_archive_preserve_since_ts": preserve_since,
        "trade_log_archive_message": "",
    }
    if not target.exists():
        state["trade_log_archive_status"] = "missing"
        state["trade_log_archive_message"] = "database not found"
        return state

    integrity = database_integrity_state(target)
    if integrity.get("db_integrity_known") and not integrity.get("db_integrity_ok"):
        detail = str(integrity.get("db_integrity_message") or "").splitlines()[0].strip()
        state["trade_log_archive_status"] = "blocked_db_integrity"
        state["trade_log_archive_message"] = (
            f"SQLite integrity check failed: {detail}" if detail else "SQLite integrity check failed"
        )
        return state

    conn: sqlite3.Connection | None = None
    try:
        conn = get_conn_for_path(target, apply_runtime_pragmas=False)
        if _table_exists(conn, "trade_log", schema="main"):
            active_row = conn.execute("SELECT COUNT(*) AS n FROM main.trade_log").fetchone()
            state["trade_log_archive_active_row_count"] = max(
                int((active_row or {})["n"] or 0),
                0,
            ) if active_row is not None else 0

        try:
            _ensure_trade_log_archive_attached(conn, target, create_if_missing=False)
        except FileNotFoundError:
            pass

        if _is_attached_schema(conn, TRADE_LOG_ARCHIVE_SCHEMA) and _table_exists(
            conn, "trade_log", schema=TRADE_LOG_ARCHIVE_SCHEMA
        ):
            archive_row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {TRADE_LOG_ARCHIVE_SCHEMA}.trade_log"
            ).fetchone()
            state["trade_log_archive_archive_row_count"] = max(
                int((archive_row or {})["n"] or 0),
                0,
            ) if archive_row is not None else 0

        if cutoff > 0 and _table_exists(conn, "trade_log", schema="main"):
            candidate_where, params = _trade_log_archive_candidate_where(
                cutoff_ts=cutoff,
                preserve_since_ts=preserve_since,
            )
            eligible_row = conn.execute(
                f"""
                SELECT COUNT(*) AS n
                FROM main.trade_log
                WHERE {" AND ".join(candidate_where)}
                """,
                tuple(params),
            ).fetchone()
            state["trade_log_archive_eligible_row_count"] = max(
                int((eligible_row or {})["n"] or 0),
                0,
            ) if eligible_row is not None else 0

        eligible_count = max(int(state.get("trade_log_archive_eligible_row_count") or 0), 0)
        state["trade_log_archive_status"] = "eligible" if eligible_count > 0 else "idle"
        if cutoff <= 0:
            state["trade_log_archive_message"] = "archive cutoff is not active"
        elif eligible_count > 0:
            state["trade_log_archive_message"] = f"{eligible_count} archived-eligible trade_log row(s) are ready to move"
        else:
            state["trade_log_archive_message"] = "no eligible trade_log rows to archive"
        return state
    except sqlite3.DatabaseError as exc:
        state["trade_log_archive_status"] = "error"
        state["trade_log_archive_message"] = str(exc)
        return state
    finally:
        if conn is not None:
            conn.close()


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


def init_db(path: Path | None = None, *, run_heavy_maintenance: bool | None = None) -> None:
    target = Path(path) if path is not None else DB_PATH
    conn = get_conn_for_path(target, apply_runtime_pragmas=True)
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

        CREATE TABLE IF NOT EXISTS managed_wallets (
            wallet_address    TEXT PRIMARY KEY,
            tracking_enabled  INTEGER NOT NULL DEFAULT 1,
            source            TEXT NOT NULL DEFAULT 'seed_env',
            added_at          INTEGER NOT NULL DEFAULT 0,
            updated_at        INTEGER NOT NULL DEFAULT 0,
            disabled_at       INTEGER,
            disabled_reason   TEXT,
            metadata_json     TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS wallet_membership_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address TEXT NOT NULL,
            action         TEXT NOT NULL DEFAULT '',
            source         TEXT NOT NULL DEFAULT '',
            reason         TEXT NOT NULL DEFAULT '',
            payload_json   TEXT NOT NULL DEFAULT '{}',
            created_at     INTEGER NOT NULL
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
            post_promotion_baseline_at           INTEGER NOT NULL DEFAULT 0,
            post_promotion_source                TEXT NOT NULL DEFAULT '',
            post_promotion_reason                TEXT NOT NULL DEFAULT '',
            post_promotion_total_buy_signals     INTEGER NOT NULL DEFAULT 0,
            post_promotion_uncopyable_skips      INTEGER NOT NULL DEFAULT 0,
            post_promotion_timing_skips          INTEGER NOT NULL DEFAULT 0,
            post_promotion_liquidity_skips       INTEGER NOT NULL DEFAULT 0,
            post_promotion_uncopyable_skip_rate  REAL NOT NULL DEFAULT 0,
            post_promotion_resolved_copied_count INTEGER NOT NULL DEFAULT 0,
            post_promotion_resolved_copied_wins  INTEGER NOT NULL DEFAULT 0,
            post_promotion_resolved_copied_win_rate REAL,
            post_promotion_resolved_copied_avg_return REAL,
            post_promotion_resolved_copied_total_pnl_usd REAL NOT NULL DEFAULT 0,
            post_promotion_last_resolved_at      INTEGER NOT NULL DEFAULT 0,
            post_promotion_evidence_ready        INTEGER NOT NULL DEFAULT 0,
            post_promotion_evidence_note         TEXT,
            updated_at                           INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wallet_discovery_candidates (
            wallet_address     TEXT PRIMARY KEY,
            username           TEXT NOT NULL DEFAULT '',
            source_labels_json TEXT NOT NULL DEFAULT '[]',
            follow_score       REAL NOT NULL DEFAULT 0,
            accepted           INTEGER NOT NULL DEFAULT 0,
            reject_reason      TEXT NOT NULL DEFAULT '',
            watch_style        TEXT NOT NULL DEFAULT '',
            leaderboard_rank   INTEGER,
            updated_at         INTEGER NOT NULL,
            payload_json       TEXT NOT NULL DEFAULT '{}'
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

        CREATE TABLE IF NOT EXISTS runtime_settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL DEFAULT '',
            updated_at  INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_seen_trades_seen_at ON seen_trades(seen_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_placed_at ON trade_log(placed_at);
        CREATE INDEX IF NOT EXISTS idx_trade_log_outcome ON trade_log(outcome);
        CREATE INDEX IF NOT EXISTS idx_trade_log_trader ON trade_log(trader_address);
        CREATE INDEX IF NOT EXISTS idx_trade_log_real_money ON trade_log(real_money);
        CREATE INDEX IF NOT EXISTS idx_trade_log_skipped ON trade_log(skipped);
        CREATE INDEX IF NOT EXISTS idx_belief_updates_applied_at ON belief_updates(applied_at);
        CREATE INDEX IF NOT EXISTS idx_wallet_watch_state_status ON wallet_watch_state(status);
        CREATE INDEX IF NOT EXISTS idx_managed_wallets_tracking_enabled ON managed_wallets(tracking_enabled);
        CREATE INDEX IF NOT EXISTS idx_managed_wallets_updated_at ON managed_wallets(updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_wallet_membership_events_created_at ON wallet_membership_events(created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_wallet_membership_events_wallet_created_at ON wallet_membership_events(wallet_address, created_at DESC, id DESC);
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
            "segment_id": "TEXT",
            "policy_id": "TEXT",
            "policy_bundle_version": "INTEGER NOT NULL DEFAULT 0",
            "promotion_epoch_id": "INTEGER NOT NULL DEFAULT 0",
            "experiment_arm": "TEXT NOT NULL DEFAULT 'champion'",
            "expected_edge": "REAL",
            "expected_fill_cost_usd": "REAL",
            "expected_exit_fee_usd": "REAL",
            "expected_close_fixed_cost_usd": "REAL",
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
        "wallet_policy_metrics",
        {
            "post_promotion_baseline_at": "INTEGER NOT NULL DEFAULT 0",
            "post_promotion_source": "TEXT NOT NULL DEFAULT ''",
            "post_promotion_reason": "TEXT NOT NULL DEFAULT ''",
            "post_promotion_total_buy_signals": "INTEGER NOT NULL DEFAULT 0",
            "post_promotion_uncopyable_skips": "INTEGER NOT NULL DEFAULT 0",
            "post_promotion_timing_skips": "INTEGER NOT NULL DEFAULT 0",
            "post_promotion_liquidity_skips": "INTEGER NOT NULL DEFAULT 0",
            "post_promotion_uncopyable_skip_rate": "REAL NOT NULL DEFAULT 0",
            "post_promotion_resolved_copied_count": "INTEGER NOT NULL DEFAULT 0",
            "post_promotion_resolved_copied_wins": "INTEGER NOT NULL DEFAULT 0",
            "post_promotion_resolved_copied_win_rate": "REAL",
            "post_promotion_resolved_copied_avg_return": "REAL",
            "post_promotion_resolved_copied_total_pnl_usd": "REAL NOT NULL DEFAULT 0",
            "post_promotion_last_resolved_at": "INTEGER NOT NULL DEFAULT 0",
            "post_promotion_evidence_ready": "INTEGER NOT NULL DEFAULT 0",
            "post_promotion_evidence_note": "TEXT",
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
            conn.rollback()
            logger.exception("Heavy startup DB maintenance failed; keeping core schema changes")
    else:
        logger.info(
            "Skipping heavy startup DB maintenance for shared/network path: %s",
            target,
        )
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
