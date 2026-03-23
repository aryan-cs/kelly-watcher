from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
LEGACY_ENV_PATH = REPO_ROOT / ".env"
ENV_PROFILE_ENV_VAR = "KELLY_ENV"
DEFAULT_ENV_PROFILE = "dev"
SUPPORTED_ENV_PROFILES = ("dev", "prod")


def _normalize_profile(value: str | None) -> str | None:
    profile = str(value or "").strip().lower()
    return profile if profile in SUPPORTED_ENV_PROFILES else None


def profile_from_argv(argv: list[str] | tuple[str, ...] | None = None) -> str | None:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    wants_dev = "--dev" in raw_args
    wants_prod = "--prod" in raw_args
    if wants_dev and wants_prod:
        raise ValueError("Pass only one of --dev or --prod.")
    if wants_prod:
        return "prod"
    if wants_dev:
        return "dev"
    return None


def profile_from_environ(environ: dict[str, str] | None = None) -> str | None:
    return _normalize_profile((environ or os.environ).get(ENV_PROFILE_ENV_VAR))


def active_env_profile(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    del environ
    return profile_from_argv(argv) or DEFAULT_ENV_PROFILE


def env_path_for_profile(profile: str, repo_root: Path = REPO_ROOT) -> Path:
    normalized = _normalize_profile(profile)
    if not normalized:
        raise ValueError(f"Unsupported env profile: {profile!r}")
    return repo_root / f".env.{normalized}"


def active_env_path(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> Path:
    profile = active_env_profile(argv, environ)
    preferred = env_path_for_profile(profile, repo_root)
    legacy = repo_root / LEGACY_ENV_PATH.name
    if preferred.exists():
        return preferred
    if profile == "dev" and legacy.exists():
        return legacy
    return preferred


def active_env_flag(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    return f"--{active_env_profile(argv, environ)}"


def init_env_profile(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    *,
    override: bool = False,
) -> tuple[str, Path]:
    profile = active_env_profile(argv, environ)
    path = active_env_path(argv, environ)
    os.environ[ENV_PROFILE_ENV_VAR] = profile
    if path.exists():
        load_dotenv(path, override=override)
    return profile, path


def add_env_profile_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dev",
        action="store_true",
        help="Use .env.dev for config (default).",
    )
    group.add_argument(
        "--prod",
        action="store_true",
        help="Use .env.prod for config.",
    )
