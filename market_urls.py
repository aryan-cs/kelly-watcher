from __future__ import annotations

from typing import Any

POLYMARKET_EVENT_BASE_URL = "https://polymarket.com/event"


def _normalize_slug(value: Any) -> str:
    return str(value or "").strip().strip("/")


def _valid_direct_url(value: Any) -> str | None:
    url = str(value or "").strip()
    if not url:
        return None
    if not (url.startswith("https://") or url.startswith("http://")):
        return None
    if "polymarket.com/" not in url.lower():
        return None
    return url


def _market_slug(meta: dict[str, Any]) -> str:
    return _normalize_slug(meta.get("slug") or meta.get("marketSlug"))


def _event_slug(meta: dict[str, Any]) -> str:
    events = meta.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            slug = _normalize_slug(event.get("slug") or event.get("marketSlug"))
            if slug:
                return slug

    nested_event = meta.get("event")
    if isinstance(nested_event, dict):
        slug = _normalize_slug(nested_event.get("slug") or nested_event.get("marketSlug"))
        if slug:
            return slug

    return ""


def _is_sports_market(meta: dict[str, Any]) -> bool:
    for key in ("sportsMarketType", "sportSlug", "leagueSlug", "seriesSlug"):
        if _normalize_slug(meta.get(key)):
            return True

    events = meta.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            for key in ("sportSlug", "leagueSlug", "seriesSlug"):
                if _normalize_slug(event.get(key)):
                    return True

    nested_event = meta.get("event")
    if isinstance(nested_event, dict):
        for key in ("sportSlug", "leagueSlug", "seriesSlug"):
            if _normalize_slug(nested_event.get(key)):
                return True

    return False


def market_url_from_metadata(meta: Any) -> str | None:
    if not isinstance(meta, dict):
        return None

    direct_url = _valid_direct_url(meta.get("url") or meta.get("marketUrl"))
    if direct_url:
        return direct_url

    nested_event = meta.get("event")
    if isinstance(nested_event, dict):
        nested_direct_url = _valid_direct_url(nested_event.get("url") or nested_event.get("marketUrl"))
        if nested_direct_url:
            return nested_direct_url

    event_slug = _event_slug(meta)
    if event_slug and _is_sports_market(meta):
        return f"{POLYMARKET_EVENT_BASE_URL}/{event_slug}"

    market_slug = _market_slug(meta)
    if market_slug:
        return f"{POLYMARKET_EVENT_BASE_URL}/{market_slug}"

    if event_slug:
        return f"{POLYMARKET_EVENT_BASE_URL}/{event_slug}"

    return None
