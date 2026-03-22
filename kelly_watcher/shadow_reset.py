from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from config import shadow_bankroll_usd, use_real_money
from db import init_db

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
LOG_DIR = REPO_ROOT / "logs"
ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
PID_FILE = DATA_DIR / "shadow_bot.pid"
BACKGROUND_LOG = LOG_DIR / "shadow_runtime.out"
RESET_FILES = (
    DATA_DIR / "trading.db",
    DATA_DIR / "trading.db-shm",
    DATA_DIR / "trading.db-wal",
    DATA_DIR / "events.jsonl",
    DATA_DIR / "bot_state.json",
    PID_FILE,
)


def _source_env_path() -> Path:
    return ENV_PATH if ENV_PATH.exists() else ENV_EXAMPLE_PATH


def _read_env_value(key: str) -> str:
    try:
        lines = _source_env_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        current_key, value = line.split("=", 1)
        if current_key.strip() == key:
            return value.strip()
    return ""


def _write_env_value(key: str, value: str) -> None:
    source_path = _source_env_path()
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

    ENV_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")


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
        "-m kelly_watcher.cli",
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


def stop_existing_bot() -> None:
    pids = find_bot_pids()
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


def reset_shadow_runtime() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for path in RESET_FILES:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
    init_db()


def runtime_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    temp_root = Path(tempfile.gettempdir())
    env.setdefault("UV_CACHE_DIR", str(temp_root / "uv-cache"))
    env.setdefault("PYTHONPYCACHEPREFIX", str(temp_root / "kelly-watcher-pycache"))
    return env


def _bot_command() -> list[str]:
    return [sys.executable, str(REPO_ROOT / "main.py")]


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


def run(*, foreground: bool, start_bot: bool, clear_wallets: bool) -> int:
    if use_real_money():
        print("Refusing to reset while USE_REAL_MONEY=true. Switch back to shadow mode first.")
        return 1

    bankroll = shadow_bankroll_usd()
    previous_wallets = _read_env_value("WATCHED_WALLETS")

    try:
        if clear_wallets:
            _write_env_value("WATCHED_WALLETS", "")

        stop_existing_bot()
        print(f"Resetting shadow runtime state back to the configured bankroll of ${bankroll:.2f}...")
        if clear_wallets:
            print("Preserving config/settings files, logs, model artifacts, and identity cache.")
            print("Clearing WATCHED_WALLETS before restarting shadow mode.")
        else:
            print("Preserving config/settings files, logs, model artifacts, identity cache, and WATCHED_WALLETS.")
        reset_shadow_runtime()

        if not start_bot:
            print("Shadow runtime reset.")
            print(f"Initial bankroll: ${bankroll:.2f}")
            print("WATCHED_WALLETS cleared." if clear_wallets else "WATCHED_WALLETS preserved.")
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
        pid = launch_background_bot()
        print("Shadow bot restarted.")
        print(f"PID: {pid}")
        print(f"Initial bankroll: ${bankroll:.2f}")
        print("WATCHED_WALLETS cleared." if clear_wallets else "WATCHED_WALLETS preserved.")
        print(f"Background log: {BACKGROUND_LOG.relative_to(REPO_ROOT)}")
        print(f"PID file: {PID_FILE.relative_to(REPO_ROOT)}")
        return 0
    except Exception:
        if clear_wallets:
            try:
                _write_env_value("WATCHED_WALLETS", previous_wallets)
            except OSError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset shadow trading runtime state and restart the bot."
    )
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
        "--clear-wallets",
        action="store_true",
        help="Clear WATCHED_WALLETS in .env before restarting shadow mode.",
    )
    args = parser.parse_args(argv)
    return run(
        foreground=bool(args.foreground),
        start_bot=not bool(args.reset_only),
        clear_wallets=bool(args.clear_wallets),
    )


if __name__ == "__main__":
    raise SystemExit(main())
