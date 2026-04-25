from __future__ import annotations

import logging
import re
import threading
from typing import Iterable

import httpx

from kelly_watcher.config import telegram_bot_token, telegram_chat_id

logger = logging.getLogger(__name__)
_TELEGRAM_ALLOWED_KINDS = frozenset(
    {"buy", "resolution", "retrain", "exit", "status", "error", "warning", "report"}
)
_URL_RE = re.compile(r"https?://\S+")
_TELEGRAM_CLIENT_LOCK = threading.Lock()
_TELEGRAM_CLIENT: httpx.Client | None = None


def _telegram_client() -> httpx.Client:
    global _TELEGRAM_CLIENT
    with _TELEGRAM_CLIENT_LOCK:
        if _TELEGRAM_CLIENT is None or bool(getattr(_TELEGRAM_CLIENT, "is_closed", False)):
            _TELEGRAM_CLIENT = httpx.Client(
                timeout=5.0,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            )
        return _TELEGRAM_CLIENT


def close_telegram_alert_client() -> None:
    global _TELEGRAM_CLIENT
    with _TELEGRAM_CLIENT_LOCK:
        client = _TELEGRAM_CLIENT
        _TELEGRAM_CLIENT = None
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _format_decimal(value: float, digits: int) -> str:
    return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")


def _format_shares(value: float) -> str:
    return _format_decimal(value, 3)


def _format_cents(price: float) -> str:
    return _format_decimal(float(price) * 100.0, 1)


def _format_usd(value: float) -> str:
    return f"${float(value):.2f}"


def _format_signed_usd(value: float) -> str:
    amount = abs(float(value))
    if value > 0:
        return f"+${amount:.2f}"
    if value < 0:
        return f"-${amount:.2f}"
    return f"${amount:.2f}"


def _format_pct(value: float) -> str:
    return f"{float(value) * 100.0:.1f}%"


def _short_wallet(wallet: str) -> str:
    text = _one_line(wallet)
    if text.lower().startswith("0x") and len(text) > 16:
        return f"{text[:8]}...{text[-6:]}"
    return text


def _looks_like_placeholder_name(name: str, wallet: str) -> bool:
    normalized_name = name.lower()
    normalized_wallet = wallet.lower()
    if not normalized_name or not normalized_wallet:
        return False
    if normalized_name == normalized_wallet:
        return True
    if normalized_name.startswith(f"{normalized_wallet}-"):
        suffix = normalized_name[len(normalized_wallet) + 1 :]
        if suffix.isdigit():
            return True
    return False


def build_lines(*lines: str | None) -> str:
    normalized: list[str] = []
    for line in lines:
        text = str(line or "").strip()
        if text:
            normalized.append(text)
    return "\n".join(normalized)


def build_bullets(lines: Iterable[str]) -> str:
    return build_lines(*(f"- {_one_line(line)}" for line in lines if _one_line(line)))


def build_market_line(question: str, market_url: str | None) -> str:
    label = _one_line(question) or "Market"
    url = _one_line(market_url)
    return f"{label}: {url}" if url else label


def build_message_with_market_block(
    summary: str,
    *,
    question: str | None = None,
    market_url: str | None = None,
    detail: str | None = None,
) -> str:
    lines = [_one_line(summary)]
    if question or market_url:
        lines.extend(["", build_market_line(question or "Market", market_url)])
    if detail:
        lines.append(_one_line(detail))
    return "\n".join(lines)


def build_tracking_line(
    tracked_trader_name: str | None = None,
    tracked_trader_address: str | None = None,
) -> str | None:
    name = _one_line(tracked_trader_name)
    wallet = _one_line(tracked_trader_address)
    if name and wallet and _looks_like_placeholder_name(name, wallet):
        name = ""
    if name and wallet:
        return f"tracking {name} ({_short_wallet(wallet)})"
    if name:
        return f"tracking {name}"
    if wallet:
        return f"tracking {wallet}"
    return None


def append_tracking_detail(
    summary: str,
    tracked_trader_name: str | None = None,
    tracked_trader_address: str | None = None,
) -> str:
    base = _one_line(summary)
    tracking = build_tracking_line(tracked_trader_name, tracked_trader_address)
    if not tracking:
        return base
    return f"{base} | {tracking}"


def build_trade_entry_alert(
    *,
    mode: str,
    side: str,
    shares: float,
    price: float,
    total_usd: float,
    confidence: float | None,
    question: str,
    market_url: str | None,
    tracked_trader_name: str | None = None,
    tracked_trader_address: str | None = None,
) -> str:
    side_label = _one_line(side).upper()
    share_noun = "share" if abs(float(shares) - 1.0) < 1e-9 else "shares"
    confidence_text = f", {_format_pct(confidence)} confident" if confidence is not None else ""
    position_text = f" {side_label}" if side_label else ""
    return build_message_with_market_block(
        append_tracking_detail(
            f"{_one_line(mode).lower()} bought {_format_shares(shares)}{position_text} {share_noun} "
            f"@ {_format_cents(price)} cents for a total of {_format_usd(total_usd)}{confidence_text}",
            tracked_trader_name,
            tracked_trader_address,
        ),
        question=question,
        market_url=market_url,
    )


def build_trade_exit_alert(
    *,
    mode: str,
    side: str,
    shares: float,
    price: float,
    total_usd: float,
    pnl_usd: float | None,
    question: str,
    market_url: str | None,
    tracked_trader_name: str | None = None,
    tracked_trader_address: str | None = None,
) -> str:
    side_label = _one_line(side).upper()
    share_noun = "share" if abs(float(shares) - 1.0) < 1e-9 else "shares"
    pnl_text = (
        f", realized {_format_signed_usd(pnl_usd)}"
        if pnl_usd is not None
        else ""
    )
    position_text = f" {side_label}" if side_label else ""
    return build_message_with_market_block(
        append_tracking_detail(
            f"{_one_line(mode).lower()} sold {_format_shares(shares)}{position_text} {share_noun} "
            f"@ {_format_cents(price)} cents for a total of {_format_usd(total_usd)}{pnl_text}",
            tracked_trader_name,
            tracked_trader_address,
        ),
        question=question,
        market_url=market_url,
    )


def build_trade_resolution_alert(
    *,
    mode: str,
    won: bool,
    side: str,
    pnl_usd: float,
    question: str,
    market_url: str | None,
    tracked_trader_name: str | None = None,
    tracked_trader_address: str | None = None,
) -> str:
    side_label = _one_line(side).upper() or "POSITION"
    amount = _format_usd(abs(float(pnl_usd)))
    if abs(float(pnl_usd)) < 1e-9:
        summary = f"➖ {_one_line(mode).lower()} resolved {side_label}, broke even"
    elif won:
        summary = f"✅ {_one_line(mode).lower()} won {side_label}, made {amount}"
    else:
        summary = f"❌ {_one_line(mode).lower()} lost {side_label}, lost {amount}"
    return build_message_with_market_block(
        append_tracking_detail(summary, tracked_trader_name, tracked_trader_address),
        question=question,
        market_url=market_url,
    )


def build_market_error_alert(
    summary: str,
    *,
    question: str | None = None,
    market_url: str | None = None,
    detail: str | None = None,
    tracked_trader_name: str | None = None,
    tracked_trader_address: str | None = None,
) -> str:
    return build_message_with_market_block(
        append_tracking_detail(summary, tracked_trader_name, tracked_trader_address),
        question=question or "Market" if question or market_url else None,
        market_url=market_url,
        detail=detail,
    )


def send_telegram_message(
    message: str,
    *,
    chat_id: str | None = None,
    reply_to_message_id: int | None = None,
) -> bool:
    token = telegram_bot_token()
    target_chat_id = str(chat_id or telegram_chat_id() or "").strip()
    if not token or not target_chat_id:
        logger.debug("Telegram not configured. Message: %s", message[:100])
        return False

    normalized_message = _normalize_telegram_text(message)
    payload: dict[str, object] = {"chat_id": target_chat_id, "text": normalized_message[:4096]}
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = int(reply_to_message_id)
        payload["allow_sending_without_reply"] = True

    try:
        response = _telegram_client().post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Telegram alert failed: %s", exc)
        return False


def send_alert(message: str, silent: bool = False, *, kind: str = "other") -> None:
    if silent:
        return
    normalized_kind = str(kind or "other").strip().lower()
    if normalized_kind not in _TELEGRAM_ALLOWED_KINDS:
        logger.debug("Telegram alert suppressed for kind=%s. Message: %s", normalized_kind, message[:100])
        return

    send_telegram_message(message)


def _normalize_telegram_text(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""

    chunks: list[str] = []
    last_index = 0
    for match in _URL_RE.finditer(text):
        chunks.append(text[last_index:match.start()].lower())
        chunks.append(match.group(0))
        last_index = match.end()
    chunks.append(text[last_index:].lower())
    return "".join(chunks)
