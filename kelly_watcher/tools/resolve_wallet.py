from __future__ import annotations

import re
import sys
from typing import Iterable

import httpx

from kelly_watcher.data.identity_cache import (
    lookup_username,
    normalize_wallet,
    resolve_username_for_wallet,
    resolve_wallet_for_username,
)

ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
PROFILE_URL_RE = re.compile(
    r"https?://polymarket\.com/(?:(?:profile/)|@)?([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
HANDLE_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_.-]+)")


def resolve_wallet(value: str, client: httpx.Client | None = None) -> str | None:
    wallets = resolve_wallets(value, client)
    return wallets[0] if wallets else None


def resolve_wallets(value: str, client: httpx.Client | None = None) -> list[str]:
    text = value.strip()
    if not text:
        return []

    resolved: list[str] = []
    seen_wallets: set[str] = set()

    def add_wallet(wallet: str | None) -> None:
        if not wallet:
            return
        normalized = wallet.lower()
        if normalized not in seen_wallets:
            seen_wallets.add(normalized)
            resolved.append(normalized)

    for match in ADDRESS_RE.findall(text):
        add_wallet(match)

    candidates: list[str] = []
    seen_candidates: set[str] = set()

    def add_candidate(candidate: str) -> None:
        normalized = candidate.strip().lstrip("@")
        if not normalized:
            return
        if normalized.lower().startswith("0x") and len(normalized) == 42:
            add_wallet(normalized)
            return
        key = normalized.lower()
        if key not in seen_candidates:
            seen_candidates.add(key)
            candidates.append(normalized)

    for match in PROFILE_URL_RE.findall(text):
        add_candidate(match)

    for match in HANDLE_RE.findall(text):
        add_candidate(match)

    if not resolved and not candidates:
        add_candidate(text)

    owns_client = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True)

    try:
        for handle in candidates:
            wallet = resolve_wallet_for_username(handle, client, force=True)
            add_wallet(wallet)
        return resolved
    finally:
        if owns_client:
            client.close()


def _iter_inputs(argv: list[str]) -> Iterable[str]:
    if len(argv) > 1:
        yield from argv[1:]
        return

    if not sys.stdin.isatty():
        for line in sys.stdin:
            if line.strip():
                yield line.strip()
        return

    print("Enter Polymarket profile URLs, @handles, or wallet addresses. Blank line to finish.")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            break
        yield line


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv
    resolved: list[str] = []
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        for raw in _iter_inputs(argv):
            direct_wallet = normalize_wallet(raw)
            if direct_wallet:
                username = lookup_username(direct_wallet) or resolve_username_for_wallet(direct_wallet, client)
                if username:
                    print(f"{raw} -> {direct_wallet} ({username})")
                    resolved.append(direct_wallet)
                else:
                    print(f"{raw} -> {direct_wallet} (username unresolved)")
                    resolved.append(direct_wallet)
                continue

            wallets = resolve_wallets(raw, client)
            if wallets:
                resolved.extend(wallets)
                decorated = []
                for wallet in wallets:
                    username = lookup_username(wallet) or resolve_username_for_wallet(wallet, client)
                    decorated.append(f"{wallet} ({username})" if username else wallet)
                print(f"{raw} -> {', '.join(decorated)}")
            else:
                print(f"{raw} -> unresolved")

    if resolved:
        unique = list(dict.fromkeys(resolved))
        joined = ",".join(unique)
        print("")
        print(joined)
        print(f"WATCHED_WALLETS={joined}")
        return 0

    print("No wallets resolved.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
