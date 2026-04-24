from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

import kelly_watcher.cli as cli


class CliTest(unittest.TestCase):
    def test_main_launcher_ignores_extra_args_and_invokes_bot(self) -> None:
        original_argv = sys.argv[:]

        with patch.object(cli.bot_main, "main") as bot_main:
            sys.argv = ["main", "pytho0n.py"]
            cli.main()

        self.assertEqual(sys.argv, ["main"])
        bot_main.assert_called_once_with()

        sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
