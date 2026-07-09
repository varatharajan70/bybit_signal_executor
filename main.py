#!/usr/bin/env python3
"""
ByBit Signal Executor - main entry point.

Runs the signal executor (monitors signal_input.txt and places demo/simulated
ByBit orders) and/or the Telegram channel listener (parses incoming channel
messages into signals) side by side.

Usage:
    python main.py                 # run both executor and telegram listener
    python main.py --mode executor # run executor only
    python main.py --mode telegram # run telegram listener only
"""

import argparse
import asyncio
import logging
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(name)s] - %(levelname)s - %(message)s",
)

from core.executor import SignalExecutor
from core.notifier import send_telegram_message
from core.colors import red, green, yellow
from core.device_lock import DEVICE_NAME, broadcast_claim, watch_for_takeover
from telegram_bot.listener import TelegramSignalListener, resolve_credentials
from config.settings import DEMO_MODE, TRADING_ENV, BYBIT_URL

logger = logging.getLogger("Main")


def print_startup_banner():
    print(yellow("=" * 70))
    print(yellow("ByBit Signal Executor"))
    print(yellow(f"  DEMO_MODE   : {DEMO_MODE}  ({'no network calls, fully simulated' if DEMO_MODE else 'REAL network requests'})"))
    env_color = green if TRADING_ENV == "demo" else red
    print(env_color(f"  TRADING_ENV : {TRADING_ENV.upper()}"))
    print(yellow(f"  Base URL    : {BYBIT_URL}"))
    if not DEMO_MODE and TRADING_ENV == "live":
        print(red("  [WARNING] LIVE trading is active - real funds are at risk."))
    print(yellow("=" * 70))


def parse_args():
    parser = argparse.ArgumentParser(description="ByBit Signal Executor")
    parser.add_argument(
        "--mode",
        choices=["executor", "telegram", "both"],
        default="both",
        help="Run executor only, telegram listener only, or both (default: both)",
    )
    return parser.parse_args()


async def run_executor_only(executor):
    while True:
        await asyncio.sleep(60)
        executor.check_positions()


async def main_async(args):
    executor = None
    listener = None
    state = {"took_over": False}

    if args.mode in ("executor", "both"):
        executor = SignalExecutor()
        executor.start()
        logger.info(green("Signal Executor started"))

    broadcast_claim()

    async def on_takeover(other_device):
        # Another device just started and is taking over - this is a routine handoff,
        # not a real stop, so no Telegram message here (that's what confused the user:
        # seeing "Bot stopped" made it look like the whole bot died). The overall
        # running/stopped status the user cares about is unaffected by a handoff.
        state["took_over"] = True
        logger.info(yellow(f"Newer start on '{other_device}' - stopping this instance ({DEVICE_NAME})"))
        if executor:
            executor.stop()
        if listener:
            await listener.client.disconnect()

    watcher_task = asyncio.create_task(watch_for_takeover(on_takeover))

    work_tasks = []
    if args.mode in ("telegram", "both"):
        api_id, api_hash, channel = resolve_credentials()
        listener = TelegramSignalListener(api_id, api_hash, channel)
        work_tasks.append(asyncio.create_task(listener.listen()))
    if args.mode in ("executor", "both"):
        # Polls check_positions() every 60s to trail SLs (Plan A/B), detect final-TP fills,
        # and clean up stopped-out trades. Must run alongside the listener in "both" mode -
        # without it, open trades are never re-checked after entry.
        work_tasks.append(asyncio.create_task(run_executor_only(executor)))

    done, pending = await asyncio.wait({*work_tasks, watcher_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        exc = task.exception()
        if exc and not isinstance(exc, asyncio.CancelledError):
            raise exc

    return state["took_over"]


if __name__ == "__main__":
    print_startup_banner()
    args = parse_args()
    send_telegram_message(
        f"🟢 Bot started (mode={args.mode}, env={TRADING_ENV.upper()}, "
        f"{'DEMO simulation' if DEMO_MODE else 'REAL network calls'})"
    )
    took_over = False
    try:
        took_over = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.info(yellow("Shutting down..."))
    finally:
        # Only report a real stop (Ctrl+C, crash) - not a handoff where another device
        # already took over, since the bot is still running (just elsewhere).
        if not took_over:
            send_telegram_message("🔴 Bot stopped")
