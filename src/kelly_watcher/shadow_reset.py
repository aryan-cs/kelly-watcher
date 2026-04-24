from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal, cast

from kelly_watcher.config import shadow_bankroll_usd, use_real_money
import kelly_watcher.data.db as db
from kelly_watcher.env_profile import (
    ENV_EXAMPLE_PATH,
    active_env_flag,
    active_env_profile,
    add_env_profile_flags,
    env_path_for_profile,
    env_paths_for_profile,
)
from kelly_watcher.runtime_paths import (
    BACKGROUND_LOG_PATH,
    BOT_PID_FILE,
    DATA_DIR,
    IDENTITY_CACHE_PATH,
    LOG_DIR,
    MODEL_ARTIFACT_PATH,
    REPO_ROOT,
    SAVE_DIR,
    TELEGRAM_STATE_FILE,
)
from kelly_watcher.engine.shadow_evidence import write_shadow_evidence_epoch

ENV_PROFILE = active_env_profile()
ENV_PATH = env_path_for_profile(ENV_PROFILE)
PID_FILE = BOT_PID_FILE
BACKGROUND_LOG = BACKGROUND_LOG_PATH
RestartWalletMode = Literal["keep_active", "keep_all", "clear_all"]


def _source_env_path() -> Path:
    expected_env_path = env_path_for_profile(ENV_PROFILE, REPO_ROOT)
    if ENV_PATH != expected_env_path:
        return ENV_PATH
    paths = [path for path in env_paths_for_profile(ENV_PROFILE, REPO_ROOT) if path.exists()]
    if paths:
        return paths[0]
    return ENV_EXAMPLE_PATH


def _source_env_paths() -> list[Path]:
    expected_env_path = env_path_for_profile(ENV_PROFILE, REPO_ROOT)
    if ENV_PATH != expected_env_path:
        return [ENV_PATH]
    paths = [path for path in env_paths_for_profile(ENV_PROFILE, REPO_ROOT) if path.exists()]
    if paths:
        return paths
    return [_source_env_path()]


def _read_env_value(key: str) -> str:
    for path in _source_env_paths():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            current_key, value = line.split("=", 1)
            if current_key.strip() == key:
                return value.strip()
    return ""


def _write_env_value(key: str, value: str) -> None:
    source_path = ENV_PATH if ENV_PATH.exists() else _source_env_path()
    try:
        lines = source_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    updated: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)

    if not found:
        if updated and updated[-1] != "":
            updated.append("")
        updated.append(f"{key}={value}")

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _parse_watched_wallets(raw: str) -> list[str]:
    seen: set[str] = set()
    wallets: list[str] = []
    for wallet in str(raw or "").split(","):
        normalized = wallet.strip().lower()
        if not normalized or normalized in seen:
            continue
        wallets.append(normalized)
        seen.add(normalized)
    return wallets


def _serialize_watched_wallets(wallets: list[str]) -> str:
    return ",".join(wallets)


def _normalize_wallet_mode(
    wallet_mode: str | None = None,
    *,
    clear_wallets: bool | None = None,
) -> RestartWalletMode:
    if clear_wallets is not None:
        return "clear_all" if clear_wallets else "keep_all"
    normalized = str(wallet_mode or "keep_all").strip().lower()
    if normalized in {"keep_active", "keep_all", "clear_all"}:
        return cast(RestartWalletMode, normalized)
    raise ValueError("wallet_mode must be keep_active, keep_all, or clear_all")


def _normalize_command(command: str) -> str:
    return " ".join(str(command or "").replace("\\", "/").strip().lower().split())


def _looks_like_bot_command(command: str) -> bool:
    normalized = _normalize_command(command)
    repo_root = str(REPO_ROOT).replace("\\", "/").lower()
    markers = (
        f"{repo_root}/main.py",
        f"python {repo_root}/main.py",
        f"python3 {repo_root}/main.py",
        f"python.exe {repo_root}/main.py",
        "-m cli",
        "uv run main",
        "uv run python main.py",
        "python main.py",
        "python3 main.py",
    )
    return any(marker in normalized for marker in markers)


def _read_pid_file() -> int | None:
    try:
        raw = PID_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None

    if not raw:
        return None

    try:
        return int(raw)
    except ValueError:
        return None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _scan_process_table() -> dict[int, str]:
    if os.name == "nt":
        return _scan_process_table_windows()
    return _scan_process_table_posix()


def _scan_process_table_posix() -> dict[int, str]:
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=", "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
    except OSError:
        return {}

    processes: dict[int, str] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_text, command = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        processes[pid] = command
    return processes


def _scan_process_table_windows() -> dict[int, str]:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
    except OSError:
        return {}

    raw = (result.stdout or "").strip()
    if not raw:
        return {}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    rows = payload if isinstance(payload, list) else [payload]
    processes: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("ProcessId")
        command = str(row.get("CommandLine") or "").strip()
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        processes[pid_int] = command
    return processes


def find_bot_pids() -> list[int]:
    current_pid = os.getpid()
    scanned = _scan_process_table()
    matches = {
        pid
        for pid, command in scanned.items()
        if pid != current_pid and _looks_like_bot_command(command)
    }

    tracked_pid = _read_pid_file()
    if tracked_pid and tracked_pid != current_pid and _process_exists(tracked_pid):
        if not scanned or tracked_pid in matches or tracked_pid not in scanned:
            matches.add(tracked_pid)

    return sorted(matches)


def _normalize_target_pids(target_pids: list[int] | tuple[int, ...] | set[int] | None = None) -> list[int]:
    current_pid = os.getpid()
    normalized: set[int] = set()
    for raw_pid in target_pids or ():
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid == current_pid or not _process_exists(pid):
            continue
        normalized.add(pid)
    return sorted(normalized)


def _terminate_process(pid: int, *, force: bool) -> None:
    if os.name == "nt":
        if force:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        return

    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        return


def _wait_for_exit(pids: list[int], timeout_seconds: float) -> list[int]:
    deadline = time.time() + max(timeout_seconds, 0.0)
    remaining = [pid for pid in pids if _process_exists(pid)]
    while remaining and time.time() < deadline:
        time.sleep(0.1)
        remaining = [pid for pid in remaining if _process_exists(pid)]
    return remaining


def stop_existing_bot(target_pids: list[int] | tuple[int, ...] | set[int] | None = None) -> None:
    pids = sorted(set(find_bot_pids()) | set(_normalize_target_pids(target_pids)))
    if not pids:
        return

    print(f"Stopping existing bot process(es): {' '.join(str(pid) for pid in pids)}")
    for pid in pids:
        _terminate_process(pid, force=False)

    remaining = _wait_for_exit(pids, timeout_seconds=2.0)
    if not remaining:
        return

    print(f"Force-stopping remaining bot process(es): {' '.join(str(pid) for pid in remaining)}")
    for pid in remaining:
        _terminate_process(pid, force=True)

    still_running = _wait_for_exit(remaining, timeout_seconds=2.0)
    if still_running:
        raise RuntimeError(
            "Could not stop existing bot process(es): "
            + " ".join(str(pid) for pid in still_running)
        )


def _active_watched_wallets(watched_wallets: list[str]) -> list[str]:
    if not watched_wallets:
        return []
    conn: sqlite3.Connection | None = None
    try:
        conn = db.get_conn()
        placeholders = ",".join("?" for _ in watched_wallets)
        rows = conn.execute(
            f"""
            SELECT wallet_address, status
            FROM wallet_watch_state
            WHERE LOWER(wallet_address) IN ({placeholders})
            """,
            tuple(wallet.lower() for wallet in watched_wallets),
        ).fetchall()
    except sqlite3.DatabaseError:
        return watched_wallets
    finally:
        if conn is not None:
            conn.close()
    dropped_wallets = {
        str(row["wallet_address"] or "").strip().lower()
        for row in rows
        if str(row["status"] or "").strip().lower() == "dropped"
    }
    return [wallet for wallet in watched_wallets if wallet not in dropped_wallets]


def _wallet_mode_intro_lines(
    wallet_mode: RestartWalletMode,
    *,
    preserve_model_artifact: bool = False,
    preserve_identity_cache: bool = False,
    preserve_telegram_state: bool = False,
) -> tuple[str, ...]:
    preserved_names: list[str] = []
    if preserve_model_artifact:
        preserved_names.append("model artifact")
    if preserve_identity_cache:
        preserved_names.append("identity cache")
    if preserve_telegram_state:
        preserved_names.append("Telegram state")
    if preserved_names:
        reset_line = (
            "Shadow account reset: deleting shadow runtime state, tracker history, signals, positions, "
            "performance snapshots, logs, training-cycle metadata, wallet watch-state memory, events, "
            f"and bot state while preserving {', '.join(preserved_names)}. Config settings stay in place."
        )
    else:
        reset_line = (
            "Full shadow account reset: deleting the entire save directory and all shadow runtime state, "
            "including tracker history, signals, positions, performance snapshots, logs, model artifacts, "
            "training cycles, wallet watch-state memory, events, and bot state. Config settings stay in place."
        )
    if wallet_mode == "keep_active":
        return (
            reset_line,
            "Reducing WATCHED_WALLETS to currently active wallets before restarting shadow mode.",
        )
    if wallet_mode == "clear_all":
        return (
            reset_line,
            "Clearing WATCHED_WALLETS before restarting shadow mode.",
        )
    return (
        reset_line,
        "Preserving WATCHED_WALLETS.",
    )


def _wallet_mode_result_line(wallet_mode: RestartWalletMode) -> str:
    if wallet_mode == "keep_active":
        return "WATCHED_WALLETS reduced to active wallets."
    if wallet_mode == "clear_all":
        return "WATCHED_WALLETS cleared."
    return "WATCHED_WALLETS preserved."


def apply_wallet_mode_for_reset(wallet_mode: str) -> tuple[RestartWalletMode, str, bool]:
    normalized_wallet_mode = _normalize_wallet_mode(wallet_mode)
    previous_wallets = _read_env_value("WATCHED_WALLETS")
    wallets_updated = False
    if normalized_wallet_mode == "keep_active":
        active_wallets = _active_watched_wallets(_parse_watched_wallets(previous_wallets))
        _write_env_value("WATCHED_WALLETS", _serialize_watched_wallets(active_wallets))
        wallets_updated = True
    elif normalized_wallet_mode == "clear_all":
        _write_env_value("WATCHED_WALLETS", "")
        wallets_updated = True
    return normalized_wallet_mode, previous_wallets, wallets_updated


def restore_watched_wallets(previous_wallets: str) -> None:
    _write_env_value("WATCHED_WALLETS", previous_wallets)


def _preserve_reset_files(paths: list[Path], temp_root: Path) -> list[tuple[Path, Path]]:
    preserved: list[tuple[Path, Path]] = []
    for source_path in paths:
        if not source_path.exists():
            continue
        try:
            relative_path = source_path.relative_to(SAVE_DIR)
        except ValueError:
            relative_path = Path(source_path.name)
        preserved_path = temp_root / relative_path
        preserved_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_dir():
            shutil.copytree(source_path, preserved_path, dirs_exist_ok=True)
        else:
            shutil.copy2(source_path, preserved_path)
        preserved.append((preserved_path, source_path))
    return preserved


def _restore_reset_files(preserved: list[tuple[Path, Path]]) -> None:
    for preserved_path, target_path in preserved:
        if not preserved_path.exists():
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if preserved_path.is_dir():
            shutil.copytree(preserved_path, target_path, dirs_exist_ok=True)
        else:
            shutil.copy2(preserved_path, target_path)


def reset_shadow_runtime(
    *,
    preserve_model_artifact: bool = False,
    preserve_identity_cache: bool = False,
    preserve_telegram_state: bool = False,
) -> None:
    paths_to_preserve: list[Path] = []
    if preserve_model_artifact:
        paths_to_preserve.append(MODEL_ARTIFACT_PATH)
    if preserve_identity_cache:
        paths_to_preserve.append(IDENTITY_CACHE_PATH)
    if preserve_telegram_state:
        paths_to_preserve.append(TELEGRAM_STATE_FILE)

    preserved_files: list[tuple[Path, Path]] = []
    with tempfile.TemporaryDirectory(prefix="kelly-shadow-reset-") as tmpdir:
        if paths_to_preserve:
            preserved_files = _preserve_reset_files(paths_to_preserve, Path(tmpdir))

        try:
            shutil.rmtree(SAVE_DIR)
        except FileNotFoundError:
            pass

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            db.init_db()
        finally:
            _restore_reset_files(preserved_files)
        write_shadow_evidence_epoch(
            path=DATA_DIR / "shadow_evidence_epoch.json",
            source="shadow_reset",
            message="fresh shadow evidence epoch started after full shadow reset",
        )
    try:
        from kelly_watcher.engine.beliefs import invalidate_belief_cache

        invalidate_belief_cache()
    except Exception:
        pass


def runtime_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    temp_root = Path(tempfile.gettempdir())
    env.setdefault("UV_CACHE_DIR", str(temp_root / "uv-cache"))
    env.setdefault("PYTHONPYCACHEPREFIX", str(temp_root / "kelly-watcher-pycache"))
    return env


def preferred_python_executable() -> str:
    if os.name == "nt":
        candidates = [
            REPO_ROOT / ".venv" / "Scripts" / "python.exe",
            REPO_ROOT / ".venv" / "Scripts" / "python",
        ]
    else:
        candidates = [
            REPO_ROOT / ".venv" / "bin" / "python",
            REPO_ROOT / ".venv" / "bin" / "python3",
        ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _bot_command() -> list[str]:
    command = [preferred_python_executable(), str(REPO_ROOT / "main.py")]
    env_flag = active_env_flag()
    if env_flag:
        command.append(env_flag)
    return command


def exec_restarted_bot() -> None:
    os.execvpe(_bot_command()[0], _bot_command(), runtime_env())


def launch_background_bot() -> int:
    env = runtime_env()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKGROUND_LOG.parent.mkdir(parents=True, exist_ok=True)

    log_handle = BACKGROUND_LOG.open("w", encoding="utf-8")
    popen_kwargs: dict[str, object] = {
        "cwd": str(REPO_ROOT),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
    }
    try:
        if os.name == "nt":
            creationflags = 0
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creationflags:
                popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(_bot_command(), **popen_kwargs)
    finally:
        log_handle.close()
    PID_FILE.write_text(f"{process.pid}\n", encoding="utf-8")
    return int(process.pid)


def _launch_background_bot_verified() -> int:
    pid = launch_background_bot()
    time.sleep(1.5)
    if not _process_exists(pid):
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass
        raise RuntimeError(
            "Shadow bot exited immediately after restart. Check save/logs/shadow_runtime.out for details."
        )
    return pid


def run(
    *,
    foreground: bool,
    start_bot: bool,
    wallet_mode: str = "keep_all",
    clear_wallets: bool | None = None,
    delay_seconds: float = 0.0,
    target_pids: list[int] | tuple[int, ...] | set[int] | None = None,
    preserve_model_artifact: bool = False,
    preserve_identity_cache: bool = False,
    preserve_telegram_state: bool = False,
) -> int:
    if use_real_money():
        print("Refusing to reset while USE_REAL_MONEY=true. Switch back to shadow mode first.")
        return 1

    normalized_wallet_mode = _normalize_wallet_mode(wallet_mode, clear_wallets=clear_wallets)
    normalized_delay_seconds = max(float(delay_seconds or 0.0), 0.0)
    bankroll = shadow_bankroll_usd()
    previous_wallets = _read_env_value("WATCHED_WALLETS")
    wallets_updated = False

    try:
        if normalized_delay_seconds > 0:
            print(f"Waiting {normalized_delay_seconds:.2f}s before stopping the current bot...")
            time.sleep(normalized_delay_seconds)
        stop_existing_bot(target_pids=target_pids)
        normalized_wallet_mode, previous_wallets, wallets_updated = apply_wallet_mode_for_reset(
            normalized_wallet_mode
        )

        if preserve_model_artifact or preserve_identity_cache or preserve_telegram_state:
            print(
                "Resetting shadow account runtime while preserving selected local artifacts "
                f"and returning to the configured bankroll of ${bankroll:.2f}..."
            )
        else:
            print(
                f"Resetting shadow account by deleting the entire save directory and returning to the configured bankroll of ${bankroll:.2f}..."
            )
        for line in _wallet_mode_intro_lines(
            normalized_wallet_mode,
            preserve_model_artifact=preserve_model_artifact,
            preserve_identity_cache=preserve_identity_cache,
            preserve_telegram_state=preserve_telegram_state,
        ):
            print(line)
        if preserve_model_artifact or preserve_identity_cache or preserve_telegram_state:
            reset_shadow_runtime(
                preserve_model_artifact=preserve_model_artifact,
                preserve_identity_cache=preserve_identity_cache,
                preserve_telegram_state=preserve_telegram_state,
            )
        else:
            reset_shadow_runtime()

        if not start_bot:
            print("Shadow runtime reset.")
            print(f"Initial bankroll: ${bankroll:.2f}")
            print(_wallet_mode_result_line(normalized_wallet_mode))
            print("Start the bot manually with: uv run main")
            return 0

        if foreground:
            print("Starting shadow bot in foreground...")
            result = subprocess.run(
                _bot_command(),
                cwd=REPO_ROOT,
                env=runtime_env(),
                check=False,
            )
            return int(result.returncode)

        print("Starting shadow bot in background...")
        pid = _launch_background_bot_verified()
        print("Shadow bot restarted.")
        print(f"PID: {pid}")
        print(f"Initial bankroll: ${bankroll:.2f}")
        print(_wallet_mode_result_line(normalized_wallet_mode))
        print(f"Background log: {BACKGROUND_LOG.relative_to(REPO_ROOT)}")
        print(f"PID file: {PID_FILE.relative_to(REPO_ROOT)}")
        return 0
    except Exception:
        if wallets_updated:
            try:
                restore_watched_wallets(previous_wallets)
            except OSError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset shadow trading runtime state and restart the bot."
    )
    add_env_profile_flags(parser)
    start_mode = parser.add_mutually_exclusive_group()
    start_mode.add_argument(
        "--foreground",
        action="store_true",
        help="Run the restarted bot in the foreground instead of detaching it.",
    )
    start_mode.add_argument(
        "--reset-only",
        action="store_true",
        help="Reset shadow runtime state without starting the bot.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.0,
        help="Wait this many seconds before stopping the current bot.",
    )
    parser.add_argument(
        "--target-pid",
        action="append",
        default=[],
        help="Specific bot PID to stop before resetting. May be passed multiple times.",
    )
    parser.add_argument(
        "--preserve-model",
        action="store_true",
        help="Keep save/model.joblib while resetting shadow account PnL and runtime state.",
    )
    parser.add_argument(
        "--preserve-identity-cache",
        action="store_true",
        help="Keep save/data/identity_cache.json while resetting shadow runtime state.",
    )
    parser.add_argument(
        "--preserve-telegram-state",
        action="store_true",
        help="Keep save/data/telegram_state.json while resetting shadow runtime state.",
    )
    wallet_mode_group = parser.add_mutually_exclusive_group()
    wallet_mode_group.add_argument(
        "--keep-active-wallets",
        action="store_true",
        help="Reduce WATCHED_WALLETS to currently active wallets before restarting shadow mode.",
    )
    wallet_mode_group.add_argument(
        "--clear-wallets",
        action="store_true",
        help="Clear WATCHED_WALLETS in config.env before restarting shadow mode.",
    )
    args = parser.parse_args(argv)
    wallet_mode: RestartWalletMode = (
        "keep_active" if args.keep_active_wallets else "clear_all" if args.clear_wallets else "keep_all"
    )
    return run(
        foreground=bool(args.foreground),
        start_bot=not bool(args.reset_only),
        wallet_mode=wallet_mode,
        delay_seconds=float(args.delay_seconds or 0.0),
        target_pids=[int(pid) for pid in args.target_pid],
        preserve_model_artifact=bool(args.preserve_model),
        preserve_identity_cache=bool(args.preserve_identity_cache),
        preserve_telegram_state=bool(args.preserve_telegram_state),
    )


if __name__ == "__main__":
    raise SystemExit(main())
