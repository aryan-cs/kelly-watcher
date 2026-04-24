from __future__ import annotations

import sys

from kelly_watcher.env_profile import apply_local_runtime_overrides, local_mode_requested


class _BotMainProxy:
    def main(self) -> None:
        from kelly_watcher import main as real_bot_main

        real_bot_main.main()


bot_main = _BotMainProxy()


def main() -> None:
    # Accept and ignore stray extra args so accidental trailing tokens still
    # launches the bot instead of failing on command parsing.
    raw_argv = sys.argv[1:]
    if local_mode_requested(raw_argv):
        apply_local_runtime_overrides()
    sys.argv = [sys.argv[0], *[arg for arg in raw_argv if arg == "--local"]]

    bot_main.main()
