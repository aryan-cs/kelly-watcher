from __future__ import annotations

import atexit
import json
import logging
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Mapping

_RUNTIME_STDIO_LOG_PATH_ENV = "KELLY_RUNTIME_STDIO_LOG_PATH"
_RUNTIME_STDIO_LOG_MAX_BYTES_ENV = "KELLY_RUNTIME_STDIO_LOG_MAX_BYTES"
_RUNTIME_STDIO_LOG_BACKUPS_ENV = "KELLY_RUNTIME_STDIO_LOG_BACKUPS"


def _parse_env_positive_int(name: str, default: int) -> int:
    raw_value = str(os.getenv(name, "") or "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


class _RotatingTextStream:
    def __init__(self, path: Path, *, max_bytes: int, backup_count: int) -> None:
        self._path = Path(path)
        self._max_bytes = max(int(max_bytes or 0), 0)
        self._backup_count = max(int(backup_count or 0), 0)
        self._handle: Any | None = None
        self._open()

    @property
    def encoding(self) -> str:
        return "utf-8"

    def _open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8", buffering=1)

    def _size_on_disk(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def _rotate(self) -> None:
        if self._handle is not None:
            try:
                self._handle.flush()
            except OSError:
                pass
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None

        if self._backup_count > 0:
            for index in range(self._backup_count - 1, 0, -1):
                source = self._path.with_name(f"{self._path.name}.{index}")
                target = self._path.with_name(f"{self._path.name}.{index + 1}")
                if target.exists():
                    try:
                        target.unlink()
                    except OSError:
                        continue
                if source.exists():
                    try:
                        source.replace(target)
                    except OSError:
                        continue

            first_backup = self._path.with_name(f"{self._path.name}.1")
            if first_backup.exists():
                try:
                    first_backup.unlink()
                except OSError:
                    pass
            if self._path.exists():
                try:
                    self._path.replace(first_backup)
                except OSError:
                    pass
        elif self._path.exists():
            try:
                self._path.unlink()
            except OSError:
                pass

        self._open()

    def write(self, data: str) -> int:
        text = str(data or "")
        if not text:
            return 0
        encoded_size = len(text.encode(self.encoding, errors="replace"))
        if self._max_bytes > 0 and self._size_on_disk() + encoded_size > self._max_bytes:
            self._rotate()
        if self._handle is None:
            self._open()
        written = self._handle.write(text)
        self._handle.flush()
        return int(written or 0)

    def flush(self) -> None:
        if self._handle is not None:
            self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.flush()
            except OSError:
                pass
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True


def _install_rotating_stdio_stream_from_env() -> None:
    raw_path = str(os.getenv(_RUNTIME_STDIO_LOG_PATH_ENV, "") or "").strip()
    if not raw_path:
        return
    stream = _RotatingTextStream(
        Path(raw_path),
        max_bytes=_parse_env_positive_int(_RUNTIME_STDIO_LOG_MAX_BYTES_ENV, 10 * 1024 * 1024),
        backup_count=_parse_env_positive_int(_RUNTIME_STDIO_LOG_BACKUPS_ENV, 5),
    )
    sys.stdout = stream
    sys.stderr = stream
    atexit.register(stream.close)


_install_rotating_stdio_stream_from_env()

from apscheduler.schedulers.background import BackgroundScheduler

from kelly_watcher.integrations.alerter import (
    build_bullets,
    build_lines,
    build_market_error_alert,
    build_trade_resolution_alert,
    send_alert,
)
from kelly_watcher.research.auto_retrain import retrain_cycle_report, should_retrain_early
from kelly_watcher.engine.beliefs import sync_belief_priors
from kelly_watcher.config import (
    ConfigError,
    duplicate_side_override_min_avg_return,
    duplicate_side_override_min_skips,
    exposure_override_min_avg_return,
    exposure_override_min_skips,
    exposure_override_total_cap_fraction,
    discovery_poll_interval_multiplier,
    heuristic_allowed_entry_price_bands,
    heuristic_max_entry_price,
    heuristic_min_time_to_close_seconds,
    hot_wallet_count,
    live_min_shadow_resolved_since_promotion,
    live_min_shadow_resolved,
    live_require_shadow_history,
    max_bet_fraction,
    max_daily_loss_pct,
    max_feed_staleness_seconds,
    model_edge_high_confidence,
    model_edge_high_threshold,
    model_edge_mid_confidence,
    model_edge_mid_threshold,
    model_path,
    max_live_drawdown_pct,
    max_live_health_failures,
    max_market_horizon_seconds,
    max_source_trade_age_seconds,
    max_market_exposure_fraction,
    max_total_open_exposure_fraction,
    max_trader_exposure_fraction,
    heuristic_min_entry_price,
    log_level,
    min_execution_window_seconds,
    min_bet_usd,
    min_confidence,
    model_min_time_to_close_seconds,
    poll_interval,
    private_key,
    replay_auto_promote,
    replay_auto_promote_min_pnl_delta_usd,
    replay_auto_promote_min_score_delta,
    replay_search_base_cadence,
    replay_search_base_policy,
    replay_search_base_policy_file,
    replay_search_constraints,
    replay_search_constraints_file,
    replay_search_grid,
    replay_search_grid_file,
    replay_search_hour_local,
    replay_search_label_prefix,
    replay_search_max_combos,
    replay_search_notes,
    replay_search_score_weights,
    replay_search_score_weights_file,
    replay_search_top,
    replay_search_window_count,
    replay_search_window_days,
    retrain_base_cadence,
    retrain_early_check_seconds,
    retrain_hour_local,
    retrain_min_samples,
    stop_loss_enabled,
    stop_loss_max_loss_pct,
    stop_loss_min_hold_seconds,
    trade_log_archive_batch_rows,
    trade_log_archive_enabled,
    trade_log_archive_min_age_days,
    trade_log_archive_vacuum_enabled,
    use_real_money,
    wallet_inactivity_limit_seconds,
    wallet_slow_drop_max_tracking_age_seconds,
    wallet_cold_start_min_observed_buys,
    wallet_discovery_analyze_limit,
    wallet_discovery_candidate_limit,
    wallet_discovery_enabled,
    wallet_discovery_leaderboard_pages,
    wallet_discovery_leaderboard_per_page,
    wallet_performance_drop_max_avg_return,
    wallet_performance_drop_max_win_rate,
    wallet_performance_drop_min_trades,
    wallet_discovery_scan_interval_seconds,
    wallet_discovery_min_observed_buys,
    wallet_discovery_min_resolved_buys,
    wallet_discovery_size_multiplier,
    wallet_quality_size_max_multiplier,
    wallet_quality_size_min_multiplier,
    wallet_probation_size_multiplier,
    wallet_trusted_min_resolved_copied_buys,
    wallet_address,
    warm_poll_interval_multiplier,
    warm_wallet_count,
    watched_wallets,
    xgboost_allowed_entry_price_bands,
)
from kelly_watcher.dashboard_api import DashboardApiServer, start_dashboard_api_server
from kelly_watcher.data.db import (
    archive_old_trade_log_rows,
    DB_PATH,
    create_verified_backup,
    database_integrity_state,
    delete_runtime_setting,
    db_recovery_state,
    get_conn,
    get_conn_for_path,
    get_runtime_setting,
    get_trade_log_read_conn,
    init_db,
    import_managed_wallets_from_env,
    load_managed_wallets,
    set_runtime_setting,
    recover_db_from_verified_backup,
    trade_log_archive_db_path,
    trade_log_archive_state,
    managed_wallet_registry_state,
)
from kelly_watcher.data.identity_cache import load_identity_cache
from kelly_watcher.engine.dedup import DedupeCache
from kelly_watcher.engine.economics import EntryEconomics
from kelly_watcher.runtime.evaluator import (
    SEGMENT_SHADOW_MIN_RESOLVED,
    UNASSIGNED_SEGMENT_ID,
    compute_segment_shadow_report,
    daily_report,
    resolve_shadow_trades,
)
from kelly_watcher.runtime.executor import PolymarketExecutor
from kelly_watcher.engine.kelly import size_signal
from kelly_watcher.shadow_reset import (
    apply_wallet_mode_for_reset,
    exec_restarted_bot,
    reset_shadow_runtime,
    restore_managed_wallet_registry_snapshot,
    restore_watched_wallets,
    wallet_registry_clear_all_requested,
)
from kelly_watcher.engine.market_scorer import MarketScorer, build_market_features
from kelly_watcher.data.market_urls import market_url_from_metadata
from kelly_watcher.runtime.performance_preview import compute_tracker_preview_summary
from kelly_watcher.research.replay import REPLAY_POLICY_CONFIG_KEY_MAP
from kelly_watcher.engine.segment_policy import SEGMENT_FALLBACK, SEGMENT_IDS
from kelly_watcher.engine.signal_engine import SignalEngine
from kelly_watcher.engine.shadow_evidence import read_shadow_evidence_epoch, write_shadow_evidence_epoch
from kelly_watcher.integrations.telegram_runtime import service_telegram_commands
from kelly_watcher.engine.trade_contract import OPEN_EXECUTED_ENTRY_SQL, RESOLVED_EXECUTED_ENTRY_SQL
from kelly_watcher.runtime.tracker import PolymarketTracker, TradeEvent
from kelly_watcher.runtime.wallet_discovery import refresh_wallet_discovery_candidates
from kelly_watcher.engine.trader_scorer import get_trader_features, refresh_trader_cache
from kelly_watcher.runtime_paths import (
    BACKGROUND_LOG_PATH,
    BOT_PID_FILE,
    BOT_STATE_FILE,
    DATA_DIR,
    DB_RECOVERY_REQUEST_FILE,
    EVENT_FILE,
    IDENTITY_CACHE_PATH,
    LOG_DIR,
    MANUAL_RETRAIN_REQUEST_FILE,
    MANUAL_TRADE_REQUEST_FILE,
    REPO_ROOT,
    SAVE_DIR,
    SHADOW_EVIDENCE_EPOCH_FILE,
    SHADOW_RESET_REQUEST_FILE,
    TRADE_LOG_ARCHIVE_REQUEST_FILE,
)
from kelly_watcher.engine.wallet_trust import (
    allow_duplicate_side_override,
    apply_wallet_trust_sizing,
    get_wallet_trust_state,
)
from kelly_watcher.engine.watchlist_manager import WatchlistManager

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=log_level(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=10 * 1024 * 1024, backupCount=5),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)
_emit_count = 0
_event_lock = threading.Lock()
# Legacy one-time bootstrap only. The runtime watch universe should come from the
# DB-backed managed wallet registry after startup import/restore runs.
WATCHED_WALLETS = watched_wallets()
_MANUAL_REQUEST_RETRY_BACKOFF_SECONDS = 30
_HEURISTIC_CONF_RE = re.compile(r"heuristic conf ([0-9.]+) < min ([0-9.]+)", re.IGNORECASE)
_MODEL_EDGE_RE = re.compile(r"model edge (-?[0-9.]+) < threshold ([0-9.]+)", re.IGNORECASE)
_MAX_SIZE_RE = re.compile(r"max size \$([0-9.]+) < min \$([0-9.]+)", re.IGNORECASE)
_BANKROLL_RE = re.compile(r"available bankroll \$([0-9.]+) < min \$([0-9.]+)", re.IGNORECASE)
_SIZE_ZERO_RE = re.compile(r"size \$([0-9.]+) <= 0", re.IGNORECASE)
_CONF_RE = re.compile(r"conf ([0-9.]+) < min ([0-9.]+)", re.IGNORECASE)
_SCORE_RE = re.compile(r"score ([0-9.]+) < min ([0-9.]+)", re.IGNORECASE)
_HEURISTIC_ENTRY_PRICE_RE = re.compile(r"heuristic entry price ([0-9.]+) < min ([0-9.]+)", re.IGNORECASE)
_INVALID_PRICE_RE = re.compile(r"invalid price ([0-9.]+)", re.IGNORECASE)
_EXPIRES_RE = re.compile(r"expires in <([0-9]+)s", re.IGNORECASE)
_MAX_HORIZON_RE = re.compile(r"beyond max horizon ([0-9.]+[smhdw])", re.IGNORECASE)


def _runtime_managed_wallets() -> list[str]:
    try:
        wallets = load_managed_wallets()
    except Exception:
        wallets = []
    return list(wallets)


def _managed_wallet_registry_total_count() -> int | None:
    try:
        state = managed_wallet_registry_state()
    except Exception:
        return None
    if not bool(state.get("managed_wallet_registry_available", True)):
        return None
    try:
        total_count = int(state.get("managed_wallet_total_count") or 0)
    except (TypeError, ValueError):
        total_count = 0
    if total_count > 0:
        return total_count
    wallets = state.get("managed_wallets") or []
    if isinstance(wallets, list):
        return len(wallets)
    return 0


def _should_import_bootstrap_watched_wallets(
    clear_all_snapshot_requested: bool,
    *,
    snapshot_restore_failed: bool = False,
) -> bool:
    if clear_all_snapshot_requested or snapshot_restore_failed or not WATCHED_WALLETS:
        return False
    total_count = _managed_wallet_registry_total_count()
    if total_count is None:
        return False
    return total_count <= 0


def _managed_wallet_count() -> int:
    return len(_runtime_managed_wallets())


class ShadowResetRequested(Exception):
    def __init__(self, request: ShadowResetRequest) -> None:
        super().__init__(f"shadow reset requested ({request.wallet_mode})")
        self.request = request


class DbRecoveryRequested(Exception):
    def __init__(self, request: DbRecoveryRequest) -> None:
        super().__init__("DB recovery requested")
        self.request = request


def _disable_windows_console_quick_edit() -> None:
    if os.name != "nt":
        return

    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return

    kernel32 = ctypes.windll.kernel32
    std_input_handle = -10
    enable_quick_edit_mode = 0x0040
    enable_extended_flags = 0x0080

    handle = kernel32.GetStdHandle(std_input_handle)
    if handle in (0, -1):
        return

    mode = wintypes.DWORD()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
        return

    current_mode = int(mode.value)
    next_mode = (current_mode | enable_extended_flags) & ~enable_quick_edit_mode
    if next_mode == current_mode:
        return

    kernel32.SetConsoleMode(handle, next_mode)


def _write_bot_pid_file() -> None:
    BOT_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOT_PID_FILE.write_text(f"{os.getpid()}\n", encoding="utf-8")


def _clear_bot_pid_file() -> None:
    try:
        raw = BOT_PID_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return
    except OSError:
        return

    if raw and raw != str(os.getpid()):
        return

    try:
        BOT_PID_FILE.unlink()
    except FileNotFoundError:
        return


def _install_shutdown_signal_handlers(stop_event: threading.Event) -> dict[int, Any]:
    previous_handlers: dict[int, Any] = {}
    signal_counts = {"count": 0}

    def _handler(signum, _frame) -> None:
        signal_counts["count"] += 1
        if signal_counts["count"] <= 1:
            logger.warning("Received signal %s. Shutting down...", signum)
            stop_event.set()
            return
        logger.error("Received signal %s again. Forcing process exit.", signum)
        os._exit(128 + int(signum))

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            previous_handlers[int(sig)] = signal.getsignal(sig)
            signal.signal(sig, _handler)
        except (OSError, RuntimeError, ValueError):
            continue
    return previous_handlers


def _restore_shutdown_signal_handlers(previous_handlers: dict[int, Any]) -> None:
    for signum, handler in previous_handlers.items():
        try:
            signal.signal(signum, handler)
        except (OSError, RuntimeError, ValueError):
            continue


@dataclass
class LiveEntryGuard:
    start_equity: float
    drawdown_limit_pct: float
    stop_equity: float
    triggered: bool = False
    alerted: bool = False

    def block_reason(self, account_equity: float) -> str | None:
        if self.drawdown_limit_pct <= 0 or self.start_equity <= 0:
            return None
        if self.triggered or account_equity <= self.stop_equity + 1e-9:
            self.triggered = True
            return (
                f"live entry guard tripped after a {self.drawdown_limit_pct * 100:.1f}% drawdown "
                f"(start ${self.start_equity:.2f}, current ${account_equity:.2f})"
            )
        return None


@dataclass
class DailyLossGuard:
    start_equity: float
    loss_limit_pct: float
    day_key: str
    _equity_locked: bool = False

    def block_reason(self, account_equity: float, now_ts: int) -> str | None:
        current_day = time.strftime("%Y-%m-%d", time.localtime(now_ts))
        if current_day != self.day_key:
            self.day_key = current_day
            self._equity_locked = False

        if not self._equity_locked and account_equity > 0:
            self.start_equity = account_equity
            self._equity_locked = True
        if self.loss_limit_pct <= 0 or self.start_equity <= 0:
            return None

        stop_equity = max(self.start_equity * (1.0 - self.loss_limit_pct), 0.0)
        if account_equity <= stop_equity + 1e-9:
            return (
                f"daily loss guard tripped after a {self.loss_limit_pct * 100:.1f}% drawdown "
                f"(today start ${self.start_equity:.2f}, current ${account_equity:.2f})"
            )
        return None


@dataclass(frozen=True)
class ManualTradeRequest:
    action: str
    market_id: str
    token_id: str
    side: str
    question: str
    trader_address: str
    amount_usd: float | None
    request_id: str
    requested_at: int
    source: str


@dataclass(frozen=True)
class ShadowResetRequest:
    wallet_mode: str
    request_id: str
    requested_at: int
    source: str


@dataclass(frozen=True)
class DbRecoveryRequest:
    candidate_path: str
    source_path: str
    request_id: str
    requested_at: int
    source: str


@dataclass(frozen=True)
class TradeLogArchiveRequest:
    request_id: str
    requested_at: int
    source: str


@dataclass(frozen=True)
class EntryPauseState:
    key: str
    reason: str


@dataclass(frozen=True)
class StopLossCandidate:
    market_id: str
    token_id: str
    side: str
    question: str
    trader_address: str
    trader_name: str
    entered_at: int
    size_usd: float


@dataclass(frozen=True)
class ExitGuardDecision:
    action: str
    reason: str
    metadata: dict[str, Any]


@dataclass
class EntryPauseAlertTracker:
    required_stable_loops: int = 2
    observed_state: EntryPauseState | None = None
    observed_count: int = 0
    notified_state: EntryPauseState | None = None

    def update(self, state: EntryPauseState | None) -> tuple[str, EntryPauseState | None] | None:
        current_key = state.key if state is not None else None
        observed_key = self.observed_state.key if self.observed_state is not None else None
        if current_key == observed_key:
            self.observed_count += 1
        else:
            self.observed_count = 1
        self.observed_state = state

        notified_key = self.notified_state.key if self.notified_state is not None else None
        if current_key == notified_key:
            self.notified_state = state
            return None
        if self.observed_count < max(int(self.required_stable_loops or 1), 1):
            return None

        previous_state = self.notified_state
        self.notified_state = state
        if state is None:
            return ("resumed", previous_state)
        return ("paused", state)


def _emit_event(payload: dict) -> None:
    global _emit_count
    EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _event_lock:
        with EVENT_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
        _emit_count += 1
        if _emit_count % 100 == 0:
            try:
                lines = EVENT_FILE.read_text(encoding="utf-8").splitlines(True)
                if len(lines) > 1000:
                    EVENT_FILE.write_text("".join(lines[-1000:]), encoding="utf-8")
            except Exception:
                pass


def _market_url_for_event(event) -> str | None:
    return market_url_from_metadata(getattr(event, "raw_market_metadata", None))


def _event_market_payload(event) -> dict[str, str]:
    market_url = _market_url_for_event(event)
    return {"market_url": market_url} if market_url else {}


def _repair_event_file_market_urls() -> None:
    if not EVENT_FILE.exists():
        return

    try:
        lines = EVENT_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return

    trade_ids: list[str] = []
    parsed_rows: list[dict[str, object] | None] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            parsed_rows.append(None)
            continue
        if not isinstance(payload, dict):
            parsed_rows.append(None)
            continue
        parsed_rows.append(payload)
        trade_id = str(payload.get("trade_id") or "").strip()
        if trade_id:
            trade_ids.append(trade_id)

    if not trade_ids:
        return

    conn = get_conn()
    placeholders = ",".join("?" for _ in trade_ids)
    rows = conn.execute(
        f"SELECT trade_id, market_url FROM trade_log WHERE trade_id IN ({placeholders})",
        tuple(trade_ids),
    ).fetchall()
    conn.close()
    market_url_by_trade_id = {
        str(row["trade_id"] or "").strip(): str(row["market_url"] or "").strip()
        for row in rows
        if str(row["trade_id"] or "").strip() and str(row["market_url"] or "").strip()
    }

    updated = False
    repaired_lines: list[str] = []
    for original_line, payload in zip(lines, parsed_rows):
        if payload is None:
            repaired_lines.append(original_line)
            continue
        trade_id = str(payload.get("trade_id") or "").strip()
        canonical_url = market_url_by_trade_id.get(trade_id)
        if canonical_url and str(payload.get("market_url") or "").strip() != canonical_url:
            payload["market_url"] = canonical_url
            repaired_lines.append(json.dumps(payload, separators=(",", ":"), default=str))
            updated = True
            continue
        repaired_lines.append(original_line)

    if updated:
        EVENT_FILE.write_text("\n".join(repaired_lines) + "\n", encoding="utf-8")


def _base_bot_state_snapshot(*, session_id: str, started_at: int) -> dict[str, object]:
    return {
        "session_id": session_id,
        "started_at": int(started_at),
        "configured_mode": "shadow",
        "mode": "shadow",
        "mode_block_reason": "",
        "last_loop_started_at": 0,
        "last_activity_at": int(started_at),
        "loop_in_progress": False,
        "startup_detail": "starting bot",
        "startup_failed": False,
        "startup_failure_message": "",
        "startup_validation_failed": False,
        "startup_validation_message": "",
        "startup_blocked": False,
        "startup_recovery_only": False,
        "startup_block_reason": "",
        "last_poll_at": 0,
        "last_poll_duration_s": 0.0,
        "bankroll_usd": None,
        "last_event_count": 0,
        "polled_wallet_count": 0,
        "retrain_in_progress": False,
        "retrain_started_at": 0,
        "last_retrain_started_at": 0,
        "last_retrain_finished_at": 0,
        "last_retrain_status": "",
        "last_retrain_message": "",
        "last_retrain_sample_count": 0,
        "last_retrain_min_samples": 0,
        "last_retrain_trigger": "",
        "last_retrain_deployed": False,
        "manual_retrain_pending": False,
        "manual_retrain_requested_at": 0,
        "manual_retrain_message": "",
        "shadow_restart_pending": False,
        "shadow_restart_kind": "",
        "shadow_restart_message": "",
        "manual_trade_pending": False,
        "manual_trade_requested_at": 0,
        "manual_trade_message": "",
        "replay_search_in_progress": False,
        "replay_search_started_at": 0,
        "last_replay_search_started_at": 0,
        "last_replay_search_finished_at": 0,
        "last_replay_search_status": "",
        "last_replay_search_message": "",
        "last_replay_search_trigger": "",
        "last_replay_search_run_id": 0,
        "last_replay_search_candidate_count": 0,
        "last_replay_search_feasible_count": 0,
        "last_replay_search_best_score": None,
        "last_replay_search_best_pnl_usd": None,
        "last_replay_search_scope": "shadow_only",
        "last_replay_promotion_id": 0,
        "last_replay_promotion_at": 0,
        "last_replay_promotion_status": "",
        "last_replay_promotion_message": "",
        "last_replay_promotion_scope": "shadow_only",
        "last_replay_promotion_run_id": 0,
        "last_replay_promotion_candidate_id": 0,
        "last_replay_promotion_score_delta": None,
        "last_replay_promotion_pnl_delta_usd": None,
        "last_applied_replay_promotion_id": 0,
        "last_applied_replay_promotion_at": 0,
        "last_applied_replay_promotion_status": "",
        "last_applied_replay_promotion_message": "",
        "last_applied_replay_promotion_scope": "shadow_only",
        "last_applied_replay_promotion_run_id": 0,
        "last_applied_replay_promotion_candidate_id": 0,
        "last_applied_replay_promotion_score_delta": None,
        "last_applied_replay_promotion_pnl_delta_usd": None,
        "shadow_history_state_known": False,
        "resolved_shadow_trade_count": 0,
        "shadow_history_all_time_resolved": 0,
        "live_require_shadow_history_enabled": False,
        "live_min_shadow_resolved": 0,
        "live_shadow_history_total_ready": True,
        "resolved_shadow_since_last_promotion": 0,
        "shadow_history_current_baseline_at": 0,
        "shadow_history_current_baseline_label": "initial policy",
        "live_min_shadow_resolved_since_last_promotion": 0,
        "live_shadow_history_ready": True,
        "shadow_history_epoch_known": False,
        "shadow_history_epoch_started_at": 0,
        "shadow_history_epoch_source_label": "",
        "shadow_history_epoch_active_scope_label": "all history",
        "shadow_history_epoch_status": "all_history",
        "shadow_history_epoch_total_resolved": 0,
        "shadow_history_epoch_ready_count": 0,
        "shadow_history_epoch_blocked_count": 0,
        "shadow_history_epoch_min_resolved": 0,
        "shadow_history_epoch_routed_resolved": 0,
        "shadow_history_epoch_legacy_resolved": 0,
        "shadow_history_epoch_coverage_pct": None,
        "shadow_history_epoch_ready": False,
        "shadow_history_epoch_block_reason": "",
        "shadow_evidence_epoch_known": False,
        "shadow_evidence_epoch_started_at": 0,
        "shadow_evidence_epoch_source": "",
        "shadow_evidence_epoch_source_label": "",
        "shadow_evidence_epoch_active_scope_label": "all history",
        "shadow_evidence_epoch_status": "all_history",
        "shadow_evidence_epoch_request_id": "",
        "shadow_evidence_epoch_message": "",
        "routed_shadow_epoch_known": False,
        "routed_shadow_epoch_started_at": 0,
        "routed_shadow_epoch_source_label": "",
        "routed_shadow_epoch_active_scope_label": "all history",
        "routed_shadow_epoch_status": "all_history",
        "shadow_snapshot_state_known": False,
        "shadow_snapshot_scope": "all_history",
        "shadow_snapshot_started_at": 0,
        "shadow_snapshot_status": "checking",
        "shadow_snapshot_resolved": 0,
        "shadow_snapshot_routed_resolved": 0,
        "shadow_snapshot_legacy_resolved": 0,
        "shadow_snapshot_coverage_pct": None,
        "shadow_snapshot_ready": False,
        "shadow_snapshot_total_pnl_usd": None,
        "shadow_snapshot_return_pct": None,
        "shadow_snapshot_profit_factor": None,
        "shadow_snapshot_expectancy_usd": None,
        "shadow_snapshot_block_reason": "",
        "shadow_snapshot_block_state": "",
        "shadow_snapshot_optimization_block_reason": "",
        "routed_shadow_state_known": False,
        "routed_shadow_status": "checking",
        "routed_shadow_min_resolved": 0,
        "routed_shadow_routed_resolved": 0,
        "routed_shadow_legacy_resolved": 0,
        "routed_shadow_total_resolved": 0,
        "routed_shadow_coverage_pct": None,
        "routed_shadow_ready": False,
        "routed_shadow_block_reason": "",
        "routed_shadow_total_pnl_usd": None,
        "routed_shadow_return_pct": None,
        "routed_shadow_profit_factor": None,
        "routed_shadow_expectancy_usd": None,
        "routed_shadow_data_warning": "",
        "shadow_segment_state_known": False,
        "shadow_segment_status": "checking",
        "shadow_segment_scope": "all_history",
        "shadow_segment_scope_started_at": 0,
        "shadow_segment_min_resolved": 0,
        "shadow_segment_total": 0,
        "shadow_segment_ready_count": 0,
        "shadow_segment_positive_count": 0,
        "shadow_segment_negative_count": 0,
        "shadow_segment_blocked_count": 0,
        "shadow_segment_history_status": "empty",
        "shadow_segment_routed_signals": 0,
        "shadow_segment_routed_acted": 0,
        "shadow_segment_routed_resolved": 0,
        "shadow_segment_legacy_resolved": 0,
        "shadow_segment_routing_coverage_pct": None,
        "shadow_segment_summary_json": "[]",
        "shadow_segment_block_reason": "",
        "db_integrity_known": False,
        "db_integrity_ok": True,
        "db_integrity_message": "",
        "db_recovery_state_known": False,
        "db_recovery_candidate_ready": False,
        "db_recovery_candidate_path": "",
        "db_recovery_candidate_source_path": "",
        "db_recovery_candidate_message": "",
        "db_recovery_candidate_mode": "unavailable",
        "db_recovery_candidate_evidence_ready": False,
        "db_recovery_candidate_class_reason": "",
        "db_recovery_latest_verified_backup_path": "",
        "db_recovery_latest_verified_backup_at": 0,
        "db_recovery_inventory": [],
        "db_recovery_inventory_count": 0,
        "db_recovery_shadow_state_known": False,
        "db_recovery_shadow_candidate_path": "",
        "db_recovery_shadow_status": "checking",
        "db_recovery_shadow_acted": 0,
        "db_recovery_shadow_resolved": 0,
        "db_recovery_shadow_total_pnl_usd": None,
        "db_recovery_shadow_return_pct": None,
        "db_recovery_shadow_profit_factor": None,
        "db_recovery_shadow_expectancy_usd": None,
        "db_recovery_shadow_data_warning": "",
        "db_recovery_shadow_segment_total": 0,
        "db_recovery_shadow_segment_ready_count": 0,
        "db_recovery_shadow_segment_blocked_count": 0,
        "db_recovery_shadow_history_status": "empty",
        "db_recovery_shadow_min_resolved": 0,
        "db_recovery_shadow_routed_resolved": 0,
        "db_recovery_shadow_legacy_resolved": 0,
        "db_recovery_shadow_total_resolved": 0,
        "db_recovery_shadow_routing_coverage_pct": None,
        "db_recovery_shadow_ready": False,
        "db_recovery_shadow_segment_summary_json": "[]",
        "db_recovery_shadow_block_reason": "",
        "trade_log_archive_enabled": trade_log_archive_enabled(),
        "trade_log_archive_state_known": False,
        "trade_log_archive_status": "disabled" if not trade_log_archive_enabled() else "checking",
        "trade_log_archive_pending": False,
        "trade_log_archive_requested_at": 0,
        "trade_log_archive_request_message": "",
        "trade_log_archive_db_path": str(DB_PATH),
        "trade_log_archive_archive_path": "",
        "trade_log_archive_archive_exists": False,
        "trade_log_archive_active_db_size_bytes": 0,
        "trade_log_archive_active_db_allocated_bytes": 0,
        "trade_log_archive_archive_db_size_bytes": 0,
        "trade_log_archive_archive_db_allocated_bytes": 0,
        "trade_log_archive_active_row_count": 0,
        "trade_log_archive_archive_row_count": 0,
        "trade_log_archive_eligible_row_count": 0,
        "trade_log_archive_cutoff_ts": 0,
        "trade_log_archive_preserve_since_ts": 0,
        "trade_log_archive_message": "",
        "trade_log_archive_last_run_at": 0,
        "trade_log_archive_last_candidate_count": 0,
        "trade_log_archive_last_archived_count": 0,
        "trade_log_archive_last_deleted_count": 0,
        "trade_log_archive_last_vacuumed": False,
        "trade_log_archive_last_message": "",
        "trade_log_archive_block_reason": "",
        "storage_state_known": False,
        "storage_save_dir_size_bytes": 0,
        "storage_data_dir_size_bytes": 0,
        "storage_log_dir_size_bytes": 0,
        "storage_trading_db_size_bytes": 0,
        "storage_trading_db_allocated_bytes": 0,
        "storage_trade_log_archive_db_size_bytes": 0,
        "storage_trade_log_archive_db_allocated_bytes": 0,
        "storage_identity_cache_size_bytes": 0,
        "storage_events_file_size_bytes": 0,
        "storage_background_log_size_bytes": 0,
        "storage_model_artifact_size_bytes": 0,
        "storage_artifact_quarantine_file_count": 0,
        "storage_artifact_quarantine_size_bytes": 0,
        "storage_db_recovery_quarantine_file_count": 0,
        "storage_db_recovery_quarantine_size_bytes": 0,
        "storage_message": "",
        "loaded_scorer": "heuristic",
        "loaded_model_backend": "heuristic",
        "model_artifact_exists": False,
        "model_artifact_path": "",
        "model_artifact_backend": "",
        "model_artifact_contract": None,
        "runtime_contract": None,
        "model_artifact_label_mode": "",
        "runtime_label_mode": "",
        "model_runtime_compatible": False,
        "model_fallback_reason": "",
        "model_load_error": "",
        "model_prediction_mode": "",
        "model_loaded_at": 0,
        "model_training_scope": "unknown",
        "model_training_since_ts": 0,
        "model_training_routed_only": False,
        "model_training_provenance_trusted": False,
        "model_training_block_reason": "",
    }


def _normalize_trading_mode(value: object, fallback: str = "shadow") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"shadow", "live"}:
        return normalized
    return fallback


def _effective_runtime_mode(configured_mode: str, state: Mapping[str, object]) -> tuple[str, str]:
    configured = _normalize_trading_mode(configured_mode)
    if configured != "live":
        return "shadow", ""
    if bool(state.get("startup_failed")) or bool(state.get("startup_validation_failed")):
        reason = (
            str(state.get("startup_block_reason") or "").strip()
            or str(state.get("startup_failure_message") or "").strip()
            or str(state.get("startup_validation_message") or "").strip()
            or str(state.get("startup_detail") or "").strip()
            or "startup failed"
        )
        return "shadow", f"configured live but forced shadow: {reason}"
    if bool(state.get("startup_blocked")) or bool(state.get("startup_recovery_only")):
        reason = str(state.get("startup_block_reason") or "").strip() or "startup blocked"
        return "shadow", f"configured live but forced shadow: {reason}"
    if bool(state.get("shadow_restart_pending")):
        reason = str(state.get("shadow_restart_message") or "").strip() or "shadow restart pending"
        return "shadow", f"configured live but forced shadow: {reason}"
    if bool(state.get("db_integrity_known")) and not bool(state.get("db_integrity_ok")):
        detail = str(state.get("db_integrity_message") or "").splitlines()[0].strip() or "unknown integrity failure"
        return "shadow", f"configured live but forced shadow: SQLite integrity check failed: {detail}"
    return "live", ""


def _write_bot_state(*, replace: bool = False, **extra) -> None:
    existing: dict[str, object] = {}
    if not replace and BOT_STATE_FILE.exists():
        try:
            payload = json.loads(BOT_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                existing = payload
        except Exception:
            existing = {}

    state = dict(existing)
    try:
        configured_mode = "live" if use_real_money() else "shadow"
    except Exception:
        raw_live_mode = str(os.environ.get("USE_REAL_MONEY") or "").strip().lower()
        if raw_live_mode in {"1", "true", "yes", "on"}:
            configured_mode = "live"
        elif raw_live_mode in {"0", "false", "no", "off"}:
            configured_mode = "shadow"
        else:
            configured_mode = _normalize_trading_mode(
                existing.get("configured_mode") or existing.get("mode") or "shadow"
            )
    try:
        interval = poll_interval()
    except Exception:
        try:
            interval = float(existing.get("poll_interval") or 0)
        except Exception:
            interval = 0.0
    state.update(
        {
            "started_at": int(extra.pop("started_at", state.get("started_at") or time.time())),
            "n_wallets": _managed_wallet_count(),
            "poll_interval": interval,
        }
    )
    state.update(extra)
    startup_failed = bool(state.get("startup_failed"))
    startup_validation_failed = bool(state.get("startup_validation_failed"))
    if startup_failed or startup_validation_failed:
        state["startup_blocked"] = True
        if not str(state.get("startup_block_reason") or "").strip():
            state["startup_block_reason"] = (
                str(state.get("startup_failure_message") or "").strip()
                or str(state.get("startup_validation_message") or "").strip()
                or str(state.get("startup_detail") or "").strip()
            )
    mode, mode_block_reason = _effective_runtime_mode(configured_mode, state)
    state.update(
        {
            "configured_mode": configured_mode,
            "mode": mode,
            "mode_block_reason": mode_block_reason,
        }
    )
    BOT_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")
    temp_path.replace(path)


def _record_manual_request_pickup_failure(path: Path, payload: dict[str, Any], exc: Exception) -> None:
    updated_payload = dict(payload)
    updated_payload["pickup_failed_at"] = int(time.time())
    updated_payload["pickup_error"] = str(exc).strip() or exc.__class__.__name__
    updated_payload["pickup_failure_count"] = int(updated_payload.get("pickup_failure_count") or 0) + 1
    updated_payload["next_retry_at"] = int(time.time()) + _MANUAL_REQUEST_RETRY_BACKOFF_SECONDS
    try:
        _write_atomic_json(path, updated_payload)
    except OSError:
        logger.warning("Failed to persist manual request pickup failure metadata for %s", path.name, exc_info=True)


def _manual_request_age_seconds(payload: dict[str, Any], now_ts: int) -> int | None:
    requested_at = int(payload.get("requested_at") or 0)
    pickup_failed_at = int(payload.get("pickup_failed_at") or 0)
    freshness_anchor = max(requested_at, pickup_failed_at)
    if freshness_anchor <= 0:
        return None
    return max(now_ts - freshness_anchor, 0)


def _persist_startup_failure_state(
    *,
    detail: str,
    message: str,
    validation_failed: bool,
) -> None:
    failed_at = int(time.time())
    state = _base_bot_state_snapshot(session_id=uuid.uuid4().hex, started_at=failed_at)
    state.update(
        last_activity_at=failed_at,
        startup_detail=str(detail or "").strip(),
        startup_failed=True,
        startup_failure_message=str(message or "").strip(),
        startup_validation_failed=bool(validation_failed),
        startup_validation_message=str(message or "").strip() if validation_failed else "",
        startup_blocked=True,
        startup_recovery_only=False,
        startup_block_reason=str(message or "").strip() or str(detail or "").strip(),
    )
    for loader, payload_builder in (
        (_latest_retrain_run, _latest_retrain_state_payload),
        (_latest_replay_search_run, _latest_replay_search_state_payload),
        (_latest_replay_promotion, _latest_replay_promotion_state_payload),
        (_latest_applied_replay_promotion, _applied_replay_promotion_state_payload),
    ):
        try:
            row = loader()
        except Exception:
            row = None
        if row is not None:
            state.update(payload_builder(row))
    try:
        shadow_history_metrics, last_promotion = _shadow_history_gate_metrics()
        require_total_history = live_require_shadow_history()
        minimum_total = live_min_shadow_resolved() if require_total_history else 0
        minimum_since_promotion = max(live_min_shadow_resolved_since_promotion(), 0)
        state.update(
            _shadow_history_state_payload(
                total_resolved_shadow=int(shadow_history_metrics["current_window_resolved"] or 0),
                resolved_since_promotion=int(shadow_history_metrics["current_baseline_resolved"] or 0),
                last_promotion=last_promotion,
                require_total_history=require_total_history,
                minimum_total=minimum_total,
                minimum_since_promotion=minimum_since_promotion,
                all_time_resolved_shadow=int(shadow_history_metrics["all_time_resolved"] or 0),
                routed_resolved_shadow=int(
                    shadow_history_metrics["routed_current_window_resolved"] or 0
                ),
            )
        )
    except Exception:
        pass
    try:
        since_ts, _ = _current_shadow_segment_scope_since_ts()
        state.update(
            _segment_shadow_state_payload(
                compute_segment_shadow_report(
                    mode="shadow",
                    since_ts=since_ts,
                )
            )
        )
    except Exception as exc:
        state.update(_segment_shadow_error_payload(str(exc)))
    try:
        preview = compute_tracker_preview_summary(
            mode="shadow",
            use_bot_state_balance=False,
            since_ts=_shadow_evidence_epoch_started_at(),
            apply_shadow_evidence_epoch=False,
        )
        state.update(
            _shadow_snapshot_state_payload(
                preview,
                all_time_resolved=_resolved_shadow_trade_count(),
            )
        )
        state.update(
            _routed_shadow_performance_state_payload(
                preview
            )
        )
    except Exception as exc:
        state.update(_shadow_snapshot_error_payload(str(exc)))
        state.update(_routed_shadow_performance_error_payload(str(exc)))
    try:
        state.update(database_integrity_state())
    except Exception:
        pass
    try:
        state.update(db_recovery_state())
    except Exception:
        pass
    try:
        state.update(_compute_db_recovery_shadow_state())
    except Exception:
        pass
    try:
        state.update(_trade_log_archive_state_payload())
    except Exception:
        pass
    _write_bot_state(replace=True, **state)


def _persist_startup_validation_failure(errors: list[str], warnings: list[str] | None = None) -> None:
    cleaned_errors = [str(item or "").strip() for item in errors if str(item or "").strip()]
    cleaned_warnings = [str(item or "").strip() for item in (warnings or []) if str(item or "").strip()]
    if not cleaned_errors:
        cleaned_errors = ["startup validation failed"]
    detail = (
        f"startup validation failed: {cleaned_errors[0]}"
        if len(cleaned_errors) == 1
        else f"startup validation failed: {len(cleaned_errors)} errors"
    )
    message_lines = ["startup validation failed", *[f"- {item}" for item in cleaned_errors]]
    if cleaned_warnings:
        message_lines.extend(["warnings", *[f"- {item}" for item in cleaned_warnings]])
    _persist_startup_failure_state(
        detail=detail,
        message="\n".join(message_lines),
        validation_failed=True,
    )


def _send_resolution_alerts(resolved_rows: list[dict[str, object]]) -> None:
    for row in resolved_rows:
        if not bool(row.get("executed")):
            continue

        mode = "live" if bool(row.get("real_money")) else "shadow"
        side = str(row.get("side") or "").strip()
        pnl = float(row.get("pnl") or 0.0)
        question = str(row.get("question") or row.get("market_id") or "").strip()
        market_url = str(row.get("market_url") or "").strip()
        send_alert(
            build_trade_resolution_alert(
                mode=mode,
                won=bool(row.get("won")),
                side=side,
                pnl_usd=pnl,
                question=question,
                market_url=market_url,
                tracked_trader_name=str(row.get("trader_name") or "").strip() or None,
                tracked_trader_address=str(row.get("trader_address") or "").strip() or None,
            ),
            kind="resolution",
        )


def _resolve_trades_and_alert() -> list[dict[str, object]]:
    resolved_rows = resolve_shadow_trades()
    _send_resolution_alerts(resolved_rows)
    return resolved_rows


def _run_deferred_startup_tasks(
    *,
    startup_wallets: list[str],
    tracker: PolymarketTracker,
    watchlist: WatchlistManager,
    dedup: DedupeCache,
    engine: SignalEngine,
    persist_state,
    run_retrain_job,
) -> None:
    def _step(label: str, fn):
        logger.info("Deferred startup warmup: %s", label)
        try:
            return fn()
        except Exception:
            logger.exception("Deferred startup warmup failed while %s", label)
            return None

    _step(f"priming {len(startup_wallets)} identities", lambda: tracker.prime_identities(startup_wallets))
    _step(f"refreshing {len(startup_wallets)} trader profiles", lambda: refresh_trader_cache(startup_wallets))
    _step(
        "refreshing watchlist",
        lambda: (
            watchlist.refresh(run_auto_drop=True),
            persist_state(**watchlist.state_fields()),
        ),
    )
    _step("resolving historical trades", _resolve_trades_and_alert)
    _step("refreshing trade cache", lambda: dedup.load_from_db(rebuild_shadow_positions=False))
    tracker.seen_ids.update(dedup.seen_ids)
    if should_retrain_early(engine):
        _step("running startup retrain", lambda: run_retrain_job("startup"))
    logger.info("Deferred startup warmup complete")


def _format_percent_text(value: str) -> str:
    return f"{float(value) * 100:.1f}%"


def _humanize_market_veto(veto: str) -> str:
    detail = (veto or "").strip()
    expires_match = _EXPIRES_RE.fullmatch(detail)
    if expires_match:
        return f"too close to resolution, less than {expires_match.group(1)} seconds remained to place the trade"
    max_horizon_match = _MAX_HORIZON_RE.fullmatch(detail)
    if max_horizon_match:
        return f"market resolves too far out, beyond the {max_horizon_match.group(1)} maximum horizon"
    if detail == "crossed order book":
        return "market data looked invalid because the order book was crossed"
    if detail == "missing order book":
        return "market data was incomplete because there was no order book snapshot"
    if detail == "no visible order book depth":
        return "market looked too thin to trade because there was no visible order book depth"
    if detail == "invalid market mid":
        return "market data looked invalid because the midpoint price was out of bounds"
    if detail == "invalid order book values":
        return "market data looked invalid because the order book values were negative"
    return f"market veto, {detail}"


def _humanize_reason(reason: str) -> str:
    text = (reason or "").strip()
    if not text:
        return "trade was rejected for an unspecified reason"

    lower = text.lower()
    if lower.startswith("heuristic sizing, "):
        return _humanize_reason(text.split(",", 1)[1].strip())
    if lower.startswith("kelly, "):
        return _humanize_reason(text.split(",", 1)[1].strip())
    if lower.startswith("market veto, "):
        return _humanize_market_veto(text.split(",", 1)[1].strip())
    if lower == "observed sell - not copying exits yet":
        return "watched trader was exiting a position, and the bot only copies entries right now"
    if lower == "missing market snapshot":
        return "market data was unavailable when this trade was observed"
    if lower == "failed to build market features":
        return "could not build the market snapshot needed to score this trade"
    if lower == "duplicate trade_id":
        return "this trade was already seen, so it was skipped as a duplicate"
    if lower == "order in-flight":
        return "an order for this market was already being placed, so this trade was skipped"
    if lower == "position already open":
        return "we already had this side of the market open, so the trade was skipped"
    if lower == "passed heuristic threshold":
        return "signal confidence cleared the heuristic threshold"
    if lower == "passed model edge threshold":
        return "model edge cleared the required threshold"
    if lower == "passed all checks":
        return "signal cleared scoring, sizing, and risk checks"
    if lower == "signal rejected":
        return "trade did not pass the signal checks"
    if lower == "bankroll depleted":
        return "balance too low, no bankroll was available for a new trade"
    if lower == "negative kelly - no edge at this price/confidence":
        return "Kelly sizing found no positive edge at this price, so the trade was skipped"
    if lower == "shadow simulation rejected the buy because the order book had no asks for a full fill":
        return "simulated live buy could not fill because there were no asks on the book"
    if lower == "shadow simulation rejected the buy because there was not enough ask depth to fill the whole order":
        return "simulated live buy could not fill because the ask book was too thin for the full size"
    if lower == "shadow simulation rejected the sell because the order book had no bids for a full fill":
        return "simulated live sell could not fill because there were no bids on the book"
    if lower == "shadow simulation rejected the sell because there was not enough bid depth to fill the whole order":
        return "simulated live sell could not fill because the bid book was too thin for the full size"

    for pattern, formatter in (
        (_HEURISTIC_CONF_RE, lambda m: f"signal confidence was {_format_percent_text(m.group(1))}, below the {_format_percent_text(m.group(2))} minimum"),
        (_MODEL_EDGE_RE, lambda m: f"model edge was {_format_percent_text(m.group(1))}, below the {_format_percent_text(m.group(2))} threshold"),
        (_MAX_SIZE_RE, lambda m: f"balance too low, calculated size was ${m.group(1)} but minimum bet size is ${m.group(2)}"),
        (_BANKROLL_RE, lambda m: f"balance too low, available bankroll was ${m.group(1)} but minimum bet size is ${m.group(2)}"),
        (_SIZE_ZERO_RE, lambda m: f"calculated trade size was ${m.group(1)}, so no order was placed"),
        (_CONF_RE, lambda m: f"confidence was {_format_percent_text(m.group(1))}, below the {_format_percent_text(m.group(2))} minimum needed to place a trade"),
        (_SCORE_RE, lambda m: f"heuristic score was {_format_percent_text(m.group(1))}, below the {_format_percent_text(m.group(2))} minimum needed to place a trade"),
        (_HEURISTIC_ENTRY_PRICE_RE, lambda m: f"entry price was {_format_percent_text(m.group(1))}, below the {_format_percent_text(m.group(2))} heuristic minimum"),
        (_INVALID_PRICE_RE, lambda m: f"trade was skipped because the market price looked invalid ({m.group(1)})"),
    ):
        match = pattern.fullmatch(text)
        if match:
            return formatter(match)

    return text


def _apply_total_exposure_cap_to_size(
    executor: PolymarketExecutor,
    *,
    requested_size_usd: float,
    account_equity: float,
    trader_address: str = "",
) -> tuple[float, str | None, str | None]:
    decision = executor.total_open_exposure_decision(
        proposed_size_usd=requested_size_usd,
        account_equity=account_equity,
        trader_address=trader_address,
    )
    if decision.block_reason:
        return 0.0, decision.block_reason, None

    allowed_size_usd = float(decision.allowed_size_usd or 0.0)
    if decision.clipped and allowed_size_usd + 1e-9 < min_bet_usd():
        return (
            0.0,
            (
                f"remaining total exposure headroom was ${allowed_size_usd:.2f}, "
                f"below the ${min_bet_usd():.2f} minimum bet size"
            ),
            None,
        )

    clip_note = None
    if decision.clipped and allowed_size_usd + 1e-9 < requested_size_usd:
        clip_note = f"total exposure cap clipped size from ${requested_size_usd:.2f} to ${allowed_size_usd:.2f}"
    return allowed_size_usd, None, clip_note


def _apply_total_exposure_cap_to_entry_cost(
    executor: PolymarketExecutor,
    *,
    requested_size_usd: float,
    fill_economics: EntryEconomics,
    account_equity: float,
    trader_address: str = "",
) -> tuple[float, str | None, str | None]:
    decision = executor.total_open_exposure_decision(
        proposed_size_usd=float(fill_economics.total_cost_usd or 0.0),
        account_equity=account_equity,
        trader_address=trader_address,
    )
    if decision.block_reason:
        return 0.0, decision.block_reason, None

    allowed_total_cost_usd = float(decision.allowed_size_usd or 0.0)
    if decision.clipped and allowed_total_cost_usd + 1e-9 < min_bet_usd():
        return (
            0.0,
            (
                f"remaining total exposure headroom was ${allowed_total_cost_usd:.2f}, "
                f"below the ${min_bet_usd():.2f} minimum bet size"
            ),
            None,
        )
    if allowed_total_cost_usd + 1e-9 >= float(fill_economics.total_cost_usd or 0.0):
        return requested_size_usd, None, None

    allowed_gross_size_usd = max(allowed_total_cost_usd - float(fill_economics.fixed_cost_usd or 0.0), 0.0)
    allowed_gross_size_usd = max(0.0, int((allowed_gross_size_usd + 1e-9) * 100.0) / 100.0)
    if allowed_gross_size_usd + 1e-9 < min_bet_usd():
        return (
            0.0,
            (
                f"remaining total exposure headroom was ${allowed_total_cost_usd:.2f}, "
                f"below the ${min_bet_usd():.2f} minimum bet size"
            ),
            None,
        )

    return (
        allowed_gross_size_usd,
        None,
        f"total exposure cap clipped size from ${requested_size_usd:.2f} to ${allowed_gross_size_usd:.2f}",
    )


def _apply_total_exposure_cap_to_sizing(
    executor: PolymarketExecutor,
    sizing: dict,
    *,
    bankroll: float,
    account_equity: float,
    trader_address: str = "",
) -> tuple[dict, str | None]:
    requested_size_usd = float(sizing.get("dollar_size") or 0.0)
    allowed_size_usd, block_reason, clip_note = _apply_total_exposure_cap_to_size(
        executor,
        requested_size_usd=requested_size_usd,
        account_equity=account_equity,
        trader_address=trader_address,
    )
    if block_reason:
        blocked = dict(sizing)
        blocked["dollar_size"] = 0.0
        blocked["kelly_f"] = 0.0
        blocked["reason"] = block_reason
        return blocked, None
    if allowed_size_usd + 1e-9 >= requested_size_usd:
        return sizing, None

    capped = dict(sizing)
    capped["dollar_size"] = allowed_size_usd
    capped["kelly_f"] = round(allowed_size_usd / bankroll, 5) if bankroll > 0 else 0.0
    return capped, clip_note


def _wait_for_next_poll(
    loop_started_at: float,
    state_snapshot: dict,
    on_tick=None,
    stop_event: threading.Event | None = None,
) -> None:
    last_interval = poll_interval()

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        if on_tick is not None:
            on_tick()

        current_interval = poll_interval()
        if current_interval != last_interval:
            logger.info("Poll interval updated to %ss", current_interval)
            last_interval = current_interval
            _write_bot_state(**state_snapshot)

        remaining = loop_started_at + current_interval - time.time()
        if remaining <= 0:
            return

        time.sleep(min(remaining, 1.0))


def _run_telegram_command_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            service_telegram_commands()
        except Exception:
            logger.exception("Telegram command service crashed")
        stop_event.wait(0.5)


def _parse_manual_trade_request_payload(payload: dict) -> ManualTradeRequest:
    if not isinstance(payload, dict):
        raise ValueError("manual trade request payload must be an object")

    raw_action = str(payload.get("action") or "").strip().lower()
    action_aliases = {
        "buy": "buy_more",
        "buy_more": "buy_more",
        "cash_out": "cash_out",
        "sell": "cash_out",
        "sell_all": "cash_out",
    }
    action = action_aliases.get(raw_action)
    if not action:
        raise ValueError(f"unsupported manual trade action: {raw_action or '-'}")

    market_id = str(payload.get("market_id") or "").strip()
    token_id = str(payload.get("token_id") or "").strip()
    side = str(payload.get("side") or "").strip().lower()
    question = str(payload.get("question") or "").strip()
    trader_address = str(payload.get("trader_address") or "").strip().lower()
    request_id = str(payload.get("request_id") or "").strip()
    requested_at = int(payload.get("requested_at") or 0)
    source = str(payload.get("source") or "unknown").strip().lower() or "unknown"
    amount_raw = payload.get("amount_usd")
    amount_usd = float(amount_raw) if amount_raw is not None else None

    if not market_id:
        raise ValueError("manual trade request is missing market_id")
    if not token_id:
        raise ValueError("manual trade request is missing token_id")
    if not side:
        raise ValueError("manual trade request is missing side")
    if action == "buy_more" and (amount_usd is None or amount_usd <= 0):
        raise ValueError("manual buy request must include a positive amount_usd")

    return ManualTradeRequest(
        action=action,
        market_id=market_id,
        token_id=token_id,
        side=side,
        question=question,
        trader_address=trader_address,
        amount_usd=amount_usd,
        request_id=request_id,
        requested_at=requested_at,
        source=source,
    )


def _consume_manual_retrain_request(run_retrain_job) -> bool:
    if not MANUAL_RETRAIN_REQUEST_FILE.exists():
        return False

    try:
        payload = json.loads(MANUAL_RETRAIN_REQUEST_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request payload must be an object")
    except Exception as exc:
        logger.warning("Discarding invalid manual retrain request: %s", exc)
        try:
            MANUAL_RETRAIN_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return False

    source = str(payload.get("source") or "unknown").strip().lower() or "unknown"
    request_id = str(payload.get("request_id") or "").strip()
    now_ts = int(time.time())
    next_retry_at = int(payload.get("next_retry_at") or 0)
    if next_retry_at > now_ts:
        return False
    request_age_seconds = _manual_request_age_seconds(payload, now_ts)
    if request_age_seconds is not None and request_age_seconds > 900:
        logger.info(
            "Ignoring stale manual retrain request from %s (age=%ss, request_id=%s)",
            source,
            request_age_seconds,
            request_id or "-",
        )
        try:
            MANUAL_RETRAIN_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return False

    logger.info(
        "Manual retrain requested by %s (request_id=%s)",
        source,
        request_id or "-",
    )
    try:
        run_retrain_job(f"manual_{source}")
    except Exception as exc:
        _record_manual_request_pickup_failure(MANUAL_RETRAIN_REQUEST_FILE, payload, exc)
        raise
    try:
        MANUAL_RETRAIN_REQUEST_FILE.unlink()
    except FileNotFoundError:
        pass
    return True


def _consume_manual_trade_request(handle_request) -> bool:
    if not MANUAL_TRADE_REQUEST_FILE.exists():
        return False

    try:
        payload = json.loads(MANUAL_TRADE_REQUEST_FILE.read_text(encoding="utf-8"))
        request = _parse_manual_trade_request_payload(payload)
    except Exception as exc:
        logger.warning("Discarding invalid manual trade request: %s", exc)
        try:
            MANUAL_TRADE_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return False

    now_ts = int(time.time())
    next_retry_at = int(payload.get("next_retry_at") or 0)
    if next_retry_at > now_ts:
        return False
    request_age_seconds = _manual_request_age_seconds(payload, now_ts)
    if request_age_seconds is not None and request_age_seconds > 900:
        logger.info(
            "Ignoring stale manual trade request from %s (action=%s age=%ss request_id=%s)",
            request.source,
            request.action,
            request_age_seconds,
            request.request_id or "-",
        )
        try:
            MANUAL_TRADE_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return False

    logger.info(
        "Manual trade requested by %s (action=%s market=%s request_id=%s)",
        request.source,
        request.action,
        request.market_id[:12],
        request.request_id or "-",
    )
    try:
        handle_request(request)
    except Exception as exc:
        _record_manual_request_pickup_failure(MANUAL_TRADE_REQUEST_FILE, payload, exc)
        raise
    try:
        MANUAL_TRADE_REQUEST_FILE.unlink()
    except FileNotFoundError:
        pass
    return True


def _parse_trade_log_archive_request_payload(payload: dict) -> TradeLogArchiveRequest:
    if not isinstance(payload, dict):
        raise ValueError("trade log archive request payload must be an object")

    return TradeLogArchiveRequest(
        request_id=str(payload.get("request_id") or "").strip(),
        requested_at=int(payload.get("requested_at") or 0),
        source=str(payload.get("source") or "dashboard").strip().lower() or "dashboard",
    )


def _consume_trade_log_archive_request(run_archive_job) -> bool:
    if not TRADE_LOG_ARCHIVE_REQUEST_FILE.exists():
        return False

    try:
        payload = json.loads(TRADE_LOG_ARCHIVE_REQUEST_FILE.read_text(encoding="utf-8"))
        request = _parse_trade_log_archive_request_payload(payload)
    except Exception as exc:
        logger.warning("Discarding invalid trade log archive request: %s", exc)
        try:
            TRADE_LOG_ARCHIVE_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return False

    now_ts = int(time.time())
    next_retry_at = int(payload.get("next_retry_at") or 0)
    if next_retry_at > now_ts:
        return False
    request_age_seconds = _manual_request_age_seconds(payload, now_ts)
    if request_age_seconds is not None and request_age_seconds > 900:
        logger.info(
            "Ignoring stale trade log archive request from %s (age=%ss request_id=%s)",
            request.source,
            request_age_seconds,
            request.request_id or "-",
        )
        try:
            TRADE_LOG_ARCHIVE_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return False

    logger.info(
        "Trade log archive requested by %s (request_id=%s)",
        request.source,
        request.request_id or "-",
    )
    try:
        run_archive_job(request)
    except Exception as exc:
        _record_manual_request_pickup_failure(TRADE_LOG_ARCHIVE_REQUEST_FILE, payload, exc)
        raise
    try:
        TRADE_LOG_ARCHIVE_REQUEST_FILE.unlink()
    except FileNotFoundError:
        pass
    return True


def _service_trade_log_archive_request(run_archive_job) -> bool:
    try:
        return _consume_trade_log_archive_request(run_archive_job)
    except Exception as exc:
        logger.error("Trade log archive request pickup failed: %s", exc, exc_info=True)
        return False


def _service_manual_retrain_request(run_retrain_job) -> bool:
    try:
        return _consume_manual_retrain_request(run_retrain_job)
    except Exception as exc:
        logger.error("Manual retrain request pickup failed: %s", exc, exc_info=True)
        return False


def _service_manual_trade_request(handle_request) -> bool:
    try:
        return _consume_manual_trade_request(handle_request)
    except Exception as exc:
        logger.error("Manual trade request pickup failed: %s", exc, exc_info=True)
        return False


def _configured_model_artifact_path() -> Path:
    artifact_path = Path(model_path())
    if artifact_path.is_absolute():
        return artifact_path
    return (REPO_ROOT / artifact_path).resolve()


def _prune_artifact_quarantine(*, quarantine_dir: Path, retain_count: int = 5) -> None:
    if retain_count <= 0 or not quarantine_dir.exists():
        return
    candidates = [path for path in quarantine_dir.iterdir() if path.is_file()]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for stale_path in candidates[retain_count:]:
        try:
            stale_path.unlink()
        except OSError:
            continue


def _quarantine_runtime_model_artifact(*, reason: str) -> str:
    artifact_path = _configured_model_artifact_path()
    if not artifact_path.exists():
        return ""
    quarantine_dir = artifact_path.parent / "artifact_quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    normalized_reason = re.sub(r"[^a-z0-9_-]+", "_", str(reason or "quarantine").strip().lower()) or "quarantine"
    candidate = quarantine_dir / f"{artifact_path.stem}.{normalized_reason}.{timestamp}{artifact_path.suffix}"
    attempt = 1
    while candidate.exists():
        candidate = quarantine_dir / (
            f"{artifact_path.stem}.{normalized_reason}.{timestamp}_{attempt}{artifact_path.suffix}"
        )
        attempt += 1
    artifact_path.replace(candidate)
    logger.warning(
        "Quarantined runtime model artifact %s to %s (%s)",
        artifact_path,
        candidate,
        normalized_reason,
    )
    _prune_artifact_quarantine(quarantine_dir=quarantine_dir)
    return str(candidate)


def _parse_shadow_reset_request_payload(payload: dict) -> ShadowResetRequest:
    if not isinstance(payload, dict):
        raise ValueError("shadow reset request payload must be an object")

    wallet_mode = str(payload.get("wallet_mode") or payload.get("walletMode") or "").strip().lower()
    if wallet_mode not in {"keep_active", "keep_all", "clear_all"}:
        raise ValueError("shadow reset request must include wallet_mode")

    return ShadowResetRequest(
        wallet_mode=wallet_mode,
        request_id=str(payload.get("request_id") or "").strip(),
        requested_at=int(payload.get("requested_at") or 0),
        source=str(payload.get("source") or "dashboard").strip().lower() or "dashboard",
    )


def _peek_shadow_reset_request() -> ShadowResetRequest | None:
    if not SHADOW_RESET_REQUEST_FILE.exists():
        return None
    try:
        payload = json.loads(SHADOW_RESET_REQUEST_FILE.read_text(encoding="utf-8"))
        request = _parse_shadow_reset_request_payload(payload)
    except Exception:
        return None
    now_ts = int(time.time())
    if request.requested_at > 0 and (now_ts - request.requested_at) > 900:
        return None
    return request


def _consume_shadow_reset_request() -> ShadowResetRequest | None:
    if not SHADOW_RESET_REQUEST_FILE.exists():
        return None

    try:
        payload = json.loads(SHADOW_RESET_REQUEST_FILE.read_text(encoding="utf-8"))
        request = _parse_shadow_reset_request_payload(payload)
    except Exception as exc:
        logger.warning("Discarding invalid shadow reset request: %s", exc)
        try:
            SHADOW_RESET_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return None

    try:
        SHADOW_RESET_REQUEST_FILE.unlink()
    except FileNotFoundError:
        pass

    now_ts = int(time.time())
    if request.requested_at > 0 and (now_ts - request.requested_at) > 900:
        logger.info(
            "Ignoring stale shadow reset request from %s (age=%ss request_id=%s)",
            request.source,
            now_ts - request.requested_at,
            request.request_id or "-",
        )
        return None

    logger.warning(
        "Shadow reset requested by %s (wallet_mode=%s request_id=%s)",
        request.source,
        request.wallet_mode,
        request.request_id or "-",
    )
    return request


def _canonical_db_recovery_request_path(path_text: str) -> str:
    raw_path = str(path_text or "").strip()
    if not raw_path:
        return ""
    try:
        return str(Path(raw_path).expanduser().resolve(strict=False))
    except Exception:
        return str(Path(raw_path).expanduser().absolute())


def _validated_db_recovery_request_paths(candidate_path: str, source_path: str) -> tuple[str, str]:
    recovery = db_recovery_state()
    expected_candidate_path = _canonical_db_recovery_request_path(
        str(recovery.get("db_recovery_candidate_path") or "").strip()
    )
    expected_source_path = _canonical_db_recovery_request_path(
        str(recovery.get("db_recovery_candidate_source_path") or DB_PATH).strip()
    )
    requested_candidate_path = _canonical_db_recovery_request_path(candidate_path)
    requested_source_path = _canonical_db_recovery_request_path(source_path or str(DB_PATH))

    if not bool(recovery.get("db_recovery_candidate_ready")) or not expected_candidate_path:
        raise ValueError("DB recovery request must target the current verified backup candidate")
    if requested_candidate_path != expected_candidate_path:
        raise ValueError(
            "DB recovery request candidate_path does not match the current verified backup candidate"
        )
    if requested_source_path != expected_source_path:
        raise ValueError(
            "DB recovery request source_path does not match the current verified recovery source"
        )
    return expected_candidate_path, expected_source_path


def _parse_db_recovery_request_payload(payload: dict) -> DbRecoveryRequest:
    if not isinstance(payload, dict):
        raise ValueError("DB recovery request payload must be an object")

    candidate_path = str(payload.get("candidate_path") or payload.get("candidatePath") or "").strip()
    source_path = str(
        payload.get("candidate_source_path")
        or payload.get("source_path")
        or payload.get("sourcePath")
        or ""
    ).strip()
    if not candidate_path:
        raise ValueError("DB recovery request must include candidate_path")
    candidate_path, source_path = _validated_db_recovery_request_paths(candidate_path, source_path)

    return DbRecoveryRequest(
        candidate_path=candidate_path,
        source_path=source_path,
        request_id=str(payload.get("request_id") or "").strip(),
        requested_at=int(payload.get("requested_at") or 0),
        source=str(payload.get("source") or "dashboard").strip().lower() or "dashboard",
    )


def _peek_db_recovery_request() -> DbRecoveryRequest | None:
    if not DB_RECOVERY_REQUEST_FILE.exists():
        return None
    try:
        payload = json.loads(DB_RECOVERY_REQUEST_FILE.read_text(encoding="utf-8"))
        request = _parse_db_recovery_request_payload(payload)
    except Exception:
        return None
    now_ts = int(time.time())
    if request.requested_at > 0 and (now_ts - request.requested_at) > 900:
        return None
    return request


def _consume_db_recovery_request() -> DbRecoveryRequest | None:
    if not DB_RECOVERY_REQUEST_FILE.exists():
        return None

    try:
        payload = json.loads(DB_RECOVERY_REQUEST_FILE.read_text(encoding="utf-8"))
        request = _parse_db_recovery_request_payload(payload)
    except Exception as exc:
        logger.warning("Discarding invalid DB recovery request: %s", exc)
        try:
            DB_RECOVERY_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        return None

    try:
        DB_RECOVERY_REQUEST_FILE.unlink()
    except FileNotFoundError:
        pass

    now_ts = int(time.time())
    if request.requested_at > 0 and (now_ts - request.requested_at) > 900:
        logger.info(
            "Ignoring stale DB recovery request from %s (age=%ss request_id=%s)",
            request.source,
            now_ts - request.requested_at,
            request.request_id or "-",
        )
        return None

    logger.warning(
        "DB recovery requested by %s (candidate=%s request_id=%s)",
        request.source,
        request.candidate_path,
        request.request_id or "-",
    )
    return request


def _queued_shadow_rebootstrap_request_label() -> str:
    db_recovery_request = _peek_db_recovery_request()
    if db_recovery_request is not None:
        return "db recovery"
    shadow_reset_request = _peek_shadow_reset_request()
    if shadow_reset_request is not None:
        return "shadow reset"
    return ""


def _manual_trade_event(
    request: ManualTradeRequest,
    *,
    trade_id: str,
    question: str,
    price: float,
    shares: float,
    size_usd: float,
    close_time: str,
    snapshot: dict | None,
    raw_market_metadata: dict | None,
    raw_orderbook: dict | None,
    metadata_fetched_at: int,
    orderbook_fetched_at: int,
) -> TradeEvent:
    now_ts = int(time.time())
    action = "buy" if request.action == "buy_more" else "sell"
    trader_address = request.trader_address or "manual-dashboard"
    return TradeEvent(
        trade_id=trade_id,
        market_id=request.market_id,
        question=question,
        side=request.side,
        action=action,
        price=price,
        shares=shares,
        size_usd=size_usd,
        token_id=request.token_id,
        trader_name="Manual Dashboard",
        trader_address=trader_address,
        timestamp=now_ts,
        close_time=close_time,
        snapshot=snapshot,
        raw_trade={
            "source": request.source,
            "request_id": request.request_id,
            "requested_at": request.requested_at,
            "manual_action": request.action,
            "amount_usd": request.amount_usd,
        },
        raw_market_metadata=raw_market_metadata or {},
        raw_orderbook=raw_orderbook,
        source_ts_raw=str(request.requested_at or now_ts),
        observed_at=now_ts,
        poll_started_at=now_ts,
        metadata_fetched_at=metadata_fetched_at,
        orderbook_fetched_at=orderbook_fetched_at,
        market_close_ts=PolymarketTracker._normalize_timestamp(close_time) if close_time else 0,
    )


def _process_manual_trade_request(
    request: ManualTradeRequest,
    *,
    tracker: PolymarketTracker,
    executor: PolymarketExecutor,
    dedup: DedupeCache,
    live_entry_guard: LiveEntryGuard | None,
    daily_loss_guard: DailyLossGuard | None,
) -> None:
    meta, metadata_fetched_at = tracker.get_market_metadata(request.market_id)
    question = request.question or str(meta.get("question") or meta.get("title") or request.market_id)
    close_time = str(meta.get("endDate") or meta.get("closedTime") or meta.get("closeTime") or "").strip()
    snapshot = dict(PolymarketTracker._metadata_snapshot(meta))
    orderbook_snapshot, raw_book, orderbook_fetched_at = tracker.get_orderbook_snapshot(request.token_id)
    if orderbook_snapshot:
        snapshot.update(orderbook_snapshot)

    manual_trade_id = f"manual-{request.action}-{request.request_id or int(time.time())}"
    trader_address = request.trader_address or "manual-dashboard"

    if request.action == "buy_more":
        account_equity = executor.get_account_equity_usd()
        entry_block_reason = _entry_pause_reason(
            tracker,
            executor,
            live_entry_guard,
            daily_loss_guard,
            account_equity,
        )
        amount_usd = float(request.amount_usd or 0.0)
        if entry_block_reason:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=float(snapshot.get("best_ask") or snapshot.get("mid") or 0.0),
                shares=0.0,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _pause_event(event, amount_usd, entry_block_reason)
            return

        capped_amount_usd, exposure_block_reason, clip_note = _apply_total_exposure_cap_to_size(
            executor,
            requested_size_usd=amount_usd,
            account_equity=account_equity,
            trader_address=trader_address,
        )
        if clip_note:
            logger.info("Manual buy %s: %s", manual_trade_id, clip_note)
        if exposure_block_reason:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=float(snapshot.get("best_ask") or snapshot.get("mid") or 0.0),
                shares=0.0,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _skip_event(event, amount_usd, exposure_block_reason, decision="MANUAL")
            return
        amount_usd = capped_amount_usd

        fill_estimate, fill_reason = executor.estimate_entry_fill(raw_book, amount_usd)
        if fill_estimate is None:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=float(snapshot.get("best_ask") or snapshot.get("mid") or 0.0),
                shares=0.0,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _skip_event(event, amount_usd, _humanize_reason(fill_reason or "manual buy quote failed"), decision="MANUAL")
            return

        fill_economics, fill_reason = executor.estimate_entry_economics(
            token_id=request.token_id,
            fill=fill_estimate,
            market_meta=meta,
        )
        if fill_economics is None:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=fill_estimate.avg_price,
                shares=fill_estimate.shares,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _skip_event(
                event,
                amount_usd,
                _humanize_reason(fill_reason or "entry fee model rejected the quoted fill"),
                decision="MANUAL",
            )
            return

        precise_amount_usd, exposure_block_reason, clip_note = _apply_total_exposure_cap_to_entry_cost(
            executor,
            requested_size_usd=amount_usd,
            fill_economics=fill_economics,
            account_equity=account_equity,
            trader_address=trader_address,
        )
        if clip_note:
            logger.info("Manual buy %s: %s", manual_trade_id, clip_note)
        if exposure_block_reason:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=fill_estimate.avg_price,
                shares=fill_estimate.shares,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _skip_event(event, amount_usd, exposure_block_reason, decision="MANUAL")
            return
        if precise_amount_usd + 1e-9 < amount_usd:
            amount_usd = precise_amount_usd
            fill_estimate, fill_reason = executor.estimate_entry_fill(raw_book, amount_usd)
            if fill_estimate is None:
                event = _manual_trade_event(
                    request,
                    trade_id=manual_trade_id,
                    question=question,
                    price=float(snapshot.get("best_ask") or snapshot.get("mid") or 0.0),
                    shares=0.0,
                    size_usd=amount_usd,
                    close_time=close_time,
                    snapshot=snapshot,
                    raw_market_metadata=meta,
                    raw_orderbook=raw_book,
                    metadata_fetched_at=metadata_fetched_at,
                    orderbook_fetched_at=orderbook_fetched_at,
                )
                _skip_event(
                    event,
                    amount_usd,
                    _humanize_reason(fill_reason or "manual buy quote failed"),
                    decision="MANUAL",
                )
                return
            fill_economics, fill_reason = executor.estimate_entry_economics(
                token_id=request.token_id,
                fill=fill_estimate,
                market_meta=meta,
            )
            if fill_economics is None:
                event = _manual_trade_event(
                    request,
                    trade_id=manual_trade_id,
                    question=question,
                    price=fill_estimate.avg_price,
                    shares=fill_estimate.shares,
                    size_usd=amount_usd,
                    close_time=close_time,
                    snapshot=snapshot,
                    raw_market_metadata=meta,
                    raw_orderbook=raw_book,
                    metadata_fetched_at=metadata_fetched_at,
                    orderbook_fetched_at=orderbook_fetched_at,
                )
                _skip_event(
                    event,
                    amount_usd,
                    _humanize_reason(fill_reason or "entry fee model rejected the quoted fill"),
                    decision="MANUAL",
                )
                return

        exposure_block_reason = executor.entry_risk_block_reason(
            market_id=request.market_id,
            trader_address=trader_address,
            proposed_size_usd=fill_economics.total_cost_usd,
            account_equity=account_equity,
        )
        if exposure_block_reason:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=fill_estimate.avg_price,
                shares=fill_estimate.shares,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _skip_event(event, amount_usd, exposure_block_reason, decision="MANUAL")
            return

        quoted_price = _quoted_execution_price(snapshot, fill_estimate.avg_price)
        market_f = build_market_features(snapshot, close_time, amount_usd, quoted_price)
        if market_f is None:
            event = _manual_trade_event(
                request,
                trade_id=manual_trade_id,
                question=question,
                price=fill_estimate.avg_price,
                shares=fill_estimate.shares,
                size_usd=amount_usd,
                close_time=close_time,
                snapshot=snapshot,
                raw_market_metadata=meta,
                raw_orderbook=raw_book,
                metadata_fetched_at=metadata_fetched_at,
                orderbook_fetched_at=orderbook_fetched_at,
            )
            _skip_event(event, amount_usd, "manual buy could not build market features", decision="MANUAL")
            return

        event = _manual_trade_event(
            request,
            trade_id=manual_trade_id,
            question=question,
            price=fill_estimate.avg_price,
            shares=fill_estimate.shares,
            size_usd=amount_usd,
            close_time=close_time,
            snapshot=snapshot,
            raw_market_metadata=meta,
            raw_orderbook=raw_book,
            metadata_fetched_at=metadata_fetched_at,
            orderbook_fetched_at=orderbook_fetched_at,
        )
        latency_reason = executor.entry_latency_block_reason(event)
        if latency_reason:
            manual_signal = {
                "mode": "manual",
                "manual": True,
                "source": request.source,
                "trader": {"score": None},
                "market": {"score": None},
            }
            executor.log_skip(
                trade_id=manual_trade_id,
                market_id=request.market_id,
                question=question,
                trader_address="manual",
                side=request.side,
                price=fill_estimate.avg_price,
                size_usd=amount_usd,
                confidence=0.0,
                kelly_f=0.0,
                reason=latency_reason,
                event=event,
                signal=manual_signal,
            )
            _skip_event(event, amount_usd, latency_reason, decision="MANUAL")
            return
        result = executor.execute(
            trade_id=manual_trade_id,
            market_id=request.market_id,
            token_id=request.token_id,
            side=request.side,
            dollar_size=amount_usd,
            kelly_f=0.0,
            confidence=0.0,
            signal={
                "mode": "manual",
                "manual": True,
                "source": request.source,
                "trader": {"score": None},
                "market": {"score": None},
            },
            event=event,
            trader_f=None,
            market_f=market_f,
            dedup=dedup,
        )
        if use_real_money():
            dedup.sync_positions_from_api(tracker, wallet_address())
        else:
            dedup.load_from_db(rebuild_shadow_positions=True)
        if result.placed:
            execution_price = (result.dollar_size / result.shares) if result.shares > 0 else event.price
            _emit_event(
                {
                    "type": "signal",
                    "trade_id": manual_trade_id,
                    "market_id": request.market_id,
                    "question": question,
                    "market_url": market_url_from_metadata(meta),
                    "side": request.side,
                    "action": "buy",
                    "price": round(execution_price, 6),
                    "shares": round(result.shares, 6),
                    "amount_usd": result.dollar_size,
                    "size_usd": result.dollar_size,
                    "username": "Manual Dashboard",
                    "trader": trader_address,
                    "decision": "MANUAL BUY",
                    "confidence": None,
                    "signal_mode": "manual",
                    "shadow": result.shadow,
                    "order_id": result.order_id,
                    "reason": "operator requested a manual buy from the dashboard",
                    "ts": int(time.time()),
                }
            )
        else:
            _skip_event(event, amount_usd, _humanize_reason(result.reason), decision="MANUAL")
        return

    position = dedup.get_position(request.market_id, request.token_id, request.side)
    position_size_usd = float((position or {}).get("size") or 0.0)
    price = float(snapshot.get("best_bid") or snapshot.get("mid") or 0.0)
    event = _manual_trade_event(
        request,
        trade_id=manual_trade_id,
        question=question,
        price=price,
        shares=0.0,
        size_usd=position_size_usd,
        close_time=close_time,
        snapshot=snapshot,
        raw_market_metadata=meta,
        raw_orderbook=raw_book,
        metadata_fetched_at=metadata_fetched_at,
        orderbook_fetched_at=orderbook_fetched_at,
    )
    result = executor.execute_exit(
        trade_id=manual_trade_id,
        market_id=request.market_id,
        token_id=request.token_id,
        side=request.side,
        event=event,
        dedup=dedup,
    )
    if use_real_money():
        dedup.sync_positions_from_api(tracker, wallet_address())
    if result.placed:
        execution_price = (result.dollar_size / result.shares) if result.shares > 0 else event.price
        _emit_event(
            {
                "type": "signal",
                "trade_id": manual_trade_id,
                "market_id": request.market_id,
                "question": question,
                "market_url": market_url_from_metadata(meta),
                "side": request.side,
                "action": "sell",
                "price": round(execution_price, 6),
                "shares": round(result.shares, 6),
                "amount_usd": result.dollar_size,
                "size_usd": result.dollar_size,
                "username": "Manual Dashboard",
                "trader": trader_address,
                "decision": "MANUAL EXIT",
                "confidence": None,
                "shadow": result.shadow,
                "order_id": result.order_id,
                "reason": "operator requested a manual cash out from the dashboard",
                "ts": int(time.time()),
            }
        )
        return

    _skip_event(event, position_size_usd, _humanize_reason(result.reason), decision="MANUAL")


def _log_runtime_ready(
    tracker: PolymarketTracker,
    watchlist: WatchlistManager,
) -> None:
    tier_state = watchlist.state_fields()
    logger.info(
        "Startup complete. Polling %s wallets every %ss "
        "(tracked=%s, dropped=%s, hot/warm/discovery=%s/%s/%s)",
        len(tracker.wallets),
        poll_interval(),
        tier_state["tracked_wallet_count"],
        tier_state["dropped_wallet_count"],
        tier_state["hot_wallet_count"],
        tier_state["warm_wallet_count"],
        tier_state["discovery_wallet_count"],
    )
    logger.info(
        "Runtime files: db=%s state=%s events=%s",
        DB_PATH,
        BOT_STATE_FILE,
        EVENT_FILE,
    )
    logger.info(
        "Console output stays quiet between events. Use %s or the dashboard to confirm liveness.",
        BOT_STATE_FILE,
    )


def _log_first_poll_summary(
    *,
    elapsed: float,
    polled_wallet_count: int,
    event_count: int,
    bankroll: float,
) -> None:
    logger.info(
        "First poll completed in %.2fs: wallets=%s events=%s bankroll=$%.2f",
        elapsed,
        polled_wallet_count,
        event_count,
        bankroll,
    )


def _reject_event(event, confidence: float, amount_usd: float, reason: str) -> None:
    shares = amount_usd / event.price if event.price > 0 else 0.0
    _emit_event(
        {
            "type": "signal",
            "trade_id": event.trade_id,
            "market_id": event.market_id,
            "question": event.question,
            **_event_market_payload(event),
            "side": event.side,
            "action": event.action,
            "price": event.price,
            "shares": round(shares, 6),
            "amount_usd": amount_usd,
            "size_usd": amount_usd,
            "username": event.trader_name,
            "trader": event.trader_address,
            "decision": "REJECT",
            "confidence": confidence,
            "reason": reason,
            "ts": int(time.time()),
        }
    )


def _skip_event(event, amount_usd: float, reason: str, decision: str = "SKIP") -> None:
    shares = amount_usd / event.price if event.price > 0 else 0.0
    _emit_event(
        {
            "type": "signal",
            "trade_id": event.trade_id,
            "market_id": event.market_id,
            "question": event.question,
            **_event_market_payload(event),
            "side": event.side,
            "action": event.action,
            "price": event.price,
            "shares": round(shares, 6),
            "amount_usd": amount_usd,
            "size_usd": amount_usd,
            "username": event.trader_name,
            "trader": event.trader_address,
            "decision": decision,
            "confidence": 0.0,
            "reason": reason,
            "ts": int(time.time()),
        }
    )


def _ignore_event(event, amount_usd: float, reason: str) -> None:
    _skip_event(event, amount_usd, reason, decision="IGNORE")


def _pause_event(event, amount_usd: float, reason: str) -> None:
    _skip_event(event, amount_usd, reason, decision="PAUSE")


def _quoted_execution_price(snapshot: Mapping[str, Any] | None, fallback: float | None = None) -> float:
    payload = snapshot if isinstance(snapshot, Mapping) else {}
    best_ask = float(payload.get("best_ask") or 0.0)
    if 0.0 < best_ask < 1.0:
        return best_ask
    mid = float(payload.get("mid") or 0.0)
    if 0.0 < mid < 1.0:
        return mid
    fallback_price = float(fallback or 0.0)
    return fallback_price if 0.0 < fallback_price < 1.0 else 0.0


def _model_fill_edge_block_reason(signal: Mapping[str, Any] | None, fill_economics) -> str | None:
    if not isinstance(signal, Mapping):
        return None
    if str(signal.get("mode") or "").strip().lower() != "xgboost":
        return None
    if fill_economics is None:
        return None
    try:
        confidence = float(signal.get("confidence"))
        effective_price = float(fill_economics.sizing_effective_price)
        edge_threshold = float(signal.get("edge_threshold") or 0.0)
    except (TypeError, ValueError):
        return None
    if confidence <= 0 or effective_price <= 0:
        return None
    estimated_edge = confidence - effective_price
    if estimated_edge + 1e-9 >= edge_threshold:
        return None
    return (
        f"estimated fill edge {estimated_edge:.3f} < threshold {edge_threshold:.3f} after slippage"
    )


def _is_non_actionable_exit_reason(reason: str) -> bool:
    return (reason or "").strip().lower() == "watched trader exited, but we had no matching position open to close"


def process_event(
    event,
    engine,
    executor,
    dedup,
    bankroll,
    account_equity,
    entry_block_reason: str | None = None,
) -> float:
    _emit_event(
        {
            "type": "incoming",
            "trade_id": event.trade_id,
            "market_id": event.market_id,
            "question": event.question,
            **_event_market_payload(event),
            "side": event.side,
            "action": event.action,
            "watch_tier": event.watch_tier,
            "price": event.price,
            "shares": event.shares,
            "amount_usd": event.size_usd,
            "size_usd": event.size_usd,
            "username": event.trader_name,
            "trader": event.trader_address,
            "ts": event.timestamp,
        }
    )

    if event.action == "sell":
        result = executor.execute_exit(
            trade_id=event.trade_id,
            market_id=event.market_id,
            token_id=event.token_id,
            side=event.side,
            event=event,
            dedup=dedup,
        )
        if result.placed:
            execution_price = (result.dollar_size / result.shares) if result.shares > 0 else event.price
            _emit_event(
                {
                    "type": "signal",
                    "trade_id": event.trade_id,
                    "market_id": event.market_id,
                    "question": event.question,
                    **_event_market_payload(event),
                    "side": event.side,
                    "action": event.action,
                    "watch_tier": event.watch_tier,
                    "price": round(execution_price, 6),
                    "shares": round(result.shares, 6),
                    "amount_usd": result.dollar_size,
                    "size_usd": result.dollar_size,
                    "username": event.trader_name,
                    "trader": event.trader_address,
                    "decision": "EXIT",
                    "confidence": 0.0,
                    "reason": result.reason,
                    "ts": int(time.time()),
                }
            )
            return result.dollar_size
        else:
            dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
            if _is_non_actionable_exit_reason(result.reason):
                _ignore_event(event, result.dollar_size, result.reason)
            else:
                executor.log_skip(
                    trade_id=event.trade_id,
                    market_id=event.market_id,
                    question=event.question,
                    trader_address=event.trader_address,
                    side=event.side,
                    price=event.price,
                    size_usd=result.dollar_size,
                    confidence=0.0,
                    kelly_f=0.0,
                    reason=result.reason,
                    event=event,
                )
                _skip_event(event, result.dollar_size, result.reason)
        return 0.0

    if event.action != "buy":
        reason = f"observed unsupported trader action, {event.action.upper()}"
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _ignore_event(event, 0.0, reason)
        return 0.0

    if entry_block_reason:
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _pause_event(event, 0.0, entry_block_reason)
        return 0.0

    market_data_ok, market_data_reason = executor.refresh_event_market_data(event)
    if not market_data_ok:
        reason = _humanize_reason(market_data_reason or "missing execution market data")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            event=event,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, 0.0, reason)
        return 0.0

    if not event.snapshot:
        reason = _humanize_reason("missing market snapshot")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            event=event,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, 0.0, reason)
        return 0.0

    trader_f = get_trader_features(event.trader_address, event.size_usd)
    rough_market_price = float(event.snapshot.get("best_ask") or event.snapshot.get("mid") or event.price or 0.0)
    if not (0.01 < rough_market_price < 0.99):
        rough_market_price = event.price
    rough = size_signal(0.65, rough_market_price, bankroll, engine.sizing_mode()).get("dollar_size", 0.0)
    rough_size = rough if rough > 0 else max(min_bet_usd(), 5.0)
    rough_fill, rough_fill_reason = executor.estimate_entry_fill(getattr(event, "raw_orderbook", None), rough_size)
    if rough_fill is None:
        reason = _humanize_reason(rough_fill_reason or "shadow simulation buy failed")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=rough_size,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            event=event,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, rough_size, reason)
        return 0.0

    rough_entry_economics, rough_econ_reason = executor.estimate_entry_economics(
        token_id=event.token_id,
        fill=rough_fill,
        market_meta=getattr(event, "raw_market_metadata", None),
    )
    if rough_entry_economics is None:
        reason = _humanize_reason(rough_econ_reason or "entry fee model rejected the quoted fill")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=rough_size,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            event=event,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, rough_size, reason)
        return 0.0

    quoted_market_price = _quoted_execution_price(event.snapshot, event.price)
    market_f = build_market_features(event.snapshot, event.close_time, rough_size, quoted_market_price)
    if market_f is None:
        reason = _humanize_reason("failed to build market features")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            event=event,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, 0.0, reason)
        return 0.0

    signal = engine.evaluate(
        trader_f,
        market_f,
        rough_size,
        trader_address=event.trader_address,
        watch_tier=event.watch_tier,
    )
    signal = dict(signal)
    signal["watch_tier"] = event.watch_tier or str(signal.get("watch_tier") or "")
    segment_meta = signal.get("segment")
    if isinstance(segment_meta, dict):
        signal["segment_id"] = str(segment_meta.get("segment_id") or signal.get("segment_id") or "")
        signal["segment_watch_tier"] = str(segment_meta.get("watch_tier") or signal["watch_tier"] or "")
        signal["segment_horizon_bucket"] = str(segment_meta.get("horizon_bucket") or "")
        signal["policy_id"] = str(segment_meta.get("policy_id") or signal.get("policy_id") or "")
        signal["policy_bundle_version"] = segment_meta.get("policy_bundle_version") or signal.get(
            "policy_bundle_version"
        )
        signal["segment_policy_id"] = signal["policy_id"]
        signal["segment_policy_bundle_version"] = signal["policy_bundle_version"]
    if signal.get("veto"):
        reason = _humanize_market_veto(signal["veto"])
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=0.0,
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, 0.0, 0.0, reason)
        return 0.0

    if not signal.get("passed", False):
        reason = _humanize_reason(signal.get("reason") or "signal rejected")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=signal["confidence"],
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, signal["confidence"], 0.0, reason)
        return 0.0

    ok, gate_reason = dedup.gate(
        event.trade_id,
        event.market_id,
        event.side,
        event.token_id,
        allow_existing_position=allow_duplicate_side_override(event.trader_address),
    )
    preview_sizing = size_signal(
        signal["confidence"],
        quoted_market_price if quoted_market_price > 0 else event.price,
        bankroll,
        signal.get("mode", "heuristic"),
        effective_market_price=rough_entry_economics.sizing_effective_price,
        min_confidence_override=signal.get("min_confidence"),
    )

    if not ok:
        reason = _humanize_reason(gate_reason)
        if gate_reason != "duplicate trade_id":
            executor.log_skip(
                trade_id=event.trade_id,
                market_id=event.market_id,
                question=event.question,
                trader_address=event.trader_address,
                side=event.side,
                price=event.price,
                size_usd=preview_sizing.get("dollar_size", 0.0),
                confidence=signal["confidence"],
                kelly_f=preview_sizing.get("kelly_f", 0.0),
                reason=reason,
                trader_f=trader_f,
                market_f=market_f,
                event=event,
                signal=signal,
            )
            dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
            _reject_event(event, signal["confidence"], preview_sizing.get("dollar_size", 0.0), reason)
        return 0.0

    trust_state = get_wallet_trust_state(event.trader_address)
    signal = dict(signal)
    signal["wallet_trust"] = trust_state.as_dict()
    wallet_quality_score = signal.get("trader", {}).get("score")
    if trust_state.skip_reason:
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=preview_sizing.get("dollar_size", 0.0),
            confidence=signal["confidence"],
            kelly_f=preview_sizing.get("kelly_f", 0.0),
            reason=trust_state.skip_reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _skip_event(event, preview_sizing.get("dollar_size", 0.0), trust_state.skip_reason)
        return 0.0

    max_wallet_size_usd = round(bankroll * max_bet_fraction(), 2)
    sizing = apply_wallet_trust_sizing(
        preview_sizing,
        trust_state,
        quality_score=wallet_quality_score,
        max_size_usd=max_wallet_size_usd,
    )
    sizing, clip_note = _apply_total_exposure_cap_to_sizing(
        executor,
        sizing,
        bankroll=bankroll,
        account_equity=account_equity,
        trader_address=event.trader_address,
    )
    if clip_note:
        logger.info("Trade %s: %s", event.trade_id, clip_note)
    fill_estimate = rough_fill
    fill_reason = None
    fill_economics = rough_entry_economics
    for _ in range(3):
        if sizing["dollar_size"] == 0.0:
            break
        fill_estimate, fill_reason = executor.estimate_entry_fill(
            getattr(event, "raw_orderbook", None),
            sizing["dollar_size"],
        )
        if fill_estimate is None:
            break
        fill_economics, fill_reason = executor.estimate_entry_economics(
            token_id=event.token_id,
            fill=fill_estimate,
            market_meta=getattr(event, "raw_market_metadata", None),
        )
        if fill_economics is None:
            break
        next_sizing = size_signal(
            signal["confidence"],
            quoted_market_price if quoted_market_price > 0 else event.price,
            bankroll,
            signal.get("mode", "heuristic"),
            effective_market_price=fill_economics.sizing_effective_price,
            min_confidence_override=signal.get("min_confidence"),
        )
        next_sizing = apply_wallet_trust_sizing(
            next_sizing,
            trust_state,
            quality_score=wallet_quality_score,
            max_size_usd=max_wallet_size_usd,
        )
        next_sizing, clip_note = _apply_total_exposure_cap_to_sizing(
            executor,
            next_sizing,
            bankroll=bankroll,
            account_equity=account_equity,
            trader_address=event.trader_address,
        )
        if clip_note:
            logger.info("Trade %s: %s", event.trade_id, clip_note)
        if (
            next_sizing["dollar_size"] == sizing["dollar_size"]
            and abs(next_sizing.get("kelly_f", 0.0) - sizing.get("kelly_f", 0.0)) < 1e-9
        ):
            sizing = next_sizing
            break
        sizing = next_sizing

    if sizing["dollar_size"] == 0.0:
        reason = _humanize_reason(sizing["reason"])
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=0.0,
            confidence=signal["confidence"],
            kelly_f=0.0,
            reason=reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, signal["confidence"], 0.0, reason)
        return 0.0

    if fill_estimate is None or fill_economics is None:
        reason = _humanize_reason(fill_reason or "entry fee model rejected the quoted fill")
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=sizing["dollar_size"],
            confidence=signal["confidence"],
            kelly_f=sizing.get("kelly_f", 0.0),
            reason=reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _reject_event(event, signal["confidence"], sizing["dollar_size"], reason)
        return 0.0

    precise_size_usd, exposure_block_reason, clip_note = _apply_total_exposure_cap_to_entry_cost(
        executor,
        requested_size_usd=sizing["dollar_size"],
        fill_economics=fill_economics,
        account_equity=account_equity,
        trader_address=event.trader_address,
    )
    if clip_note:
        logger.info("Trade %s: %s", event.trade_id, clip_note)
    if exposure_block_reason:
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=sizing["dollar_size"],
            confidence=signal["confidence"],
            kelly_f=sizing.get("kelly_f", 0.0),
            reason=exposure_block_reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _skip_event(event, sizing["dollar_size"], exposure_block_reason)
        return 0.0
    if precise_size_usd + 1e-9 < sizing["dollar_size"]:
        sizing["dollar_size"] = precise_size_usd
        sizing["kelly_f"] = round(precise_size_usd / bankroll, 5) if bankroll > 0 else 0.0
        fill_estimate, fill_reason = executor.estimate_entry_fill(
            getattr(event, "raw_orderbook", None),
            sizing["dollar_size"],
        )
        if fill_estimate is None:
            reason = _humanize_reason(fill_reason or "entry fee model rejected the quoted fill")
            executor.log_skip(
                trade_id=event.trade_id,
                market_id=event.market_id,
                question=event.question,
                trader_address=event.trader_address,
                side=event.side,
                price=event.price,
                size_usd=sizing["dollar_size"],
                confidence=signal["confidence"],
                kelly_f=sizing.get("kelly_f", 0.0),
                reason=reason,
                trader_f=trader_f,
                market_f=market_f,
                event=event,
                signal=signal,
            )
            dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
            _reject_event(event, signal["confidence"], sizing["dollar_size"], reason)
            return 0.0
        fill_economics, fill_reason = executor.estimate_entry_economics(
            token_id=event.token_id,
            fill=fill_estimate,
            market_meta=getattr(event, "raw_market_metadata", None),
        )
        if fill_economics is None:
            reason = _humanize_reason(fill_reason or "entry fee model rejected the quoted fill")
            executor.log_skip(
                trade_id=event.trade_id,
                market_id=event.market_id,
                question=event.question,
                trader_address=event.trader_address,
                side=event.side,
                price=event.price,
                size_usd=sizing["dollar_size"],
                confidence=signal["confidence"],
                kelly_f=sizing.get("kelly_f", 0.0),
                reason=reason,
                trader_f=trader_f,
                market_f=market_f,
                event=event,
                signal=signal,
            )
            dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
            _reject_event(event, signal["confidence"], sizing["dollar_size"], reason)
            return 0.0

    exposure_block_reason = executor.entry_risk_block_reason(
        market_id=event.market_id,
        trader_address=event.trader_address,
        proposed_size_usd=fill_economics.total_cost_usd,
        account_equity=account_equity,
    )
    if exposure_block_reason:
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=sizing["dollar_size"],
            confidence=signal["confidence"],
            kelly_f=sizing.get("kelly_f", 0.0),
            reason=exposure_block_reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _skip_event(event, sizing["dollar_size"], exposure_block_reason)
        return 0.0

    fill_edge_block_reason = _model_fill_edge_block_reason(signal, fill_economics)
    if fill_edge_block_reason:
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=sizing["dollar_size"],
            confidence=signal["confidence"],
            kelly_f=sizing.get("kelly_f", 0.0),
            reason=fill_edge_block_reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _skip_event(event, sizing["dollar_size"], fill_edge_block_reason)
        return 0.0

    latency_reason = executor.entry_latency_block_reason(event)
    if latency_reason:
        executor.log_skip(
            trade_id=event.trade_id,
            market_id=event.market_id,
            question=event.question,
            trader_address=event.trader_address,
            side=event.side,
            price=event.price,
            size_usd=sizing["dollar_size"],
            confidence=signal["confidence"],
            kelly_f=sizing.get("kelly_f", 0.0),
            reason=latency_reason,
            trader_f=trader_f,
            market_f=market_f,
            event=event,
            signal=signal,
        )
        dedup.mark_seen(event.trade_id, event.market_id, event.trader_address)
        _skip_event(event, sizing["dollar_size"], latency_reason)
        return 0.0

    market_f_final = build_market_features(
        event.snapshot,
        event.close_time,
        sizing["dollar_size"],
        quoted_market_price if quoted_market_price > 0 else fill_estimate.avg_price,
    )
    result = executor.execute(
        trade_id=event.trade_id,
        market_id=event.market_id,
        token_id=event.token_id,
        side=event.side,
        dollar_size=sizing["dollar_size"],
        kelly_f=sizing["kelly_f"],
        confidence=signal["confidence"],
        signal=signal,
        event=event,
        trader_f=trader_f,
        market_f=market_f_final or market_f,
        dedup=dedup,
    )

    if result.placed:
        execution_price = (result.dollar_size / result.shares) if result.shares > 0 else event.price
        _emit_event(
            {
                "type": "signal",
                "trade_id": event.trade_id,
                "market_id": event.market_id,
                "question": event.question,
                **_event_market_payload(event),
                "side": event.side,
                "action": event.action,
                "watch_tier": event.watch_tier,
                "price": round(execution_price, 6),
                "shares": round(result.shares, 6),
                "amount_usd": result.dollar_size,
                "size_usd": result.dollar_size,
                "username": event.trader_name,
                "trader": event.trader_address,
                "decision": "ACCEPT",
                "confidence": signal["confidence"],
                "raw_confidence": signal.get("raw_confidence"),
                "signal_mode": signal.get("mode"),
                "belief_prior": signal.get("belief_prior"),
                "belief_blend": signal.get("belief_blend"),
                "belief_evidence": signal.get("belief_evidence"),
                "trader_score": signal.get("trader", {}).get("score"),
                "market_score": signal.get("market", {}).get("score"),
                "segment_id": signal.get("segment_id"),
                "watch_tier": signal.get("watch_tier"),
                "segment_horizon_bucket": signal.get("segment_horizon_bucket"),
                "segment_policy_id": signal.get("segment_policy_id"),
                "segment_policy_bundle_version": signal.get("segment_policy_bundle_version"),
                "wallet_trust_tier": trust_state.tier,
                "wallet_trust_note": sizing.get("wallet_trust_note"),
                "wallet_quality_score": sizing.get("wallet_quality_score"),
                "wallet_quality_multiplier": sizing.get("wallet_quality_multiplier"),
                "shadow": result.shadow,
                "order_id": result.order_id,
                "reason": _humanize_reason("passed all checks"),
                "ts": int(time.time()),
            }
        )
        return -result.dollar_size
    else:
        _reject_event(event, signal["confidence"], 0.0, _humanize_reason(result.reason))
        return 0.0


def _backup_db() -> None:
    result = create_verified_backup()
    if bool(result.get("ok")):
        logger.info("Verified DB backup created at %s", result.get("backup_path"))
    else:
        logger.warning("Verified DB backup skipped: %s", result.get("message") or "unknown error")


def _trade_log_archive_state_payload(
    *,
    last_result: dict[str, Any] | None = None,
    archive_request: TradeLogArchiveRequest | None = None,
) -> dict[str, object]:
    enabled = trade_log_archive_enabled()
    latest_promotion = _latest_applied_replay_promotion()
    preserve_since_ts = _effective_shadow_evidence_since_ts(
        int((latest_promotion or {}).get("applied_at") or 0)
    )
    cutoff_ts = int(time.time()) - (trade_log_archive_min_age_days() * 86400) if enabled else 0
    payload = trade_log_archive_state(
        cutoff_ts=cutoff_ts,
        preserve_since_ts=preserve_since_ts,
    )
    payload["trade_log_archive_enabled"] = enabled
    payload["trade_log_archive_pending"] = bool(archive_request is not None)
    payload["trade_log_archive_requested_at"] = int(
        archive_request.requested_at if archive_request is not None else 0
    )
    payload["trade_log_archive_request_message"] = (
        f"Trade log archive requested by {archive_request.source} (request_id={archive_request.request_id or '-'})"
        if archive_request is not None
        else ""
    )
    if not enabled:
        payload.update(
            trade_log_archive_status="disabled",
            trade_log_archive_eligible_row_count=0,
            trade_log_archive_cutoff_ts=0,
            trade_log_archive_message="trade_log archiving is disabled",
        )
    status = str(payload.get("trade_log_archive_status") or "").strip().lower()
    message = str(payload.get("trade_log_archive_message") or "").strip()
    payload["trade_log_archive_block_reason"] = message if status.startswith("blocked") or status in {"error", "disabled"} else ""
    if last_result is not None:
        payload.update(
            trade_log_archive_last_run_at=int(time.time()),
            trade_log_archive_last_candidate_count=max(int(last_result.get("candidate_count") or 0), 0),
            trade_log_archive_last_archived_count=max(int(last_result.get("archived_count") or 0), 0),
            trade_log_archive_last_deleted_count=max(int(last_result.get("deleted_count") or 0), 0),
            trade_log_archive_last_vacuumed=bool(last_result.get("vacuumed")),
            trade_log_archive_last_message=str(last_result.get("message") or "").strip(),
        )
    return payload


def _path_size_bytes(path: Path) -> int:
    try:
        if not path.exists():
            return 0
        if path.is_file():
            return int(path.stat().st_size)
        total = 0
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += int(child.stat().st_size)
            except OSError:
                continue
        return total
    except OSError:
        return 0


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


def _directory_file_count(path: Path) -> int:
    try:
        if not path.exists() or not path.is_dir():
            return 0
        return sum(1 for child in path.rglob("*") if child.is_file())
    except OSError:
        return 0


def _storage_health_state_payload() -> dict[str, object]:
    trading_db_path = DB_PATH
    archive_db_path = trade_log_archive_db_path(trading_db_path)
    identity_cache_path = IDENTITY_CACHE_PATH
    background_log_path = BACKGROUND_LOG_PATH
    model_artifact_path = _configured_model_artifact_path()
    artifact_quarantine_dir = model_artifact_path.parent / "artifact_quarantine"
    db_recovery_quarantine_dir = trading_db_path.parent / "db_recovery_quarantine"
    integrity = database_integrity_state()

    save_dir_size = _path_size_bytes(SAVE_DIR)
    data_dir_size = _path_size_bytes(DATA_DIR)
    log_dir_size = _path_size_bytes(LOG_DIR)
    trading_db_size, trading_db_allocated = _file_size_metrics(trading_db_path)
    archive_db_size, archive_db_allocated = _file_size_metrics(archive_db_path)
    identity_cache_size = _path_size_bytes(identity_cache_path)
    events_file_size = _path_size_bytes(EVENT_FILE)
    background_log_size = _path_size_bytes(background_log_path)
    model_artifact_size = _path_size_bytes(model_artifact_path)
    artifact_quarantine_size = _path_size_bytes(artifact_quarantine_dir)
    artifact_quarantine_count = _directory_file_count(artifact_quarantine_dir)
    db_recovery_quarantine_size = _path_size_bytes(db_recovery_quarantine_dir)
    db_recovery_quarantine_count = _directory_file_count(db_recovery_quarantine_dir)

    message_parts: list[str] = []
    if integrity.get("db_integrity_known") and not integrity.get("db_integrity_ok"):
        message_parts.append("SQLite integrity check failed; hot-DB cleanup remains partially blocked")
    if trading_db_size >= 512 * 1024 * 1024:
        message_parts.append("active trading.db is oversized")
    if trading_db_allocated > max(trading_db_size, 1) * 1.25:
        message_parts.append("trading.db consumes materially more disk than its logical size")
    if save_dir_size >= 1024 * 1024 * 1024:
        message_parts.append("save/ is above 1 GB")
    if artifact_quarantine_count > 0:
        message_parts.append(f"artifact quarantine holds {artifact_quarantine_count} file(s)")
    if db_recovery_quarantine_count > 0:
        message_parts.append(f"DB recovery quarantine holds {db_recovery_quarantine_count} file(s)")

    return {
        "storage_state_known": True,
        "storage_save_dir_size_bytes": save_dir_size,
        "storage_data_dir_size_bytes": data_dir_size,
        "storage_log_dir_size_bytes": log_dir_size,
        "storage_trading_db_size_bytes": trading_db_size,
        "storage_trading_db_allocated_bytes": trading_db_allocated,
        "storage_trade_log_archive_db_size_bytes": archive_db_size,
        "storage_trade_log_archive_db_allocated_bytes": archive_db_allocated,
        "storage_identity_cache_size_bytes": identity_cache_size,
        "storage_events_file_size_bytes": events_file_size,
        "storage_background_log_size_bytes": background_log_size,
        "storage_model_artifact_size_bytes": model_artifact_size,
        "storage_artifact_quarantine_file_count": artifact_quarantine_count,
        "storage_artifact_quarantine_size_bytes": artifact_quarantine_size,
        "storage_db_recovery_quarantine_file_count": db_recovery_quarantine_count,
        "storage_db_recovery_quarantine_size_bytes": db_recovery_quarantine_size,
        "storage_message": "; ".join(message_parts),
    }


def _archive_old_trade_log_batch() -> dict[str, object]:
    if not trade_log_archive_enabled():
        return {
            "ok": True,
            "candidate_count": 0,
            "archived_count": 0,
            "deleted_count": 0,
            "vacuumed": False,
            "message": "trade_log archiving is disabled",
        }

    integrity = database_integrity_state()
    if integrity.get("db_integrity_known") and not integrity.get("db_integrity_ok"):
        detail = str(integrity.get("db_integrity_message") or "").splitlines()[0].strip() or "unknown error"
        logger.warning(
            "Trade log archive maintenance skipped: SQLite integrity check failed (%s)",
            detail,
        )
        return {
            "ok": False,
            "candidate_count": 0,
            "archived_count": 0,
            "deleted_count": 0,
            "vacuumed": False,
            "message": f"SQLite integrity check failed: {detail}",
        }

    latest_promotion = _latest_applied_replay_promotion()
    preserve_since_ts = _effective_shadow_evidence_since_ts(
        int((latest_promotion or {}).get("applied_at") or 0)
    )
    cutoff_ts = int(time.time()) - (trade_log_archive_min_age_days() * 86400)
    result = archive_old_trade_log_rows(
        cutoff_ts=cutoff_ts,
        preserve_since_ts=preserve_since_ts,
        batch_size=trade_log_archive_batch_rows(),
        vacuum=trade_log_archive_vacuum_enabled(),
    )
    if not bool(result.get("ok")):
        logger.warning("Trade log archive maintenance failed: %s", result.get("message") or "unknown error")
        return result
    deleted_count = max(int(result.get("deleted_count") or 0), 0)
    if deleted_count <= 0:
        logger.info("Trade log archive maintenance found no eligible rows to archive")
        return result
    logger.info(
        "Archived %s old trade_log row(s) to %s (preserve_since_ts=%s, cutoff_ts=%s, vacuumed=%s)",
        deleted_count,
        result.get("archive_path") or "",
        result.get("preserve_since_ts") or 0,
        result.get("cutoff_ts") or 0,
        bool(result.get("vacuumed")),
    )
    return result


def _shadow_history_trust_block_reason(action_label: str) -> str:
    integrity = database_integrity_state()
    if integrity.get("db_integrity_known") and not integrity.get("db_integrity_ok"):
        detail = str(integrity.get("db_integrity_message") or "").splitlines()[0].strip()
        suffix = f" ({detail})" if detail else ""
        return (
            f"{str(action_label or 'shadow optimization').strip() or 'shadow optimization'} blocked: "
            f"SQLite integrity check failed; shadow history is not trustworthy{suffix}"
        )
    return ""


def _segment_routed_history_trust_block_reason(action_label: str) -> str:
    since_ts = None
    try:
        since_ts, _ = _current_shadow_segment_scope_since_ts()
    except Exception:
        since_ts = None
    try:
        report = compute_segment_shadow_report(mode="shadow", since_ts=since_ts)
    except Exception as exc:
        return (
            f"{str(action_label or 'shadow optimization').strip() or 'shadow optimization'} blocked: "
            f"champion segment shadow history could not be evaluated ({exc})"
        )

    routed_state = _routed_shadow_gate_state(report)
    if bool(routed_state.get("routed_shadow_ready")):
        return ""
    detail = str(routed_state.get("routed_shadow_block_reason") or "").strip()
    return (
        f"{str(action_label or 'shadow optimization').strip() or 'shadow optimization'} blocked: "
        f"{detail or 'routed post-segmentation shadow evidence is insufficient'}"
    )


def _shadow_snapshot_trust_block_reason(action_label: str) -> str:
    try:
        preview = compute_tracker_preview_summary(
            mode="shadow",
            use_bot_state_balance=False,
            since_ts=_shadow_evidence_epoch_started_at(),
            apply_shadow_evidence_epoch=False,
        )
        payload = _shadow_snapshot_state_payload(
            preview,
            all_time_resolved=_resolved_shadow_trade_count(),
        )
    except Exception as exc:
        return (
            f"{str(action_label or 'shadow optimization').strip() or 'shadow optimization'} blocked: "
            f"current shadow snapshot could not be evaluated ({exc})"
        )

    if bool(payload.get("shadow_snapshot_ready")):
        return ""

    detail = str(payload.get("shadow_snapshot_block_reason") or "").strip()
    if not detail:
        detail = "current shadow snapshot is not trustworthy"
    return (
        f"{str(action_label or 'shadow optimization').strip() or 'shadow optimization'} blocked: "
        f"{detail}"
    )


def _retrain_trust_block_state(action_label: str) -> tuple[str, str]:
    blocked_message = _shadow_history_trust_block_reason(action_label)
    if blocked_message:
        return "blocked_db_integrity", blocked_message
    blocked_message = _shadow_snapshot_trust_block_reason(action_label)
    if blocked_message:
        return "blocked_shadow_snapshot", blocked_message
    return "", ""


def _looks_like_placeholder(value: str) -> bool:
    text = (value or "").strip().lower()
    return (
        not text
        or "your_" in text
        or text.endswith("_here")
        or text in {"changeme", "replace_me"}
    )


_ROUTED_SHADOW_SEGMENT_IDS: tuple[str, ...] = (*SEGMENT_IDS, SEGMENT_FALLBACK)
_ROUTED_SHADOW_SEGMENT_PLACEHOLDERS = ",".join("?" for _ in _ROUTED_SHADOW_SEGMENT_IDS)


def _resolved_shadow_trade_count_filtered(
    *,
    since_ts: int = 0,
    routed_only: bool | None = None,
) -> int:
    where_clauses = [
        "real_money=0",
        RESOLVED_EXECUTED_ENTRY_SQL,
    ]
    params: list[Any] = []
    if since_ts > 0:
        where_clauses.append("resolved_at > ?")
        params.append(int(since_ts))
    if routed_only is True:
        where_clauses.append(
            f"COALESCE(TRIM(segment_id), '') IN ({_ROUTED_SHADOW_SEGMENT_PLACEHOLDERS})"
        )
        params.extend(_ROUTED_SHADOW_SEGMENT_IDS)
    elif routed_only is False:
        where_clauses.append(
            f"COALESCE(TRIM(segment_id), '') NOT IN ({_ROUTED_SHADOW_SEGMENT_PLACEHOLDERS})"
        )
        params.extend(_ROUTED_SHADOW_SEGMENT_IDS)

    conn = None
    try:
        conn = get_trade_log_read_conn()
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM trade_log
            WHERE {" AND ".join(where_clauses)}
            """,
            tuple(params),
        ).fetchone()
        return int(row["n"] or 0)
    except sqlite3.DatabaseError as exc:
        if _is_missing_schema_error(exc):
            raise
        logger.warning("Resolved shadow trade count unavailable: %s", exc)
        return 0
    finally:
        if conn is not None:
            conn.close()


def _resolved_shadow_trade_count() -> int:
    return _resolved_shadow_trade_count_filtered()


def _resolved_shadow_trade_count_since(since_ts: int) -> int:
    return _resolved_shadow_trade_count_filtered(since_ts=max(int(since_ts or 0), 0))


def _resolved_shadow_trade_count_in_current_evidence_window() -> int:
    return _resolved_shadow_trade_count_since(_shadow_evidence_epoch_started_at())


def _compact_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _json_object_or_empty(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _env_value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return _compact_json(value)
    return str(value)


def _replay_search_transient_status_state(
    *,
    status: str,
    message: str,
    trigger: str,
    started_at: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "last_replay_search_status": status,
        "last_replay_search_message": message,
        "last_replay_search_trigger": trigger,
        "last_replay_search_scope": "shadow_only",
        "last_replay_search_run_id": 0,
        "last_replay_search_candidate_count": 0,
        "last_replay_search_feasible_count": 0,
        "last_replay_search_best_score": None,
        "last_replay_search_best_pnl_usd": None,
    }
    if started_at is not None:
        payload.update(
            replay_search_in_progress=True,
            replay_search_started_at=int(started_at),
            last_replay_search_started_at=int(started_at),
        )
    return payload


PROMOTABLE_REPLAY_CONFIG_KEYS = frozenset(str(key).strip().upper() for key in REPLAY_POLICY_CONFIG_KEY_MAP.values())


def _latest_retrain_state_payload(run_row: dict[str, Any] | None) -> dict[str, object]:
    row = run_row or {}
    return {
        "last_retrain_started_at": int(row.get("started_at") or 0),
        "last_retrain_finished_at": int(row.get("finished_at") or 0),
        "last_retrain_status": str(row.get("status") or ""),
        "last_retrain_message": str(row.get("message") or ""),
        "last_retrain_sample_count": int(row.get("sample_count") or 0),
        "last_retrain_min_samples": int(row.get("min_samples") or 0),
        "last_retrain_trigger": str(row.get("trigger") or ""),
        "last_retrain_deployed": bool(row.get("deployed")),
    }


def _latest_replay_search_state_payload(run_row: dict[str, Any] | None) -> dict[str, object]:
    row = run_row or {}
    run_id = int(row.get("id") or 0)
    candidate_count = int(row.get("candidate_count") or 0)
    feasible_count = int(row.get("feasible_count") or 0)
    status = str(row.get("status") or "").strip()
    status_text = status or "completed"
    message = str(row.get("status_message") or "").strip()
    if not message and run_id > 0:
        message = (
            f"Replay search {status_text} "
            f"(run={run_id}, candidates={candidate_count}, feasible={feasible_count})"
        )
    return {
        "last_replay_search_started_at": int(row.get("started_at") or 0),
        "last_replay_search_finished_at": int(row.get("finished_at") or 0),
        "last_replay_search_status": status,
        "last_replay_search_message": message,
        "last_replay_search_trigger": str(row.get("trigger") or ""),
        "last_replay_search_scope": "shadow_only",
        "last_replay_search_run_id": run_id,
        "last_replay_search_candidate_count": candidate_count,
        "last_replay_search_feasible_count": feasible_count,
        "last_replay_search_best_score": row.get("best_feasible_score"),
        "last_replay_search_best_pnl_usd": row.get("best_feasible_total_pnl_usd"),
    }


def _latest_replay_promotion() -> dict[str, Any] | None:
    conn = None
    try:
        conn = get_conn()
        row = conn.execute(
            """
            SELECT *
            FROM replay_promotions
            ORDER BY
                CASE
                    WHEN finished_at > 0 THEN finished_at
                    ELSE requested_at
                END DESC,
                id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.DatabaseError as exc:
        logger.warning("Replay promotion history unavailable: %s", exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def _latest_applied_replay_promotion(*, db_path: Path | None = None) -> dict[str, Any] | None:
    conn = None
    try:
        conn = get_conn_for_path(db_path, apply_runtime_pragmas=False) if db_path is not None else get_conn()
        try:
            row = conn.execute(
                """
                SELECT *
                FROM replay_promotions
                WHERE status='applied'
                  AND applied_at > 0
                ORDER BY applied_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return None
            raise
        return dict(row) if row is not None else None
    except sqlite3.DatabaseError as exc:
        logger.warning("Applied replay promotion history unavailable: %s", exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def _resolved_shadow_trade_count_since_last_promotion(
    promotion: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    current_promotion = promotion if promotion is not None else _latest_applied_replay_promotion()
    since_ts = int((current_promotion or {}).get("applied_at") or 0)
    effective_since_ts = _effective_shadow_evidence_since_ts(since_ts)
    return _resolved_shadow_trade_count_since(effective_since_ts), current_promotion


def _current_shadow_segment_scope_since_ts(
    promotion: dict[str, Any] | None = None,
) -> tuple[int | None, dict[str, Any] | None]:
    current_promotion = promotion if promotion is not None else _latest_applied_replay_promotion()
    promotion_since_ts = int((current_promotion or {}).get("applied_at") or 0)
    effective_since_ts = _effective_shadow_evidence_since_ts(promotion_since_ts)
    return (effective_since_ts if effective_since_ts > 0 else None), current_promotion


def _shadow_history_gate_metrics(
    *,
    last_promotion: dict[str, Any] | None = None,
) -> tuple[dict[str, int], dict[str, Any] | None]:
    current_promotion = last_promotion if last_promotion is not None else _latest_applied_replay_promotion()
    all_time_resolved = _resolved_shadow_trade_count()
    current_window_resolved = _resolved_shadow_trade_count_in_current_evidence_window()
    current_baseline_resolved, current_promotion = _resolved_shadow_trade_count_since_last_promotion(
        current_promotion
    )
    routed_current_window_resolved = _resolved_shadow_trade_count_filtered(
        since_ts=_shadow_evidence_epoch_started_at(),
        routed_only=True,
    )
    return (
        {
            "all_time_resolved": all_time_resolved,
            "current_window_resolved": current_window_resolved,
            "current_baseline_resolved": current_baseline_resolved,
            "routed_current_window_resolved": routed_current_window_resolved,
        },
        current_promotion,
    )


def _shadow_history_current_baseline_label(
    *,
    last_promotion: dict[str, Any] | None = None,
    epoch_payload: dict[str, Any] | None = None,
) -> str:
    current_promotion = last_promotion if last_promotion is not None else _latest_applied_replay_promotion()
    current_epoch_payload = epoch_payload if epoch_payload is not None else _shadow_evidence_epoch_state_payload()
    epoch_started_at = max(int(current_epoch_payload.get("shadow_evidence_epoch_started_at") or 0), 0)
    epoch_source_label = str(current_epoch_payload.get("shadow_evidence_epoch_source_label") or "").strip()
    promotion_applied_at = int((current_promotion or {}).get("applied_at") or 0)
    if epoch_started_at > 0 and epoch_started_at >= promotion_applied_at:
        return f"{epoch_source_label or 'current evidence epoch'} at {epoch_started_at}"
    if promotion_applied_at > 0:
        return f"last replay promotion at {promotion_applied_at}"
    return "initial policy"


def _replay_promotion_event_at(promotion: dict[str, Any] | None) -> int:
    row = promotion or {}
    applied_at = int(row.get("applied_at") or 0)
    if applied_at > 0:
        return applied_at
    finished_at = int(row.get("finished_at") or 0)
    if finished_at > 0:
        return finished_at
    return int(row.get("requested_at") or 0)


def _replay_promotion_state_payload(
    *,
    prefix: str,
    promotion_id: Any = 0,
    event_at: Any = 0,
    status: Any = "",
    message: Any = "",
    scope: Any = "shadow_only",
    run_id: Any = 0,
    candidate_id: Any = 0,
    score_delta: Any = None,
    pnl_delta_usd: Any = None,
) -> dict[str, object]:
    return {
        f"{prefix}_id": int(promotion_id or 0),
        f"{prefix}_at": int(event_at or 0),
        f"{prefix}_status": str(status or ""),
        f"{prefix}_message": str(message or ""),
        f"{prefix}_scope": str(scope or "shadow_only"),
        f"{prefix}_run_id": int(run_id or 0),
        f"{prefix}_candidate_id": int(candidate_id or 0),
        f"{prefix}_score_delta": score_delta,
        f"{prefix}_pnl_delta_usd": pnl_delta_usd,
    }


def _applied_replay_promotion_state_payload(promotion: dict[str, Any] | None) -> dict[str, object]:
    row = promotion or {}
    return _replay_promotion_state_payload(
        prefix="last_applied_replay_promotion",
        promotion_id=row.get("id"),
        event_at=row.get("applied_at"),
        status=row.get("status"),
        message=row.get("reason"),
        scope=row.get("scope"),
        run_id=row.get("replay_search_run_id"),
        candidate_id=row.get("replay_search_candidate_id"),
        score_delta=row.get("score_delta"),
        pnl_delta_usd=row.get("pnl_delta_usd"),
    )


def _shadow_evidence_epoch_state_payload(report: dict[str, Any] | None = None) -> dict[str, object]:
    epoch_state = read_shadow_evidence_epoch(path=SHADOW_EVIDENCE_EPOCH_FILE)
    report = report or {}
    source = str(
        report.get("shadow_evidence_epoch_source")
        or epoch_state.get("shadow_evidence_epoch_source")
        or ""
    ).strip().lower()
    started_at = max(
        int(
            report.get("shadow_evidence_epoch_started_at")
            or report.get("routed_shadow_epoch_started_at")
            or epoch_state.get("shadow_evidence_epoch_started_at")
            or 0
        ),
        0,
    )
    source_label = str(
        report.get("shadow_evidence_epoch_source_label")
        or report.get("routed_shadow_epoch_source_label")
        or source.replace("_", " ").strip()
        or ""
    ).strip()
    known = bool(
        report.get("shadow_evidence_epoch_known")
        if "shadow_evidence_epoch_known" in report
        else report.get("routed_shadow_epoch_known")
        if "routed_shadow_epoch_known" in report
        else epoch_state.get("shadow_evidence_epoch_known")
    )
    active_scope_label = str(
        report.get("shadow_evidence_epoch_active_scope_label")
        or report.get("routed_shadow_epoch_active_scope_label")
        or (f"since {source_label}" if started_at > 0 and source_label else "all history")
    ).strip()
    status = str(
        report.get("shadow_evidence_epoch_status")
        or report.get("routed_shadow_epoch_status")
        or ("active" if started_at > 0 else "all_history")
    ).strip()
    return {
        "shadow_evidence_epoch_known": known,
        "shadow_evidence_epoch_started_at": started_at,
        "shadow_evidence_epoch_source": source,
        "shadow_evidence_epoch_source_label": source_label,
        "shadow_evidence_epoch_active_scope_label": active_scope_label,
        "shadow_evidence_epoch_status": status,
        "shadow_evidence_epoch_request_id": str(
            report.get("shadow_evidence_epoch_request_id")
            or epoch_state.get("shadow_evidence_epoch_request_id")
            or ""
        ).strip(),
        "shadow_evidence_epoch_message": str(
            report.get("shadow_evidence_epoch_message")
            or epoch_state.get("shadow_evidence_epoch_message")
            or ""
        ).strip(),
        "routed_shadow_epoch_known": known,
        "routed_shadow_epoch_started_at": started_at,
        "routed_shadow_epoch_source_label": source_label,
        "routed_shadow_epoch_active_scope_label": active_scope_label,
        "routed_shadow_epoch_status": status,
    }


def _shadow_evidence_epoch_started_at() -> int:
    return max(
        int(
            read_shadow_evidence_epoch(path=SHADOW_EVIDENCE_EPOCH_FILE).get(
                "shadow_evidence_epoch_started_at"
            )
            or 0
        ),
        0,
    )


def _effective_shadow_evidence_since_ts(*timestamps: int | None) -> int:
    effective = _shadow_evidence_epoch_started_at()
    for raw in timestamps:
        candidate = max(int(raw or 0), 0)
        if candidate > effective:
            effective = candidate
    return effective


def _latest_replay_promotion_state_payload(promotion: dict[str, Any] | None) -> dict[str, object]:
    row = promotion or {}
    return _replay_promotion_state_payload(
        prefix="last_replay_promotion",
        promotion_id=row.get("id"),
        event_at=_replay_promotion_event_at(row),
        status=row.get("status"),
        message=row.get("reason"),
        scope=row.get("scope"),
        run_id=row.get("replay_search_run_id"),
        candidate_id=row.get("replay_search_candidate_id"),
        score_delta=row.get("score_delta"),
        pnl_delta_usd=row.get("pnl_delta_usd"),
    )


def _replay_promotion_state_updates(result: dict[str, Any]) -> dict[str, object]:
    payload = _replay_promotion_state_payload(
        prefix="last_replay_promotion",
        promotion_id=result.get("promotion_id"),
        event_at=result.get("event_at"),
        status=result.get("status"),
        message=result.get("message"),
        scope=result.get("scope"),
        run_id=result.get("run_id"),
        candidate_id=result.get("candidate_id"),
        score_delta=result.get("score_delta"),
        pnl_delta_usd=result.get("pnl_delta_usd"),
    )
    if str(result.get("status") or "").strip().lower() == "applied":
        payload.update(
            _applied_replay_promotion_state_payload(
                {
                    "id": result.get("promotion_id"),
                    "applied_at": result.get("applied_at"),
                    "status": result.get("status"),
                    "reason": result.get("message"),
                    "scope": result.get("scope"),
                    "replay_search_run_id": result.get("run_id"),
                    "replay_search_candidate_id": result.get("candidate_id"),
                    "score_delta": result.get("score_delta"),
                    "pnl_delta_usd": result.get("pnl_delta_usd"),
                }
            )
        )
    return payload


def _shadow_history_state_payload(
    *,
    total_resolved_shadow: int,
    resolved_since_promotion: int,
    last_promotion: dict[str, Any] | None,
    require_total_history: bool,
    minimum_total: int,
    minimum_since_promotion: int,
    all_time_resolved_shadow: int | None = None,
    routed_resolved_shadow: int | None = None,
) -> dict[str, object]:
    epoch_payload = _shadow_evidence_epoch_state_payload()
    total_resolved = max(int(total_resolved_shadow or 0), 0)
    resolved_since = max(int(resolved_since_promotion or 0), 0)
    all_time_resolved = max(
        int(all_time_resolved_shadow if all_time_resolved_shadow is not None else total_resolved),
        0,
    )
    routed_resolved = max(int(routed_resolved_shadow or 0), 0)
    total_required = max(int(minimum_total or 0), 0) if require_total_history else 0
    since_required = max(int(minimum_since_promotion or 0), 0)
    epoch_started_at = max(int(epoch_payload.get("shadow_evidence_epoch_started_at") or 0), 0)
    promotion_applied_at = int((last_promotion or {}).get("applied_at") or 0)
    current_baseline_at = _effective_shadow_evidence_since_ts(promotion_applied_at)
    epoch_source_label = str(epoch_payload.get("shadow_evidence_epoch_source_label") or "").strip()
    current_baseline_label = _shadow_history_current_baseline_label(
        last_promotion=last_promotion,
        epoch_payload=epoch_payload,
    )
    legacy_all_time_resolved = max(all_time_resolved - total_resolved, 0)
    current_window_blocked = max(total_resolved - resolved_since, 0)
    current_window_coverage_pct = (
        _finite_float_or_none(round(resolved_since / total_resolved, 6))
        if total_resolved > 0
        else None
    )
    total_ready = (total_resolved >= total_required) if require_total_history else True
    since_ready = resolved_since >= since_required
    gate_ready = total_ready and since_ready
    block_reasons: list[str] = []
    if require_total_history and not total_ready:
        block_reasons.append(
            f"need {total_resolved}/{total_required} resolved shadow trades in the current evidence window"
        )
    if since_required > 0 and not since_ready:
        block_reasons.append(
            f"need {resolved_since}/{since_required} resolved shadow trades since {current_baseline_label}"
        )
    if legacy_all_time_resolved > 0 and total_resolved <= 0:
        block_reasons.append(
            f"{legacy_all_time_resolved} legacy/all-time resolved shadow trades remain excluded from the current evidence window"
        )
    block_reason = "; ".join(block_reasons)
    if gate_ready:
        epoch_status = "ready"
    elif total_resolved > 0:
        epoch_status = "blocked"
    elif legacy_all_time_resolved > 0 and epoch_started_at > 0:
        epoch_status = "legacy_only"
    else:
        epoch_status = str(epoch_payload.get("shadow_evidence_epoch_status") or "all_history")
    payload: dict[str, object] = {
        "shadow_history_state_known": True,
        "resolved_shadow_trade_count": total_resolved,
        "shadow_history_all_time_resolved": all_time_resolved,
        "live_require_shadow_history_enabled": bool(require_total_history),
        "live_min_shadow_resolved": total_required,
        "live_shadow_history_total_ready": total_ready,
        "resolved_shadow_since_last_promotion": resolved_since,
        "shadow_history_current_baseline_at": current_baseline_at,
        "shadow_history_current_baseline_label": current_baseline_label,
        "live_min_shadow_resolved_since_last_promotion": since_required,
        "live_shadow_history_ready": since_ready,
        "shadow_history_epoch_known": bool(epoch_payload.get("shadow_evidence_epoch_known")),
        "shadow_history_epoch_started_at": epoch_started_at,
        "shadow_history_epoch_source_label": epoch_source_label,
        "shadow_history_epoch_active_scope_label": str(
            epoch_payload.get("shadow_evidence_epoch_active_scope_label") or "all history"
        ).strip(),
        "shadow_history_epoch_status": epoch_status,
        "shadow_history_epoch_total_resolved": total_resolved,
        "shadow_history_epoch_ready_count": resolved_since,
        "shadow_history_epoch_blocked_count": current_window_blocked,
        "shadow_history_epoch_min_resolved": max(total_required, since_required),
        "shadow_history_epoch_routed_resolved": routed_resolved,
        "shadow_history_epoch_legacy_resolved": legacy_all_time_resolved,
        "shadow_history_epoch_coverage_pct": current_window_coverage_pct,
        "shadow_history_epoch_ready": gate_ready,
        "shadow_history_epoch_block_reason": block_reason,
    }
    payload.update(epoch_payload)
    payload.update(_applied_replay_promotion_state_payload(last_promotion))
    return payload


def _segment_shadow_state_payload(report: dict[str, Any]) -> dict[str, object]:
    segments = report.get("segments") or []
    routed_resolved, legacy_resolved, history_status, routed_coverage_pct = _segment_history_metrics_from_report(report)
    routed_state = _routed_shadow_gate_state(report)
    return {
        **_shadow_evidence_epoch_state_payload(report),
        "shadow_segment_state_known": True,
        "shadow_segment_status": str(report.get("status") or "unknown"),
        "shadow_segment_scope": str(report.get("scope") or "all_history"),
        "shadow_segment_scope_started_at": int(report.get("since_ts") or 0),
        "shadow_segment_min_resolved": max(int(report.get("min_resolved") or 0), 0),
        "shadow_segment_total": max(int(report.get("total_segments") or 0), 0),
        "shadow_segment_ready_count": max(int(report.get("ready_count") or 0), 0),
        "shadow_segment_positive_count": max(int(report.get("positive_count") or 0), 0),
        "shadow_segment_negative_count": max(int(report.get("negative_count") or 0), 0),
        "shadow_segment_blocked_count": max(int(report.get("blocked_count") or 0), 0),
        "shadow_segment_history_status": history_status,
        "shadow_segment_routed_signals": max(int(report.get("routed_signals") or 0), 0),
        "shadow_segment_routed_acted": max(int(report.get("routed_acted") or 0), 0),
        "shadow_segment_routed_resolved": routed_resolved,
        "shadow_segment_legacy_resolved": legacy_resolved,
        "shadow_segment_routing_coverage_pct": routed_coverage_pct,
        "shadow_segment_summary_json": json.dumps(_json_safe_value(segments), separators=(",", ":")),
        "shadow_segment_block_reason": str(report.get("summary") or "").strip(),
        **routed_state,
    }


def _segment_shadow_error_payload(message: str) -> dict[str, object]:
    return {
        **_shadow_evidence_epoch_state_payload(),
        "shadow_segment_state_known": True,
        "shadow_segment_status": "error",
        "shadow_segment_scope": "all_history",
        "shadow_segment_scope_started_at": 0,
        "shadow_segment_min_resolved": 0,
        "shadow_segment_total": 0,
        "shadow_segment_ready_count": 0,
        "shadow_segment_positive_count": 0,
        "shadow_segment_negative_count": 0,
        "shadow_segment_blocked_count": 0,
        "shadow_segment_history_status": "empty",
        "shadow_segment_routed_signals": 0,
        "shadow_segment_routed_acted": 0,
        "shadow_segment_routed_resolved": 0,
        "shadow_segment_legacy_resolved": 0,
        "shadow_segment_routing_coverage_pct": None,
        "shadow_segment_summary_json": "[]",
        "shadow_segment_block_reason": str(message or "").strip(),
        "routed_shadow_state_known": True,
        "routed_shadow_status": "error",
        "routed_shadow_min_resolved": max(int(SEGMENT_SHADOW_MIN_RESOLVED or 0), 0),
        "routed_shadow_routed_resolved": 0,
        "routed_shadow_legacy_resolved": 0,
        "routed_shadow_total_resolved": 0,
        "routed_shadow_coverage_pct": None,
        "routed_shadow_ready": False,
        "routed_shadow_block_reason": str(message or "").strip(),
    }


def _finite_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _message_looks_like_integrity_failure(message: object) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(
        needle in text
        for needle in (
            "integrity check failed",
            "database disk image is malformed",
            "sqlite",
            "corrupt",
            "checksum",
            "invalid page",
            "rowid out of order",
            "btreeinitpage",
        )
    )


def _routed_shadow_performance_state_payload(preview) -> dict[str, object]:
    return {
        "routed_shadow_total_pnl_usd": _finite_float_or_none(getattr(preview, "routed_total_pnl", None)),
        "routed_shadow_return_pct": _finite_float_or_none(getattr(preview, "routed_return_pct", None)),
        "routed_shadow_profit_factor": _finite_float_or_none(getattr(preview, "routed_profit_factor", None)),
        "routed_shadow_expectancy_usd": _finite_float_or_none(getattr(preview, "routed_expectancy_usd", None)),
        "routed_shadow_data_warning": str(getattr(preview, "data_warning", "") or "").strip(),
    }


def _shadow_snapshot_state_payload(
    preview,
    *,
    all_time_resolved: int | None = None,
) -> dict[str, object]:
    epoch_state = _shadow_evidence_epoch_state_payload()
    scope_started_at = max(
        int(getattr(preview, "shadow_evidence_epoch_started_at", 0) or 0),
        int(epoch_state.get("shadow_evidence_epoch_started_at") or 0),
        0,
    )
    scope_source = str(getattr(preview, "shadow_evidence_epoch_source", "") or "").strip().lower()
    scope = "all_history"
    if scope_started_at > 0:
        scope = "current_evidence_window"
    resolved = max(int(getattr(preview, "resolved", 0) or 0), 0)
    total_all_time_resolved = max(int(all_time_resolved if all_time_resolved is not None else resolved), 0)
    excluded_pre_window_resolved = max(total_all_time_resolved - resolved, 0)
    routed_resolved = max(int(getattr(preview, "routed_resolved", 0) or 0), 0)
    legacy_resolved = max(int(getattr(preview, "routed_legacy_resolved", 0) or 0), 0)
    total_resolved_history = routed_resolved + legacy_resolved
    coverage_pct = (
        _finite_float_or_none(round(routed_resolved / total_resolved_history, 6))
        if total_resolved_history > 0
        else None
    )
    data_warning = str(getattr(preview, "data_warning", "") or "").strip()
    if data_warning:
        status = "error"
    elif scope == "current_evidence_window":
        if resolved > 0 and routed_resolved > 0 and legacy_resolved > 0:
            status = "mixed"
        elif resolved > 0 and routed_resolved <= 0 and legacy_resolved > 0:
            status = "legacy_only"
        elif resolved > 0 and routed_resolved > 0:
            status = "ready"
        elif resolved <= 0 and excluded_pre_window_resolved > 0:
            status = "excluded"
        else:
            status = "idle"
    else:
        status = "all_history"
    ready = (
        scope == "current_evidence_window"
        and not data_warning
        and resolved > 0
        and routed_resolved > 0
        and (coverage_pct is None or coverage_pct > 0)
    )
    block_reasons: list[str] = []
    if scope != "current_evidence_window":
        block_reasons.append(
            "current evidence window is not active yet; shadow snapshot still reflects all history"
        )
    if data_warning:
        block_reasons.append(data_warning)
    if scope == "current_evidence_window" and routed_resolved <= 0 and legacy_resolved > 0:
        block_reasons.append(
            f"need routed fixed-segment evidence; {legacy_resolved} legacy/unassigned resolved rows are not sufficient"
        )
    if scope == "current_evidence_window" and resolved <= 0 and excluded_pre_window_resolved > 0:
        block_reasons.append(
            f"{excluded_pre_window_resolved} pre-window resolved shadow trades remain excluded from this snapshot"
        )
    block_reason = "; ".join(reason for reason in block_reasons if reason)
    if data_warning:
        block_state = "blocked_db_integrity" if _message_looks_like_integrity_failure(data_warning) else "blocked_error"
    elif scope != "current_evidence_window":
        block_state = "blocked_all_history"
    elif resolved > 0 and routed_resolved <= 0 and legacy_resolved > 0:
        block_state = "blocked_legacy_only"
    elif resolved <= 0 and excluded_pre_window_resolved > 0:
        block_state = "blocked_pre_window_excluded"
    elif resolved <= 0:
        block_state = "blocked_empty"
    else:
        block_state = "ready" if ready else "partial"
    return {
        "shadow_snapshot_state_known": True,
        "shadow_snapshot_scope": scope,
        "shadow_snapshot_started_at": scope_started_at,
        "shadow_snapshot_status": status,
        "shadow_snapshot_resolved": resolved,
        "shadow_snapshot_routed_resolved": routed_resolved,
        "shadow_snapshot_legacy_resolved": legacy_resolved,
        "shadow_snapshot_coverage_pct": coverage_pct,
        "shadow_snapshot_ready": ready,
        "shadow_snapshot_total_pnl_usd": _finite_float_or_none(getattr(preview, "total_pnl", None)),
        "shadow_snapshot_return_pct": _finite_float_or_none(getattr(preview, "return_pct", None)),
        "shadow_snapshot_profit_factor": _finite_float_or_none(getattr(preview, "profit_factor", None)),
        "shadow_snapshot_expectancy_usd": _finite_float_or_none(getattr(preview, "expectancy_usd", None)),
        "shadow_snapshot_block_reason": block_reason,
        "shadow_snapshot_block_state": block_state,
        "shadow_snapshot_optimization_block_reason": "" if ready else block_reason,
    }


def _shadow_snapshot_error_payload(message: str) -> dict[str, object]:
    block_reason = str(message or "").strip()
    block_state = "blocked_db_integrity" if _message_looks_like_integrity_failure(block_reason) else "blocked_error"
    return {
        "shadow_snapshot_state_known": True,
        "shadow_snapshot_scope": "all_history",
        "shadow_snapshot_started_at": 0,
        "shadow_snapshot_status": "error",
        "shadow_snapshot_resolved": 0,
        "shadow_snapshot_routed_resolved": 0,
        "shadow_snapshot_legacy_resolved": 0,
        "shadow_snapshot_coverage_pct": None,
        "shadow_snapshot_ready": False,
        "shadow_snapshot_total_pnl_usd": None,
        "shadow_snapshot_return_pct": None,
        "shadow_snapshot_profit_factor": None,
        "shadow_snapshot_expectancy_usd": None,
        "shadow_snapshot_block_reason": block_reason,
        "shadow_snapshot_block_state": block_state,
        "shadow_snapshot_optimization_block_reason": block_reason,
    }


def _routed_shadow_performance_error_payload(message: str) -> dict[str, object]:
    return {
        "routed_shadow_total_pnl_usd": None,
        "routed_shadow_return_pct": None,
        "routed_shadow_profit_factor": None,
        "routed_shadow_expectancy_usd": None,
        "routed_shadow_data_warning": str(message or "").strip(),
    }


def _db_recovery_shadow_idle_payload(
    *,
    candidate_path: str = "",
    status: str = "idle",
    block_reason: str = "",
) -> dict[str, object]:
    return {
        "db_recovery_shadow_state_known": True,
        "db_recovery_shadow_candidate_path": str(candidate_path or "").strip(),
        "db_recovery_shadow_status": str(status or "idle").strip() or "idle",
        "db_recovery_shadow_acted": 0,
        "db_recovery_shadow_resolved": 0,
        "db_recovery_shadow_total_pnl_usd": None,
        "db_recovery_shadow_return_pct": None,
        "db_recovery_shadow_profit_factor": None,
        "db_recovery_shadow_expectancy_usd": None,
        "db_recovery_shadow_data_warning": "",
        "db_recovery_shadow_segment_total": 0,
        "db_recovery_shadow_segment_ready_count": 0,
        "db_recovery_shadow_segment_blocked_count": 0,
        "db_recovery_shadow_history_status": "empty",
        "db_recovery_shadow_min_resolved": 0,
        "db_recovery_shadow_routed_resolved": 0,
        "db_recovery_shadow_legacy_resolved": 0,
        "db_recovery_shadow_total_resolved": 0,
        "db_recovery_shadow_routing_coverage_pct": None,
        "db_recovery_shadow_ready": False,
        "db_recovery_shadow_segment_summary_json": "[]",
        "db_recovery_shadow_block_reason": str(block_reason or "").strip(),
    }


def _db_recovery_candidate_classification_state(
    *,
    recovery_state: dict[str, object] | None = None,
    shadow_state: dict[str, object] | None = None,
) -> dict[str, object]:
    recovery = dict(recovery_state or {})
    shadow = dict(shadow_state or {})
    candidate_ready = bool(recovery.get("db_recovery_candidate_ready"))
    candidate_path = str(
        recovery.get("db_recovery_candidate_path")
        or shadow.get("db_recovery_shadow_candidate_path")
        or ""
    ).strip()
    unavailable_reason = str(recovery.get("db_recovery_candidate_message") or "").strip()

    if not candidate_ready or not candidate_path:
        return {
            "db_recovery_candidate_mode": "unavailable",
            "db_recovery_candidate_evidence_ready": False,
            "db_recovery_candidate_class_reason": unavailable_reason,
        }

    shadow_status = str(shadow.get("db_recovery_shadow_status") or "").strip().lower()
    shadow_ready = bool(shadow.get("db_recovery_shadow_ready"))
    shadow_reason = str(shadow.get("db_recovery_shadow_block_reason") or "").strip()
    history_status = str(shadow.get("db_recovery_shadow_history_status") or "").strip().lower()

    evidence_ready = shadow_ready and shadow_status.endswith("ready")
    if evidence_ready:
        return {
            "db_recovery_candidate_mode": "evidence_ready",
            "db_recovery_candidate_evidence_ready": True,
            "db_recovery_candidate_class_reason": (
                shadow_reason
                or "verified backup is both integrity-restorable and fixed-segment evidence-ready"
            ),
        }

    if not shadow_reason:
        if history_status == "legacy_only":
            shadow_reason = (
                "verified backup restores ledger integrity but only contains legacy/unassigned resolved history"
            )
        elif shadow_status in {"legacy", "legacy_only", "migrated_legacy_only"}:
            shadow_reason = "verified backup restores ledger integrity but is legacy-only, not evidence-ready"
        elif shadow_status in {"insufficient", "migrated_insufficient"}:
            shadow_reason = "verified backup restores ledger integrity but lacks enough routed shadow evidence"
        elif shadow_status in {"error", "blocked"}:
            shadow_reason = "verified backup restores ledger integrity but shadow evidence evaluation is not trustworthy"
        else:
            shadow_reason = "verified backup restores ledger integrity but is not fixed-segment evidence-ready"

    return {
        "db_recovery_candidate_mode": "integrity_only",
        "db_recovery_candidate_evidence_ready": False,
        "db_recovery_candidate_class_reason": shadow_reason,
    }


def _segment_history_metrics_from_report(report: dict[str, Any]) -> tuple[int, int, str, float | None]:
    segments = report.get("segments") or []
    routed_resolved = max(int(report.get("routed_resolved") or 0), 0)
    legacy_resolved = max(int(report.get("legacy_unassigned_resolved") or 0), 0)

    if segments and routed_resolved <= 0:
        routed_resolved = sum(
            max(int((row or {}).get("resolved") or 0), 0)
            for row in segments
            if str((row or {}).get("segment_id") or "").strip() != UNASSIGNED_SEGMENT_ID
            and str((row or {}).get("health") or "").strip().lower() != "unexpected"
        )
    if segments and legacy_resolved <= 0:
        legacy_resolved = sum(
            max(int((row or {}).get("resolved") or 0), 0)
            for row in segments
            if str((row or {}).get("segment_id") or "").strip() == UNASSIGNED_SEGMENT_ID
        )

    history_status = str(report.get("history_status") or "").strip()
    if not history_status:
        if routed_resolved <= 0 and legacy_resolved > 0:
            history_status = "legacy_only"
        elif routed_resolved > 0 and legacy_resolved > 0:
            history_status = "mixed"
        elif routed_resolved > 0:
            history_status = "routed_only"
        else:
            history_status = "empty"

    routed_coverage_pct = _finite_float_or_none(report.get("routed_coverage_pct"))
    total_resolved = routed_resolved + legacy_resolved
    if routed_coverage_pct is None and total_resolved > 0:
        routed_coverage_pct = routed_resolved / total_resolved
    return routed_resolved, legacy_resolved, history_status, routed_coverage_pct


def _routed_shadow_gate_state(
    report: dict[str, Any],
    *,
    min_resolved: int = SEGMENT_SHADOW_MIN_RESOLVED,
) -> dict[str, object]:
    routed_resolved, legacy_resolved, history_status, routed_coverage_pct = _segment_history_metrics_from_report(report)
    minimum = max(int(min_resolved or 0), 0)
    total_resolved = routed_resolved + legacy_resolved
    ready = total_resolved > 0 if minimum <= 0 else routed_resolved >= minimum

    if total_resolved <= 0:
        status = "empty"
        block_reason = "no routed post-segmentation shadow evidence yet"
        ready = False
    elif history_status == "legacy_only":
        status = "legacy_only"
        block_reason = (
            "all resolved shadow rows predate fixed segment routing; "
            f"{routed_resolved} routed resolved, {legacy_resolved} legacy/unassigned resolved"
        )
        ready = False
    elif not ready:
        status = "insufficient"
        block_reason = (
            f"need {routed_resolved}/{minimum} routed resolved shadow trades; "
            f"{legacy_resolved} legacy/unassigned resolved remain excluded from fixed-segment evidence"
        )
    else:
        status = "ready"
        block_reason = (
            f"{routed_resolved}/{total_resolved} resolved shadow trades are routed fixed-segment rows"
            if legacy_resolved > 0
            else f"{routed_resolved} routed resolved shadow trades available"
        )

    return {
        "routed_shadow_state_known": True,
        "routed_shadow_status": status,
        "routed_shadow_min_resolved": minimum,
        "routed_shadow_routed_resolved": routed_resolved,
        "routed_shadow_legacy_resolved": legacy_resolved,
        "routed_shadow_total_resolved": total_resolved,
        "routed_shadow_coverage_pct": routed_coverage_pct,
        "routed_shadow_ready": bool(ready),
        "routed_shadow_block_reason": block_reason,
    }


def _db_recovery_shadow_state_payload(
    *,
    candidate_path: Path,
    preview,
    report: dict[str, Any],
    extra_data_warning: str = "",
    status_override: str = "",
) -> dict[str, object]:
    segments = report.get("segments") or []
    routed_resolved, legacy_resolved, history_status, routed_coverage_pct = _segment_history_metrics_from_report(report)
    routed_state = _routed_shadow_gate_state(report)
    data_warning_parts = [str(preview.data_warning or "").strip(), str(extra_data_warning or "").strip()]
    data_warning = " | ".join(part for part in data_warning_parts if part)
    return {
        "db_recovery_shadow_state_known": True,
        "db_recovery_shadow_candidate_path": str(candidate_path),
        "db_recovery_shadow_status": str(status_override or report.get("status") or "unknown"),
        "db_recovery_shadow_acted": max(int(preview.acted or 0), 0),
        "db_recovery_shadow_resolved": max(int(preview.resolved or 0), 0),
        "db_recovery_shadow_total_pnl_usd": _finite_float_or_none(preview.total_pnl),
        "db_recovery_shadow_return_pct": _finite_float_or_none(preview.return_pct),
        "db_recovery_shadow_profit_factor": _finite_float_or_none(preview.profit_factor),
        "db_recovery_shadow_expectancy_usd": _finite_float_or_none(preview.expectancy_usd),
        "db_recovery_shadow_data_warning": data_warning,
        "db_recovery_shadow_segment_total": max(int(report.get("total_segments") or 0), 0),
        "db_recovery_shadow_segment_ready_count": max(int(report.get("ready_count") or 0), 0),
        "db_recovery_shadow_segment_blocked_count": max(int(report.get("blocked_count") or 0), 0),
        "db_recovery_shadow_history_status": history_status,
        "db_recovery_shadow_min_resolved": max(int(routed_state.get("routed_shadow_min_resolved") or 0), 0),
        "db_recovery_shadow_routed_resolved": routed_resolved,
        "db_recovery_shadow_legacy_resolved": legacy_resolved,
        "db_recovery_shadow_total_resolved": max(int(routed_state.get("routed_shadow_total_resolved") or 0), 0),
        "db_recovery_shadow_routing_coverage_pct": routed_coverage_pct,
        "db_recovery_shadow_ready": bool(routed_state.get("routed_shadow_ready")),
        "db_recovery_shadow_segment_summary_json": json.dumps(
            _json_safe_value(segments),
            separators=(",", ":"),
        ),
        "db_recovery_shadow_block_reason": str(report.get("summary") or "").strip(),
    }


def _db_recovery_shadow_preview_only_payload(
    *,
    candidate_path: Path,
    preview,
    status: str,
    block_reason: str,
    extra_data_warning: str = "",
) -> dict[str, object]:
    data_warning_parts = [str(preview.data_warning or "").strip(), str(extra_data_warning or "").strip()]
    data_warning = " | ".join(part for part in data_warning_parts if part)
    return {
        "db_recovery_shadow_state_known": True,
        "db_recovery_shadow_candidate_path": str(candidate_path),
        "db_recovery_shadow_status": str(status or "legacy").strip() or "legacy",
        "db_recovery_shadow_acted": max(int(preview.acted or 0), 0),
        "db_recovery_shadow_resolved": max(int(preview.resolved or 0), 0),
        "db_recovery_shadow_total_pnl_usd": _finite_float_or_none(preview.total_pnl),
        "db_recovery_shadow_return_pct": _finite_float_or_none(preview.return_pct),
        "db_recovery_shadow_profit_factor": _finite_float_or_none(preview.profit_factor),
        "db_recovery_shadow_expectancy_usd": _finite_float_or_none(preview.expectancy_usd),
        "db_recovery_shadow_data_warning": data_warning,
        "db_recovery_shadow_segment_total": 0,
        "db_recovery_shadow_segment_ready_count": 0,
        "db_recovery_shadow_segment_blocked_count": 0,
        "db_recovery_shadow_history_status": "empty",
        "db_recovery_shadow_min_resolved": max(int(SEGMENT_SHADOW_MIN_RESOLVED or 0), 0),
        "db_recovery_shadow_routed_resolved": 0,
        "db_recovery_shadow_legacy_resolved": 0,
        "db_recovery_shadow_total_resolved": 0,
        "db_recovery_shadow_routing_coverage_pct": None,
        "db_recovery_shadow_ready": False,
        "db_recovery_shadow_segment_summary_json": "[]",
        "db_recovery_shadow_block_reason": str(block_reason or "").strip(),
    }


def _is_missing_schema_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "no such table" in message or "no such column" in message


def _evaluate_db_recovery_shadow_state(
    candidate_path: Path,
    *,
    evaluation_path: Path,
    status_prefix: str = "",
    extra_data_warning: str = "",
) -> dict[str, object]:
    preview = compute_tracker_preview_summary(
        mode="shadow",
        db_path=evaluation_path,
        use_bot_state_balance=False,
        apply_shadow_evidence_epoch=False,
    )
    last_promotion = _latest_applied_replay_promotion(db_path=evaluation_path)
    since_ts = int((last_promotion or {}).get("applied_at") or 0)
    report = compute_segment_shadow_report(
        mode="shadow",
        since_ts=since_ts if since_ts > 0 else None,
        db_path=evaluation_path,
    )
    status_override = ""
    reported_status = str(report.get("status") or "").strip()
    if status_prefix and reported_status:
        status_override = f"{status_prefix}_{reported_status}"
    elif status_prefix:
        status_override = status_prefix
    return _db_recovery_shadow_state_payload(
        candidate_path=candidate_path,
        preview=preview,
        report=report,
        extra_data_warning=extra_data_warning,
        status_override=status_override,
    )


def _evaluate_db_recovery_shadow_state_from_migrated_clone(candidate_path: Path) -> dict[str, object]:
    note = (
        "evaluated from a migrated temp clone because the verified backup predates the current schema; "
        "backup file unchanged"
    )
    with tempfile.TemporaryDirectory(prefix="db-recovery-shadow-") as tmpdir:
        temp_db = Path(tmpdir) / candidate_path.name
        shutil.copy2(candidate_path, temp_db)
        init_db(path=temp_db, run_heavy_maintenance=False)
        return _evaluate_db_recovery_shadow_state(
            candidate_path,
            evaluation_path=temp_db,
            status_prefix="migrated",
            extra_data_warning=note,
        )


def _evaluate_migrated_recovery_candidate_shadow_state(candidate_path: Path) -> dict[str, object]:
    return _evaluate_db_recovery_shadow_state_from_migrated_clone(Path(candidate_path))


def _compute_db_recovery_shadow_state() -> dict[str, object]:
    recovery = db_recovery_state()
    candidate_text = str(recovery.get("db_recovery_candidate_path") or "").strip()
    if not candidate_text:
        failure_message = str(recovery.get("db_recovery_candidate_message") or "").strip()
        shadow_state = _db_recovery_shadow_idle_payload(
            status="blocked" if failure_message else "idle",
            block_reason=failure_message,
        )
        return {
            **shadow_state,
            **_db_recovery_candidate_classification_state(
                recovery_state=recovery,
                shadow_state=shadow_state,
            ),
        }

    candidate_path = Path(candidate_text)
    try:
        try:
            shadow_state = _evaluate_db_recovery_shadow_state(
                candidate_path,
                evaluation_path=candidate_path,
            )
        except sqlite3.OperationalError as exc:
            if _is_missing_schema_error(exc):
                shadow_state = _evaluate_db_recovery_shadow_state_from_migrated_clone(candidate_path)
            else:
                raise
        return {
            **shadow_state,
            **_db_recovery_candidate_classification_state(
                recovery_state=recovery,
                shadow_state=shadow_state,
            ),
        }
    except Exception as exc:
        shadow_state = _db_recovery_shadow_idle_payload(
            candidate_path=str(candidate_path),
            status="error",
            block_reason=str(exc).strip() or exc.__class__.__name__,
        )
        return {
            **shadow_state,
            **_db_recovery_candidate_classification_state(
                recovery_state=recovery,
                shadow_state=shadow_state,
            ),
        }


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if value > 0:
            return "inf"
        if value < 0:
            return "-inf"
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    return value


def _latest_replay_search_run_id() -> int:
    conn = None
    try:
        conn = get_conn()
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM replay_search_runs").fetchone()
        return int(row["id"] or 0)
    except sqlite3.DatabaseError as exc:
        logger.warning("Replay search run id unavailable: %s", exc)
        return 0
    finally:
        if conn is not None:
            conn.close()


def _latest_retrain_run_id() -> int:
    conn = None
    try:
        conn = get_conn()
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM retrain_runs").fetchone()
        return int(row["id"] or 0)
    except sqlite3.DatabaseError as exc:
        logger.warning("Retrain run id unavailable: %s", exc)
        return 0
    finally:
        if conn is not None:
            conn.close()


def _latest_retrain_run() -> dict[str, Any] | None:
    conn = None
    try:
        conn = get_conn()
        row = conn.execute(
            """
            SELECT *
            FROM retrain_runs
            ORDER BY
                CASE
                    WHEN finished_at > 0 THEN finished_at
                    ELSE started_at
                END DESC,
                id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.DatabaseError as exc:
        logger.warning("Retrain history unavailable: %s", exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def _latest_replay_search_run() -> dict[str, Any] | None:
    conn = None
    try:
        conn = get_conn()
        row = conn.execute(
            """
            SELECT *
            FROM replay_search_runs
            ORDER BY
                CASE
                    WHEN finished_at > 0 THEN finished_at
                    ELSE started_at
                END DESC,
                id DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.DatabaseError as exc:
        logger.warning("Replay search history unavailable: %s", exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def _load_retrain_run_after(after_id: int) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM retrain_runs
            WHERE id > ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(after_id),),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _load_replay_search_run_after(after_id: int, *, request_token: str = "") -> dict[str, Any] | None:
    conn = get_conn()
    try:
        if request_token:
            row = conn.execute(
                """
                SELECT *
                FROM replay_search_runs
                WHERE id > ?
                  AND request_token=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(after_id), str(request_token)),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT *
                FROM replay_search_runs
                WHERE id > ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(after_id),),
            ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _insert_retrain_run(
    *,
    started_at: int,
    finished_at: int,
    trigger: str,
    status: str,
    ok: bool,
    deployed: bool,
    sample_count: int = 0,
    min_samples: int = 0,
    message: str = "",
) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO retrain_runs (
                started_at, finished_at, trigger, status, ok, deployed, sample_count, min_samples, message
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                int(started_at),
                int(finished_at),
                str(trigger or ""),
                str(status or ""),
                1 if ok else 0,
                1 if deployed else 0,
                int(sample_count or 0),
                int(min_samples or 0),
                str(message or ""),
            ),
        )
        run_id = int(cursor.lastrowid or 0)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM retrain_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    except Exception:
        logger.exception("Failed to insert fallback retrain run")
        return None
    finally:
        conn.close()


def _persist_replay_search_run_runtime_context(
    run_id: int,
    *,
    trigger: str,
    message: str,
    status: str | None = None,
) -> None:
    if int(run_id or 0) <= 0:
        return
    assignments = ["trigger=?", "status_message=?"]
    params: list[object] = [str(trigger or ""), str(message or "")]
    if status is not None:
        assignments.append("status=?")
        params.append(str(status or ""))
    params.append(int(run_id))
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE replay_search_runs SET {', '.join(assignments)} WHERE id=?",
            tuple(params),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to persist replay-search runtime context for run %s", int(run_id))
    finally:
        conn.close()


def _insert_replay_search_failure_run(
    *,
    started_at: int,
    finished_at: int,
    request_token: str,
    trigger: str,
    label_prefix: str,
    notes: str,
    message: str,
) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO replay_search_runs (
                started_at, finished_at, request_token, trigger, label_prefix, status, status_message, notes
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                int(started_at),
                int(finished_at),
                str(request_token or ""),
                str(trigger or ""),
                str(label_prefix or ""),
                "failed",
                str(message or ""),
                str(notes or ""),
            ),
        )
        run_id = int(cursor.lastrowid or 0)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM replay_search_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        return dict(row) if row is not None else None
    except Exception:
        logger.exception("Failed to insert fallback replay-search failure row")
        return None
    finally:
        conn.close()


def _load_replay_search_candidate(
    replay_search_run_id: int,
    *,
    candidate_index: int | None = None,
    current_policy: bool = False,
    feasible_only: bool = False,
) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        if current_policy:
            row = conn.execute(
                """
                SELECT *
                FROM replay_search_candidates
                WHERE replay_search_run_id=?
                  AND is_current_policy=1
                ORDER BY id ASC
                LIMIT 1
                """,
                (int(replay_search_run_id),),
            ).fetchone()
        elif candidate_index is not None:
            row = conn.execute(
                """
                SELECT *
                FROM replay_search_candidates
                WHERE replay_search_run_id=?
                  AND candidate_index=?
                ORDER BY id ASC
                LIMIT 1
                """,
                (int(replay_search_run_id), int(candidate_index)),
            ).fetchone()
        elif feasible_only:
            row = conn.execute(
                """
                SELECT *
                FROM replay_search_candidates
                WHERE replay_search_run_id=?
                  AND feasible=1
                ORDER BY score DESC, candidate_index ASC, id ASC
                LIMIT 1
                """,
                (int(replay_search_run_id),),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT *
                FROM replay_search_candidates
                WHERE replay_search_run_id=?
                ORDER BY score DESC, candidate_index ASC, id ASC
                LIMIT 1
                """,
                (int(replay_search_run_id),),
            ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _insert_replay_promotion(payload: dict[str, Any]) -> int:
    record = {
        "requested_at": int(payload.get("requested_at") or 0),
        "finished_at": int(payload.get("finished_at") or 0),
        "applied_at": int(payload.get("applied_at") or 0),
        "trigger": str(payload.get("trigger") or ""),
        "scope": str(payload.get("scope") or "shadow_only"),
        "source_mode": str(payload.get("source_mode") or ""),
        "status": str(payload.get("status") or ""),
        "reason": str(payload.get("reason") or ""),
        "replay_search_run_id": payload.get("replay_search_run_id"),
        "replay_search_candidate_id": payload.get("replay_search_candidate_id"),
        "config_json": _compact_json(_json_object_or_empty(payload.get("config_json"))),
        "previous_config_json": _compact_json(_json_object_or_empty(payload.get("previous_config_json"))),
        "updated_keys_json": _compact_json(payload.get("updated_keys_json") or []),
        "candidate_result_json": _compact_json(_json_object_or_empty(payload.get("candidate_result_json"))),
        "score": payload.get("score"),
        "score_delta": payload.get("score_delta"),
        "total_pnl_usd": payload.get("total_pnl_usd"),
        "pnl_delta_usd": payload.get("pnl_delta_usd"),
        "shadow_resolved_count": int(payload.get("shadow_resolved_count") or 0),
        "shadow_resolved_since_previous": int(payload.get("shadow_resolved_since_previous") or 0),
    }
    conn = get_conn()
    try:
        cursor = conn.execute(
            """
            INSERT INTO replay_promotions (
                requested_at, finished_at, applied_at, trigger, scope, source_mode, status, reason,
                replay_search_run_id, replay_search_candidate_id, config_json, previous_config_json, updated_keys_json,
                candidate_result_json, score, score_delta, total_pnl_usd, pnl_delta_usd,
                shadow_resolved_count, shadow_resolved_since_previous
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record["requested_at"],
                record["finished_at"],
                record["applied_at"],
                record["trigger"],
                record["scope"],
                record["source_mode"],
                record["status"],
                record["reason"],
                record["replay_search_run_id"],
                record["replay_search_candidate_id"],
                record["config_json"],
                record["previous_config_json"],
                record["updated_keys_json"],
                record["candidate_result_json"],
                record["score"],
                record["score_delta"],
                record["total_pnl_usd"],
                record["pnl_delta_usd"],
                record["shadow_resolved_count"],
                record["shadow_resolved_since_previous"],
            ),
        )
        conn.commit()
        return int(cursor.lastrowid or 0)
    finally:
        conn.close()


def _filtered_replay_promotion_config_payload(config_payload: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    filtered: dict[str, Any] = {}
    ignored_keys: list[str] = []
    for raw_key, raw_value in sorted(_json_object_or_empty(config_payload).items()):
        key = str(raw_key or "").strip().upper()
        if not key:
            continue
        if key not in PROMOTABLE_REPLAY_CONFIG_KEYS:
            ignored_keys.append(key)
            continue
        filtered[key] = raw_value
    return filtered, ignored_keys


def _apply_env_config_payload(config_payload: dict[str, Any]) -> dict[str, Any]:
    filtered_payload, ignored_keys = _filtered_replay_promotion_config_payload(config_payload)
    prepared = [(key, _env_value_text(raw_value)) for key, raw_value in sorted(filtered_payload.items())]
    if not prepared:
        raise ValueError("Promotion config payload did not contain any promotable config keys")
    snapshot = {
        "runtime_settings": {key: get_runtime_setting(key) for key, _value in prepared},
        "env_values": {key: os.environ.get(key) for key, _value in prepared},
    }
    for key, value in prepared:
        set_runtime_setting(key, value)
        os.environ[key] = value
    return {
        "applied_keys": [key for key, _value in prepared],
        "ignored_keys": ignored_keys,
        "config": filtered_payload,
        "snapshot": snapshot,
    }


def _restore_env_config_payload(snapshot: dict[str, Any]) -> None:
    runtime_settings = snapshot.get("runtime_settings") or {}
    if not isinstance(runtime_settings, dict):
        raise ValueError("Invalid runtime settings snapshot")
    for raw_key, previous_value in runtime_settings.items():
        key = str(raw_key or "").strip().upper()
        if not key:
            continue
        if previous_value is None:
            delete_runtime_setting(key)
        else:
            set_runtime_setting(key, str(previous_value))

    env_values = snapshot.get("env_values") or {}
    if isinstance(env_values, dict):
        for raw_key, previous_value in env_values.items():
            key = str(raw_key or "").strip().upper()
            if not key:
                continue
            if previous_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(previous_value)


def _normalize_replay_search_flag_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("Replay-search constraint key cannot be blank")
    if raw.startswith("--"):
        return raw
    return "--" + raw.lstrip("-").replace("_", "-")


def _replay_search_flag_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (dict, list, tuple)):
        return _compact_json(value)
    return str(value)


def _build_replay_search_command(*, request_token: str = "", trigger: str = "") -> list[str]:
    command = [
        sys.executable,
        "-m",
        "kelly_watcher.research.replay_search",
        "--db",
        str(DB_PATH),
        "--label-prefix",
        replay_search_label_prefix(),
        "--top",
        str(replay_search_top()),
        "--max-combos",
        str(replay_search_max_combos()),
        "--window-days",
        str(replay_search_window_days()),
        "--window-count",
        str(replay_search_window_count()),
    ]
    if request_token:
        command.extend(["--request-token", request_token])
    if trigger:
        command.extend(["--trigger", trigger])
    notes = replay_search_notes()
    if notes:
        command.extend(["--notes", notes])
    base_policy_file = replay_search_base_policy_file()
    if base_policy_file:
        command.extend(["--base-policy-file", base_policy_file])
    base_policy = replay_search_base_policy()
    if base_policy:
        command.extend(["--base-policy-json", _compact_json(base_policy)])
    grid_file = replay_search_grid_file()
    if grid_file:
        command.extend(["--grid-file", grid_file])
    grid = replay_search_grid()
    if grid:
        command.extend(["--grid-json", _compact_json(grid)])
    constraints_file = replay_search_constraints_file()
    if constraints_file:
        command.extend(["--constraints-file", constraints_file])
    constraints = replay_search_constraints()
    if constraints:
        command.extend(["--constraints-json", _compact_json(constraints)])
    score_weights_file = replay_search_score_weights_file()
    if score_weights_file:
        command.extend(["--score-weights-file", score_weights_file])
    score_weights = replay_search_score_weights()
    if score_weights:
        command.extend(["--score-weights-json", _compact_json(score_weights)])
    return command


def _weighted_open_entry_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    total_weight = 0.0
    confidence_total = 0.0
    edge_total = 0.0
    edge_weight = 0.0
    market_score_total = 0.0
    market_score_weight = 0.0
    entry_price_total = 0.0
    signal_mode_weights: dict[str, float] = {}

    for entry in entries:
        weight = float(entry.get("remaining_entry_size_usd") or entry.get("actual_entry_size_usd") or entry.get("signal_size_usd") or 0.0)
        if weight <= 1e-9:
            weight = 1.0
        total_weight += weight

        confidence = entry.get("confidence")
        if confidence is not None:
            confidence_total += float(confidence) * weight

        entry_price = entry.get("actual_entry_price") or entry.get("price_at_signal")
        if entry_price is not None:
            entry_price_total += float(entry_price) * weight

        signal_mode = str(entry.get("signal_mode") or "").strip().lower()
        if signal_mode:
            signal_mode_weights[signal_mode] = signal_mode_weights.get(signal_mode, 0.0) + weight

        market_score = entry.get("market_score")
        if market_score is not None:
            market_score_total += float(market_score) * weight
            market_score_weight += weight

        edge = None
        raw_context = entry.get("decision_context_json")
        if raw_context:
            try:
                context = json.loads(str(raw_context))
            except Exception:
                context = None
            if isinstance(context, dict):
                edge = context.get("edge")
                if edge is None:
                    signal = context.get("signal")
                    if isinstance(signal, dict):
                        edge = signal.get("edge")
                        if market_score is None:
                            nested_market = signal.get("market")
                            if isinstance(nested_market, dict) and nested_market.get("score") is not None:
                                market_score_total += float(nested_market["score"]) * weight
                                market_score_weight += weight
        if edge is None and confidence is not None and entry_price is not None:
            edge = float(confidence) - float(entry_price)
        if edge is not None:
            edge_total += float(edge) * weight
            edge_weight += weight

    dominant_signal_mode = ""
    if signal_mode_weights:
        dominant_signal_mode = max(signal_mode_weights.items(), key=lambda item: item[1])[0]

    return {
        "avg_confidence": (confidence_total / total_weight) if total_weight > 0 else None,
        "avg_edge": (edge_total / edge_weight) if edge_weight > 0 else None,
        "avg_market_score": (market_score_total / market_score_weight) if market_score_weight > 0 else None,
        "avg_entry_price": (entry_price_total / total_weight) if total_weight > 0 else None,
        "signal_mode": dominant_signal_mode or None,
    }


def _persist_exit_audit(
    *,
    candidate: StopLossCandidate,
    real_money: bool,
    decision: ExitGuardDecision,
    estimated_return: float,
    max_loss_pct: float,
    hard_exit_loss_pct: float,
    open_size_usd: float,
    open_shares: float,
    quoted_price: float,
    snapshot: dict[str, Any] | None,
) -> None:
    metadata = dict(decision.metadata or {})
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO exit_audits (
                audited_at, market_id, token_id, side, real_money, trader_address, question,
                strategy, decision, reason, estimated_return_pct, loss_limit_pct,
                hard_exit_loss_pct, open_size_usd, open_shares, quoted_price,
                best_bid, best_ask, bid_depth_usd, ask_depth_usd, market_score, market_veto,
                time_to_close_seconds, avg_entry_price, avg_entry_confidence, avg_entry_edge,
                avg_entry_market_score, signal_mode, metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(time.time()),
                candidate.market_id,
                candidate.token_id,
                candidate.side,
                1 if real_money else 0,
                candidate.trader_address,
                candidate.question,
                "stop_loss_v2",
                decision.action,
                decision.reason,
                estimated_return,
                max_loss_pct,
                hard_exit_loss_pct,
                open_size_usd,
                open_shares,
                quoted_price,
                float((snapshot or {}).get("best_bid") or 0.0) or None,
                float((snapshot or {}).get("best_ask") or 0.0) or None,
                float((snapshot or {}).get("bid_depth_usd") or 0.0) or None,
                float((snapshot or {}).get("ask_depth_usd") or 0.0) or None,
                metadata.get("market_score"),
                metadata.get("market_veto"),
                metadata.get("time_to_close_seconds"),
                metadata.get("avg_entry_price"),
                metadata.get("avg_entry_confidence"),
                metadata.get("avg_entry_edge"),
                metadata.get("avg_entry_market_score"),
                metadata.get("signal_mode"),
                json.dumps(metadata, separators=(",", ":"), default=str),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _evaluate_exit_guard(
    *,
    candidate: StopLossCandidate,
    entries: list[dict[str, Any]],
    snapshot: dict[str, Any] | None,
    market_meta: dict[str, Any] | None,
    quoted_price: float,
    estimated_return: float,
    max_loss_pct: float,
    open_size_usd: float,
    open_shares: float,
) -> ExitGuardDecision:
    entry_summary = _weighted_open_entry_summary(entries)
    hard_exit_loss_pct = max_loss_pct + 0.10
    if estimated_return <= -hard_exit_loss_pct:
        metadata = {
            **entry_summary,
            "hard_exit_loss_pct": hard_exit_loss_pct,
            "trigger_loss_pct": max_loss_pct,
            "market_score": None,
            "market_veto": None,
            "time_to_close_seconds": None,
            "spread_pct": None,
            "depth_multiple": None,
        }
        return ExitGuardDecision(
            action="exit",
            reason=(
                "hard exit because the executable quote fell far beyond the normal stop limit "
                f"({estimated_return * 100:.1f}% vs limit -{hard_exit_loss_pct * 100:.1f}%)"
            ),
            metadata=metadata,
        )

    close_time = str(
        (market_meta or {}).get("endDate")
        or (market_meta or {}).get("closedTime")
        or (market_meta or {}).get("closeTime")
        or ""
    ).strip()
    market_features = build_market_features(
        snapshot or {},
        close_time,
        open_size_usd,
        execution_price=quoted_price if quoted_price > 0 else None,
    )
    market_result = MarketScorer().score(market_features) if market_features is not None else {"score": None, "veto": "missing_market_features"}
    market_score = float(market_result["score"]) if market_result.get("score") is not None else None
    market_veto = str(market_result.get("veto") or "").strip() or None
    if market_veto and (
        market_veto.startswith("beyond max horizon")
        or market_veto.startswith("expires in <")
    ):
        market_veto = None
        market_score = None
    best_bid = float((snapshot or {}).get("best_bid") or 0.0)
    best_ask = float((snapshot or {}).get("best_ask") or 0.0)
    mid = float((snapshot or {}).get("mid") or 0.0)
    if mid <= 0 and best_bid > 0 and best_ask > 0:
        mid = (best_bid + best_ask) / 2.0
    spread_pct = ((best_ask - best_bid) / mid) if mid > 0 and best_ask >= best_bid > 0 else None
    bid_depth_usd = float((snapshot or {}).get("bid_depth_usd") or 0.0)
    depth_reference_usd = quoted_price * open_shares if quoted_price > 0 and open_shares > 0 else open_size_usd
    depth_multiple = (
        (bid_depth_usd / depth_reference_usd)
        if depth_reference_usd > 0 and bid_depth_usd > 0
        else None
    )
    time_to_close_seconds = (market_features.days_to_res * 86400.0) if market_features is not None else None
    min_depth_multiple = 0.85

    signal_mode = str(entry_summary.get("signal_mode") or "").strip().lower()
    required_market_score = None
    avg_entry_price = entry_summary.get("avg_entry_price")
    if signal_mode == "heuristic" and isinstance(avg_entry_price, (float, int)) and 0.0 < float(avg_entry_price) < 1.0:
        required_market_score, _ = SignalEngine._heuristic_min_market_score(
            float(avg_entry_price),
            heuristic_min_entry_price(),
            heuristic_max_entry_price(),
        )
    elif signal_mode:
        required_market_score = 0.45

    patience_buffer = 0.0
    avg_entry_edge = entry_summary.get("avg_entry_edge")
    if isinstance(avg_entry_edge, (float, int)):
        if float(avg_entry_edge) >= 0.05:
            patience_buffer += 0.04
        elif float(avg_entry_edge) >= 0.02:
            patience_buffer += 0.02
    avg_entry_confidence = entry_summary.get("avg_entry_confidence")
    if isinstance(avg_entry_confidence, (float, int)):
        if float(avg_entry_confidence) >= 0.68:
            patience_buffer += 0.03
        elif float(avg_entry_confidence) >= 0.60:
            patience_buffer += 0.015
    avg_entry_market_score = entry_summary.get("avg_entry_market_score")
    if isinstance(avg_entry_market_score, (float, int)) and float(avg_entry_market_score) >= 0.70:
        patience_buffer += 0.01
    if signal_mode == "xgboost":
        patience_buffer += 0.01
    if isinstance(time_to_close_seconds, (float, int)):
        if time_to_close_seconds >= 86400:
            patience_buffer += 0.03
        elif time_to_close_seconds >= 21600:
            patience_buffer += 0.015

    trigger_loss_pct = max_loss_pct + patience_buffer
    metadata = {
        **entry_summary,
        "market_score": round(market_score, 6) if market_score is not None else None,
        "market_veto": market_veto,
        "required_market_score": round(required_market_score, 6) if required_market_score is not None else None,
        "time_to_close_seconds": round(time_to_close_seconds, 3) if time_to_close_seconds is not None else None,
        "spread_pct": round(spread_pct, 6) if spread_pct is not None else None,
        "depth_multiple": round(depth_multiple, 6) if depth_multiple is not None else None,
        "min_depth_multiple": round(min_depth_multiple, 6),
        "trigger_loss_pct": round(trigger_loss_pct, 6),
        "hard_exit_loss_pct": round(hard_exit_loss_pct, 6),
    }

    if market_veto:
        return ExitGuardDecision(
            action="hold",
            reason=f"holding because the current market state is not actionable ({market_veto})",
            metadata=metadata,
        )
    if spread_pct is not None and spread_pct > 0.05:
        return ExitGuardDecision(
            action="hold",
            reason=f"holding because the current spread is too wide ({spread_pct * 100:.1f}%) to trust the quote",
            metadata=metadata,
        )
    if depth_multiple is not None and depth_multiple < min_depth_multiple:
        return ExitGuardDecision(
            action="hold",
            reason=f"holding because visible bid depth only covers {depth_multiple:.2f}x of position size",
            metadata=metadata,
        )
    if required_market_score is not None and market_score is not None and market_score < required_market_score:
        return ExitGuardDecision(
            action="hold",
            reason=(
                "holding because current market quality is below the minimum score "
                f"({market_score:.2f} < {required_market_score:.2f})"
            ),
            metadata=metadata,
        )
    if estimated_return > -trigger_loss_pct:
        return ExitGuardDecision(
            action="hold",
            reason=(
                "holding because the loss breach is not severe enough after accounting for entry quality "
                f"({estimated_return * 100:.1f}% vs trigger -{trigger_loss_pct * 100:.1f}%)"
            ),
            metadata=metadata,
        )
    return ExitGuardDecision(
        action="exit",
        reason=(
            "exit confirmed because the executable loss breached the adjusted trigger "
            f"({estimated_return * 100:.1f}% vs trigger -{trigger_loss_pct * 100:.1f}%)"
        ),
        metadata=metadata,
    )


def _load_stop_loss_candidates(*, real_money: bool) -> list[StopLossCandidate]:
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT
                p.market_id,
                p.token_id,
                p.side,
                p.size_usd,
                p.entered_at,
                COALESCE(t.question, p.market_id) AS question,
                COALESCE(t.trader_address, '') AS trader_address,
                COALESCE(t.trader_name, '') AS trader_name
            FROM positions p
            LEFT JOIN trade_log t
              ON t.id = (
                    SELECT t2.id
                    FROM trade_log t2
                    WHERE t2.market_id = p.market_id
                      AND t2.real_money = p.real_money
                      AND LOWER(COALESCE(t2.side, '')) = LOWER(COALESCE(p.side, ''))
                      AND COALESCE(t2.token_id, '') = COALESCE(p.token_id, '')
                      AND {OPEN_EXECUTED_ENTRY_SQL}
                    ORDER BY t2.placed_at DESC, t2.id DESC
                    LIMIT 1
              )
            WHERE p.real_money=?
            ORDER BY p.entered_at ASC, p.market_id ASC, p.token_id ASC
            """,
            (1 if real_money else 0,),
        ).fetchall()
        return [
            StopLossCandidate(
                market_id=str(row["market_id"] or "").strip(),
                token_id=str(row["token_id"] or "").strip(),
                side=str(row["side"] or "").strip().lower(),
                question=str(row["question"] or row["market_id"] or "").strip(),
                trader_address=str(row["trader_address"] or "").strip().lower(),
                trader_name=str(row["trader_name"] or "").strip(),
                entered_at=int(row["entered_at"] or 0),
                size_usd=float(row["size_usd"] or 0.0),
            )
            for row in rows
            if str(row["market_id"] or "").strip() and str(row["token_id"] or "").strip()
        ]
    finally:
        conn.close()


def _build_stop_loss_reason(*, estimated_return: float, max_loss_pct: float) -> str:
    return (
        "stop-loss triggered because the current executable exit quote fell to "
        f"{estimated_return * 100:.1f}% return (limit -{max_loss_pct * 100:.1f}%)"
    )


def _normalize_exit_snapshot(
    snapshot: dict[str, Any] | None,
    raw_book: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = dict(snapshot or {})
    bids = raw_book.get("bids", []) if isinstance(raw_book, dict) else []
    asks = raw_book.get("asks", []) if isinstance(raw_book, dict) else []

    if normalized.get("best_bid") in {None, ""} and bids:
        try:
            normalized["best_bid"] = float(bids[0].get("price") or 0.0)
        except (TypeError, ValueError, AttributeError):
            pass
    if normalized.get("best_ask") in {None, ""} and asks:
        try:
            normalized["best_ask"] = float(asks[0].get("price") or 0.0)
        except (TypeError, ValueError, AttributeError):
            pass
    if normalized.get("mid") in {None, "", 0, 0.0}:
        try:
            best_bid = float(normalized.get("best_bid") or 0.0)
            best_ask = float(normalized.get("best_ask") or 0.0)
        except (TypeError, ValueError):
            best_bid = 0.0
            best_ask = 0.0
        if best_bid > 0 and best_ask > 0:
            normalized["mid"] = (best_bid + best_ask) / 2.0

    if normalized.get("bid_depth_usd") in {None, "", 0, 0.0} and bids:
        normalized["bid_depth_usd"] = sum(
            float(level.get("size") or 0.0) * float(level.get("price") or 0.0)
            for level in bids[:5]
            if isinstance(level, dict)
        )
    if normalized.get("ask_depth_usd") in {None, "", 0, 0.0} and asks:
        normalized["ask_depth_usd"] = sum(
            float(level.get("size") or 0.0) * float(level.get("price") or 0.0)
            for level in asks[:5]
            if isinstance(level, dict)
        )

    return normalized


def _run_stop_loss_checks(
    tracker: PolymarketTracker,
    executor: PolymarketExecutor,
    dedup: DedupeCache,
) -> None:
    if not stop_loss_enabled():
        return

    real_money = use_real_money()
    if real_money and not dedup.sync_positions_from_api(tracker, wallet_address()):
        logger.warning("Stop-loss check skipped because live positions could not be refreshed")
        return

    now_ts = int(time.time())
    max_loss_pct = stop_loss_max_loss_pct()
    hard_exit_loss_pct = max_loss_pct + 0.10
    min_hold_seconds = stop_loss_min_hold_seconds()
    candidates = _load_stop_loss_candidates(real_money=real_money)
    for candidate in candidates:
        if candidate.entered_at > 0 and (now_ts - candidate.entered_at) < min_hold_seconds:
            continue

        position_state = executor._load_open_position_state(
            candidate.market_id,
            candidate.side,
            candidate.token_id,
            real_money=real_money,
        )
        if position_state is None:
            continue

        position, entries = position_state
        open_shares = sum(executor._entry_open_shares(entry) for entry in entries)
        open_size_usd = sum(executor._entry_open_size(entry) for entry in entries)
        if open_shares <= 1e-9:
            continue
        if open_size_usd <= 1e-9:
            open_size_usd = float(position.get("size_usd") or candidate.size_usd or 0.0)
        if open_size_usd <= 1e-9:
            continue

        market_meta, metadata_fetched_at = tracker.get_market_metadata(candidate.market_id)
        snapshot, raw_book, orderbook_fetched_at = tracker.get_orderbook_snapshot(candidate.token_id)
        if raw_book is None or snapshot is None:
            logger.debug(
                "Stop-loss quote unavailable for %s/%s: missing order book",
                candidate.market_id[:12],
                candidate.side.upper(),
            )
            continue

        fill, fill_reason = executor.estimate_exit_fill(raw_book, open_shares)
        if fill is None:
            logger.debug(
                "Stop-loss quote unavailable for %s/%s: %s",
                candidate.market_id[:12],
                candidate.side.upper(),
                fill_reason or "sell simulation failed",
            )
            continue

        exit_economics, economics_reason = executor.estimate_exit_economics(
            token_id=candidate.token_id,
            fill=fill,
            market_meta=market_meta,
        )
        if exit_economics is None:
            logger.debug(
                "Stop-loss quote rejected for %s/%s: %s",
                candidate.market_id[:12],
                candidate.side.upper(),
                economics_reason or "exit economics unavailable",
            )
            continue

        snapshot = _normalize_exit_snapshot(snapshot, raw_book)
        estimated_return = (float(exit_economics.net_proceeds_usd) / open_size_usd) - 1.0
        if estimated_return > (-max_loss_pct + 1e-9):
            continue

        question = str(market_meta.get("question") or market_meta.get("title") or candidate.question or candidate.market_id)
        close_time = str(
            market_meta.get("endDate")
            or market_meta.get("closedTime")
            or market_meta.get("closeTime")
            or ""
        ).strip()
        quoted_price = float(snapshot.get("best_bid") or snapshot.get("mid") or fill.avg_price or 0.0)
        if quoted_price <= 0:
            quoted_price = float(exit_economics.effective_exit_price or 0.0)
        if quoted_price <= 0:
            continue

        decision = _evaluate_exit_guard(
            candidate=candidate,
            entries=entries,
            snapshot=snapshot,
            market_meta=market_meta,
            quoted_price=quoted_price,
            estimated_return=estimated_return,
            max_loss_pct=max_loss_pct,
            open_size_usd=open_size_usd,
            open_shares=open_shares,
        )
        _persist_exit_audit(
            candidate=candidate,
            real_money=real_money,
            decision=decision,
            estimated_return=estimated_return,
            max_loss_pct=max_loss_pct,
            hard_exit_loss_pct=hard_exit_loss_pct,
            open_size_usd=open_size_usd,
            open_shares=open_shares,
            quoted_price=quoted_price,
            snapshot=snapshot,
        )
        if decision.action != "exit":
            logger.info(
                "Exit guard held %s/%s: %s",
                candidate.market_id[:12],
                candidate.side.upper(),
                decision.reason,
            )
            continue

        reason = decision.reason
        trade_id = f"stop-loss-{uuid.uuid4().hex}"
        event = TradeEvent(
            trade_id=trade_id,
            market_id=candidate.market_id,
            question=question,
            side=candidate.side,
            action="sell",
            price=quoted_price,
            shares=0.0,
            size_usd=round(open_size_usd, 6),
            token_id=candidate.token_id,
            trader_name=candidate.trader_name,
            trader_address=candidate.trader_address or "risk-engine",
            timestamp=now_ts,
            close_time=close_time,
            snapshot=dict(snapshot or {}),
            raw_trade={
                "source": "risk-engine",
                "risk_action": "stop_loss",
                "estimated_return": estimated_return,
                "max_loss_pct": max_loss_pct,
                "exit_guard": decision.metadata,
                "entered_at": candidate.entered_at,
            },
            raw_market_metadata=market_meta or {},
            raw_orderbook=raw_book,
            source_ts_raw=str(now_ts),
            observed_at=now_ts,
            poll_started_at=now_ts,
            metadata_fetched_at=metadata_fetched_at,
            orderbook_fetched_at=orderbook_fetched_at,
            market_close_ts=PolymarketTracker._normalize_timestamp(close_time) if close_time else 0,
        )
        result = executor.execute_exit(
            trade_id=trade_id,
            market_id=candidate.market_id,
            token_id=candidate.token_id,
            side=candidate.side,
            event=event,
            dedup=dedup,
            reason_override=reason,
        )
        if not result.placed:
            reason_text = str(result.reason or "").strip()
            if (
                reason_text
                and "order in-flight" not in reason_text.lower()
                and not _is_non_actionable_exit_reason(reason_text)
            ):
                logger.warning(
                    "Stop-loss exit failed for %s/%s: %s",
                    candidate.market_id[:12],
                    candidate.side.upper(),
                    reason_text,
                )
            continue

        execution_price = (result.dollar_size / result.shares) if result.shares > 0 else quoted_price
        _emit_event(
            {
                "type": "signal",
                "trade_id": trade_id,
                "market_id": candidate.market_id,
                "question": question,
                **_event_market_payload(event),
                "side": candidate.side,
                "action": "sell",
                "price": round(execution_price, 6),
                "shares": round(result.shares, 6),
                "amount_usd": result.dollar_size,
                "size_usd": result.dollar_size,
                "username": candidate.trader_name,
                "trader": candidate.trader_address,
                "decision": "STOP LOSS",
                "confidence": 0.0,
                "shadow": result.shadow,
                "order_id": result.order_id,
                "reason": reason,
                "estimated_return": round(estimated_return, 6),
                "stop_loss_limit_pct": max_loss_pct,
                "hard_exit_loss_pct": round(hard_exit_loss_pct, 6),
                "exit_guard": decision.metadata,
                "ts": int(time.time()),
            }
        )


def _validate_startup() -> None:
    errors: list[str] = []
    warnings: list[str] = []

    def _capture_config(getter):
        try:
            return getter()
        except ConfigError as exc:
            errors.append(str(exc))
            return None

    runtime_wallets = _runtime_managed_wallets()
    if not runtime_wallets:
        errors.append("managed wallet registry is empty")

    confidence = _capture_config(min_confidence)
    if confidence is not None and not (0.0 < confidence < 1.0):
        errors.append(f"MIN_CONFIDENCE must be between 0 and 1, got {confidence}")

    max_fraction = _capture_config(max_bet_fraction)
    if max_fraction is not None and not (0.0 < max_fraction <= 1.0):
        errors.append(f"MAX_BET_FRACTION must be between 0 and 1, got {max_fraction}")

    minimum_bet = _capture_config(min_bet_usd)
    if minimum_bet is not None and minimum_bet <= 0:
        errors.append(f"MIN_BET_USD must be positive, got {minimum_bet}")

    min_entry_price = _capture_config(heuristic_min_entry_price)
    if min_entry_price is not None and not (0.0 <= min_entry_price < 1.0):
        errors.append(f"HEURISTIC_MIN_ENTRY_PRICE must be between 0 and 1, got {min_entry_price}")
    max_entry_price = _capture_config(heuristic_max_entry_price)
    if max_entry_price is not None and not (0.0 < max_entry_price <= 1.0):
        errors.append(f"HEURISTIC_MAX_ENTRY_PRICE must be between 0 and 1, got {max_entry_price}")
    if (
        min_entry_price is not None
        and max_entry_price is not None
        and min_entry_price >= max_entry_price
    ):
        errors.append("HEURISTIC_MIN_ENTRY_PRICE must be smaller than HEURISTIC_MAX_ENTRY_PRICE")
    _capture_config(heuristic_allowed_entry_price_bands)
    _capture_config(heuristic_min_time_to_close_seconds)
    _capture_config(xgboost_allowed_entry_price_bands)
    _capture_config(model_min_time_to_close_seconds)
    mid_edge_confidence = _capture_config(model_edge_mid_confidence)
    high_edge_confidence = _capture_config(model_edge_high_confidence)
    mid_edge_threshold = _capture_config(model_edge_mid_threshold)
    high_edge_threshold = _capture_config(model_edge_high_threshold)
    duplicate_override_min_skips = _capture_config(duplicate_side_override_min_skips)
    duplicate_override_min_avg_return_value = _capture_config(duplicate_side_override_min_avg_return)
    exposure_override_min_skips_value = _capture_config(exposure_override_min_skips)
    exposure_override_min_avg_return_value = _capture_config(exposure_override_min_avg_return)
    exposure_override_cap_fraction = _capture_config(exposure_override_total_cap_fraction)

    _capture_config(hot_wallet_count)
    _capture_config(warm_wallet_count)
    _capture_config(warm_poll_interval_multiplier)
    _capture_config(discovery_poll_interval_multiplier)
    _capture_config(wallet_discovery_enabled)
    _capture_config(wallet_discovery_scan_interval_seconds)
    _capture_config(wallet_discovery_leaderboard_pages)
    _capture_config(wallet_discovery_leaderboard_per_page)
    _capture_config(wallet_discovery_analyze_limit)
    _capture_config(wallet_discovery_candidate_limit)
    _capture_config(wallet_inactivity_limit_seconds)
    _capture_config(wallet_slow_drop_max_tracking_age_seconds)
    _capture_config(wallet_performance_drop_min_trades)
    _capture_config(wallet_performance_drop_max_win_rate)
    _capture_config(wallet_performance_drop_max_avg_return)
    cold_start_min_observed = _capture_config(wallet_cold_start_min_observed_buys)
    discovery_min_observed = _capture_config(wallet_discovery_min_observed_buys)
    discovery_min_resolved = _capture_config(wallet_discovery_min_resolved_buys)
    discovery_multiplier = _capture_config(wallet_discovery_size_multiplier)
    quality_min_multiplier = _capture_config(wallet_quality_size_min_multiplier)
    quality_max_multiplier = _capture_config(wallet_quality_size_max_multiplier)
    probation_multiplier = _capture_config(wallet_probation_size_multiplier)
    trusted_min_resolved = _capture_config(wallet_trusted_min_resolved_copied_buys)
    min_window_seconds = _capture_config(min_execution_window_seconds)
    max_horizon_seconds = _capture_config(max_market_horizon_seconds)
    _capture_config(max_source_trade_age_seconds)
    total_open_exposure_limit = _capture_config(max_total_open_exposure_fraction)
    retrain_hour_value = _capture_config(retrain_hour_local)
    _capture_config(retrain_min_samples)
    replay_search_cadence = _capture_config(replay_search_base_cadence)
    replay_search_hour = _capture_config(replay_search_hour_local)
    replay_search_window_days_value = _capture_config(replay_search_window_days)
    replay_search_window_count_value = _capture_config(replay_search_window_count)
    _capture_config(replay_search_label_prefix)
    _capture_config(replay_search_notes)
    _capture_config(replay_search_top)
    _capture_config(replay_search_max_combos)
    _capture_config(replay_search_base_policy)
    replay_search_grid_value = _capture_config(replay_search_grid)
    _capture_config(replay_search_constraints)
    replay_search_score_weights_value = _capture_config(replay_search_score_weights)
    auto_promote_enabled = _capture_config(replay_auto_promote)
    auto_promote_min_score = _capture_config(replay_auto_promote_min_score_delta)
    auto_promote_min_pnl = _capture_config(replay_auto_promote_min_pnl_delta_usd)
    live_min_shadow_since_promotion = _capture_config(live_min_shadow_resolved_since_promotion)

    if (
        cold_start_min_observed is not None
        and discovery_min_observed is not None
        and cold_start_min_observed > discovery_min_observed
    ):
        errors.append(
            "WALLET_COLD_START_MIN_OBSERVED_BUYS must be <= WALLET_DISCOVERY_MIN_OBSERVED_BUYS"
        )
    if (
        discovery_multiplier is not None
        and probation_multiplier is not None
        and discovery_multiplier > probation_multiplier
    ):
        warnings.append(
            "WALLET_DISCOVERY_SIZE_MULTIPLIER is greater than WALLET_PROBATION_SIZE_MULTIPLIER; discovery trades will size larger than probation trades"
        )
    if (
        quality_min_multiplier is not None
        and quality_max_multiplier is not None
        and quality_min_multiplier > quality_max_multiplier
    ):
        errors.append(
            "WALLET_QUALITY_SIZE_MIN_MULTIPLIER must be <= WALLET_QUALITY_SIZE_MAX_MULTIPLIER"
        )
    if (
        mid_edge_confidence is not None
        and high_edge_confidence is not None
        and mid_edge_confidence > high_edge_confidence
    ):
        errors.append("MODEL_EDGE_MID_CONFIDENCE must be <= MODEL_EDGE_HIGH_CONFIDENCE")
    if (
        mid_edge_threshold is not None
        and high_edge_threshold is not None
        and mid_edge_threshold < high_edge_threshold
    ):
        errors.append("MODEL_EDGE_MID_THRESHOLD must be >= MODEL_EDGE_HIGH_THRESHOLD")
    if (
        min_window_seconds is not None
        and max_horizon_seconds is not None
        and max_horizon_seconds != float("inf")
        and min_window_seconds >= max_horizon_seconds
    ):
        errors.append("MIN_EXECUTION_WINDOW must be smaller than MAX_MARKET_HORIZON")
    if (
        discovery_min_resolved is not None
        and trusted_min_resolved is not None
        and discovery_min_resolved > trusted_min_resolved
    ):
        errors.append(
            "WALLET_DISCOVERY_MIN_RESOLVED_BUYS must be <= WALLET_TRUSTED_MIN_RESOLVED_COPIED_BUYS"
        )
    if exposure_override_cap_fraction is not None and not (0.0 <= exposure_override_cap_fraction <= 1.0):
        errors.append(
            "EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION must be between 0 and 1, "
            f"got {exposure_override_cap_fraction}"
        )
    if (
        exposure_override_cap_fraction is not None
        and total_open_exposure_limit is not None
        and total_open_exposure_limit > exposure_override_cap_fraction + 1e-9
    ):
        warnings.append(
            "EXPOSURE_OVERRIDE_TOTAL_CAP_FRACTION is below MAX_TOTAL_OPEN_EXPOSURE_FRACTION, "
            "so wallet-specific exposure overrides will have no effect"
        )
    if duplicate_override_min_skips is not None and duplicate_override_min_skips < 0:
        errors.append("DUPLICATE_SIDE_OVERRIDE_MIN_SKIPS must be >= 0")
    if exposure_override_min_skips_value is not None and exposure_override_min_skips_value < 0:
        errors.append("EXPOSURE_OVERRIDE_MIN_SKIPS must be >= 0")
    if (
        duplicate_override_min_avg_return_value is not None
        and not (-1.0 <= duplicate_override_min_avg_return_value <= 1.0)
    ):
        errors.append(
            "DUPLICATE_SIDE_OVERRIDE_MIN_AVG_RETURN must be between -1 and 1, "
            f"got {duplicate_override_min_avg_return_value}"
        )
    if (
        exposure_override_min_avg_return_value is not None
        and not (-1.0 <= exposure_override_min_avg_return_value <= 1.0)
    ):
        errors.append(
            "EXPOSURE_OVERRIDE_MIN_AVG_RETURN must be between -1 and 1, "
            f"got {exposure_override_min_avg_return_value}"
        )
    if (
        replay_search_window_days_value is not None
        and replay_search_window_days_value == 0
        and replay_search_window_count_value is not None
        and replay_search_window_count_value != 1
    ):
        warnings.append(
            "REPLAY_SEARCH_WINDOW_COUNT has no effect while REPLAY_SEARCH_WINDOW_DAYS is 0; replay search will use the full history"
        )
    if replay_search_cadence not in {None, "off"} and replay_search_grid_value == {}:
        warnings.append(
            "REPLAY_SEARCH_BASE_CADENCE is enabled but the replay-search grid config is empty, so scheduled replay search will only re-evaluate the current policy"
        )
    if replay_search_cadence not in {None, "off"} and replay_search_score_weights_value == {}:
        warnings.append(
            "REPLAY_SEARCH_BASE_CADENCE is enabled but replay-search score weights are empty, so scheduled ranking will rely mostly on drawdown defaults and hard constraints"
        )
    if replay_search_hour is not None and retrain_hour_value is not None and replay_search_hour == retrain_hour_value:
        warnings.append(
            "REPLAY_SEARCH_HOUR_LOCAL matches RETRAIN_HOUR_LOCAL; scheduled replay search and retrain may compete for the same hour"
        )
    if auto_promote_enabled and replay_search_cadence == "off":
        warnings.append("REPLAY_AUTO_PROMOTE is enabled but REPLAY_SEARCH_BASE_CADENCE is off")
    if auto_promote_min_score is not None and auto_promote_min_score < 0:
        warnings.append(
            f"REPLAY_AUTO_PROMOTE_MIN_SCORE_DELTA is {auto_promote_min_score:.4f}; negative thresholds will allow score regressions"
        )
    if auto_promote_min_pnl is not None and auto_promote_min_pnl < 0:
        warnings.append(
            f"REPLAY_AUTO_PROMOTE_MIN_PNL_DELTA_USD is {auto_promote_min_pnl:.2f}; negative thresholds will allow lower replay P&L promotions"
        )

    db_integrity = database_integrity_state()
    db_integrity_failed = bool(db_integrity.get("db_integrity_known")) and not bool(db_integrity.get("db_integrity_ok"))
    if db_integrity.get("db_integrity_known") and not db_integrity.get("db_integrity_ok"):
        db_error = str(db_integrity.get("db_integrity_message") or "").strip()
        db_summary = db_error.splitlines()[0].strip() if db_error else "unknown integrity failure"
        if use_real_money():
            errors.append(f"LIVE mode is blocked because SQLite integrity check failed: {db_summary}")
        else:
            queued_rebootstrap = _queued_shadow_rebootstrap_request_label()
            if queued_rebootstrap:
                warnings.append(
                    "SQLite integrity check failed: "
                    f"{db_summary} (allowing startup because a queued {queued_rebootstrap} request will restart on a clean base)"
                )
            else:
                errors.append(
                    "SHADOW mode is blocked because SQLite integrity check failed: "
                    f"{db_summary}. Queue Recover DB or Shadow Reset before collecting fresh evidence."
                )

    if use_real_money():
        our_wallet = wallet_address()
        if _looks_like_placeholder(private_key()):
            errors.append("POLYGON_PRIVATE_KEY is missing or still set to a placeholder")
        if _looks_like_placeholder(our_wallet):
            errors.append("POLYGON_WALLET_ADDRESS is missing or still set to a placeholder")
        if our_wallet and our_wallet in runtime_wallets:
            errors.append(
                "POLYGON_WALLET_ADDRESS is also in the managed wallet registry, which can create a self-copy loop"
            )
        if max_fraction is not None and max_fraction > 0.10:
            warnings.append(
                f"MAX_BET_FRACTION is {max_fraction:.2f}; consider keeping live single-trade risk at 10% or below"
            )
        current_interval = poll_interval()
        if current_interval < 0.25:
            warnings.append(
                f"POLL_INTERVAL_SECONDS is {current_interval:.2f}s; extremely fast live polling can amplify duplicate/latency risk"
            )
        live_drawdown_limit = _capture_config(max_live_drawdown_pct)
        if live_drawdown_limit is not None and not (0.0 <= live_drawdown_limit <= 1.0):
            errors.append(f"MAX_LIVE_DRAWDOWN_PCT must be between 0 and 1, got {live_drawdown_limit}")
        daily_loss_limit = _capture_config(max_daily_loss_pct)
        if daily_loss_limit is not None and not (0.0 <= daily_loss_limit <= 1.0):
            errors.append(f"MAX_DAILY_LOSS_PCT must be between 0 and 1, got {daily_loss_limit}")
        total_exposure_limit = _capture_config(max_total_open_exposure_fraction)
        if total_exposure_limit is not None and not (0.0 <= total_exposure_limit <= 1.0):
            errors.append(
                "MAX_TOTAL_OPEN_EXPOSURE_FRACTION must be between 0 and 1, "
                f"got {total_exposure_limit}"
            )
        market_exposure_limit = _capture_config(max_market_exposure_fraction)
        if market_exposure_limit is not None and not (0.0 <= market_exposure_limit <= 1.0):
            errors.append(
                "MAX_MARKET_EXPOSURE_FRACTION must be between 0 and 1, "
                f"got {market_exposure_limit}"
            )
        trader_exposure_limit = _capture_config(max_trader_exposure_fraction)
        if trader_exposure_limit is not None and not (0.0 <= trader_exposure_limit <= 1.0):
            errors.append(
                "MAX_TRADER_EXPOSURE_FRACTION must be between 0 and 1, "
                f"got {trader_exposure_limit}"
            )
        live_health_failure_limit = _capture_config(max_live_health_failures)
        if live_health_failure_limit is not None and live_health_failure_limit < 1:
            errors.append(
                "MAX_LIVE_HEALTH_FAILURES must be at least 1, "
                f"got {live_health_failure_limit}"
            )
        if live_require_shadow_history():
            resolved = _resolved_shadow_trade_count_in_current_evidence_window()
            minimum = _capture_config(live_min_shadow_resolved)
            if minimum is None:
                minimum = 0
            if resolved < minimum:
                errors.append(
                    "LIVE mode is blocked until shadow history is available in the current evidence window: "
                    f"{resolved} resolved shadow trades < required {minimum}"
                )
        if auto_promote_enabled:
            warnings.append("REPLAY_AUTO_PROMOTE is enabled, but scheduled auto-promotion only applies in shadow mode")
        if live_min_shadow_since_promotion is None:
            live_min_shadow_since_promotion = 0
        if live_min_shadow_since_promotion > 0:
            resolved_since_promotion, last_promotion = _resolved_shadow_trade_count_since_last_promotion()
            if resolved_since_promotion < live_min_shadow_since_promotion:
                baseline = _shadow_history_current_baseline_label(last_promotion=last_promotion)
                errors.append(
                    "LIVE mode is blocked until post-promotion shadow history is available in the current evidence window: "
                    f"{resolved_since_promotion} resolved shadow trades since {baseline} < required {live_min_shadow_since_promotion}"
                )
        if not db_integrity_failed:
            try:
                since_ts, _ = _current_shadow_segment_scope_since_ts()
                segment_report = compute_segment_shadow_report(
                    mode="shadow",
                    since_ts=since_ts,
                )
                segment_status = str(segment_report.get("status") or "").strip().lower()
                if segment_status != "ready":
                    segment_summary = str(segment_report.get("summary") or "").strip()
                    if segment_summary:
                        errors.append(
                            "LIVE mode is blocked until champion segment shadow readiness is ready: "
                            f"{segment_summary}"
                        )
                    else:
                        errors.append(
                            "LIVE mode is blocked until champion segment shadow readiness is ready: "
                            f"status={segment_status or 'unknown'}"
                        )
            except Exception as exc:
                errors.append(
                    "LIVE mode is blocked because champion segment shadow readiness could not be computed: "
                    f"{exc}"
                )

    for warning in warnings:
        logger.warning("Startup warning: %s", warning)

    if errors:
        _persist_startup_validation_failure(errors, warnings)
        message = "Startup validation failed:\n- " + "\n- ".join(errors)
        logger.error(message)
        if use_real_money():
            send_alert(build_lines("startup validation failed", build_bullets(errors)), kind="error")
        raise RuntimeError(message)


def _shadow_startup_blocked_service_reason(exc: BaseException) -> str:
    if use_real_money():
        return ""
    if _queued_shadow_rebootstrap_request_label():
        return ""

    error_lines = [
        line[2:].strip()
        for line in str(exc or "").splitlines()
        if str(line or "").strip().startswith("- ")
    ]
    if not error_lines:
        message = str(exc or "").strip()
        if message:
            error_lines = [message]
    reason = ""
    for line in error_lines:
        if line.startswith("SHADOW mode is blocked because SQLite integrity check failed:"):
            reason = line
            break
    if not reason:
        return ""

    db_integrity = database_integrity_state()
    if not bool(db_integrity.get("db_integrity_known")):
        return ""
    if bool(db_integrity.get("db_integrity_ok")):
        return ""
    return reason


def _run_shadow_startup_blocked_service(
    *,
    reason: str,
    validation_error: str,
    shutdown_event: threading.Event,
    persist_state: Callable[..., None],
    heartbeat: Callable[..., None],
    service_rebootstrap_requests_only: Callable[[], None],
    refresh_db_integrity_state: Callable[[], None] | None = None,
    refresh_db_recovery_state: Callable[[], None] | None = None,
    refresh_db_recovery_shadow_state: Callable[[], None] | None = None,
) -> None:
    detail = "startup blocked: waiting for Recover DB or Shadow Reset"
    next_refresh_at = 0.0
    logger.warning("%s Entering startup blocked-service mode.", reason)

    while not shutdown_event.is_set():
        now_ts = time.time()
        if now_ts >= next_refresh_at:
            if refresh_db_integrity_state is not None:
                refresh_db_integrity_state()
            if refresh_db_recovery_state is not None:
                refresh_db_recovery_state()
            if refresh_db_recovery_shadow_state is not None:
                refresh_db_recovery_shadow_state()
            persist_state(
                startup_failed=True,
                startup_validation_failed=True,
                startup_detail=detail,
                startup_failure_message=validation_error,
                startup_validation_message=validation_error,
                startup_blocked=True,
                startup_recovery_only=True,
                startup_block_reason=reason,
                loop_in_progress=False,
            )
            next_refresh_at = now_ts + 30.0
        heartbeat(force=True)
        service_rebootstrap_requests_only()
        if shutdown_event.wait(0.5):
            break


def _init_live_entry_guard(executor: PolymarketExecutor) -> LiveEntryGuard | None:
    if not use_real_money():
        return None

    start_equity = max(executor.get_account_equity_usd(), 0.0)
    drawdown_limit_pct = max_live_drawdown_pct()
    stop_equity = max(start_equity * (1.0 - drawdown_limit_pct), 0.0)
    logger.info(
        "Live entry guard armed: start_equity=$%.2f stop_equity=$%.2f drawdown_limit=%.1f%%",
        start_equity,
        stop_equity,
        drawdown_limit_pct * 100.0,
    )
    return LiveEntryGuard(
        start_equity=start_equity,
        drawdown_limit_pct=drawdown_limit_pct,
        stop_equity=stop_equity,
    )


def _init_daily_loss_guard(executor: PolymarketExecutor) -> DailyLossGuard:
    start_equity = max(executor.get_account_equity_usd(), 0.0)
    return DailyLossGuard(
        start_equity=start_equity,
        loss_limit_pct=max_daily_loss_pct(),
        day_key=time.strftime("%Y-%m-%d", time.localtime()),
    )


def _entry_pause_state(
    tracker: PolymarketTracker,
    executor: PolymarketExecutor,
    live_entry_guard: LiveEntryGuard | None,
    daily_loss_guard: DailyLossGuard,
    account_equity: float,
) -> EntryPauseState | None:
    now_ts = int(time.time())
    if live_entry_guard is not None:
        reason = live_entry_guard.block_reason(account_equity)
        if reason:
            return EntryPauseState(key="live_drawdown_guard", reason=reason)

    daily_loss_guard.loss_limit_pct = max_daily_loss_pct()
    reason = daily_loss_guard.block_reason(account_equity, now_ts)
    if reason:
        return EntryPauseState(key="daily_loss_guard", reason=reason)

    last_ok_at, consecutive_failures = tracker.trade_feed_health()
    if consecutive_failures >= max_live_health_failures():
        return EntryPauseState(
            key="trade_feed_failures",
            reason=(
                f"source trade feed degraded after {consecutive_failures} consecutive trade-feed failures"
            ),
        )
    if last_ok_at > 0 and (now_ts - last_ok_at) > max_feed_staleness_seconds():
        return EntryPauseState(
            key="trade_feed_stale",
            reason=(
                f"source trade feed is stale; the last successful trade poll was {now_ts - last_ok_at}s ago"
            ),
        )

    if use_real_money():
        live_status = getattr(executor, "live_entry_health_status", None)
        if callable(live_status):
            status = live_status()
            if status:
                status_key, live_reason = status
                return EntryPauseState(key=f"live_health:{status_key}", reason=live_reason)
        else:
            live_reason = executor.live_entry_health_reason()
            if live_reason:
                return EntryPauseState(key="live_health", reason=live_reason)

    return None


def _entry_pause_reason(
    tracker: PolymarketTracker,
    executor: PolymarketExecutor,
    live_entry_guard: LiveEntryGuard | None,
    daily_loss_guard: DailyLossGuard,
    account_equity: float,
) -> str | None:
    state = _entry_pause_state(
        tracker,
        executor,
        live_entry_guard,
        daily_loss_guard,
        account_equity,
    )
    return state.reason if state is not None else None


def main() -> None:
    _disable_windows_console_quick_edit()
    logger.info("=" * 60)
    logger.info("Polymarket copy-trading bot starting")
    logger.info("Mode: %s", "LIVE (REAL MONEY)" if use_real_money() else "SHADOW (no real money)")
    logger.info("=" * 60)

    tracker: PolymarketTracker | None = None
    scheduler: BackgroundScheduler | None = None
    dashboard_api_server: DashboardApiServer | None = None
    telegram_command_stop: threading.Event | None = None
    telegram_command_thread: threading.Thread | None = None
    engine: SignalEngine | None = None
    shutdown_event = threading.Event()
    previous_signal_handlers = _install_shutdown_signal_handlers(shutdown_event)
    pending_shadow_reset: ShadowResetRequest | None = None
    pending_db_recovery: DbRecoveryRequest | None = None
    cleanup_done = False
    startup_ready = False
    shadow_startup_blocked_reason = ""
    startup_validation_error = ""

    init_db()
    clear_all_snapshot_requested = False
    snapshot_restore_failed = False
    try:
        snapshot_result = restore_managed_wallet_registry_snapshot()
        clear_all_snapshot_requested = bool(snapshot_result.get("clear_all"))
        restored_wallets = list(snapshot_result.get("wallets") or [])
        if restored_wallets:
            logger.info("Restored %s managed wallet(s) from shadow-reset snapshot", len(restored_wallets))
    except Exception:
        snapshot_restore_failed = True
        logger.warning("Managed wallet registry snapshot restore failed", exc_info=True)
    try:
        if _should_import_bootstrap_watched_wallets(
            clear_all_snapshot_requested,
            snapshot_restore_failed=snapshot_restore_failed,
        ):
            imported_count = import_managed_wallets_from_env(WATCHED_WALLETS)
            if imported_count > 0:
                logger.info("Imported %s managed wallet(s) from WATCHED_WALLETS bootstrap", imported_count)
    except Exception:
        logger.warning("Managed wallet bootstrap from WATCHED_WALLETS failed", exc_info=True)
    startup_trade_log_archive_result = _archive_old_trade_log_batch()
    try:
        load_identity_cache()
    except Exception:
        logger.warning("Identity-cache maintenance skipped during startup", exc_info=True)
    try:
        _prune_artifact_quarantine(quarantine_dir=_configured_model_artifact_path().parent / "artifact_quarantine")
    except Exception:
        logger.warning("Artifact-quarantine pruning skipped during startup", exc_info=True)
    startup_storage_state = _storage_health_state_payload()
    try:
        _validate_startup()
    except RuntimeError as exc:
        shadow_startup_blocked_reason = _shadow_startup_blocked_service_reason(exc)
        if not shadow_startup_blocked_reason:
            raise
        startup_validation_error = str(exc).strip() or shadow_startup_blocked_reason
    _write_bot_pid_file()
    EVENT_FILE.touch(exist_ok=True)
    _repair_event_file_market_urls()
    start_ts = int(time.time())
    session_id = uuid.uuid4().hex
    runtime_wallets = _runtime_managed_wallets()
    watchlist = WatchlistManager(runtime_wallets)
    bot_state_snapshot: dict[str, object] = _base_bot_state_snapshot(session_id=session_id, started_at=start_ts)
    latest_retrain = _latest_retrain_run()
    latest_replay_search = _latest_replay_search_run()
    latest_promotion = _latest_applied_replay_promotion()
    latest_promotion_attempt = _latest_replay_promotion()
    if latest_retrain is not None:
        bot_state_snapshot.update(_latest_retrain_state_payload(latest_retrain))
    if latest_replay_search is not None:
        bot_state_snapshot.update(_latest_replay_search_state_payload(latest_replay_search))
    if latest_promotion_attempt is not None:
        bot_state_snapshot.update(_latest_replay_promotion_state_payload(latest_promotion_attempt))
    if latest_promotion is not None:
        bot_state_snapshot.update(_applied_replay_promotion_state_payload(latest_promotion))
    bot_state_snapshot.update(_shadow_evidence_epoch_state_payload())
    bot_state_snapshot.update(_trade_log_archive_state_payload(last_result=startup_trade_log_archive_result))
    bot_state_snapshot.update(startup_storage_state)
    bot_state_snapshot.update(managed_wallet_registry_state())
    last_activity_write_at = 0.0
    current_loop_started_at = 0
    bot_state_lock = threading.Lock()
    retrain_lock = threading.Lock()
    replay_search_lock = threading.Lock()

    def _persist_bot_state(**updates: object) -> None:
        with bot_state_lock:
            bot_state_snapshot.update(updates)
            _write_bot_state(**bot_state_snapshot)

    def _set_startup_detail(detail: str) -> None:
        _persist_bot_state(startup_detail=str(detail or "").strip())
        _heartbeat(force=True)

    def _heartbeat(*, force: bool = False) -> None:
        nonlocal last_activity_write_at
        now_ts = time.time()
        if not force and (now_ts - last_activity_write_at) < 1.0:
            return
        last_activity_write_at = now_ts
        updates: dict[str, object] = {
            "last_activity_at": int(now_ts),
            "loop_in_progress": current_loop_started_at > 0,
        }
        if current_loop_started_at > 0:
            updates["last_loop_started_at"] = current_loop_started_at
        _persist_bot_state(**updates)

    def _persist_runtime_truth() -> None:
        if engine is None:
            return
        _persist_bot_state(**engine.runtime_info())

    def _cleanup_runtime() -> None:
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        if dashboard_api_server is not None:
            dashboard_api_server.stop()
        _clear_bot_pid_file()
        if telegram_command_stop is not None:
            telegram_command_stop.set()
        if telegram_command_thread is not None:
            telegram_command_thread.join(timeout=1.0)
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=True)
            except Exception:
                logger.debug("Scheduler shutdown skipped during cleanup", exc_info=True)
        if tracker is not None:
            try:
                tracker.close()
            except Exception:
                logger.debug("Tracker close skipped during cleanup", exc_info=True)
        _restore_shutdown_signal_handlers(previous_signal_handlers)

    def _persist_replay_promotion_state(result: dict[str, Any]) -> None:
        _persist_bot_state(**_replay_promotion_state_updates(result))

    def _refresh_shadow_history_state() -> None:
        shadow_history_metrics, last_promotion = _shadow_history_gate_metrics()
        require_total_history = live_require_shadow_history()
        minimum_total = live_min_shadow_resolved() if require_total_history else 0
        required_since_promotion = max(live_min_shadow_resolved_since_promotion(), 0)
        _persist_bot_state(
            **_shadow_history_state_payload(
                total_resolved_shadow=int(shadow_history_metrics["current_window_resolved"] or 0),
                resolved_since_promotion=int(shadow_history_metrics["current_baseline_resolved"] or 0),
                last_promotion=last_promotion,
                require_total_history=require_total_history,
                minimum_total=minimum_total,
                minimum_since_promotion=required_since_promotion,
                all_time_resolved_shadow=int(shadow_history_metrics["all_time_resolved"] or 0),
                routed_resolved_shadow=int(
                    shadow_history_metrics["routed_current_window_resolved"] or 0
                ),
            )
        )

    def _refresh_segment_shadow_state() -> None:
        try:
            since_ts, _ = _current_shadow_segment_scope_since_ts()
            report = compute_segment_shadow_report(
                mode="shadow",
                since_ts=since_ts,
            )
            _persist_bot_state(**_segment_shadow_state_payload(report))
        except Exception as exc:
            _persist_bot_state(**_segment_shadow_error_payload(str(exc)))

    def _refresh_routed_shadow_performance_state() -> None:
        try:
            preview = compute_tracker_preview_summary(
                mode="shadow",
                use_bot_state_balance=False,
                since_ts=_shadow_evidence_epoch_started_at(),
                apply_shadow_evidence_epoch=False,
            )
            _persist_bot_state(
                **_shadow_snapshot_state_payload(
                    preview,
                    all_time_resolved=_resolved_shadow_trade_count(),
                ),
                **_routed_shadow_performance_state_payload(preview),
            )
        except Exception as exc:
            _persist_bot_state(
                **_shadow_snapshot_error_payload(str(exc)),
                **_routed_shadow_performance_error_payload(str(exc)),
            )

    def _refresh_db_integrity_state() -> None:
        _persist_bot_state(**database_integrity_state())

    def _refresh_db_recovery_state() -> None:
        _persist_bot_state(**db_recovery_state())

    def _refresh_db_recovery_shadow_state() -> None:
        _persist_bot_state(**_compute_db_recovery_shadow_state())

    def _refresh_trade_log_archive_state(*, last_result: dict[str, Any] | None = None) -> None:
        _persist_bot_state(**_trade_log_archive_state_payload(last_result=last_result))

    def _refresh_storage_health_state() -> None:
        _persist_bot_state(**_storage_health_state_payload())

    def _run_storage_maintenance() -> None:
        try:
            load_identity_cache()
        except Exception:
            logger.warning("Identity-cache maintenance failed", exc_info=True)
        try:
            _prune_artifact_quarantine(
                quarantine_dir=_configured_model_artifact_path().parent / "artifact_quarantine"
            )
        except Exception:
            logger.warning("Artifact-quarantine pruning failed", exc_info=True)
        _refresh_storage_health_state()

    def _run_trade_log_archive_maintenance() -> None:
        result = _archive_old_trade_log_batch()
        _refresh_trade_log_archive_state(last_result=result)

    def _run_trade_log_archive_request(request: TradeLogArchiveRequest) -> None:
        _persist_bot_state(**_trade_log_archive_state_payload(archive_request=request))
        result = _archive_old_trade_log_batch()
        _refresh_trade_log_archive_state(last_result=result)

    def _service_rebootstrap_requests_only() -> None:
        db_recovery_request = _consume_db_recovery_request()
        if db_recovery_request is not None:
            _persist_bot_state(
                shadow_restart_pending=True,
                shadow_restart_kind="db_recovery",
                shadow_restart_message=(
                    "Shadow DB recovery in progress. Waiting for backend to restart."
                ),
            )
            raise DbRecoveryRequested(db_recovery_request)
        request = _consume_shadow_reset_request()
        if request is not None:
            _persist_bot_state(
                shadow_restart_pending=True,
                shadow_restart_kind="shadow_reset",
                shadow_restart_message=f"Shadow restart in progress ({request.wallet_mode}). Waiting for backend to restart.",
            )
            raise ShadowResetRequested(request)

    def _record_replay_promotion(
        *,
        trigger: str,
        run_row: dict[str, Any],
        candidate_row: dict[str, Any] | None,
        current_candidate_row: dict[str, Any] | None,
        status: str,
        reason: str,
        applied_at: int = 0,
        score_delta: float | None = None,
        pnl_delta_usd: float | None = None,
    ) -> dict[str, Any]:
        candidate_config, ignored_candidate_keys = _filtered_replay_promotion_config_payload(
            _json_object_or_empty((candidate_row or {}).get("config_json"))
        )
        previous_config, _ignored_previous_keys = _filtered_replay_promotion_config_payload(
            _json_object_or_empty((current_candidate_row or {}).get("config_json"))
        )
        updated_keys = sorted(
            key
            for key in set(candidate_config) | set(previous_config)
            if _env_value_text(candidate_config.get(key)) != _env_value_text(previous_config.get(key))
        )
        requested_at = int(time.time())
        finished_at = int(time.time())
        total_resolved_shadow = _resolved_shadow_trade_count()
        resolved_since_previous, _ = _resolved_shadow_trade_count_since_last_promotion()
        promotion_id = _insert_replay_promotion(
            {
                "requested_at": requested_at,
                "finished_at": finished_at,
                "applied_at": int(applied_at or 0),
                "trigger": trigger,
                "scope": "shadow_only",
                "source_mode": "live" if use_real_money() else "shadow",
                "status": status,
                "reason": reason,
                "replay_search_run_id": run_row.get("id"),
                "replay_search_candidate_id": (candidate_row or {}).get("id"),
                "config_json": candidate_config,
                "previous_config_json": previous_config,
                "updated_keys_json": updated_keys,
                "candidate_result_json": (candidate_row or {}).get("result_json"),
                "score": (candidate_row or {}).get("score"),
                "score_delta": score_delta,
                "total_pnl_usd": (candidate_row or {}).get("total_pnl_usd"),
                "pnl_delta_usd": pnl_delta_usd,
                "shadow_resolved_count": total_resolved_shadow,
                "shadow_resolved_since_previous": resolved_since_previous,
            }
        )
        return {
            "promotion_id": promotion_id,
            "event_at": int(applied_at or 0) or finished_at or requested_at,
            "applied_at": int(applied_at or 0),
            "status": status,
            "message": reason,
            "scope": "shadow_only",
            "run_id": int(run_row.get("id") or 0),
            "candidate_id": int((candidate_row or {}).get("id") or 0),
            "score_delta": score_delta,
            "pnl_delta_usd": pnl_delta_usd,
            "updated_keys": updated_keys,
            "ignored_keys": ignored_candidate_keys,
        }

    def _maybe_auto_promote_replay_candidate(*, trigger: str, run_row: dict[str, Any]) -> dict[str, Any]:
        nonlocal engine
        run_id = int(run_row.get("id") or 0)
        current_candidate_row = _load_replay_search_candidate(run_id, current_policy=True) if run_id > 0 else None
        best_candidate_index = run_row.get("best_feasible_candidate_index")
        if best_candidate_index is None:
            best_candidate_row = _load_replay_search_candidate(run_id, feasible_only=True) if run_id > 0 else None
        else:
            best_candidate_row = _load_replay_search_candidate(
                run_id,
                candidate_index=int(best_candidate_index),
            ) if run_id > 0 else None
        best_config, ignored_best_keys = _filtered_replay_promotion_config_payload(
            _json_object_or_empty((best_candidate_row or {}).get("config_json"))
        )
        current_config, _ignored_current_keys = _filtered_replay_promotion_config_payload(
            _json_object_or_empty((current_candidate_row or {}).get("config_json"))
        )
        current_feasible = bool((current_candidate_row or {}).get("feasible"))
        score_delta = (
            float(run_row.get("best_vs_current_score"))
            if run_row.get("best_vs_current_score") is not None
            else None
        )
        pnl_delta_usd = (
            float(run_row.get("best_vs_current_pnl_usd"))
            if run_row.get("best_vs_current_pnl_usd") is not None
            else None
        )

        if not replay_auto_promote():
            result = _record_replay_promotion(
                trigger=trigger,
                run_row=run_row,
                candidate_row=best_candidate_row,
                current_candidate_row=current_candidate_row,
                status="disabled",
                reason="Auto-promotion disabled by config",
                score_delta=score_delta,
                pnl_delta_usd=pnl_delta_usd,
            )
            _persist_replay_promotion_state(result)
            return result

        if use_real_money():
            result = _record_replay_promotion(
                trigger=trigger,
                run_row=run_row,
                candidate_row=best_candidate_row,
                current_candidate_row=current_candidate_row,
                status="skipped_live_mode",
                reason="Auto-promotion is blocked while live trading is enabled",
                score_delta=score_delta,
                pnl_delta_usd=pnl_delta_usd,
            )
            _persist_replay_promotion_state(result)
            return result

        if best_candidate_row is None or not bool(best_candidate_row.get("feasible")):
            result = _record_replay_promotion(
                trigger=trigger,
                run_row=run_row,
                candidate_row=best_candidate_row,
                current_candidate_row=current_candidate_row,
                status="skipped_no_feasible_candidate",
                reason="Replay search did not produce a feasible promotion candidate",
                score_delta=score_delta,
                pnl_delta_usd=pnl_delta_usd,
            )
            _persist_replay_promotion_state(result)
            return result

        if not best_config:
            result = _record_replay_promotion(
                trigger=trigger,
                run_row=run_row,
                candidate_row=best_candidate_row,
                current_candidate_row=current_candidate_row,
                status="skipped_no_promotable_config",
                reason="Best feasible candidate did not contain any promotable editable config keys",
                score_delta=score_delta,
                pnl_delta_usd=pnl_delta_usd,
            )
            _persist_replay_promotion_state(result)
            return result

        if current_config and _compact_json(current_config) == _compact_json(best_config):
            result = _record_replay_promotion(
                trigger=trigger,
                run_row=run_row,
                candidate_row=best_candidate_row,
                current_candidate_row=current_candidate_row,
                status="skipped_unchanged",
                reason="Best feasible replay candidate matches the current policy config",
                score_delta=score_delta,
                pnl_delta_usd=pnl_delta_usd,
            )
            _persist_replay_promotion_state(result)
            return result

        if current_feasible:
            score_threshold = replay_auto_promote_min_score_delta()
            if score_delta is None or score_delta <= score_threshold:
                result = _record_replay_promotion(
                    trigger=trigger,
                    run_row=run_row,
                    candidate_row=best_candidate_row,
                    current_candidate_row=current_candidate_row,
                    status="skipped_score_delta",
                    reason=(
                        "Best feasible replay score delta did not clear the promotion threshold "
                        f"({0.0 if score_delta is None else score_delta:.6f} <= {score_threshold:.6f})"
                    ),
                    score_delta=score_delta,
                    pnl_delta_usd=pnl_delta_usd,
                )
                _persist_replay_promotion_state(result)
                return result

            pnl_threshold = replay_auto_promote_min_pnl_delta_usd()
            if pnl_delta_usd is not None and pnl_delta_usd < pnl_threshold:
                result = _record_replay_promotion(
                    trigger=trigger,
                    run_row=run_row,
                    candidate_row=best_candidate_row,
                    current_candidate_row=current_candidate_row,
                    status="skipped_pnl_delta",
                    reason=(
                        f"Best feasible replay P&L delta ${pnl_delta_usd:.2f} is below the promotion threshold ${pnl_threshold:.2f}"
                    ),
                    score_delta=score_delta,
                    pnl_delta_usd=pnl_delta_usd,
                )
                _persist_replay_promotion_state(result)
                return result

        try:
            apply_result = _apply_env_config_payload(best_config)
            engine = SignalEngine()
            applied_at = int(time.time())
            message = (
                "Applied replay promotion "
                f"(run={run_id}, candidate={int(best_candidate_row.get('id') or 0)}, "
                f"score_delta={score_delta if score_delta is not None else 0.0:.6f}, "
                f"pnl_delta=${pnl_delta_usd if pnl_delta_usd is not None else 0.0:.2f})"
            )
            ignored_key_count = len(set(ignored_best_keys) | set(apply_result.get("ignored_keys") or []))
            if ignored_key_count > 0:
                message += f"; ignored {ignored_key_count} non-promotable key(s)"
            logger.info(message)
            result = _record_replay_promotion(
                trigger=trigger,
                run_row=run_row,
                candidate_row=best_candidate_row,
                current_candidate_row=current_candidate_row,
                status="applied",
                reason=message,
                applied_at=applied_at,
                score_delta=score_delta,
                pnl_delta_usd=pnl_delta_usd,
            )
            _persist_runtime_truth()
            _persist_replay_promotion_state(result)
            _refresh_shadow_history_state()
            _refresh_segment_shadow_state()
            _refresh_routed_shadow_performance_state()
            return result
        except Exception as exc:
            if isinstance(apply_result, dict):
                try:
                    _restore_env_config_payload(apply_result.get("snapshot") or {})
                except Exception:
                    logger.exception("Failed to roll back replay auto-promotion config after runtime init error")
            message = f"Replay auto-promotion failed and was rolled back: {exc}"
            logger.exception(message)
            result = _record_replay_promotion(
                trigger=trigger,
                run_row=run_row,
                candidate_row=best_candidate_row,
                current_candidate_row=current_candidate_row,
                status="failed",
                reason=message,
                score_delta=score_delta,
                pnl_delta_usd=pnl_delta_usd,
            )
            _persist_replay_promotion_state(result)
            return result

    def _run_replay_search_job(trigger: str) -> bool:
        blocked_message = _shadow_history_trust_block_reason("Replay search")
        if blocked_message:
            logger.warning(blocked_message)
            _persist_bot_state(
                **_replay_search_transient_status_state(
                    status="blocked_db_integrity",
                    message=blocked_message,
                    trigger=trigger,
                )
            )
            return False

        snapshot_blocked_message = _shadow_snapshot_trust_block_reason("Replay search")
        if snapshot_blocked_message:
            logger.warning(snapshot_blocked_message)
            _persist_bot_state(
                **_replay_search_transient_status_state(
                    status="blocked_shadow_snapshot",
                    message=snapshot_blocked_message,
                    trigger=trigger,
                )
            )
            return False

        routed_history_blocked_message = _segment_routed_history_trust_block_reason("Replay search")
        if routed_history_blocked_message:
            logger.warning(routed_history_blocked_message)
            _persist_bot_state(
                **_replay_search_transient_status_state(
                    status="blocked_routed_history",
                    message=routed_history_blocked_message,
                    trigger=trigger,
                )
            )
            return False

        if not replay_search_lock.acquire(blocking=False):
            message = f"Replay search request ignored: already running ({trigger})"
            logger.info(message)
            _persist_bot_state(
                **_replay_search_transient_status_state(
                    status="already_running",
                    message=message,
                    trigger=trigger,
                )
            )
            return False

        started_at = int(time.time())
        request_token = ""
        _persist_bot_state(
            **_replay_search_transient_status_state(
                status="running",
                message=f"Replay search running ({trigger})",
                trigger=trigger,
                started_at=started_at,
            )
        )
        _heartbeat(force=True)
        try:
            previous_run_id = _latest_replay_search_run_id()
            request_token = f"replay-search-{started_at}-{uuid.uuid4().hex}"
            command = _build_replay_search_command(request_token=request_token, trigger=trigger)
            logger.info("Running replay search (%s): %s", trigger, command)
            completed = subprocess.run(
                command,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            finished_at = int(time.time())
            run_row = _load_replay_search_run_after(previous_run_id, request_token=request_token)
            stderr_tail = " | ".join(
                line.strip()
                for line in str(completed.stderr or "").splitlines()[-3:]
                if line.strip()
            )
            if completed.returncode != 0:
                message = f"Replay search failed with exit code {completed.returncode}"
                if stderr_tail:
                    message = f"{message}: {stderr_tail}"
                logger.error(message)
                if run_row is not None:
                    run_id = int(run_row.get("id") or 0)
                    _persist_replay_search_run_runtime_context(
                        run_id,
                        trigger=trigger,
                        message=message,
                        status="failed",
                    )
                    run_row = {
                        **run_row,
                        "trigger": trigger,
                        "status_message": message,
                        "status": "failed",
                    }
                else:
                    run_row = _insert_replay_search_failure_run(
                        started_at=started_at,
                        finished_at=finished_at,
                        request_token=request_token,
                        trigger=trigger,
                        label_prefix=replay_search_label_prefix(),
                        notes=replay_search_notes(),
                        message=message,
                    )
                _persist_bot_state(
                    replay_search_in_progress=False,
                    replay_search_started_at=0,
                    last_replay_search_finished_at=finished_at,
                    last_replay_search_status="failed",
                    last_replay_search_message=message,
                    last_replay_search_trigger=trigger,
                    last_replay_search_scope="shadow_only",
                    last_replay_search_run_id=int((run_row or {}).get('id') or 0),
                    last_replay_search_candidate_count=int((run_row or {}).get('candidate_count') or 0),
                    last_replay_search_feasible_count=int((run_row or {}).get('feasible_count') or 0),
                    last_replay_search_best_score=(run_row or {}).get("best_feasible_score"),
                    last_replay_search_best_pnl_usd=(run_row or {}).get("best_feasible_total_pnl_usd"),
                )
                return False

            if run_row is None:
                message = (
                    "Replay search completed without persisting a matching replay_search_runs row "
                    f"for request_token={request_token}"
                )
                logger.error(message)
                run_row = _insert_replay_search_failure_run(
                    started_at=started_at,
                    finished_at=finished_at,
                    request_token=request_token,
                    trigger=trigger,
                    label_prefix=replay_search_label_prefix(),
                    notes=replay_search_notes(),
                    message=message,
                )
                _persist_bot_state(
                    replay_search_in_progress=False,
                    replay_search_started_at=0,
                    last_replay_search_finished_at=finished_at,
                    last_replay_search_status="failed",
                    last_replay_search_message=message,
                    last_replay_search_trigger=trigger,
                    last_replay_search_scope="shadow_only",
                    last_replay_search_run_id=int((run_row or {}).get("id") or 0),
                )
                return False

            promotion_result = _maybe_auto_promote_replay_candidate(trigger=trigger, run_row=run_row)
            message = (
                "Replay search completed "
                f"(run={int(run_row.get('id') or 0)}, "
                f"candidates={int(run_row.get('candidate_count') or 0)}, "
                f"feasible={int(run_row.get('feasible_count') or 0)})"
            )
            if promotion_result.get("status") == "applied":
                message += "; promotion applied"
            elif promotion_result.get("status") not in {"disabled", ""}:
                message += f"; promotion {promotion_result.get('status')}"
            if stderr_tail:
                message += f" [{stderr_tail}]"
            _persist_replay_search_run_runtime_context(
                int(run_row.get("id") or 0),
                trigger=trigger,
                message=message,
                status=str(run_row.get("status") or "completed"),
            )
            run_row = {
                **run_row,
                "trigger": trigger,
                "status_message": message,
            }
            _persist_bot_state(
                replay_search_in_progress=False,
                replay_search_started_at=0,
                last_replay_search_finished_at=finished_at,
                last_replay_search_status=str(run_row.get("status") or "completed"),
                last_replay_search_message=message,
                last_replay_search_trigger=trigger,
                last_replay_search_scope="shadow_only",
                last_replay_search_run_id=int(run_row.get("id") or 0),
                last_replay_search_candidate_count=int(run_row.get("candidate_count") or 0),
                last_replay_search_feasible_count=int(run_row.get("feasible_count") or 0),
                last_replay_search_best_score=run_row.get("best_feasible_score"),
                last_replay_search_best_pnl_usd=run_row.get("best_feasible_total_pnl_usd"),
            )
            return True
        except Exception as exc:
            finished_at = int(time.time())
            message = f"Replay search failed: {exc}"
            logger.exception(message)
            run_row = _insert_replay_search_failure_run(
                started_at=started_at,
                finished_at=finished_at,
                request_token=request_token,
                trigger=trigger,
                label_prefix=replay_search_label_prefix(),
                notes=replay_search_notes(),
                message=message,
            )
            _persist_bot_state(
                replay_search_in_progress=False,
                replay_search_started_at=0,
                last_replay_search_finished_at=finished_at,
                last_replay_search_status="failed",
                last_replay_search_message=message,
                last_replay_search_trigger=trigger,
                last_replay_search_scope="shadow_only",
                last_replay_search_run_id=int((run_row or {}).get("id") or 0),
            )
            return False
        finally:
            _heartbeat(force=True)
            replay_search_lock.release()

    def _run_retrain_job(trigger: str) -> bool:
        blocked_status, blocked_message = _retrain_trust_block_state("Retrain")
        if blocked_message:
            logger.warning(blocked_message)
            _persist_bot_state(
                last_retrain_status=blocked_status,
                last_retrain_message=blocked_message,
                last_retrain_trigger=trigger,
                last_retrain_deployed=False,
            )
            return False

        if not retrain_lock.acquire(blocking=False):
            message = f"Retrain request ignored: already running ({trigger})"
            logger.info(message)
            _persist_bot_state(
                last_retrain_status="already_running",
                last_retrain_message=message,
                last_retrain_trigger=trigger,
            )
            return False

        started_at = int(time.time())
        retrain_run_before_id = _latest_retrain_run_id()
        _persist_bot_state(
            retrain_in_progress=True,
            retrain_started_at=started_at,
            last_retrain_started_at=started_at,
            last_retrain_status="running",
            last_retrain_message=f"Retrain running ({trigger})",
            last_retrain_trigger=trigger,
        )
        _heartbeat(force=True)
        try:
            report = retrain_cycle_report(engine, trigger=trigger, started_at=started_at)
            finished_at = int(report.get("finished_at") or time.time())
            run_row = _load_retrain_run_after(retrain_run_before_id)
            if run_row is None:
                logger.error("Retrain completed without persisting retrain_runs row; inserting fallback row")
                run_row = _insert_retrain_run(
                    started_at=started_at,
                    finished_at=finished_at,
                    trigger=trigger,
                    status=str(report.get("status") or ""),
                    ok=bool(report.get("ok")),
                    deployed=bool(report.get("deployed")),
                    sample_count=int(report.get("sample_count") or 0),
                    min_samples=int(report.get("min_samples") or 0),
                    message=str(report.get("message") or ""),
                )
            _persist_bot_state(
                retrain_in_progress=False,
                retrain_started_at=0,
                last_retrain_finished_at=finished_at,
                last_retrain_status=str(report.get("status") or ""),
                last_retrain_message=str(report.get("message") or ""),
                last_retrain_sample_count=int(report.get("sample_count") or 0),
                last_retrain_min_samples=int(report.get("min_samples") or 0),
                last_retrain_trigger=trigger,
                last_retrain_deployed=bool(report.get("deployed")),
            )
            _persist_runtime_truth()
            if bool(report.get("ok")):
                _run_replay_search_job(f"post_retrain_{trigger}")
            return bool(report.get("ok"))
        except Exception as exc:
            finished_at = int(time.time())
            message = f"Retrain failed: {exc}"
            logger.exception(message)
            run_row = _load_retrain_run_after(retrain_run_before_id)
            if run_row is None:
                logger.error("Retrain failed before persisting retrain_runs row; inserting fallback row")
                _insert_retrain_run(
                    started_at=started_at,
                    finished_at=finished_at,
                    trigger=trigger,
                    status="failed",
                    ok=False,
                    deployed=False,
                    message=message,
                )
            _persist_bot_state(
                retrain_in_progress=False,
                retrain_started_at=0,
                last_retrain_finished_at=finished_at,
                last_retrain_status="failed",
                last_retrain_message=message,
                last_retrain_trigger=trigger,
                last_retrain_deployed=False,
            )
            _persist_runtime_truth()
            raise
        finally:
            _heartbeat(force=True)
            retrain_lock.release()

    def _service_runtime_requests() -> None:
        _service_rebootstrap_requests_only()
        _service_trade_log_archive_request(_run_trade_log_archive_request)
        _service_manual_retrain_request(_run_retrain_job)
        _service_manual_trade_request(
            lambda request: _process_manual_trade_request(
                request,
                tracker=tracker,
                executor=executor,
                dedup=dedup,
                live_entry_guard=live_entry_guard,
                daily_loss_guard=daily_loss_guard,
            )
        )

    try:
        _service_rebootstrap_requests_only()
        _persist_bot_state(**watchlist.state_fields())
        dashboard_api_server = start_dashboard_api_server()
        if shadow_startup_blocked_reason:
            _run_shadow_startup_blocked_service(
                reason=shadow_startup_blocked_reason,
                validation_error=startup_validation_error or shadow_startup_blocked_reason,
                shutdown_event=shutdown_event,
                persist_state=_persist_bot_state,
                heartbeat=_heartbeat,
                service_rebootstrap_requests_only=_service_rebootstrap_requests_only,
                refresh_db_integrity_state=_refresh_db_integrity_state,
                refresh_db_recovery_state=_refresh_db_recovery_state,
                refresh_db_recovery_shadow_state=_refresh_db_recovery_shadow_state,
            )
        else:
            _set_startup_detail("loading watchlist")
            _set_startup_detail("syncing belief priors")
            sync_belief_priors()

            _set_startup_detail("creating tracker")
            tracker = PolymarketTracker(runtime_wallets, activity_callback=_heartbeat)
            _set_startup_detail("connecting executor")
            executor = PolymarketExecutor()
            _set_startup_detail("checking wallet balance")
            executor.validate_live_wallet_ready(min_required_balance_usd=min_bet_usd())
            _persist_bot_state(bankroll_usd=round(executor.get_usdc_balance(), 2))
            _set_startup_detail("starting telegram replies")
            telegram_command_stop = threading.Event()
            telegram_command_thread = threading.Thread(
                target=_run_telegram_command_loop,
                args=(telegram_command_stop,),
                name="telegram-command-loop",
                daemon=True,
            )
            telegram_command_thread.start()
            startup_wallets = watchlist.startup_wallets()
            _set_startup_detail("initializing risk guards")
            live_entry_guard = _init_live_entry_guard(executor)
            daily_loss_guard = _init_daily_loss_guard(executor)
            _set_startup_detail("loading signal engine")
            engine = SignalEngine()
            _persist_runtime_truth()
            _set_startup_detail("loading trade cache")
            dedup = DedupeCache()
            dedup.load_from_db(rebuild_shadow_positions=True)
            tracker.seen_ids.update(dedup.seen_ids)
            _set_startup_detail(
                f"loaded {len(dedup.seen_ids)} seen ids, {len(dedup.open_positions)} open positions"
            )
            _set_startup_detail("syncing live positions" if use_real_money() else "rebuilding shadow positions")
            initial_live_sync_ok = dedup.sync_positions_from_api(tracker, wallet_address())
            if use_real_money() and not initial_live_sync_ok:
                raise RuntimeError("Initial live positions sync failed; refusing to start without a confirmed view of open positions")
            _refresh_shadow_history_state()
            _refresh_segment_shadow_state()
            _refresh_routed_shadow_performance_state()
            _refresh_db_integrity_state()
            _refresh_db_recovery_state()
            _refresh_db_recovery_shadow_state()

            def _refresh_watchlist() -> None:
                refresh_trader_cache(watchlist.active_wallets())
                watchlist.refresh(run_auto_drop=True)
                _persist_bot_state(**watchlist.state_fields())

            def _refresh_managed_wallet_registry() -> None:
                nonlocal runtime_wallets
                managed_wallets = load_managed_wallets()
                if managed_wallets == runtime_wallets:
                    return
                runtime_wallets = managed_wallets
                watchlist.replace_wallets(managed_wallets)
                if tracker is not None:
                    tracker.replace_wallets(managed_wallets)
                refresh_trader_cache(managed_wallets)
                _persist_bot_state(
                    **watchlist.state_fields(),
                    **managed_wallet_registry_state(),
                )

            def _refresh_wallet_discovery() -> None:
                summary = refresh_wallet_discovery_candidates(runtime_wallets)
                message = str(summary.get("message") or "").strip()
                if message:
                    if bool(summary.get("ok")):
                        logger.info("Wallet discovery: %s", message)
                    else:
                        logger.info("Wallet discovery skipped: %s", message)
                _persist_bot_state(
                    wallet_discovery_last_scan_at=int(summary.get("finished_at") or 0),
                    wallet_discovery_scanned_count=int(summary.get("scanned_count") or 0),
                    wallet_discovery_candidate_count=int(summary.get("stored_count") or 0),
                    wallet_discovery_last_scan_ok=bool(summary.get("ok")),
                    wallet_discovery_last_scan_message=message,
                )

            scheduler = BackgroundScheduler()
            scheduler.add_job(
                lambda: (
                    _resolve_trades_and_alert(),
                    dedup.load_from_db(rebuild_shadow_positions=False),
                    _refresh_shadow_history_state(),
                    _refresh_segment_shadow_state(),
                    _refresh_routed_shadow_performance_state(),
                ),
                "interval",
                minutes=2,
                id="resolve_trades",
            )
            scheduler.add_job(
                daily_report,
                "cron",
                hour=8,
                minute=0,
                id="daily_report",
            )
            retrain_cadence = retrain_base_cadence()
            retrain_hour = retrain_hour_local()
            retrain_trigger = {"hour": retrain_hour, "minute": 0, "id": "scheduled_retrain"}
            if retrain_cadence == "weekly":
                scheduler.add_job(
                    lambda: _run_retrain_job("scheduled"),
                    "cron",
                    day_of_week="mon",
                    **retrain_trigger,
                )
            else:
                scheduler.add_job(
                    lambda: _run_retrain_job("scheduled"),
                    "cron",
                    **retrain_trigger,
                )
            replay_search_cadence = replay_search_base_cadence()
            replay_search_hour = replay_search_hour_local()
            replay_search_trigger = {"hour": replay_search_hour, "minute": 0, "id": "scheduled_replay_search"}
            if replay_search_cadence == "weekly":
                scheduler.add_job(
                    lambda: _run_replay_search_job("scheduled"),
                    "cron",
                    day_of_week="mon",
                    **replay_search_trigger,
                )
            elif replay_search_cadence == "daily":
                scheduler.add_job(
                    lambda: _run_replay_search_job("scheduled"),
                    "cron",
                    **replay_search_trigger,
                )
            scheduler.add_job(
                lambda: should_retrain_early(engine) and _run_retrain_job("early"),
                "interval",
                seconds=retrain_early_check_seconds(),
                id="early_retrain_check",
            )
            scheduler.add_job(
                lambda: dedup.sync_positions_from_api(tracker, wallet_address()),
                "interval",
                minutes=5,
                id="sync_positions",
            )
            scheduler.add_job(
                lambda: _run_stop_loss_checks(tracker, executor, dedup),
                "interval",
                minutes=1,
                id="stop_loss_check",
            )
            scheduler.add_job(
                _refresh_watchlist,
                "interval",
                minutes=10,
                id="refresh_trader_cache",
            )
            scheduler.add_job(
                _refresh_managed_wallet_registry,
                "interval",
                minutes=5,
                id="managed_wallet_registry_sync",
            )
            if wallet_discovery_enabled():
                scheduler.add_job(
                    _refresh_wallet_discovery,
                    "interval",
                    seconds=max(int(wallet_discovery_scan_interval_seconds()), 300),
                    id="wallet_discovery_scan",
                    next_run_time=datetime.now(),
                )
            scheduler.add_job(
                _backup_db,
                "cron",
                hour=4,
                id="db_backup",
            )
            scheduler.add_job(
                _run_trade_log_archive_maintenance,
                "cron",
                hour=3,
                minute=30,
                id="trade_log_archive",
            )
            scheduler.add_job(
                _refresh_db_integrity_state,
                "interval",
                hours=1,
                id="db_integrity_check",
            )
            scheduler.add_job(
                _refresh_db_recovery_state,
                "interval",
                hours=1,
                id="db_recovery_check",
            )
            scheduler.add_job(
                _refresh_trade_log_archive_state,
                "interval",
                hours=1,
                id="trade_log_archive_state_refresh",
            )
            scheduler.add_job(
                _run_storage_maintenance,
                "interval",
                hours=1,
                id="storage_health_refresh",
            )
            scheduler.add_job(
                _refresh_db_recovery_shadow_state,
                "interval",
                hours=1,
                id="db_recovery_shadow_check",
            )
            _set_startup_detail("starting scheduler")
            scheduler.start()
            _log_runtime_ready(tracker, watchlist)
            _persist_bot_state(startup_detail="waiting for first poll")
            startup_warmup_thread = threading.Thread(
                target=_run_deferred_startup_tasks,
                kwargs={
                    "startup_wallets": startup_wallets,
                    "tracker": tracker,
                    "watchlist": watchlist,
                    "dedup": dedup,
                    "engine": engine,
                    "persist_state": _persist_bot_state,
                    "run_retrain_job": _run_retrain_job,
                },
                name="startup-warmup",
                daemon=True,
            )
            startup_warmup_thread.start()

            mode_str = "LIVE" if use_real_money() else "SHADOW"
            tier_state = watchlist.state_fields()
            send_alert(
                build_lines(
                    f"bot started in {mode_str.lower()} mode",
                    f"watching {len(tracker.wallets)} wallets",
                    (
                        "tracked/dropped: "
                        f"{tier_state['tracked_wallet_count']}/{tier_state['dropped_wallet_count']}"
                    ),
                    (
                        "hot/warm/discovery: "
                        f"{tier_state['hot_wallet_count']}/{tier_state['warm_wallet_count']}/{tier_state['discovery_wallet_count']}"
                    ),
                    f"poll interval: {poll_interval()}s",
                ),
                kind="status",
            )
            startup_ready = True
    except ShadowResetRequested as exc:
        pending_shadow_reset = exc.request
        shutdown_event.set()
        logger.warning(
            "Processing shadow reset request from %s (wallet_mode=%s request_id=%s)",
            exc.request.source,
            exc.request.wallet_mode,
            exc.request.request_id or "-",
        )
    except DbRecoveryRequested as exc:
        pending_db_recovery = exc.request
        shutdown_event.set()
        logger.warning(
            "Processing DB recovery request from %s (candidate=%s request_id=%s)",
            exc.request.source,
            exc.request.candidate_path,
            exc.request.request_id or "-",
        )
    except Exception as exc:
        detail = f"startup failed: {exc}"
        logger.exception(detail)
        _persist_startup_failure_state(
            detail=detail,
            message=detail,
            validation_failed=False,
        )
        try:
            send_alert(build_lines("startup failed", str(exc)), kind="error")
        except Exception:
            logger.debug("Startup failure alert skipped", exc_info=True)
        _cleanup_runtime()
        raise

    try:
        if startup_ready:
            entry_pause_alerts = EntryPauseAlertTracker()
            first_poll_logged = False
            while not shutdown_event.is_set():
                loop_start = time.time()
                current_loop_started_at = int(loop_start)
                _heartbeat(force=True)
                event_count = 0
                polled_wallet_count = 0
                bankroll = 0.0
                account_equity = 0.0
                entry_block_reason = None
                try:
                    bankroll = executor.get_usdc_balance()
                    account_equity = executor.get_account_equity_usd()
                    if bankroll < 1.0:
                        logger.warning("Low balance: $%.2f - skipping poll", bankroll)
                    else:
                        _heartbeat()
                        watchlist.refresh(run_auto_drop=False)
                        poll_batches = watchlist.poll_batches()
                        polled_wallet_count = sum(len(batch.wallets) for batch in poll_batches)
                        _persist_bot_state(
                            polled_wallet_count=polled_wallet_count,
                            **watchlist.state_fields(),
                        )
                        events = []
                        for batch in poll_batches:
                            if shutdown_event.is_set():
                                break
                            if not batch.wallets:
                                continue
                            events.extend(
                                tracker.poll(
                                    list(batch.wallets),
                                    trade_limit=batch.trade_limit,
                                    watch_tier=batch.watch_tier,
                                )
                            )
                        event_count = len(events)
                        entry_pause_state = _entry_pause_state(
                            tracker,
                            executor,
                            live_entry_guard,
                            daily_loss_guard,
                            account_equity,
                        )
                        entry_block_reason = entry_pause_state.reason if entry_pause_state is not None else None
                        entry_pause_alert = entry_pause_alerts.update(entry_pause_state)
                        if entry_pause_alert is not None:
                            transition, alert_state = entry_pause_alert
                            if transition == "paused" and alert_state is not None:
                                logger.error(alert_state.reason)
                                send_alert(
                                    build_lines(
                                        "entries paused",
                                        alert_state.reason,
                                        "new entries are paused until the condition clears",
                                    ),
                                    kind="warning",
                                )
                            elif transition == "resumed":
                                if alert_state is not None:
                                    logger.info("Entry pause cleared: %s", alert_state.reason)
                                send_alert(
                                    build_lines(
                                        "entries resumed",
                                        "the pause condition cleared and new entries are enabled again",
                                    ),
                                    kind="status",
                                )
                        for event in events:
                            if shutdown_event.is_set():
                                break
                            _heartbeat()
                            try:
                                bankroll = max(
                                    bankroll
                                    + process_event(
                                        event,
                                        engine,
                                        executor,
                                        dedup,
                                        bankroll,
                                        account_equity,
                                        entry_block_reason=entry_block_reason,
                                    ),
                                    0.0,
                                )
                                account_equity = executor.get_account_equity_usd()
                            except Exception as exc:
                                logger.error(
                                    "Event processing failed for trade %s: %s",
                                    event.trade_id,
                                    exc,
                                    exc_info=True,
                                )
                                send_alert(
                                    build_market_error_alert(
                                        "event processing failed",
                                        question=event.question,
                                        market_url=_market_url_for_event(event),
                                        detail=f"trade {event.trade_id[:12]} failed: {exc}",
                                        tracked_trader_name=getattr(event, "trader_name", None),
                                        tracked_trader_address=getattr(event, "trader_address", None),
                                    ),
                                    kind="error",
                                )
                            finally:
                                if event.trade_id in dedup.seen_ids:
                                    tracker.seen_ids.add(event.trade_id)
                except Exception as exc:
                    logger.error("Main loop error: %s", exc, exc_info=True)
                    send_alert(build_lines("bot loop error", str(exc)), kind="error")

                elapsed = time.time() - loop_start
                state_snapshot = {
                    "started_at": start_ts,
                    "last_loop_started_at": current_loop_started_at,
                    "last_activity_at": int(time.time()),
                    "startup_detail": "",
                    "last_poll_at": int(time.time()),
                    "last_poll_duration_s": round(elapsed, 3),
                    "bankroll_usd": round(bankroll, 2),
                    "last_event_count": event_count,
                    "polled_wallet_count": polled_wallet_count,
                    "loop_in_progress": False,
                    **watchlist.state_fields(),
                }
                current_loop_started_at = 0
                _persist_bot_state(**state_snapshot)
                _service_runtime_requests()
                if not first_poll_logged:
                    _log_first_poll_summary(
                        elapsed=elapsed,
                        polled_wallet_count=polled_wallet_count,
                        event_count=event_count,
                        bankroll=bankroll,
                    )
                    first_poll_logged = True
                _wait_for_next_poll(
                    loop_start,
                    state_snapshot,
                    _service_runtime_requests,
                    stop_event=shutdown_event,
                )
            logger.info("Shutdown requested. Exiting main loop.")
    except ShadowResetRequested as exc:
        pending_shadow_reset = exc.request
        shutdown_event.set()
        logger.warning(
            "Processing shadow reset request from %s (wallet_mode=%s request_id=%s)",
            exc.request.source,
            exc.request.wallet_mode,
            exc.request.request_id or "-",
        )
    except DbRecoveryRequested as exc:
        pending_db_recovery = exc.request
        shutdown_event.set()
        logger.warning(
            "Processing DB recovery request from %s (candidate=%s request_id=%s)",
            exc.request.source,
            exc.request.candidate_path,
            exc.request.request_id or "-",
        )
    except KeyboardInterrupt:
        shutdown_event.set()
        logger.info("Shutting down...")
        send_alert("bot stopped", kind="status")
    finally:
        _cleanup_runtime()
        if pending_db_recovery is not None:
            try:
                recovery_result = recover_db_from_verified_backup(
                    path=Path(pending_db_recovery.source_path) if pending_db_recovery.source_path else DB_PATH,
                    backup_path=Path(pending_db_recovery.candidate_path),
                )
                if not bool(recovery_result.get("ok")):
                    raise RuntimeError(str(recovery_result.get("message") or "DB recovery failed"))
                logger.warning(
                    "DB recovery restored %s from %s (quarantined=%s)",
                    recovery_result.get("restored_path"),
                    recovery_result.get("backup_path"),
                    recovery_result.get("quarantined_path") or "-",
                )
                model_quarantined_path = _quarantine_runtime_model_artifact(reason="db_recovery")
                if model_quarantined_path:
                    logger.warning(
                        "DB recovery quarantined deployed model artifact at %s",
                        model_quarantined_path,
                    )
                write_shadow_evidence_epoch(
                    path=SHADOW_EVIDENCE_EPOCH_FILE,
                    source="db_recovery",
                    request_id=pending_db_recovery.request_id,
                    message="fresh shadow evidence epoch started after DB recovery",
                )
                logging.shutdown()
                exec_restarted_bot()
            except Exception:
                _persist_bot_state(
                    shadow_restart_pending=False,
                    shadow_restart_kind="",
                    shadow_restart_message="",
                    startup_failed=True,
                    startup_failure_message="DB recovery failed before restart",
                )
                raise
        elif pending_shadow_reset is not None:
            normalized_wallet_mode = pending_shadow_reset.wallet_mode
            previous_wallets = ""
            wallets_updated = False
            try:
                normalized_wallet_mode, previous_wallets, wallets_updated = apply_wallet_mode_for_reset(
                    pending_shadow_reset.wallet_mode
                )
                logging.shutdown()
                reset_shadow_runtime()
                exec_restarted_bot()
            except Exception:
                if wallets_updated:
                    try:
                        restore_watched_wallets(previous_wallets)
                    except OSError:
                        pass
                raise


if __name__ == "__main__":
    main()
