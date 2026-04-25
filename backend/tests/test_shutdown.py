from __future__ import annotations

import signal
import threading
import unittest
from unittest.mock import patch

from kelly_watcher import main as bot_main


class ShutdownSignalTests(unittest.TestCase):
    def test_first_shutdown_signal_sets_event_and_interrupts_main_thread(self) -> None:
        installed_handlers: dict[int, object] = {}

        def install_handler(signum: int, handler: object) -> object:
            installed_handlers[int(signum)] = handler
            return None

        stop_event = threading.Event()
        with (
            patch.object(bot_main.signal, "getsignal", return_value=None),
            patch.object(bot_main.signal, "signal", side_effect=install_handler),
        ):
            bot_main._install_shutdown_signal_handlers(stop_event)

        handler = installed_handlers[int(signal.SIGINT)]
        self.assertFalse(stop_event.is_set())

        with self.assertRaises(KeyboardInterrupt):
            handler(int(signal.SIGINT), None)  # type: ignore[operator]

        self.assertTrue(stop_event.is_set())

    def test_second_shutdown_signal_forces_process_exit(self) -> None:
        installed_handlers: dict[int, object] = {}

        def install_handler(signum: int, handler: object) -> object:
            installed_handlers[int(signum)] = handler
            return None

        stop_event = threading.Event()
        with (
            patch.object(bot_main.signal, "getsignal", return_value=None),
            patch.object(bot_main.signal, "signal", side_effect=install_handler),
        ):
            bot_main._install_shutdown_signal_handlers(stop_event)

        handler = installed_handlers[int(signal.SIGINT)]
        with self.assertRaises(KeyboardInterrupt):
            handler(int(signal.SIGINT), None)  # type: ignore[operator]

        with patch.object(bot_main.os, "_exit", side_effect=SystemExit) as forced_exit:
            with self.assertRaises(SystemExit):
                handler(int(signal.SIGINT), None)  # type: ignore[operator]

        forced_exit.assert_called_once_with(128 + int(signal.SIGINT))


if __name__ == "__main__":
    unittest.main()
