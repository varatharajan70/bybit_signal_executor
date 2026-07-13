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
from config.settings import SIGNAL_INPUT_FILE, MAX_CONSECUTIVE_FAILURES, SL_STAGES

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
                    # Still mark as processed so we don't re-log the same invalid signal forever
                    self.processed_signals.add(signal_hash)
                    self.last_signal_time = file_time
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
                # Still mark as processed so we don't re-log the same unparseable signal forever
                self.processed_signals.add(signal_hash)
                self.last_signal_time = file_time

        except Exception as e:
            logger.error(red(f"Error checking signals: {e}"))

    def execute_trade(self, signal):
        """Execute trade for a signal. Entry + stop-loss go on the main order; each TP
        level is placed as a separate reduce-only limit leg so partial exits actually
        happen (and can be tracked one at a time by check_positions)."""
        try:
            logger.info(yellow(f"Executing: {signal.symbol} {signal.side} @ {signal.entry}"))
            logger.info(yellow(f"  Qty: {signal.qty}, Stop: {signal.stop}, TPs: {signal.tps}"))

            stop_pct = abs(signal.entry - signal.stop) / signal.entry
            leverage = self.client.calc_safe_leverage(signal.symbol, stop_pct)
            logger.info(yellow(f"{signal.symbol}: using {leverage}x leverage (stop {stop_pct*100:.2f}%)"))
            lev_result = self.client.set_leverage(signal.symbol, leverage)
            if lev_result.get("retCode") != 0:
                logger.warning(yellow(
                    f"{signal.symbol}: could not set leverage to {leverage}x - "
                    f"{lev_result} - proceeding with whatever leverage is already set"
                ))

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
                    f"✅ Order placed: #{signal.symbol} {signal.side} @ {signal.entry}\n"
                    f"Qty {signal.qty}  SL {signal.stop}  TPs {signal.tps}\n"
                    f"Order ID: {order_id}",
                    reply_to=signal.msg_id,
                )
                self._place_runner_exit(signal, order_id)
            else:
                logger.error(red(f"[FAIL] Order failed: {result}"))
                self._note_failure()
                send_telegram_message(
                    f"❌ Order failed: #{signal.symbol} {signal.side} @ {signal.entry}\n"
                    f"{result.get('retMsg', result)}",
                    reply_to=signal.msg_id,
                )

        except Exception as e:
            logger.error(red(f"Trade execution error: {e}"))
            self._note_failure()
            send_telegram_message(
                f"❌ Trade execution error: #{signal.symbol} {signal.side} - {e}",
                reply_to=signal.msg_id,
            )

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
            strategy="legs",
            leg_qty=leg_qty,
            tp_order_ids=tp_order_ids,
            signal_msg_id=signal.msg_id,
        )

    def _place_runner_exit(self, signal, entry_order_id):
        """Breakeven-runner strategy: no partial exits anywhere. The active risk plan's
        SL_STAGES only trail the stop-loss (handled in _check_runner_trade); the FULL
        position size exits in one shot via a single reduce-only limit order at the final TP.

        The entry order just placed may still be an unfilled limit order (not yet a live
        position), in which case Bybit rejects a reduce-only order against it (retCode
        110017, "current position is zero"). That's expected, not fatal: exit_order_id is
        left None here and _check_runner_trade retries this same placement once the
        position is confirmed live."""
        close_side = "Sell" if signal.side == "Buy" else "Buy"
        exit_tp = signal.tps[-1]
        resp = self.client.place_reduce_only_limit(signal.symbol, close_side, signal.qty, exit_tp)
        if resp.get("retCode") == 0:
            exit_order_id = resp.get("result", {}).get("orderId")
        else:
            logger.warning(yellow(
                f"[RETRY] Exit order not placed yet for {signal.symbol} @ {exit_tp} (entry likely "
                f"still unfilled): {resp} - will retry once the position is live"
            ))
            exit_order_id = None

        self.trades.open_trade(
            symbol=signal.symbol,
            side=signal.side,
            entry=signal.entry,
            stop=signal.stop,
            tps=signal.tps,
            qty_total=signal.qty,
            strategy="runner",
            exit_tp=exit_tp,
            exit_order_id=exit_order_id,
            signal_msg_id=signal.msg_id,
            sl_stages=SL_STAGES,
            entry_order_id=entry_order_id,
        )

    def _note_failure(self):
        self.consecutive_failures += 1
        # Only fire once when crossing the threshold, not on every failure after it - this
        # is meant to be a one-time alert, not a critical-level log line per signal for the
        # rest of the run.
        if self.consecutive_failures == MAX_CONSECUTIVE_FAILURES:
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

                tracked_symbols = self._check_trades(live_symbols)

                # A symbol counts as "still open" for risk-manager purposes if it's either a
                # live position OR still has a pending trade_tracker record (e.g. entry order
                # placed but not filled yet) - otherwise sync_open_symbols would drop it from
                # open_symbols the moment it's not live, wrongly allowing a duplicate signal
                # for the same symbol to pass the "already have an open position" re-entry
                # check while the original entry order is still resting on the exchange.
                still_open_symbols = live_symbols | set(self.trades.all_trades().keys())
                closed_symbols = self.risk.sync_open_symbols(still_open_symbols)
                for symbol in closed_symbols:
                    if symbol in tracked_symbols:
                        continue  # already notified with full detail by _check_tp_legs
                    if self.trades.get_trade(symbol) is not None:
                        # risk_manager marks a symbol "open" as soon as its entry order is
                        # placed, not once it fills - so a still-resting, never-yet-filled
                        # entry looks identical to a just-closed position here (tracked but
                        # not live). trade_tracker still holding a record for it means
                        # _check_trades looked and correctly decided it's not actually closed
                        # yet (e.g. entry order still "New") - don't misreport that as closed.
                        continue
                    logger.info(green(f"Position closed: {symbol}"))
                    send_telegram_message(f"🔔 Position closed: #{symbol}")

                fixes = self.client.check_and_fix_protection(skip_tp_symbols=set(self.trades.all_trades().keys()))
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

    def _position_still_open(self, symbol):
        """Fresh, targeted re-check for one symbol - used before declaring a trade closed
        via its stop-loss because it was absent from live_symbols. live_symbols is a single
        batch snapshot taken once per check_positions() poll; it can be stale or racy right
        after entry (or on a transient API hiccup), and trusting it alone would falsely
        declare "SL hit", detach tracking from a position that's actually still open, and
        free up its risk-manager slot for a duplicate entry. On an API error, assume still
        open rather than risk a false SL-hit."""
        result = self.client.get_positions(symbol)
        if result.get("retCode") != 0:
            return True
        positions = result.get("result", {}).get("list", [])
        return any(float(p.get("size", 0) or 0) > 0 for p in positions)

    def _check_trades(self, live_symbols):
        """Poll every tracked trade and drive its lifecycle (TP hits, SL moves, close
        notifications), dispatching per trade on its "strategy" - old "legs" trades (partial
        exit per TP level) vs new "runner" trades (TP1 only arms breakeven, full qty exits at
        the final TP). Returns the set of symbols notified this round, so check_positions
        doesn't also send a generic close notification."""
        notified_closed = set()

        for symbol, trade in self.trades.all_trades().items():
            if trade["strategy"] == "runner":
                closed = self._check_runner_trade(symbol, trade, live_symbols)
            else:
                closed = self._check_legs_trade(symbol, trade, live_symbols)
            if closed:
                notified_closed.add(symbol)

        return notified_closed

    def _check_legs_trade(self, symbol, trade, live_symbols):
        """Old strategy: qty split evenly across every TP level, each its own reduce-only
        leg order. Returns True if the trade closed (and was notified) this round."""
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
            return True  # closed already (e.g. all-targets-hit fired above)

        if all(trade["legs_filled"]):
            self._handle_all_targets_hit(symbol, trade)
            return True
        elif symbol not in live_symbols:
            if self._position_still_open(symbol):
                return False  # stale/racy snapshot this poll - still open, recheck next cycle
            self._handle_sl_hit(symbol, trade)
            return True
        return False

    def _maybe_advance_sl(self, symbol, trade, new_stage, trigger, sl_price, trigger_label, sl_label):
        """Runner strategy: move the SL to sl_price once price crosses trigger, and record
        the new sl_stage. No qty ever exits here - only the stop-loss price moves."""
        price = self.client.get_ticker(symbol)
        if price is None:
            return
        crossed = price >= trigger if trade["side"] == "Buy" else price <= trigger
        if not crossed:
            return
        self.client.set_stop_loss(symbol, sl_price)
        self.trades.mark_sl_stage(symbol, new_stage, sl_label)
        emoji = "🔒" if sl_label == "breakeven" else "🔐"
        logger.info(green(
            f"{symbol}: {trigger_label} {trigger} reached, SL moved to {sl_label} ({sl_price})"
        ))
        send_telegram_message(
            f"{emoji} SL → {sl_label} ({sl_price}): #{symbol} reached {trigger_label} {trigger}",
            reply_to=trade.get("signal_msg_id"),
        )

    def _retry_place_runner_exit(self, symbol, trade):
        """Retry the reduce-only full-qty exit order for a runner trade whose entry order
        hadn't filled yet when execute_trade() first tried to place it. Called once the
        position is confirmed live (in live_symbols)."""
        close_side = "Sell" if trade["side"] == "Buy" else "Buy"
        resp = self.client.place_reduce_only_limit(symbol, close_side, trade["qty_total"], trade["exit_tp"])
        if resp.get("retCode") == 0:
            exit_order_id = resp.get("result", {}).get("orderId")
            self.trades.set_exit_order(symbol, exit_order_id)
            logger.info(green(f"{symbol}: exit order placed (retry) @ {trade['exit_tp']}"))
            return exit_order_id
        logger.error(red(f"[FAIL] Exit order retry failed for {symbol} @ {trade['exit_tp']}: {resp}"))
        return None

    def _check_runner_trade(self, symbol, trade, live_symbols):
        """Breakeven-runner strategy: no partial exits anywhere. The active risk plan's
        SL_STAGES trail the SL through progressively later TP prices (breakeven, then
        further). Full qty exits in one shot via a single reduce-only limit order at the
        final TP. Returns True if the trade closed (and was notified) this round."""
        if trade["exit_order_id"] is None:
            if symbol not in live_symbols:
                # Entry order (a limit order) may just not have filled yet - or it may have
                # filled AND already closed (native SL hit) in the time between polls, before
                # we ever got to place the exit order. Those look identical from here (no
                # exit order, not live), so check the entry order's own status to tell them
                # apart instead of assuming "still waiting" forever.
                entry_order_id = trade.get("entry_order_id")
                entry_status = self.client.get_order_status(symbol, entry_order_id) if entry_order_id else None
                if entry_status == "Filled":
                    return self._close_runner_stopped(symbol, trade)
                if entry_status in ("Cancelled", "Rejected", "Deactivated"):
                    logger.info(yellow(f"{symbol}: entry order {entry_status.lower()}, no position was opened"))
                    send_telegram_message(
                        f"⚪ Entry order {entry_status.lower()}: #{symbol} - no position opened",
                        reply_to=trade.get("signal_msg_id"),
                    )
                    self.trades.close_trade(symbol)
                    return True
                return False  # still New/unknown - genuinely still waiting
            if self._retry_place_runner_exit(symbol, trade) is None:
                return False  # still couldn't place it - try again next cycle
            trade = self.trades.get_trade(symbol)

        tps = trade["tps"]
        sl_stages = trade["sl_stages"]
        stage = trade["sl_stage"]
        for new_stage in range(len(sl_stages), stage, -1):
            if stage >= new_stage:
                continue
            trigger_idx, sl_idx = sl_stages[new_stage - 1]
            if trigger_idx >= len(tps):
                continue  # signal has fewer TPs than this plan's stage needs - skip it
            trigger = tps[trigger_idx]
            sl_price = trade["entry"] if sl_idx is None else tps[sl_idx]
            self._maybe_advance_sl(
                symbol, trade, new_stage, trigger, sl_price,
                trigger_label=f"TP{trigger_idx + 1}",
                sl_label="breakeven" if sl_idx is None else f"TP{sl_idx + 1}",
            )
            trade = self.trades.get_trade(symbol)
            stage = trade["sl_stage"]

        exit_order_id = trade["exit_order_id"]
        status = self.client.get_order_status(symbol, exit_order_id) if exit_order_id else None

        if status == "Filled":
            profit = abs(trade["exit_tp"] - trade["entry"]) * trade["qty_total"]
            risk = abs(trade["entry"] - trade["stop"])
            rr = profit / (risk * trade["qty_total"]) if risk > 0 else 0.0
            duration_min = (time.time() - trade["opened_at"]) / 60
            logger.info(green(f"{symbol}: final target hit, profit {profit:.2f} USDT"))
            send_telegram_message(
                f"🎉 Final target hit! #{symbol} {trade['side']} @ {trade['exit_tp']}\n"
                f"Full qty profit: {profit:.2f} USDT (RR {rr:.2f})\n"
                f"Duration: {duration_min:.1f} min",
                reply_to=trade.get("signal_msg_id"),
            )
            self.trades.close_trade(symbol)
            self.risk.record_exit(symbol)
            return True

        if symbol not in live_symbols:
            if self._position_still_open(symbol):
                return False  # stale/racy snapshot this poll - still open, recheck next cycle
            if exit_order_id:
                self.client.cancel_order(symbol, exit_order_id)
            return self._close_runner_stopped(symbol, trade)

        return False

    def _close_runner_stopped(self, symbol, trade):
        """Shared notify+cleanup for a runner trade that closed via its stop-loss, at
        whatever stage the SL had trailed to (0 = original stop, still the entry price).
        Used both when we had a live exit order to react to, and when the position
        closed before an exit order ever got placed (see _check_runner_trade)."""
        tps = trade["tps"]
        sl_stages = trade["sl_stages"]
        stage = trade["sl_stage"]
        duration_min = (time.time() - trade["opened_at"]) / 60

        if stage == 0:
            loss = abs(trade["entry"] - trade["stop"]) * trade["qty_total"]
            logger.info(red(f"{symbol}: SL hit, position closed"))
            msg = f"🛑 SL hit: #{symbol} {trade['side']}\nLoss: {loss:.2f} USDT"
        else:
            trigger_idx, sl_idx = sl_stages[stage - 1]
            sl_price = trade["entry"] if sl_idx is None else tps[sl_idx]
            if sl_idx is None:
                logger.info(green(f"{symbol}: breakeven hit, position closed"))
                msg = (
                    f"🔒 Breakeven stop hit: #{symbol} {trade['side']}\n"
                    f"No loss - exited at entry ({sl_price})"
                )
            else:
                pnl = abs(sl_price - trade["entry"]) * trade["qty_total"]
                logger.info(green(f"{symbol}: locked-in profit stop hit, position closed"))
                msg = (
                    f"💰 Locked-in profit stop hit: #{symbol} {trade['side']}\n"
                    f"Exited at TP{sl_idx + 1} ({sl_price}) - profit: {pnl:.2f} USDT"
                )

        send_telegram_message(
            msg + f"\nDuration: {duration_min:.1f} min",
            reply_to=trade.get("signal_msg_id"),
        )
        self.trades.close_trade(symbol)
        self.risk.record_exit(symbol)
        return True

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

        reply_to = trade.get("signal_msg_id")
        if index == 0:
            send_telegram_message(
                f"🎯 TP1 hit: #{symbol} @ {tp_price}\nProfit this leg: {profit:.2f} USDT",
                reply_to=reply_to,
            )
        elif index == 1:
            self.client.set_stop_loss(symbol, entry)
            self.trades.update_sl_stage(symbol, "breakeven")
            send_telegram_message(
                f"🎯 TP2 hit: #{symbol} @ {tp_price}\nProfit this leg: {profit:.2f} USDT\n"
                f"🔒 SL moved to breakeven ({entry})",
                reply_to=reply_to,
            )
        else:
            new_sl = trade["tps"][index - 1]
            self.client.set_stop_loss(symbol, new_sl)
            self.trades.update_sl_stage(symbol, f"tp{index}")
            send_telegram_message(
                f"🎯 TP{tp_num} hit: #{symbol} @ {tp_price}\nProfit this leg: {profit:.2f} USDT\n"
                f"🔒 SL moved to TP{index} ({new_sl})",
                reply_to=reply_to,
            )

    def _handle_all_targets_hit(self, symbol, trade):
        total_profit = sum(abs(tp - trade["entry"]) * trade["leg_qty"] for tp in trade["tps"])
        risk = abs(trade["entry"] - trade["stop"])
        avg_rr = (total_profit / trade["qty_total"]) / risk if risk > 0 else 0.0
        duration_min = (time.time() - trade["opened_at"]) / 60
        logger.info(green(f"{symbol}: all TP targets hit, total profit {total_profit:.2f} USDT"))

        send_telegram_message(
            f"🎉 All targets achieved! #{symbol} {trade['side']}\n"
            f"Entry {trade['entry']} - all {len(trade['tps'])} TPs hit\n"
            f"Total profit: {total_profit:.2f} USDT (avg RR {avg_rr:.2f})\n"
            f"Duration: {duration_min:.1f} min",
            reply_to=trade.get("signal_msg_id"),
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
            f"🛑 SL hit — position closed: #{symbol} {trade['side']}\n"
            f"{legs_hit}/{len(trade['tps'])} TP legs had already filled\n"
            f"Banked profit from filled legs: {filled_profit:.2f} USDT\n"
            f"Duration: {duration_min:.1f} min",
            reply_to=trade.get("signal_msg_id"),
        )
        self.trades.close_trade(symbol)
        self.risk.record_exit(symbol)
