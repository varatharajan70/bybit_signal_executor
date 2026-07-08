# core/risk_manager.py
# Guard rails around signal execution: daily signal cap, max concurrent positions,
# and duplicate/re-entry protection. This is the layer that says "no" before an order
# ever reaches ByBitClient - none of these checks require network access.

import json
import logging
import os
import threading
from datetime import datetime, timezone

from config.settings import (
    DAILY_STATS_FILE,
    MAX_SIGNALS_PER_DAY,
    MAX_CONCURRENT_POSITIONS,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class RiskManager:
    """Tracks how many signals have been executed today and which symbols currently
    have an open position, persisting across restarts via DAILY_STATS_FILE so a
    restart can't be used to bypass the daily cap."""

    def __init__(self):
        self._pending_summary = None
        self._state = self._load()

    def _load(self):
        if os.path.exists(DAILY_STATS_FILE):
            try:
                with open(DAILY_STATS_FILE, "r") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                state = {}
        else:
            state = {}

        today = _today_utc()
        if state.get("date") != today:
            # New day (or first run) - reset the daily counter. open_symbols is not reset
            # here; it's kept in sync with live exchange state separately (see sync_open_symbols)
            # so a date rollover doesn't forget genuinely still-open positions.
            state = {"date": today, "signals_today": 0, "open_symbols": state.get("open_symbols", [])}
            self._save(state)
        return state

    def _save(self, state=None):
        state = state if state is not None else self._state
        with open(DAILY_STATS_FILE, "w") as f:
            json.dump(state, f)

    def _refresh_day(self):
        today = _today_utc()
        if self._state.get("date") != today:
            self._pending_summary = {
                "date": self._state.get("date"),
                "signals_today": self._state.get("signals_today", 0),
            }
            self._state = {"date": today, "signals_today": 0, "open_symbols": self._state.get("open_symbols", [])}
            self._save()

    def pop_pending_summary(self):
        """Returns and clears the previous day's stats snapshot, if a UTC rollover
        happened since the last call. None if no rollover is pending."""
        with _lock:
            summary = self._pending_summary
            self._pending_summary = None
            return summary

    def can_trade(self, symbol):
        """Returns (allowed: bool, reason: str). Call this before placing any new order."""
        with _lock:
            self._refresh_day()

            if self._state["signals_today"] >= MAX_SIGNALS_PER_DAY:
                return False, (
                    f"Daily signal cap reached ({self._state['signals_today']}/"
                    f"{MAX_SIGNALS_PER_DAY}) - no more entries until UTC rollover"
                )

            open_symbols = self._state.get("open_symbols", [])
            if len(open_symbols) >= MAX_CONCURRENT_POSITIONS:
                return False, (
                    f"Max concurrent positions reached ({len(open_symbols)}/"
                    f"{MAX_CONCURRENT_POSITIONS})"
                )

            if symbol in open_symbols:
                return False, f"Already have an open position on {symbol} - refusing re-entry"

            return True, "OK"

    def record_entry(self, symbol):
        """Call after a successful order fill to count it against the daily cap and
        track the symbol as having an open position."""
        with _lock:
            self._refresh_day()
            self._state["signals_today"] += 1
            open_symbols = self._state.get("open_symbols", [])
            if symbol not in open_symbols:
                open_symbols.append(symbol)
            self._state["open_symbols"] = open_symbols
            self._save()
            logger.info(
                f"Recorded entry for {symbol} - signals today: "
                f"{self._state['signals_today']}/{MAX_SIGNALS_PER_DAY}, "
                f"open positions: {len(open_symbols)}/{MAX_CONCURRENT_POSITIONS}"
            )

    def record_exit(self, symbol):
        """Call after a position is closed so the symbol can be re-entered / no longer
        counts toward the concurrent-position cap."""
        with _lock:
            open_symbols = self._state.get("open_symbols", [])
            if symbol in open_symbols:
                open_symbols.remove(symbol)
                self._state["open_symbols"] = open_symbols
                self._save()
                logger.info(f"Recorded exit for {symbol} - open positions: {len(open_symbols)}")

    def sync_open_symbols(self, live_symbols):
        """Reconcile tracked open_symbols against the exchange's actual open positions
        (from get_positions()). Corrects drift from a missed record_exit call, a crash,
        or a position closed outside this bot (manually, or via SL/TP fill).

        Returns the set of symbols that were tracked as open but are no longer live -
        i.e. positions that just closed, for notification purposes."""
        with _lock:
            live_set = set(live_symbols)
            tracked_set = set(self._state.get("open_symbols", []))
            closed = tracked_set - live_set
            if live_set != tracked_set:
                logger.info(f"Reconciling open positions: tracked={tracked_set} live={live_set}")
                self._state["open_symbols"] = sorted(live_set)
                self._save()
            return closed

    def stats(self):
        with _lock:
            self._refresh_day()
            return dict(self._state)
