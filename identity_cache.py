from __future__ import annotations

import html
import json
import logging
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CACHE_PATH = Path("data/identity_cache.json")
CACHE_TTL_SECONDS = 6 * 60 * 60

ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
CANONICAL_HANDLE_RE = re.compile(
    r'<link[^>]+rel="canonical"[^>]+href="https://polymarket\.com/@([^"/?#]+)"',
    re.IGNORECASE,
)
OG_TITLE_RE = re.compile(
    r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
    re.IGNORECASE,
)
OG_IMAGE_USERNAME_RE = re.compile(
    r'https://polymarket\.com/api/og\?username=([^"&]+)',
    re.IGNORECASE,
)


def normalize_wallet(wallet: str | None) -> str:
    text = (wallet or "").strip().lower()
    return text if ADDRESS_RE.fullmatch(text) else ""


def normalize_username(username: str | None) -> str:
    return (username or "").strip().lstrip("@").lower()


def is_placeholder_username(username: str | None, wallet: str | None = None) -> bool:
    display = (username or "").strip()
    if not display:
        return True

    normalized = display.lower()
    normalized_wallet = normalize_wallet(wallet)
    if normalized_wallet and normalized == normalized_wallet:
        return True
    if normalized_wallet and normalized.startswith(f"{normalized_wallet}-"):
        suffix = normalized[len(normalized_wallet) + 1 :]
        if suffix.isdigit():
            return True
    return False


def _default_cache() -> dict:
    return {"wallets": {}, "usernames": {}}


def load_identity_cache() -> dict:
    if not CACHE_PATH.exists():
        return _default_cache()
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return _default_cache()
        payload.setdefault("wallets", {})
        payload.setdefault("usernames", {})
        return payload
    except Exception:
        return _default_cache()


def _write_identity_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _clear_placeholder_identity(cache: dict, wallet: str) -> None:
    cache.setdefault("wallets", {})
    cache.setdefault("usernames", {})
    entry = cache["wallets"].get(wallet, {})
    normalized_username = str(entry.get("normalized_username") or "").strip().lower()
    if normalized_username:
        cache["usernames"].pop(normalized_username, None)
    entry.pop("username", None)
    entry.pop("normalized_username", None)
    if entry:
        cache["wallets"][wallet] = entry
    else:
        cache["wallets"].pop(wallet, None)


def lookup_username(wallet: str | None) -> str | None:
    normalized_wallet = normalize_wallet(wallet)
    if not normalized_wallet:
        return None
    cache = load_identity_cache()
    entry = cache.get("wallets", {}).get(normalized_wallet, {})
    username = str(entry.get("username") or "").strip()
    if is_placeholder_username(username, normalized_wallet):
        _clear_placeholder_identity(cache, normalized_wallet)
        _write_identity_cache(cache)
        return None
    return username or None


def lookup_wallet(username: str | None) -> str | None:
    normalized_username = normalize_username(username)
    if not normalized_username:
        return None
    cache = load_identity_cache()
    entry = cache.get("usernames", {}).get(normalized_username, {})
    wallet = normalize_wallet(entry.get("wallet"))
    return wallet or None


def remember_identity(wallet: str | None, username: str | None, checked_at: int | None = None) -> str | None:
    normalized_wallet = normalize_wallet(wallet)
    display_name = clean_display_name(username)
    if not normalized_wallet or not display_name:
        return None

    now = int(checked_at or time.time())
    normalized_username = normalize_username(display_name)
    cache = load_identity_cache()
    cache.setdefault("wallets", {})
    cache.setdefault("usernames", {})
    cache["wallets"][normalized_wallet] = {
        "username": display_name,
        "normalized_username": normalized_username,
        "checked_at": now,
        "updated_at": now,
    }
    cache["usernames"][normalized_username] = {
        "wallet": normalized_wallet,
        "username": display_name,
        "checked_at": now,
        "updated_at": now,
    }
    _write_identity_cache(cache)
    return display_name


def mark_wallet_checked(wallet: str | None, checked_at: int | None = None) -> None:
    normalized_wallet = normalize_wallet(wallet)
    if not normalized_wallet:
        return

    now = int(checked_at or time.time())
    cache = load_identity_cache()
    cache.setdefault("wallets", {})
    entry = cache["wallets"].get(normalized_wallet, {})
    if is_placeholder_username(entry.get("username"), normalized_wallet):
        _clear_placeholder_identity(cache, normalized_wallet)
        entry = cache["wallets"].get(normalized_wallet, {})
    entry["checked_at"] = now
    cache["wallets"][normalized_wallet] = entry
    _write_identity_cache(cache)


def clean_display_name(username: str | None) -> str:
    display = html.unescape((username or "").strip())
    if display.lower().endswith(" on polymarket"):
        display = display[: -len(" on polymarket")].strip()
    if display.startswith("@"):
        display = display[1:].strip()
    if display.lower() == "polymarket":
        return ""
    return display


def _wallet_checked_recently(wallet: str, cache: dict, ttl_seconds: int) -> bool:
    entry = cache.get("wallets", {}).get(wallet, {})
    checked_at = int(entry.get("checked_at") or 0)
    return checked_at > 0 and (time.time() - checked_at) < ttl_seconds


def extract_username_from_profile_html(text: str) -> str | None:
    for pattern in (CANONICAL_HANDLE_RE, OG_IMAGE_USERNAME_RE):
        match = pattern.search(text)
        if match:
            username = clean_display_name(match.group(1))
            if username:
                return username

    match = OG_TITLE_RE.search(text)
    if match:
        username = clean_display_name(match.group(1))
        if username:
            return username

    return None


def resolve_username_for_wallet(
    wallet: str | None,
    client: httpx.Client | None = None,
    ttl_seconds: int = CACHE_TTL_SECONDS,
    force: bool = False,
) -> str | None:
    normalized_wallet = normalize_wallet(wallet)
    if not normalized_wallet:
        return None

    cached_username = lookup_username(normalized_wallet)
    if cached_username and not force:
        return cached_username

    cache = load_identity_cache()
    if not force and _wallet_checked_recently(normalized_wallet, cache, ttl_seconds):
        return cached_username

    owns_client = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True)
    try:
        response = client.get(f"https://polymarket.com/profile/{normalized_wallet}")
        response.raise_for_status()
        username = extract_username_from_profile_html(response.text)
        now = int(time.time())
        if username:
            return remember_identity(normalized_wallet, username, checked_at=now)
        mark_wallet_checked(normalized_wallet, checked_at=now)
        return None
    except Exception as exc:
        logger.debug("Username lookup failed for %s: %s", normalized_wallet[:10], exc)
        mark_wallet_checked(normalized_wallet)
        return cached_username
    finally:
        if owns_client:
            client.close()


def resolve_wallet_for_username(username: str | None, client: httpx.Client | None = None) -> str | None:
    normalized_username = normalize_username(username)
    if not normalized_username:
        return None

    cached_wallet = lookup_wallet(normalized_username)
    if cached_wallet:
        return cached_wallet

    owns_client = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True)
    try:
        for url in (
            f"https://polymarket.com/@{normalized_username}",
            f"https://polymarket.com/profile/{normalized_username}",
            f"https://polymarket.com/{normalized_username}",
        ):
            try:
                response = client.get(url)
                response.raise_for_status()
                match = ADDRESS_RE.search(response.text)
                if match:
                    return remember_identity(match.group(0), normalized_username)
            except Exception:
                continue
    finally:
        if owns_client:
            client.close()
    return None


def hydrate_observed_identity(
    wallet: str | None,
    observed_username: str | None,
    client: httpx.Client | None = None,
) -> str:
    normalized_wallet = normalize_wallet(wallet)
    observed = clean_display_name(observed_username)
    if not normalized_wallet:
        return observed

    if observed and not is_placeholder_username(observed, normalized_wallet):
        remember_identity(normalized_wallet, observed)
        return observed

    cached_username = lookup_username(normalized_wallet)
    if cached_username:
        return cached_username

    resolved_username = resolve_username_for_wallet(normalized_wallet, client=client)
    if resolved_username:
        return resolved_username

    if observed:
        return observed
    return ""
