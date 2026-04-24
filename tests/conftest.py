from __future__ import annotations

import os
from pathlib import Path


# The repository now intentionally tracks config.env for deployable non-secret
# settings. Unit tests must stay isolated from those live/tuned settings.
os.environ["KELLY_DISABLE_ENV_FILE_LOADING"] = "1"
os.environ.setdefault("WATCHED_WALLETS", "0x0000000000000000000000000000000000000001")

import kelly_watcher.config as _config  # noqa: E402

_config.ENV_PATH = Path("/tmp/kelly-watcher-tests-no-config.env")
os.environ.pop("KELLY_DISABLE_ENV_FILE_LOADING", None)
