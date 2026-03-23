from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeLayout:
    repo_root: Path
    save_dir: Path
    data_dir: Path
    log_dir: Path
    trading_db_path: Path
    event_file: Path
    bot_state_file: Path
    bot_pid_file: Path
    identity_cache_path: Path
    manual_retrain_request_file: Path
    manual_trade_request_file: Path
    telegram_state_file: Path
    background_log_path: Path
    model_artifact_path: Path


def runtime_layout(repo_root: Path | None = None) -> RuntimeLayout:
    base = Path(repo_root or Path(__file__).resolve().parent)
    save_dir = base / "save"
    data_dir = save_dir / "data"
    log_dir = save_dir / "logs"
    return RuntimeLayout(
        repo_root=base,
        save_dir=save_dir,
        data_dir=data_dir,
        log_dir=log_dir,
        trading_db_path=data_dir / "trading.db",
        event_file=data_dir / "events.jsonl",
        bot_state_file=data_dir / "bot_state.json",
        bot_pid_file=data_dir / "shadow_bot.pid",
        identity_cache_path=data_dir / "identity_cache.json",
        manual_retrain_request_file=data_dir / "manual_retrain_request.json",
        manual_trade_request_file=data_dir / "manual_trade_request.json",
        telegram_state_file=data_dir / "telegram_state.json",
        background_log_path=log_dir / "shadow_runtime.out",
        model_artifact_path=save_dir / "model.joblib",
    )


def _merge_directory(source: Path, destination: Path) -> None:
    if not source.exists() or not source.is_dir():
        return

    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if target.exists():
            continue
        child.replace(target)

    try:
        source.rmdir()
    except OSError:
        return


def _move_file_if_missing(source: Path, destination: Path) -> None:
    if not source.exists() or destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)


def migrate_runtime_state(repo_root: Path | None = None) -> RuntimeLayout:
    layout = runtime_layout(repo_root)
    legacy_data_dir = layout.repo_root / "data"
    legacy_log_dir = layout.repo_root / "logs"
    legacy_model_path = layout.repo_root / "model.joblib"

    layout.save_dir.mkdir(parents=True, exist_ok=True)
    _merge_directory(legacy_data_dir, layout.data_dir)
    _merge_directory(legacy_log_dir, layout.log_dir)
    _move_file_if_missing(legacy_model_path, layout.model_artifact_path)
    layout.data_dir.mkdir(parents=True, exist_ok=True)
    layout.log_dir.mkdir(parents=True, exist_ok=True)
    return layout


_LAYOUT = migrate_runtime_state()

REPO_ROOT = _LAYOUT.repo_root
SAVE_DIR = _LAYOUT.save_dir
DATA_DIR = _LAYOUT.data_dir
LOG_DIR = _LAYOUT.log_dir
TRADING_DB_PATH = _LAYOUT.trading_db_path
EVENT_FILE = _LAYOUT.event_file
BOT_STATE_FILE = _LAYOUT.bot_state_file
BOT_PID_FILE = _LAYOUT.bot_pid_file
IDENTITY_CACHE_PATH = _LAYOUT.identity_cache_path
MANUAL_RETRAIN_REQUEST_FILE = _LAYOUT.manual_retrain_request_file
MANUAL_TRADE_REQUEST_FILE = _LAYOUT.manual_trade_request_file
TELEGRAM_STATE_FILE = _LAYOUT.telegram_state_file
BACKGROUND_LOG_PATH = _LAYOUT.background_log_path
MODEL_ARTIFACT_PATH = _LAYOUT.model_artifact_path
