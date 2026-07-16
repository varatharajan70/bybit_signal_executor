# telegram_bot/listener.py
# Listens to trading signals from a Telegram channel via a user-account client (Telethon)
# and writes parsed signals to the shared signal input file for the executor to pick up.

import json
import os
import logging
from datetime import datetime

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

from config.settings import (
    SIGNAL_INPUT_FILE,
    CHANNEL_USERNAME,
    TELEGRAM_SESSION_PATH,
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
)
from core.signal_handler import parse_signal_text, calc_runner_profit
from core.notifier import send_telegram_message

logger = logging.getLogger(__name__)


class TelegramSignalListener:
    def __init__(self, api_id, api_hash, channel):
        self.api_id = api_id
        self.api_hash = api_hash
        self.channel = channel
        self.client = TelegramClient(TELEGRAM_SESSION_PATH, api_id, api_hash)
        self.last_message_id = 0

    async def listen(self):
        """Connect and listen for new messages from the configured channel."""
        await self.client.start()

        try:
            entity = await self.client.get_entity(self.channel)
            logger.info(f"Connected to channel: {entity.title or entity.username}")

            async for msg in self.client.iter_messages(entity, limit=1):
                self.last_message_id = msg.id
                logger.info(f"Synced to message ID: {self.last_message_id}")
                break

            @self.client.on(events.NewMessage(chats=[entity]))
            async def handler(event):
                if event.message.id > self.last_message_id:
                    await self.process_message(event.message)
                    self.last_message_id = event.message.id

            logger.info(f"Listening to {self.channel}...")
            await self.client.run_until_disconnected()

        except SessionPasswordNeededError:
            logger.error("Two-factor authentication required. Please set your password.")
        except Exception as e:
            logger.error(f"Listen error: {e}")

    async def process_message(self, message):
        """Process a Telegram message and extract a trading signal, if present."""
        try:
            text = message.text or message.raw_text

            if not text:
                return

            if "#" not in text or "STOP" not in text:
                return

            logger.info(f"Processing message: {text[:100]}...")

            signal = parse_signal_text(text)

            if signal:
                info = calc_runner_profit(signal)
                lines = [
                    f"💫 Signal received: #{signal.symbol} 💫",
                    "",
                    f"📌 {signal.side} @ {signal.entry} Qty {signal.qty}  SL {signal.stop}  (max loss ${info['max_loss']:.2f})",
                    "",
                ]
                for s in info["stages"]:
                    lines.append(
                        f"🔶 SL → {s['sl_label']} ({s['sl_price']}) @ "
                        f"{s['trigger_label']} {s['trigger_price']}"
                    )
                lines.append("")
                lines.append(
                    f"🏷 Full qty exits @ TP{len(signal.tps)} {info['exit_tp']}  "
                    f"RR {info['rr']:.2f}  profit ${info['profit']:.2f}"
                )
                msg_id = send_telegram_message("\n".join(lines))

                signal_json = {
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "entry": signal.entry,
                    "stop": signal.stop,
                    "tp": signal.tp,
                    "tps": signal.tps,
                    "qty": signal.qty,
                    "timestamp": datetime.now().isoformat(),
                    "source": "telegram",
                    "msg_id": msg_id,
                }

                with open(SIGNAL_INPUT_FILE, "w") as f:
                    json.dump(signal_json, f)

                logger.info(f"[OK] Signal saved: {signal.symbol} {signal.side} @ {signal.entry}")
            else:
                logger.warning("Could not parse signal from message")

        except Exception as e:
            logger.error(f"Error processing message: {e}")


def setup_telegram_credentials():
    """Interactive setup for Telegram credentials (used only if nothing else is configured)."""
    print("\n" + "=" * 60)
    print("TELEGRAM SIGNAL LISTENER SETUP")
    print("=" * 60)

    api_id = input("\nEnter your Telegram API_ID (from https://my.telegram.org/apps): ").strip()
    api_hash = input("Enter your Telegram API_HASH: ").strip()
    channel = input("Enter channel username (e.g., @gatetest) or ID: ").strip()

    return int(api_id), api_hash, channel


def resolve_credentials():
    """Resolve Telegram API credentials: environment variables first, then
    config defaults, then an interactive prompt as a last resort."""
    api_id = os.getenv("TELEGRAM_API_ID") or TELEGRAM_API_ID
    api_hash = os.getenv("TELEGRAM_API_HASH") or TELEGRAM_API_HASH
    channel = os.getenv("TELEGRAM_CHANNEL") or CHANNEL_USERNAME

    if not api_id or not api_hash:
        print("\nMissing Telegram credentials!")
        print("Set environment variables to skip this prompt next time:")
        print('  export TELEGRAM_API_ID="your_api_id"')
        print('  export TELEGRAM_API_HASH="your_api_hash"')
        print('  export TELEGRAM_CHANNEL="@your_channel"')
        return setup_telegram_credentials()

    return int(api_id), api_hash, channel
