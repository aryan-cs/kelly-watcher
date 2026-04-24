from __future__ import annotations

import importlib
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    # Accept and ignore stray extra args so `uv run main pytho0n.py` still
    # launches the bot instead of failing on command parsing.
    sys.argv = [sys.argv[0]]

    bot = importlib.import_module("main")
    bot.main()
