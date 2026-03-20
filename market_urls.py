from __future__ import annotations

from typing import Any

POLYMARKET_EVENT_BASE_URL = "https://polymarket.com/event"
POLYMARKET_SPORTS_BASE_URL = "https://polymarket.com/sports"

_SPORTS_ROUTE_ALIASES: dict[str, str] = {
    "atp": "tennis",
    "blast": "counter-strike",
    "bra": "bra",
    "bun": "bundesliga",
    "cbb": "cbb",
    "cfb": "cfb",
    "counterstrike": "counter-strike",
    "counter-strike": "counter-strike",
    "cs": "counter-strike",
    "cs2": "counter-strike",
    "epl": "epl",
    "ere": "ere",
    "esl": "counter-strike",
    "euroleague": "euroleague",
    "f1": "f1",
    "iem": "counter-strike",
    "ipl": "ipl",
    "laliga": "laliga",
    "ligue1": "ligue1",
    "mlb": "mlb",
    "mls": "mls",
    "nba": "nba",
    "nfl": "nfl",
    "nhl": "nhl",
    "pfl": "pfl",
    "pga": "golf",
    "pgl": "counter-strike",
    "seriea": "seriea",
    "tennis": "tennis",
    "ucl": "ucl",
    "uecl": "uecl",
    "uel": "uel",
    "ufc": "ufc",
    "val": "valorant",
    "valorant": "valorant",
    "wnba": "wnba",
    "wta": "tennis",
}


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


def _canonical_event_slug(event_slug: Any, market_slug: Any) -> str:
    normalized_event = _normalize_slug(event_slug)
    normalized_market = _normalize_slug(market_slug)
    if normalized_event.endswith("-more-markets"):
        base_event = normalized_event[: -len("-more-markets")]
        if base_event and (not normalized_market or normalized_market.startswith(f"{base_event}-")):
            return base_event
    return normalized_event


def _sports_route_slug(event_slug: str) -> str | None:
    normalized_event = _normalize_slug(event_slug).lower()
    if not normalized_event:
        return None
    return _SPORTS_ROUTE_ALIASES.get(normalized_event.split("-", 1)[0])


def market_url_from_metadata(meta: Any) -> str | None:
    if not isinstance(meta, dict):
        return None

    market_slug = _market_slug(meta)
    event_slug = _canonical_event_slug(_event_slug(meta), market_slug)
    sports_route = _sports_route_slug(event_slug)

    direct_url = _valid_direct_url(meta.get("url") or meta.get("marketUrl"))
    if direct_url and not sports_route:
        return direct_url

    if sports_route and event_slug:
        return f"{POLYMARKET_SPORTS_BASE_URL}/{sports_route}/{event_slug}"

    if market_slug:
        if event_slug and event_slug.lower() != market_slug.lower():
            return f"{POLYMARKET_EVENT_BASE_URL}/{event_slug}/{market_slug}"
        return f"{POLYMARKET_EVENT_BASE_URL}/{market_slug}"

    nested_event = meta.get("event")
    if isinstance(nested_event, dict):
        nested_event_slug = _canonical_event_slug(
            nested_event.get("slug") or nested_event.get("marketSlug"),
            market_slug,
        )
        nested_sports_route = _sports_route_slug(nested_event_slug)
        if nested_sports_route and nested_event_slug:
            return f"{POLYMARKET_SPORTS_BASE_URL}/{nested_sports_route}/{nested_event_slug}"
        if nested_event_slug:
            return f"{POLYMARKET_EVENT_BASE_URL}/{nested_event_slug}"
        direct_url = _valid_direct_url(nested_event.get("url") or nested_event.get("marketUrl"))
        if direct_url:
            return direct_url

    if direct_url:
        return direct_url

    return None
