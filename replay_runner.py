from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from replay import ReplayPolicy, run_replay


def _load_policy(args: argparse.Namespace) -> ReplayPolicy:
    payload: dict | None = None
    if args.policy_file:
        payload = json.loads(Path(args.policy_file).read_text(encoding="utf-8"))
    elif args.policy_json:
        payload = json.loads(args.policy_json)
    return ReplayPolicy.from_payload(payload)


def _compact_segment_value(segment_kind: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if segment_kind == "trader_address" and text.startswith("0x") and len(text) > 12:
        return f"{text[:6]}..{text[-4:]}"
    return text


def _format_segment_row(segment_kind: str, row: dict[str, Any]) -> str:
    label = _compact_segment_value(segment_kind, row.get("segment_value"))
    pnl = float(row.get("total_pnl_usd") or 0.0)
    accepted = int(row.get("accepted_count") or 0)
    resolved = int(row.get("resolved_count") or 0)
    win_rate = row.get("win_rate")
    win_rate_text = f", win {float(win_rate) * 100:.1f}%" if win_rate is not None else ""
    return f"{label} ({pnl:+.3f}, acc {accepted}, res {resolved}{win_rate_text})"


def _print_segment_summary(result: dict[str, Any]) -> None:
    leaders = result.get("segment_leaders") or {}
    if not isinstance(leaders, dict) or not leaders:
        print("Replay segment leaders: none", file=sys.stderr)
        return

    preferred_order = ["signal_mode", "trader_address", "entry_price_band", "time_to_close_band", "source_status"]
    segment_kinds = [kind for kind in preferred_order if kind in leaders]
    segment_kinds.extend(sorted(kind for kind in leaders if kind not in preferred_order))

    print("Replay segment leaders:", file=sys.stderr)
    for segment_kind in segment_kinds:
        entry = leaders.get(segment_kind)
        if not isinstance(entry, dict):
            continue
        best = entry.get("best")
        worst = entry.get("worst")
        if not isinstance(best, dict) or not isinstance(worst, dict):
            continue
        print(
            f"  {segment_kind}: best {_format_segment_row(segment_kind, best)} | worst {_format_segment_row(segment_kind, worst)}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay historical trade_log rows under a policy.")
    parser.add_argument("--db", default="", help="Path to a trading.db snapshot. Defaults to the runtime DB.")
    parser.add_argument("--label", default="", help="Optional label to store with the replay run.")
    parser.add_argument("--notes", default="", help="Optional notes to store with the replay run.")
    parser.add_argument("--policy-file", default="", help="JSON file containing replay policy overrides.")
    parser.add_argument("--policy-json", default="", help="Inline JSON payload containing replay policy overrides.")
    parser.add_argument("--start-ts", type=int, default=0, help="Optional lower bound on trade placed_at timestamps (inclusive).")
    parser.add_argument("--end-ts", type=int, default=0, help="Optional upper bound on trade placed_at timestamps (exclusive).")
    args = parser.parse_args()

    policy = _load_policy(args)
    result = run_replay(
        policy=policy,
        db_path=Path(args.db) if args.db else None,
        label=args.label,
        notes=args.notes,
        start_ts=args.start_ts or None,
        end_ts=args.end_ts or None,
    )
    print(json.dumps({"policy": policy.as_dict(), "result": result}, indent=2, sort_keys=True))
    print(file=sys.stderr)
    _print_segment_summary(result)


if __name__ == "__main__":
    main()
