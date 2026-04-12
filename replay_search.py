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


def _constraint_failures(
    result: dict[str, Any],
    *,
    min_accepted_count: int,
    min_resolved_count: int,
    min_win_rate: float,
    max_drawdown_pct: float,
) -> list[str]:
    failures: list[str] = []
    accepted_count = int(result.get("accepted_count") or 0)
    resolved_count = int(result.get("resolved_count") or 0)
    raw_win_rate = result.get("win_rate")
    win_rate = float(raw_win_rate) if raw_win_rate is not None else None
    drawdown_pct = float(result.get("max_drawdown_pct") or 0.0)

    if accepted_count < max(min_accepted_count, 0):
        failures.append("accepted_count")
    if resolved_count < max(min_resolved_count, 0):
        failures.append("resolved_count")
    if min_win_rate > 0 and (win_rate is None or win_rate < min_win_rate):
        failures.append("win_rate")
    if max_drawdown_pct > 0 and drawdown_pct > max_drawdown_pct:
        failures.append("max_drawdown_pct")
    return failures


def _compact_override_summary(payload: dict[str, Any]) -> str:
    if not payload:
        return "default"
    parts = [f"{key}={payload[key]}" for key in sorted(payload)]
    return ", ".join(parts)


def _print_ranked_summary(results: list[dict[str, Any]], *, top: int, title: str) -> None:
    print(title, file=sys.stderr)
    for index, row in enumerate(results[:top], start=1):
        failures = row.get("constraint_failures") or []
        feasibility_suffix = "" if not failures else f" | reject {','.join(str(value) for value in failures)}"
        print(
            "  "
            f"{index}. score {row['score']:+.2f} | pnl {row['result']['total_pnl_usd']:+.2f} | "
            f"dd {float(row['result'].get('max_drawdown_pct') or 0.0) * 100:.1f}% | "
            f"acc {int(row['result'].get('accepted_count') or 0)} | "
            f"win {float(row['result'].get('win_rate') or 0.0) * 100:.1f}% | "
            f"{_compact_override_summary(row['overrides'])}{feasibility_suffix}",
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
    parser.add_argument("--min-accepted-count", type=int, default=0, help="Minimum accepted trades required for a candidate to be feasible.")
    parser.add_argument("--min-resolved-count", type=int, default=0, help="Minimum resolved trades required for a candidate to be feasible.")
    parser.add_argument("--min-win-rate", type=float, default=0.0, help="Minimum replay win rate required for a candidate to be feasible.")
    parser.add_argument("--max-drawdown-pct", type=float, default=0.0, help="Maximum replay drawdown allowed for a candidate to be feasible.")
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
                "constraint_failures": _constraint_failures(
                    result,
                    min_accepted_count=args.min_accepted_count,
                    min_resolved_count=args.min_resolved_count,
                    min_win_rate=max(args.min_win_rate, 0.0),
                    max_drawdown_pct=max(args.max_drawdown_pct, 0.0),
                ),
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
    feasible = [row for row in ranked if not row["constraint_failures"]]
    rejected = [row for row in ranked if row["constraint_failures"]]
    print(
        json.dumps(
            {
                "base_policy": base_policy.as_dict(),
                "grid": grid,
                "drawdown_penalty": max(args.drawdown_penalty, 0.0),
                "constraints": {
                    "min_accepted_count": max(args.min_accepted_count, 0),
                    "min_resolved_count": max(args.min_resolved_count, 0),
                    "min_win_rate": max(args.min_win_rate, 0.0),
                    "max_drawdown_pct": max(args.max_drawdown_pct, 0.0),
                },
                "candidate_count": len(ranked),
                "feasible_count": len(feasible),
                "rejected_count": len(rejected),
                "best_feasible": feasible[0] if feasible else None,
                "ranked": ranked,
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(file=sys.stderr)
    _print_ranked_summary(feasible if feasible else ranked, top=max(args.top, 1), title="Replay sweep top candidates:")
    if rejected:
        print(file=sys.stderr)
        _print_ranked_summary(rejected, top=min(max(args.top, 1), len(rejected)), title="Replay sweep rejected candidates:")


if __name__ == "__main__":
    main()
