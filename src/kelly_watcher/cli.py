from __future__ import annotations

import sys

from kelly_watcher import main as bot_main


def main() -> None:
    # Accept and ignore stray extra args so `uv run main pytho0n.py` still
    # launches the bot instead of failing on command parsing.
    sys.argv = [sys.argv[0]]

    bot_main.main()
