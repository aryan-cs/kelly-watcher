from __future__ import annotations

import logging

import requests

from config import telegram_bot_token, telegram_chat_id

logger = logging.getLogger(__name__)


def send_alert(message: str, silent: bool = False) -> None:
    if silent:
        return

    token = telegram_bot_token()
    chat_id = telegram_chat_id()
    if not token or not chat_id:
        logger.debug("Telegram not configured. Message: %s", message[:100])
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:4096]},
            timeout=5,
        )
    except Exception as exc:
        logger.warning("Telegram alert failed: %s", exc)

