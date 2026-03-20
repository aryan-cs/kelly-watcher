from __future__ import annotations

import logging

import httpx

from config import telegram_bot_token, telegram_chat_id

logger = logging.getLogger(__name__)
_TELEGRAM_ALLOWED_KINDS = frozenset({"buy", "resolution", "retrain"})


def send_alert(message: str, silent: bool = False, *, kind: str = "other") -> None:
    if silent:
        return
    normalized_kind = str(kind or "other").strip().lower()
    if normalized_kind not in _TELEGRAM_ALLOWED_KINDS:
        logger.debug("Telegram alert suppressed for kind=%s. Message: %s", normalized_kind, message[:100])
        return

    token = telegram_bot_token()
    chat_id = telegram_chat_id()
    if not token or not chat_id:
        logger.debug("Telegram not configured. Message: %s", message[:100])
        return

    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message[:4096]},
            )
    except Exception as exc:
        logger.warning("Telegram alert failed: %s", exc)
