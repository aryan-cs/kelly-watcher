from __future__ import annotations

import json
import logging

from kelly_watcher.research.train import train


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
    metrics = train()
    print(json.dumps(metrics, indent=2, default=str))
