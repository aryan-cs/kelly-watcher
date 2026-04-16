from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from kelly_watcher.runtime.wallet_discovery import (
    load_wallet_discovery_candidates,
    refresh_wallet_discovery_candidates,
)


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text.ljust(width)
    if width <= 3:
        return text[:width]
    return f"{text[:width - 3]}..."


def _short_wallet(address: str) -> str:
    wallet = str(address or "").strip().lower()
    if len(wallet) <= 14:
        return wallet
    return f"{wallet[:8]}...{wallet[-4:]}"


def _format_pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "-"


def _format_usd(value: Any) -> str:
    if value is None:
        return "-"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if amount > 0 else ""
    return f"{sign}${amount:,.0f}"


def _format_hours(value: Any) -> str:
    if value is None:
        return "-"
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return "-"
    if hours >= 24:
        return f"{hours / 24:.1f}d"
    return f"{hours:.1f}h"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan and print candidate wallets for future copy-trading."
    )
    parser.add_argument("--cached", action="store_true", help="Use the cached discovery results instead of running a fresh scan.")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--wallets-only", action="store_true")
    parser.add_argument("--json-out")
    return parser.parse_args(argv)


def print_candidates(rows: list[dict[str, Any]], *, wallets_only: bool) -> None:
    if wallets_only:
        for row in rows:
            print(str(row.get("wallet_address") or "").strip().lower())
        return

    headers = [
        ("#", 3),
        ("status", 8),
        ("score", 7),
        ("wallet", 16),
        ("user", 18),
        ("style", 20),
        ("source", 18),
        ("buys", 5),
        ("lead", 7),
        ("late", 7),
        ("reason", 28),
        ("pnl", 10),
    ]
    print(" ".join(_fit(name, width) for name, width in headers))
    for index, row in enumerate(rows, start=1):
        source = ",".join(str(label) for label in row.get("source_labels") or [])
        accepted = bool(row.get("accepted"))
        values = [
            (str(index), 3),
            ("ready" if accepted else "review", 8),
            (f"{float(row.get('follow_score') or 0.0):.3f}", 7),
            (_short_wallet(str(row.get("wallet_address") or "")), 16),
            (str(row.get("username") or "-"), 18),
            (str(row.get("style") or "-"), 20),
            (source or "-", 18),
            (str(int(row.get("recent_buys") or 0)), 5),
            (_format_hours(row.get("median_buy_lead_hours")), 7),
            (_format_pct(row.get("late_buy_ratio")), 7),
            (str(row.get("reject_reason") or "-"), 28),
            (_format_usd(row.get("realized_pnl_usd")), 10),
        ]
        print(" ".join(_fit(value, width) for value, width in values))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    summary: dict[str, Any] | None = None
    if not args.cached:
        summary = refresh_wallet_discovery_candidates()

    rows = load_wallet_discovery_candidates(limit=max(int(args.top or 0), 1))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "summary": summary or {},
                    "candidates": rows,
                    "selected_wallets": [row["wallet_address"] for row in rows],
                },
                handle,
                indent=2,
            )

    if not rows:
        if summary and summary.get("message"):
            print(str(summary["message"]))
        else:
            print("No discovery candidates available.")
        return 1

    print_candidates(rows, wallets_only=args.wallets_only)
    if not args.wallets_only:
        accepted_count = sum(1 for row in rows if bool(row.get("accepted")))
        print("")
        if summary and summary.get("message"):
            print(str(summary["message"]))
        print(f"Visible candidates: {len(rows)} (ready={accepted_count}, review={len(rows) - accepted_count})")
        print(f"CANDIDATE_WALLETS={','.join(row['wallet_address'] for row in rows)}")
    if summary and summary.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
