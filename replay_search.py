from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from typing import Any

from replay import ReplayPolicy, run_replay


def _load_payload(*, file_path: str, inline_json: str) -> dict[str, Any] | None:
    if file_path:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    if inline_json:
        return json.loads(inline_json)
    return None


def _load_base_policy(args: argparse.Namespace) -> ReplayPolicy:
    payload = _load_payload(file_path=args.base_policy_file, inline_json=args.base_policy_json)
    return ReplayPolicy.from_payload(payload)


def _load_grid(args: argparse.Namespace) -> dict[str, list[Any]]:
    payload = _load_payload(file_path=args.grid_file, inline_json=args.grid_json)
    if payload is None:
        raise ValueError("A grid payload is required via --grid-file or --grid-json")
    if not isinstance(payload, dict):
        raise ValueError("Grid payload must be a JSON object")

    base_keys = ReplayPolicy.default().as_dict().keys()
    grid: dict[str, list[Any]] = {}
    for key, value in payload.items():
        if key not in base_keys:
            raise ValueError(f"Unknown replay policy key in grid: {key}")
        if isinstance(value, list):
            values = value
        else:
            values = [value]
        if not values:
            raise ValueError(f"Grid key {key} must have at least one value")
        grid[str(key)] = values
    if not grid:
        raise ValueError("Grid payload must include at least one varying key")
    return grid


def _iter_policy_overrides(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    return [
        dict(zip(keys, combo, strict=True))
        for combo in itertools.product(*(grid[key] for key in keys))
    ]


def _score_result(result: dict[str, Any], *, initial_bankroll_usd: float, drawdown_penalty: float) -> float:
    pnl = float(result.get("total_pnl_usd") or 0.0)
    max_drawdown_pct = float(result.get("max_drawdown_pct") or 0.0)
    return pnl - (initial_bankroll_usd * drawdown_penalty * max_drawdown_pct)


def _compact_override_summary(payload: dict[str, Any]) -> str:
    if not payload:
        return "default"
    parts = [f"{key}={payload[key]}" for key in sorted(payload)]
    return ", ".join(parts)


def _print_ranked_summary(results: list[dict[str, Any]], *, top: int) -> None:
    print("Replay sweep top candidates:", file=sys.stderr)
    for index, row in enumerate(results[:top], start=1):
        print(
            "  "
            f"{index}. score {row['score']:+.2f} | pnl {row['result']['total_pnl_usd']:+.2f} | "
            f"dd {float(row['result'].get('max_drawdown_pct') or 0.0) * 100:.1f}% | "
            f"acc {int(row['result'].get('accepted_count') or 0)} | "
            f"win {float(row['result'].get('win_rate') or 0.0) * 100:.1f}% | "
            f"{_compact_override_summary(row['overrides'])}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a replay policy sweep over a parameter grid.")
    parser.add_argument("--db", default="", help="Path to a trading.db snapshot. Defaults to the runtime DB.")
    parser.add_argument("--label-prefix", default="sweep", help="Label prefix stored with each replay run.")
    parser.add_argument("--notes", default="", help="Optional notes stored with each replay run.")
    parser.add_argument("--base-policy-file", default="", help="JSON file with base replay policy overrides.")
    parser.add_argument("--base-policy-json", default="", help="Inline JSON payload with base replay policy overrides.")
    parser.add_argument("--grid-file", default="", help="JSON file describing the parameter grid to sweep.")
    parser.add_argument("--grid-json", default="", help="Inline JSON object describing the parameter grid to sweep.")
    parser.add_argument("--top", type=int, default=10, help="How many ranked candidates to print in the stderr summary.")
    parser.add_argument(
        "--drawdown-penalty",
        type=float,
        default=1.0,
        help="Penalty multiplier applied to max drawdown in bankroll-dollar terms when ranking candidates.",
    )
    parser.add_argument("--max-combos", type=int, default=256, help="Safety cap on total grid combinations.")
    args = parser.parse_args()

    base_policy = _load_base_policy(args)
    grid = _load_grid(args)
    overrides_list = _iter_policy_overrides(grid)
    if len(overrides_list) > max(args.max_combos, 1):
        raise ValueError(f"Grid expands to {len(overrides_list)} combinations, above --max-combos={args.max_combos}")

    candidates: list[dict[str, Any]] = []
    for index, overrides in enumerate(overrides_list, start=1):
        policy_payload = base_policy.as_dict()
        policy_payload.update(overrides)
        policy = ReplayPolicy.from_payload(policy_payload)
        result = run_replay(
            policy=policy,
            db_path=Path(args.db) if args.db else None,
            label=f"{args.label_prefix}-{index:03d}",
            notes=args.notes,
        )
        score = _score_result(
            result,
            initial_bankroll_usd=policy.initial_bankroll_usd,
            drawdown_penalty=max(args.drawdown_penalty, 0.0),
        )
        candidates.append(
            {
                "index": index,
                "score": round(score, 6),
                "overrides": overrides,
                "policy": policy.as_dict(),
                "result": result,
            }
        )

    ranked = sorted(
        candidates,
        key=lambda row: (
            float(row["score"]),
            float(row["result"].get("total_pnl_usd") or 0.0),
            -float(row["result"].get("max_drawdown_pct") or 0.0),
            float(row["result"].get("win_rate") or 0.0),
        ),
        reverse=True,
    )
    print(
        json.dumps(
            {
                "base_policy": base_policy.as_dict(),
                "grid": grid,
                "drawdown_penalty": max(args.drawdown_penalty, 0.0),
                "candidate_count": len(ranked),
                "ranked": ranked,
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(file=sys.stderr)
    _print_ranked_summary(ranked, top=max(args.top, 1))


if __name__ == "__main__":
    main()
