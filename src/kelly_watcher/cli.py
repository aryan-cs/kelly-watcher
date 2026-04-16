from __future__ import annotations

import sys

from kelly_watcher import main as bot_main


def main() -> None:
    # Accept and ignore stray extra args so `uv run main pytho0n.py` still
    # launches the bot instead of failing on command parsing, but preserve
    # the env-profile flags used to select .env.dev or .env.prod.
    preserved_args = [arg for arg in sys.argv[1:] if arg in {"--dev", "--prod"}]
    sys.argv = [sys.argv[0], *preserved_args]

    bot_main.main()
