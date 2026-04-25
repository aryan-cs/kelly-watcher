from __future__ import annotations

import inspect

from kelly_watcher import main


def test_main_poll_loop_does_not_refresh_watchlist_on_each_poll() -> None:
    source = inspect.getsource(main.main)

    assert 'poll_stage="selecting_poll_batches"' in source
    assert "watchlist.refresh(run_auto_drop=False)" not in source
    assert "watchlist.poll_batches()" in source
