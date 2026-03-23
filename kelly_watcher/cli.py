from __future__ import annotations

import importlib
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    # Accept and ignore stray extra args so `uv run main pytho0n.py` still
    # launches the bot instead of failing on command parsing, but preserve
    # the env-profile flags used to select .env.dev or .env.prod.
    preserved_args = [arg for arg in sys.argv[1:] if arg in {"--dev", "--prod"}]
    sys.argv = [sys.argv[0], *preserved_args]

    bot = importlib.import_module("main")
    bot.main()
