from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import cli


class CliTest(unittest.TestCase):
    def test_main_launcher_ignores_extra_args_and_invokes_bot(self) -> None:
        original_argv = sys.argv[:]
        repo_root = str(Path(cli.__file__).resolve().parent)
        path_was_present = repo_root in sys.path

        bot_module = types.SimpleNamespace(main=lambda: None)
        with patch.object(bot_module, "main") as bot_main, patch(
            "importlib.import_module", return_value=bot_module
        ) as import_module:
            sys.argv = ["main", "pytho0n.py"]
            cli.main()

        self.assertEqual(sys.argv, ["main"])
        import_module.assert_called_once_with("main")
        bot_main.assert_called_once_with()

        if not path_was_present and repo_root in sys.path:
            sys.path.remove(repo_root)
        sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
