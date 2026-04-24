from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE_PATH = REPO_ROOT / "kelly-config.env.example"
CONFIG_ENV_PATH = REPO_ROOT / "kelly-config.env"
SECRETS_ENV_PATH = REPO_ROOT / "kelly-secrets.env"
ENV_PROFILE_ENV_VAR = "KELLY_ENV"
LOCAL_MODE_ENV_VAR = "KELLY_LOCAL_MODE"
DISABLE_ENV_FILE_LOADING_ENV_VAR = "KELLY_DISABLE_ENV_FILE_LOADING"
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
    return repo_root / "kelly-config.env"


def secrets_env_path_for_profile(profile: str, repo_root: Path = REPO_ROOT) -> Path:
    del profile
    return repo_root / "kelly-secrets.env"


def env_paths_for_profile(profile: str, repo_root: Path = REPO_ROOT) -> tuple[Path, Path]:
    return (
        env_path_for_profile(profile, repo_root),
        secrets_env_path_for_profile(profile, repo_root),
    )


def repo_env_path_for_profile(profile: str, repo_root: Path = REPO_ROOT) -> Path:
    return env_path_for_profile(profile, repo_root)


def ensure_persistent_env_path(
    profile: str,
    repo_root: Path = REPO_ROOT,
) -> Path:
    return env_path_for_profile(profile, repo_root)


def active_env_path(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> Path:
    del argv, environ
    return repo_root / "kelly-config.env"


def active_env_paths(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> tuple[Path, Path]:
    profile = active_env_profile(argv, environ)
    return env_paths_for_profile(profile, repo_root)


def active_env_flag(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    return "--local" if local_mode_requested(argv, environ) else ""


def env_file_loading_disabled() -> bool:
    return os.getenv(DISABLE_ENV_FILE_LOADING_ENV_VAR, "").lower() in {"1", "true", "yes", "on"}


def _runtime_argv(argv: list[str] | tuple[str, ...] | None = None) -> list[str]:
    if argv is not None:
        return list(argv)
    return list(sys.argv[1:])


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def local_mode_requested(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
) -> bool:
    env = os.environ if environ is None else environ
    return "--local" in _runtime_argv(argv) or _truthy(env.get(LOCAL_MODE_ENV_VAR))


def apply_local_runtime_overrides(environ: dict[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    env[LOCAL_MODE_ENV_VAR] = "1"
    port = str(env.get("DASHBOARD_API_PORT") or "8765").strip() or "8765"
    env["DASHBOARD_API_HOST"] = "127.0.0.1"
    env["KELLY_API_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["DASHBOARD_WEB_URL"] = f"http://127.0.0.1:{port}"
    if not str(env.get("KELLY_API_TOKEN") or "").strip() and str(env.get("DASHBOARD_API_TOKEN") or "").strip():
        env["KELLY_API_TOKEN"] = str(env["DASHBOARD_API_TOKEN"]).strip()
    if not str(env.get("DASHBOARD_API_TOKEN") or "").strip() and str(env.get("KELLY_API_TOKEN") or "").strip():
        env["DASHBOARD_API_TOKEN"] = str(env["KELLY_API_TOKEN"]).strip()


def init_env_profile(
    argv: list[str] | tuple[str, ...] | None = None,
    environ: dict[str, str] | None = None,
    *,
    override: bool = False,
) -> tuple[str, Path]:
    del environ
    profile = DEFAULT_ENV_PROFILE
    paths = active_env_paths(repo_root=REPO_ROOT)
    os.environ[ENV_PROFILE_ENV_VAR] = profile
    if env_file_loading_disabled():
        return profile, paths[0]
    for path in paths:
        if path.exists():
            load_dotenv(path, override=override)
    if local_mode_requested(argv, os.environ):
        apply_local_runtime_overrides(os.environ)
    return profile, paths[0]


def add_env_profile_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--local", action="store_true", help="Run backend/dashboard API on localhost without editing env files.")
    # Older commands may still pass these flags. Keep accepting them as no-ops,
    # but do not create or read .env.dev/.env.prod anymore.
    parser.add_argument("--dev", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--prod", action="store_true", help=argparse.SUPPRESS)
