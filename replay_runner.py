from __future__ import annotations

import argparse
import json
from pathlib import Path

from replay import ReplayPolicy, run_replay


def _load_policy(args: argparse.Namespace) -> ReplayPolicy:
    payload: dict | None = None
    if args.policy_file:
        payload = json.loads(Path(args.policy_file).read_text(encoding="utf-8"))
    elif args.policy_json:
        payload = json.loads(args.policy_json)
    return ReplayPolicy.from_payload(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay historical trade_log rows under a policy.")
    parser.add_argument("--db", default="", help="Path to a trading.db snapshot. Defaults to the runtime DB.")
    parser.add_argument("--label", default="", help="Optional label to store with the replay run.")
    parser.add_argument("--notes", default="", help="Optional notes to store with the replay run.")
    parser.add_argument("--policy-file", default="", help="JSON file containing replay policy overrides.")
    parser.add_argument("--policy-json", default="", help="Inline JSON payload containing replay policy overrides.")
    args = parser.parse_args()

    policy = _load_policy(args)
    result = run_replay(
        policy=policy,
        db_path=Path(args.db) if args.db else None,
        label=args.label,
        notes=args.notes,
    )
    print(json.dumps({"policy": policy.as_dict(), "result": result}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
