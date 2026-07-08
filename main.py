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

    if args.mode in ("executor", "both"):
        executor = SignalExecutor()
        executor.start()
        logger.info(green("Signal Executor started"))

    if args.mode in ("telegram", "both"):
        api_id, api_hash, channel = resolve_credentials()
        listener = TelegramSignalListener(api_id, api_hash, channel)
        await listener.listen()
    elif executor:
        await run_executor_only(executor)


if __name__ == "__main__":
    print_startup_banner()
    args = parse_args()
    send_telegram_message(
        f"🟢 Bot started (mode={args.mode}, env={TRADING_ENV.upper()}, "
        f"{'DEMO simulation' if DEMO_MODE else 'REAL network calls'})"
    )
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.info(yellow("Shutting down..."))
    finally:
        send_telegram_message("🔴 Bot stopped")
