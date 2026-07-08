# core/signal_handler.py
# Parse and validate incoming trading signals

from datetime import datetime
import json
import re

from config.settings import RISK_USD, TAKER_FEE_RATE, MIN_NET_PROFIT_MULTIPLE


class Signal:
    """Represent a trading signal. Supports any number of take-profit levels
    (tps[0] is nearest to entry / hit first, tps[-1] is the final target)."""
    def __init__(self, symbol, side, entry, stop, tp=None, tps=None, qty=None, timestamp=None):
        self.symbol = symbol  # e.g., "BTCUSDT"
        self.side = side  # "Buy" or "Sell"
        self.entry = float(entry)
        self.stop = float(stop)

        if tps:
            self.tps = [float(t) for t in tps]
        elif tp is not None:
            self.tps = [float(tp)]
        else:
            raise ValueError("Signal requires either tp or tps")
        self.tp = self.tps[0]  # backward-compat single-TP accessor

        self.qty = qty or self._calculate_qty()
        self.timestamp = timestamp or datetime.now().isoformat()
        self.status = "pending"  # pending, executed, closed

    def _calculate_qty(self):
        """Calculate quantity based on fixed USD risk."""
        stop_distance = abs(self.entry - self.stop)
        qty = RISK_USD / stop_distance if stop_distance > 0 else 1.0
        return round(qty, 4)

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry": self.entry,
            "stop": self.stop,
            "tp": self.tp,
            "tps": self.tps,
            "qty": self.qty,
            "timestamp": self.timestamp,
            "status": self.status,
        }

    def __repr__(self):
        return f"Signal({self.symbol} {self.side} @ {self.entry} | stop {self.stop} | tps {self.tps})"


def calc_rr_and_profit(signal):
    """Per-TP-leg risk:reward ratio and USDT profit, assuming qty is split evenly
    across all TP legs (matches how the executor places reduce-only leg orders)."""
    risk = abs(signal.entry - signal.stop)
    leg_qty = signal.qty / len(signal.tps)
    legs = []
    for tp in signal.tps:
        reward = abs(tp - signal.entry)
        rr = reward / risk if risk > 0 else 0.0
        profit = reward * leg_qty
        legs.append({"tp": tp, "rr": rr, "profit": profit})
    return legs


def parse_signal_text(text):
    """
    Parse signal from text format.
    Supports multiple formats:

    1. Telegram format (emoji-based):
       #BTCUSDT | SHORT SETUP
       Entry: $45000
       Target1: $44000
       STOP: $46000

    2. JSON format:
       {"symbol": "BTCUSDT", "side": "Buy", "entry": 45000, "stop": 44500, "tp": 46000}

    3. Key-value format:
       symbol: BTCUSDT
       side: Buy
       entry: 45000
       stop: 44500
       tp: 46000
    """

    try:
        # Try JSON first
        try:
            data = json.loads(text)
            return Signal(
                symbol=data["symbol"],
                side=data["side"],
                entry=data["entry"],
                stop=data["stop"],
                tp=data.get("tp"),
                tps=data.get("tps"),
                qty=data.get("qty")
            )
        except json.JSONDecodeError:
            pass

        # Try Telegram format (emoji-based with #symbol)
        symbol_match = re.search(r'#(\w+)', text)
        if symbol_match:
            raw_symbol = symbol_match.group(1).upper()
            symbol = raw_symbol if raw_symbol.endswith("USDT") else raw_symbol + "USDT"

            side = "Sell" if "SHORT" in text.upper() else "Buy"

            entry_match = re.search(r'Entry[:\s]*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
            stop_match = re.search(r'STOP[:\s]*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)

            tps = []
            for i in range(1, 11):  # Target1..Target10 - no TP is skipped
                m = re.search(rf'Target{i}[:\s]*\$?([\d,]+\.?\d*)', text, re.IGNORECASE)
                if m:
                    tps.append(float(m.group(1).replace(",", "")))
                else:
                    break  # stop at the first missing target number

            if entry_match and stop_match and tps:
                entry = float(entry_match.group(1).replace(",", ""))
                stop = float(stop_match.group(1).replace(",", ""))

                return Signal(
                    symbol=symbol,
                    side=side,
                    entry=entry,
                    stop=stop,
                    tps=tps,
                )

        # Try key-value format
        lines = text.strip().split("\n")
        data = {}
        for line in lines:
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip().replace("$", "").replace(",", "")
                data[key] = value

        if all(k in data for k in ["symbol", "side", "entry", "stop"]) and ("tp" in data or "tps" in data):
            tps = None
            if "tps" in data:
                tps = [float(v) for v in data["tps"].split(",") if v.strip()]
            return Signal(
                symbol=data["symbol"],
                side=data["side"],
                entry=data["entry"],
                stop=data["stop"],
                tp=data.get("tp"),
                tps=tps,
                qty=data.get("qty")
            )

        return None
    except Exception as e:
        print(f"[Signal Parser] Error: {e}")
        return None


def validate_signal(signal):
    """Validate signal parameters."""
    if not signal:
        return False, "Invalid signal"

    if not signal.symbol.endswith("USDT"):
        return False, f"Invalid symbol: {signal.symbol}"

    if signal.side not in ["Buy", "Sell"]:
        return False, f"Invalid side: {signal.side}"

    if signal.side == "Buy":
        if not all(signal.stop < signal.entry < tp for tp in signal.tps):
            return False, f"Invalid prices for LONG: stop {signal.stop} < entry {signal.entry} < tps {signal.tps}"
        if signal.tps != sorted(signal.tps):
            return False, f"TPs must be ascending for LONG (nearest first): {signal.tps}"
    else:
        if not all(tp < signal.entry < signal.stop for tp in signal.tps):
            return False, f"Invalid prices for SHORT: tps {signal.tps} < entry {signal.entry} < stop {signal.stop}"
        if signal.tps != sorted(signal.tps, reverse=True):
            return False, f"TPs must be descending for SHORT (nearest first): {signal.tps}"

    stop_pct = abs(signal.entry - signal.stop) / signal.entry
    if not (0.001 <= stop_pct <= 0.06):  # 0.1% to 6% (relaxed for edge cases)
        return False, f"Stop % out of range: {stop_pct*100:.2f}%"

    # Fee-aware check: for scalping, a tight TP can be a guaranteed loser once round-trip
    # taker fees (entry + exit) are subtracted. Require every TP leg's distance to clear the
    # round-trip fee cost by a safety multiple before we ever place the trade - the nearest TP
    # is hit first and most often, so it matters most, but a losing later leg isn't acceptable
    # either.
    round_trip_fee_pct = TAKER_FEE_RATE * 2
    min_required_tp_pct = round_trip_fee_pct * MIN_NET_PROFIT_MULTIPLE
    for i, tp in enumerate(signal.tps, start=1):
        tp_pct = abs(tp - signal.entry) / signal.entry
        if tp_pct < min_required_tp_pct:
            return False, (
                f"TP{i} too tight to clear fees: TP{i} {tp_pct*100:.3f}% < required "
                f"{min_required_tp_pct*100:.3f}% (round-trip fee {round_trip_fee_pct*100:.3f}% "
                f"x {MIN_NET_PROFIT_MULTIPLE})"
            )

    return True, "OK"
