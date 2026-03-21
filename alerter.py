from __future__ import annotations

import logging

import httpx

from config import telegram_bot_token, telegram_chat_id

logger = logging.getLogger(__name__)
_TELEGRAM_ALLOWED_KINDS = frozenset({"buy", "resolution", "retrain"})


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

    payload: dict[str, object] = {"chat_id": target_chat_id, "text": message[:4096]}
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = int(reply_to_message_id)
        payload["allow_sending_without_reply"] = True

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.post(
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
