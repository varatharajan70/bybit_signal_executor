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
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
            # Records saved before the "strategy" field existed are all old-style leg-split
            # trades - default them so in-flight trades keep running under the logic that
            # actually placed their exchange-side orders.
            for trade in state.values():
                trade.setdefault("strategy", "legs")
            return state
        return {}

    def _save(self):
        with open(TRADE_TRACKER_FILE, "w") as f:
            json.dump(self._state, f)

    def open_trade(self, symbol, side, entry, stop, tps, qty_total, signal_msg_id=None,
                    strategy="runner", leg_qty=None, tp_order_ids=None,
                    exit_tp=None, exit_order_id=None, sl_stages=None, entry_order_id=None):
        with _lock:
            record = {
                "strategy": strategy,
                "side": side,
                "entry": entry,
                "stop": stop,
                "tps": tps,
                "qty_total": qty_total,
                "sl_moved_to": None,
                "opened_at": time.time(),
                # Telegram message_id of the original "signal received" post, so later TP/SL
                # notifications for this coin can reply to it and thread together.
                "signal_msg_id": signal_msg_id,
            }
            if strategy == "legs":
                record["leg_qty"] = leg_qty
                record["tp_order_ids"] = tp_order_ids  # None for a leg that failed to place
                record["legs_filled"] = [False] * len(tps)
                logger.info(f"Trade tracker: opened {symbol} with {len(tps)} TP legs")
            else:
                record["exit_tp"] = exit_tp
                record["exit_order_id"] = exit_order_id
                # sl_stages: the active risk plan's trail shape, copied onto the trade at
                # open time (config/settings.py: SL_STAGES) so it doesn't shift mid-flight
                # if RISK_PLAN changes later. sl_stage: index into sl_stages reached so far
                # (0 = original stop still live). Final TP always exits full qty regardless
                # of stage - see core/executor.py: _check_runner_trade.
                record["sl_stages"] = sl_stages
                record["sl_stage"] = 0
                # Entry order id, so a later check can tell "entry still unfilled" apart
                # from "entry filled and the position already closed" when the exit order
                # was never placed - see core/executor.py: _check_runner_trade.
                record["entry_order_id"] = entry_order_id
                logger.info(
                    f"Trade tracker: opened {symbol} (runner) - exit @ {exit_tp}"
                )
            self._state[symbol] = record
            self._save()

    def get_trade(self, symbol):
        with _lock:
            return self._state.get(symbol)

    def mark_leg_filled(self, symbol, index):
        with _lock:
            trade = self._state.get(symbol)
            if trade:
                trade["legs_filled"][index] = True
                self._save()

    def set_exit_order(self, symbol, exit_order_id):
        """Runner strategy: record the exit order once it's actually placed. Used both at
        open time and as a retry - the entry may still be an unfilled limit order when
        execute_trade() first tries, so the reduce-only exit order placement can fail
        (Bybit rejects it against a zero position) and gets retried once the position is
        confirmed live (core/executor.py: _check_runner_trade)."""
        with _lock:
            trade = self._state.get(symbol)
            if trade:
                trade["exit_order_id"] = exit_order_id
                self._save()

    def mark_sl_stage(self, symbol, stage, label):
        """Runner strategy: record that the SL was just trailed to a new stage. label is a
        human-readable string (e.g. "breakeven" or "TP2") describing where it moved to."""
        with _lock:
            trade = self._state.get(symbol)
            if trade:
                trade["sl_stage"] = stage
                trade["sl_moved_to"] = label
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
