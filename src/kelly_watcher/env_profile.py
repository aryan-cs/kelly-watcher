from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
LEGACY_ENV_PATH = REPO_ROOT / ".env"
ENV_PROFILE_ENV_VAR = "KELLY_ENV"
DEFAULT_ENV_PROFILE = "default"
SUPPORTED_ENV_PROFILES = ("default",)
SAVE_ENV_PATH = REPO_ROOT / "save" / ".env"
REPO_ENV_PATH = REPO_ROOT / ".env"

ENV_ONLY_KEYS = frozenset(
    {
        "POLYGON_PRIVATE_KEY",
        "POLYGON_WALLET_ADDRESS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DASHBOARD_API_HOST",
        "DASHBOARD_API_PORT",
        "DASHBOARD_API_TOKEN",
        "DASHBOARD_WEB_URL",
        "KELLY_API_BASE_URL",
        "KELLY_API_TOKEN",
        "KELLY_RUNTIME_STDIO_LOG_PATH",
        "KELLY_RUNTIME_STDIO_LOG_MAX_BYTES",
        "KELLY_RUNTIME_STDIO_LOG_BACKUPS",
    }
)
BOOTSTRAP_ENV_KEYS = frozenset({"WATCHED_WALLETS"})
_BOOTSTRAP_COMPLETE = False


def _normalize_profile(_value: str | None) -> str:
    return DEFAULT_ENV_PROFILE


def profile_from_argv(argv: list[str] | tuple[str, ...] | None = None) -> str | None:
    del argv
    return None


def profile_from_environ(environ: dict[str, str] | None = None) -> str | None:
    del environ
    return DEFAULT_ENV_PROFILE


def active_env_profile(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    del argv, environ
    return DEFAULT_ENV_PROFILE


def save_dir_for_repo(repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / "save"


def env_path_for_profile(_profile: str, repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / ".env"


def repo_env_path_for_profile(_profile: str, repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / ".env"


def ensure_persistent_env_path(
    profile: str = DEFAULT_ENV_PROFILE,
    repo_root: Path = REPO_ROOT,
) -> Path:
    preferred = env_path_for_profile(profile, repo_root)
    if preferred.exists():
        return preferred

    legacy_save_env = save_dir_for_repo(repo_root) / ".env"
    source = legacy_save_env if legacy_save_env.exists() else None
    if source is None or source.resolve() == preferred.resolve():
        return preferred

    preferred.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return preferred


def active_env_path(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> Path:
    del argv, environ
    return env_path_for_profile(DEFAULT_ENV_PROFILE, repo_root)


def active_env_flag(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    del argv, environ
    return ""


def init_env_profile(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    *,
    override: bool = False,
) -> tuple[str, Path]:
    global _BOOTSTRAP_COMPLETE

    profile = active_env_profile(argv, environ)
    ensure_persistent_env_path(profile)
    path = active_env_path(argv, environ)
    os.environ[ENV_PROFILE_ENV_VAR] = profile
    if path.exists():
        load_dotenv(path, override=override)
    if not _BOOTSTRAP_COMPLETE:
        try:
            from kelly_watcher.data.db import bootstrap_runtime_settings_from_env

            bootstrap_runtime_settings_from_env(
                path,
                env_only_keys=ENV_ONLY_KEYS,
                bootstrap_env_keys=BOOTSTRAP_ENV_KEYS,
            )
        except Exception:
            pass
        _BOOTSTRAP_COMPLETE = True
    return profile, path


def add_env_profile_flags(parser: argparse.ArgumentParser) -> None:
    del parser
    return None
