# core/device_lock.py
# Cross-device coordination: when this bot starts, it broadcasts a "claim" over the
# existing Telegram bot chat and pins it. Every running instance (on any device) polls
# the chat's pinned message for a newer claim from a DIFFERENT device and stops itself
# gracefully - the only way to prevent the same signal being executed twice if a phone
# and PC are both left running.
#
# Uses getChat's pinned_message rather than getUpdates: a bot never receives its own
# sendMessage calls back as incoming updates, so getUpdates can't see our own claims.
# Reading the pinned message is a plain, stateless poll that sidesteps that entirely.
#
# The claim message is only ever sent+pinned once (the very first time this feature
# runs); every later claim just edits that same message's text in place. This keeps the
# chat free of a growing pile of "__CLAIM__ ..." posts - there's a single pinned line
# that gets silently updated, not a new message per device start.

import asyncio
import logging
import socket
import time

import requests

from config.settings import BOT_TOKEN, CHAT_ID

logger = logging.getLogger(__name__)

DEVICE_NAME = socket.gethostname()
_CLAIM_PREFIX = "__CLAIM__"
_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

_claim_time = None


def _parse_claim(text):
    if not text or not text.startswith(_CLAIM_PREFIX):
        return None
    parts = text.split()
    if len(parts) != 3:
        return None
    try:
        return parts[1], float(parts[2])
    except ValueError:
        return None


def _get_pinned_claim_id():
    resp = requests.get(f"{_API_BASE}/getChat", params={"chat_id": CHAT_ID}, timeout=10)
    resp.raise_for_status()
    pinned = resp.json().get("result", {}).get("pinned_message")
    if pinned and (pinned.get("text") or "").startswith(_CLAIM_PREFIX):
        return pinned["message_id"]
    return None


def broadcast_claim():
    """Announce this instance. Call once at startup. Best-effort - a notification
    hiccup must never block trading. Silent and non-spammy: edits the existing pinned
    claim message in place if one exists, only sends+pins a brand new message the very
    first time this feature is ever used."""
    global _claim_time
    _claim_time = time.time()
    if not BOT_TOKEN or not CHAT_ID:
        return
    text = f"{_CLAIM_PREFIX} {DEVICE_NAME} {_claim_time}"
    try:
        existing_id = _get_pinned_claim_id()
        if existing_id:
            resp = requests.post(
                f"{_API_BASE}/editMessageText",
                data={"chat_id": CHAT_ID, "message_id": existing_id, "text": text},
                timeout=10,
            )
            # "message is not modified" (near-impossible since the timestamp always
            # changes, but harmless either way) is the only expected non-200 case here.
            if resp.status_code != 200:
                logger.warning(f"device_lock edit claim failed: {resp.text[:200]}")
        else:
            resp = requests.post(
                f"{_API_BASE}/sendMessage",
                data={"chat_id": CHAT_ID, "text": text, "disable_notification": True},
                timeout=10,
            )
            resp.raise_for_status()
            message_id = resp.json()["result"]["message_id"]
            requests.post(
                f"{_API_BASE}/pinChatMessage",
                data={"chat_id": CHAT_ID, "message_id": message_id, "disable_notification": True},
                timeout=10,
            )
    except Exception as e:
        logger.warning(f"device_lock broadcast_claim error: {e}")


async def watch_for_takeover(on_takeover, poll_interval=15):
    """Poll the chat's pinned message for a newer claim from another device; call
    on_takeover(device) and return as soon as one is seen. No-ops forever if
    BOT_TOKEN/CHAT_ID aren't set."""
    if not BOT_TOKEN or not CHAT_ID:
        return
    if _claim_time is None:
        broadcast_claim()

    while True:
        await asyncio.sleep(poll_interval)
        try:
            resp = await asyncio.to_thread(
                requests.get, f"{_API_BASE}/getChat", params={"chat_id": CHAT_ID}, timeout=10
            )
            resp.raise_for_status()
            pinned = resp.json().get("result", {}).get("pinned_message")
        except Exception as e:
            logger.warning(f"device_lock getChat error: {e}")
            continue

        if not pinned:
            continue
        claim = _parse_claim(pinned.get("text", ""))
        if not claim:
            continue
        other_device, other_ts = claim
        if other_device == DEVICE_NAME or other_ts <= _claim_time:
            continue
        await on_takeover(other_device)
        return
