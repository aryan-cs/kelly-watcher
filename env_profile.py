from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
LEGACY_ENV_PATH = REPO_ROOT / ".env"
ENV_PROFILE_ENV_VAR = "KELLY_ENV"
DEFAULT_ENV_PROFILE = "default"
SUPPORTED_ENV_PROFILES = (DEFAULT_ENV_PROFILE,)


def profile_from_argv(argv: list[str] | tuple[str, ...] | None = None) -> str | None:
    del argv
    return None


def profile_from_environ(environ: dict[str, str] | None = None) -> str | None:
    del environ
    return None


def active_env_profile(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    del argv, environ
    return DEFAULT_ENV_PROFILE


def save_dir_for_repo(repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / "save"


def env_path_for_profile(profile: str, repo_root: Path = REPO_ROOT) -> Path:
    del profile
    return repo_root / ".env"


def repo_env_path_for_profile(profile: str, repo_root: Path = REPO_ROOT) -> Path:
    del profile
    return repo_root / ".env"


def ensure_persistent_env_path(
    profile: str,
    repo_root: Path = REPO_ROOT,
) -> Path:
    del profile
    return repo_root / ".env"


def active_env_path(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> Path:
    del argv, environ
    return repo_root / ".env"


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
    del argv, environ
    profile = DEFAULT_ENV_PROFILE
    path = active_env_path()
    os.environ[ENV_PROFILE_ENV_VAR] = profile
    if path.exists():
        load_dotenv(path, override=override)
    return profile, path


def add_env_profile_flags(parser: argparse.ArgumentParser) -> None:
    # Older commands may still pass these flags. Keep accepting them as no-ops,
    # but do not create or read .env.dev/.env.prod anymore.
    parser.add_argument("--dev", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prod", action="store_true", help=argparse.SUPPRESS)
