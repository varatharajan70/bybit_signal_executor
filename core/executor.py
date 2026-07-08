# core/executor.py
# Executes trading signals read from the signal input file.
# Monitors the file for new signals and places (demo/simulated) trades on ByBit.

import os
import time
import threading
import logging

from core.bybit_client import ByBitClient
from core.signal_handler import parse_signal_text, validate_signal
from core.risk_manager import RiskManager
from core.trade_tracker import TradeTracker
from core.notifier import send_telegram_message
from core.colors import red, green, yellow
from config.settings import SIGNAL_INPUT_FILE, MAX_CONSECUTIVE_FAILURES

logger = logging.getLogger(__name__)


class SignalExecutor:
    def __init__(self):
        self.client = ByBitClient()
        self.risk = RiskManager()
        self.trades = TradeTracker()
        self.running = False
        self.last_signal_time = 0
        self.processed_signals = set()
        self.consecutive_failures = 0

    def start(self):
        """Start monitoring for signals."""
        self.running = True
        logger.info(yellow("Starting Signal Executor"))
        logger.info(yellow(f"Monitoring {SIGNAL_INPUT_FILE} for new signals..."))

        if not os.path.exists(SIGNAL_INPUT_FILE):
            with open(SIGNAL_INPUT_FILE, "w") as f:
                f.write("")

        # Ignore whatever is already sitting in the file at startup (stale test data,
        # a leftover signal from before a restart, etc.) - only react to writes that
        # happen from this point forward.
        self.last_signal_time = time.time()

        monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        monitor_thread.start()

    def stop(self):
        """Stop monitoring."""
        self.running = False
        logger.info(yellow("Stopping Signal Executor"))

    def _monitor_loop(self):
        while self.running:
            try:
                self._check_for_signals()
                time.sleep(1)
            except Exception as e:
                logger.error(red(f"Monitor loop error: {e}"))
                time.sleep(5)

    def _check_for_signals(self):
        try:
            if not os.path.exists(SIGNAL_INPUT_FILE):
                return

            with open(SIGNAL_INPUT_FILE, "r") as f:
                content = f.read().strip()

            if not content:
                return

            file_time = os.path.getmtime(SIGNAL_INPUT_FILE)
            signal_hash = hash(content)

            if signal_hash in self.processed_signals:
                return

            if file_time <= self.last_signal_time:
                return

            signal = parse_signal_text(content)

            if signal:
                is_valid, msg = validate_signal(signal)
                if not is_valid:
                    logger.warning(red(f"Invalid signal: {msg}"))
                    return

                allowed, reason = self.risk.can_trade(signal.symbol)
                if not allowed:
                    logger.warning(yellow(f"[RISK BLOCK] {signal.symbol}: {reason}"))
                    # Still mark as processed so we don't re-log the same blocked signal forever
                    self.processed_signals.add(signal_hash)
                    self.last_signal_time = file_time
                    return

                self.execute_trade(signal)
                self.processed_signals.add(signal_hash)
                self.last_signal_time = file_time
            else:
                logger.warning(red("Could not parse signal"))

        except Exception as e:
            logger.error(red(f"Error checking signals: {e}"))

    def execute_trade(self, signal):
        """Execute trade for a signal. Entry + stop-loss go on the main order; each TP
        level is placed as a separate reduce-only limit leg so partial exits actually
        happen (and can be tracked one at a time by check_positions)."""
        try:
            logger.info(yellow(f"Executing: {signal.symbol} {signal.side} @ {signal.entry}"))
            logger.info(yellow(f"  Qty: {signal.qty}, Stop: {signal.stop}, TPs: {signal.tps}"))

            result = self.client.place_order(
                symbol=signal.symbol,
                side=signal.side,
                qty=signal.qty,
                price=signal.entry,
                stop_price=signal.stop,
                tp_price=None,  # TP legs are placed separately below
            )

            if result.get("retCode") == 0:
                order_id = result.get("result", {}).get("orderId", "N/A")
                logger.info(green(f"[OK] Order placed: {order_id}"))
                logger.info(green(f"  Details: {result}"))
                self.risk.record_entry(signal.symbol)
                self.consecutive_failures = 0
                send_telegram_message(
                    f"✅ Order placed: {signal.symbol} {signal.side} @ {signal.entry}\n"
                    f"Qty {signal.qty}  SL {signal.stop}  TPs {signal.tps}\n"
                    f"Order ID: {order_id}"
                )
                self._place_tp_legs(signal)
            else:
                logger.error(red(f"[FAIL] Order failed: {result}"))
                self._note_failure()
                send_telegram_message(
                    f"❌ Order failed: {signal.symbol} {signal.side} @ {signal.entry}\n"
                    f"{result.get('retMsg', result)}"
                )

        except Exception as e:
            logger.error(red(f"Trade execution error: {e}"))
            self._note_failure()
            send_telegram_message(f"❌ Trade execution error: {signal.symbol} {signal.side} - {e}")

    def _place_tp_legs(self, signal):
        """Split the position into one reduce-only limit order per TP level. If a leg's
        share of qty would round below the instrument's minOrderQty, collapse to fewer,
        larger legs instead of placing dust orders."""
        tps = list(signal.tps)
        info = self.client.get_instrument_info(signal.symbol)
        min_qty = info["minOrderQty"] if info else 0.0

        num_legs = len(tps)
        while num_legs > 1 and (signal.qty / num_legs) < min_qty:
            num_legs -= 1
        if num_legs < len(tps):
            logger.warning(yellow(
                f"{signal.symbol}: qty {signal.qty} too small to split into {len(tps)} legs "
                f"(min {min_qty}) - collapsing to {num_legs} leg(s)"
            ))
            tps = [tps[0]] if num_legs == 1 else tps[:num_legs - 1] + [tps[-1]]

        leg_qty = signal.qty / len(tps)
        close_side = "Sell" if signal.side == "Buy" else "Buy"
        tp_order_ids = []
        for tp_price in tps:
            resp = self.client.place_reduce_only_limit(signal.symbol, close_side, leg_qty, tp_price)
            if resp.get("retCode") == 0:
                tp_order_ids.append(resp.get("result", {}).get("orderId"))
            else:
                logger.error(red(f"[FAIL] TP leg order failed for {signal.symbol} @ {tp_price}: {resp}"))
                tp_order_ids.append(None)

        self.trades.open_trade(
            symbol=signal.symbol,
            side=signal.side,
            entry=signal.entry,
            stop=signal.stop,
            tps=tps,
            qty_total=signal.qty,
            leg_qty=leg_qty,
            tp_order_ids=tp_order_ids,
        )

    def _note_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.critical(red(
                f"{self.consecutive_failures} consecutive trade execution failures - "
                "check API connectivity/credentials/margin. Executor is still running "
                "but something is likely wrong."
            ))

    def check_positions(self):
        """Check open positions, reconcile risk-manager tracking against them, drive the
        multi-TP leg lifecycle (TP hits, SL moves, SL-hit/all-targets-hit notifications),
        and make sure every open position still has stop-loss/take-profit protection."""
        try:
            result = self.client.get_positions()
            if result.get("retCode") == 0:
                positions = [p for p in result.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
                live_symbols = {p.get("symbol") for p in positions}
                if positions:
                    logger.info(yellow(f"Open positions: {len(positions)}"))
                    for pos in positions:
                        logger.info(yellow(f"  {pos.get('symbol')}: {pos.get('side')} @ {pos.get('avgPrice')}"))
                else:
                    logger.info(yellow("No open positions"))

                tracked_symbols = self._check_tp_legs(live_symbols)

                closed_symbols = self.risk.sync_open_symbols([p.get("symbol") for p in positions])
                for symbol in closed_symbols:
                    if symbol in tracked_symbols:
                        continue  # already notified with full detail by _check_tp_legs
                    logger.info(green(f"Position closed: {symbol}"))
                    send_telegram_message(f"🔔 Position closed: {symbol}")

                fixes = self.client.check_and_fix_protection()
                for fix in fixes:
                    if fix["errors"]:
                        logger.error(red(f"[PROTECTION] {fix['symbol']}: failed to fix {fix['fixed']} - {fix['errors']}"))
                    else:
                        logger.warning(yellow(f"[PROTECTION] {fix['symbol']}: was missing {fix['fixed']}, re-applied"))
            else:
                logger.error(red(f"Could not fetch positions: {result}"))

            stats = self.risk.stats()
            logger.info(yellow(
                f"Risk status: {stats['signals_today']} signals today, "
                f"{len(stats.get('open_symbols', []))} tracked open positions"
            ))

            summary = self.risk.pop_pending_summary()
            if summary:
                send_telegram_message(
                    f"📊 Daily summary for {summary['date']}: "
                    f"{summary['signals_today']} signals executed"
                )
        except Exception as e:
            logger.error(red(f"Position check error: {e}"))

    def _check_tp_legs(self, live_symbols):
        """Poll each tracked trade's pending TP leg orders for fills, react to newly
        filled legs (SL moves, notifications), and detect full closes (all targets hit,
        or SL hit before every leg filled). Returns the set of symbols notified this
        round, so check_positions doesn't also send a generic close notification."""
        notified_closed = set()

        for symbol, trade in self.trades.all_trades().items():
            newly_filled = []
            for i, (order_id, filled) in enumerate(zip(trade["tp_order_ids"], trade["legs_filled"])):
                if filled or not order_id:
                    continue
                if self.client.get_order_status(symbol, order_id) == "Filled":
                    self.trades.mark_leg_filled(symbol, i)
                    newly_filled.append(i)

            for i in newly_filled:
                self._handle_tp_leg_filled(symbol, trade, i)

            trade = self.trades.get_trade(symbol)
            if trade is None:
                continue  # closed already (e.g. all-targets-hit fired above)

            if all(trade["legs_filled"]):
                self._handle_all_targets_hit(symbol, trade)
                notified_closed.add(symbol)
            elif symbol not in live_symbols:
                self._handle_sl_hit(symbol, trade)
                notified_closed.add(symbol)

        return notified_closed

    def _handle_tp_leg_filled(self, symbol, trade, index):
        """React to one TP leg filling. Works for any number of TP levels:
        TP1 -> notify only, no SL move.
        TP2 -> SL to breakeven (entry).
        TP3 and every level after -> SL trails up to the previous TP's price.
        The final TP is not handled here - it falls through to
        _handle_all_targets_hit, which fires right after in the same sweep."""
        tp_price = trade["tps"][index]
        leg_qty = trade["leg_qty"]
        entry = trade["entry"]
        profit = abs(tp_price - entry) * leg_qty
        tp_num = index + 1
        is_final = index == len(trade["tps"]) - 1
        logger.info(green(f"{symbol}: TP{tp_num} hit @ {tp_price}, profit {profit:.2f} USDT"))

        if is_final:
            return  # all-targets summary covers this leg too, no separate SL move needed

        if index == 0:
            send_telegram_message(f"🎯 TP1 hit: {symbol} @ {tp_price}\nProfit this leg: {profit:.2f} USDT")
        elif index == 1:
            self.client.set_stop_loss(symbol, entry)
            self.trades.update_sl_stage(symbol, "breakeven")
            send_telegram_message(
                f"🎯 TP2 hit: {symbol} @ {tp_price}\nProfit this leg: {profit:.2f} USDT\n"
                f"🔒 SL moved to breakeven ({entry})"
            )
        else:
            new_sl = trade["tps"][index - 1]
            self.client.set_stop_loss(symbol, new_sl)
            self.trades.update_sl_stage(symbol, f"tp{index}")
            send_telegram_message(
                f"🎯 TP{tp_num} hit: {symbol} @ {tp_price}\nProfit this leg: {profit:.2f} USDT\n"
                f"🔒 SL moved to TP{index} ({new_sl})"
            )

    def _handle_all_targets_hit(self, symbol, trade):
        total_profit = sum(abs(tp - trade["entry"]) * trade["leg_qty"] for tp in trade["tps"])
        risk = abs(trade["entry"] - trade["stop"])
        avg_rr = (total_profit / trade["qty_total"]) / risk if risk > 0 else 0.0
        duration_min = (time.time() - trade["opened_at"]) / 60
        logger.info(green(f"{symbol}: all TP targets hit, total profit {total_profit:.2f} USDT"))

        send_telegram_message(
            f"🎉 All targets achieved! {symbol} {trade['side']}\n"
            f"Entry {trade['entry']} - all {len(trade['tps'])} TPs hit\n"
            f"Total profit: {total_profit:.2f} USDT (avg RR {avg_rr:.2f})\n"
            f"Duration: {duration_min:.1f} min"
        )
        self.trades.close_trade(symbol)
        self.risk.record_exit(symbol)

    def _handle_sl_hit(self, symbol, trade):
        filled_profit = sum(
            abs(tp - trade["entry"]) * trade["leg_qty"]
            for tp, filled in zip(trade["tps"], trade["legs_filled"]) if filled
        )
        legs_hit = sum(trade["legs_filled"])
        duration_min = (time.time() - trade["opened_at"]) / 60
        logger.info(red(f"{symbol}: SL hit, position closed ({legs_hit}/{len(trade['tps'])} legs had filled)"))

        # Clean up any TP leg orders that never filled - they'd otherwise sit as dangling
        # reduce-only orders against a position that no longer exists.
        for order_id, filled in zip(trade["tp_order_ids"], trade["legs_filled"]):
            if order_id and not filled:
                self.client.cancel_order(symbol, order_id)

        send_telegram_message(
            f"🛑 SL hit — position closed: {symbol} {trade['side']}\n"
            f"{legs_hit}/{len(trade['tps'])} TP legs had already filled\n"
            f"Banked profit from filled legs: {filled_profit:.2f} USDT\n"
            f"Duration: {duration_min:.1f} min"
        )
        self.trades.close_trade(symbol)
        self.risk.record_exit(symbol)
