# core/notifier.py
# Best-effort Telegram notifications for signal/trade/position events. A notification
# failure (bad token, network blip) must never interrupt trading, so every call here
# swallows its own errors and only logs a warning.

import logging
import requests

from config.settings import BOT_TOKEN, CHAT_ID

logger = logging.getLogger(__name__)


def send_telegram_message(text, reply_to=None):
    """Send a message; returns its message_id (so a later related update can thread onto
    it via reply_to), or None if sending failed / no bot is configured."""
    if not BOT_TOKEN or not CHAT_ID:
        return None
    data = {"chat_id": CHAT_ID, "text": text}
    if reply_to:
        data["reply_to_message_id"] = reply_to
        data["allow_sending_without_reply"] = True
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=data,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"Telegram notify failed: {resp.status_code} {resp.text[:200]}")
            return None
        return resp.json().get("result", {}).get("message_id")
    except Exception as e:
        logger.warning(f"Telegram notify error: {e}")
        return None
