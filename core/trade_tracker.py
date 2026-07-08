# core/trade_tracker.py
# Persists state for active multi-leg (multi-TP) trades so the executor's monitoring
# loop can detect which TP leg just filled, move the stop-loss accordingly, and send
# the right notification - and so a restart mid-trade doesn't lose track of it.

import json
import logging
import os
import threading
import time

from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

_lock = threading.Lock()
TRADE_TRACKER_FILE = os.path.join(DATA_DIR, "active_trades.json")


class TradeTracker:
    def __init__(self):
        self._state = self._load()

    def _load(self):
        if os.path.exists(TRADE_TRACKER_FILE):
            try:
                with open(TRADE_TRACKER_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self):
        with open(TRADE_TRACKER_FILE, "w") as f:
            json.dump(self._state, f)

    def open_trade(self, symbol, side, entry, stop, tps, qty_total, leg_qty, tp_order_ids, signal_msg_id=None):
        with _lock:
            self._state[symbol] = {
                "side": side,
                "entry": entry,
                "stop": stop,
                "tps": tps,
                "qty_total": qty_total,
                "leg_qty": leg_qty,
                "tp_order_ids": tp_order_ids,  # None for a leg that failed to place
                "legs_filled": [False] * len(tps),
                "sl_moved_to": None,
                "opened_at": time.time(),
                # Telegram message_id of the original "signal received" post, so later TP/SL
                # notifications for this coin can reply to it and thread together.
                "signal_msg_id": signal_msg_id,
            }
            self._save()
            logger.info(f"Trade tracker: opened {symbol} with {len(tps)} TP legs")

    def get_trade(self, symbol):
        with _lock:
            return self._state.get(symbol)

    def mark_leg_filled(self, symbol, index):
        with _lock:
            trade = self._state.get(symbol)
            if trade:
                trade["legs_filled"][index] = True
                self._save()

    def update_sl_stage(self, symbol, stage):
        with _lock:
            trade = self._state.get(symbol)
            if trade:
                trade["sl_moved_to"] = stage
                self._save()

    def close_trade(self, symbol):
        """Removes tracking for a symbol and returns its final record (for a summary
        message), or None if it wasn't tracked."""
        with _lock:
            trade = self._state.pop(symbol, None)
            if trade is not None:
                self._save()
            return trade

    def all_trades(self):
        with _lock:
            return dict(self._state)
