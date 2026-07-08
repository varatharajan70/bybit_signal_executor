# core/notifier.py
# Best-effort Telegram notifications for signal/trade/position events. A notification
# failure (bad token, network blip) must never interrupt trading, so every call here
# swallows its own errors and only logs a warning.

import logging
import requests

from config.settings import BOT_TOKEN, CHAT_ID

logger = logging.getLogger(__name__)


def send_telegram_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"Telegram notify failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Telegram notify error: {e}")
