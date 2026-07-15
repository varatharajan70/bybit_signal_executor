# core/trade_ledger.py
# Append-only record of every REAL signal trade the bot places. Daily P&L reporting reads
# this to tell signal trades apart from manual test trades without a hardcoded symbol list -
# so the journal stays correct even if a future real signal lands on a symbol we once tested.

import json
import os
import threading
import time

from config.settings import DATA_DIR

SIGNAL_TRADES_FILE = os.path.join(DATA_DIR, "signal_trades.jsonl")
_lock = threading.Lock()


def record_signal_trade(symbol, side, entry, order_id):
    """Append one line per placed signal order. Best-effort by design: it must never raise
    into the caller, because a journaling write is not allowed to disrupt live trading."""
    try:
        with _lock:
            with open(SIGNAL_TRADES_FILE, "a") as f:
                f.write(json.dumps({
                    "symbol": symbol,
                    "side": side,
                    "entry": entry,
                    "order_id": order_id,
                    "placed_at": time.time(),
                }) + "\n")
    except Exception:
        pass


def signal_symbols():
    """Set of every symbol the bot has placed a real signal trade on."""
    symbols = set()
    if not os.path.exists(SIGNAL_TRADES_FILE):
        return symbols
    try:
        with open(SIGNAL_TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    symbols.add(json.loads(line)["symbol"])
                except (json.JSONDecodeError, KeyError):
                    continue
    except OSError:
        pass
    return symbols
