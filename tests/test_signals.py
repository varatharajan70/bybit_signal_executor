#!/usr/bin/env python3
"""
Generate test signals to verify bot functionality.
Tests: risk calculation, position sizing, TP/SL execution.
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.signal_handler import Signal, validate_signal
from config.settings import SIGNAL_INPUT_FILE, RISK_USD

test_signals = [
    {
        "symbol": "ADAUSDT",
        "side": "Buy",
        "entry": 0.52,
        "stop": 0.50,  # 3.8% stop
        "tp": 0.56,    # 7.7% TP
        "description": "ADA LONG - Low price, 3.8% stop"
    },
    {
        "symbol": "DOGEUSDT",
        "side": "Sell",
        "entry": 0.19,
        "stop": 0.20,  # 5.2% stop
        "tp": 0.17,    # 10.5% TP
        "description": "DOGE SHORT - Medium volatility"
    },
    {
        "symbol": "ALGOUSDT",
        "side": "Buy",
        "entry": 0.47,
        "stop": 0.45,  # 4.2% stop
        "tp": 0.51,    # 8.5% TP
        "description": "ALGO LONG - Moderate risk"
    },
    {
        "symbol": "1000BONKUSDT",
        "side": "Sell",
        "entry": 0.00033,
        "stop": 0.00035,  # 6% stop
        "tp": 0.00031,    # 6% TP
        "description": "BONK SHORT - High volatility"
    },
    {
        "symbol": "LINKUSDT",
        "side": "Buy",
        "entry": 14.5,
        "stop": 13.8,  # 4.8% stop
        "tp": 15.5,    # 6.9% TP
        "description": "LINK LONG - Stable asset"
    },
    {
        "symbol": "XRPUSDT",
        "side": "Sell",
        "entry": 2.15,
        "stop": 2.30,  # 7% stop (near max) - expected to be rejected
        "tp": 1.95,    # 9.3% TP
        "description": "XRP SHORT - High risk"
    },
]

print("=" * 70)
print("ByBit Demo - TEST SIGNAL GENERATOR")
print("=" * 70)
print(f"\nRisk per trade: ${RISK_USD}")
print(f"Sending {len(test_signals)} test signals...\n")

for i, test_signal in enumerate(test_signals, 1):
    print(f"\n[Signal {i}] {test_signal['description']}")
    print(f"  Symbol: {test_signal['symbol']}")
    print(f"  Side: {test_signal['side']}")
    print(f"  Entry: ${test_signal['entry']}")
    print(f"  Stop: ${test_signal['stop']}")
    print(f"  TP: ${test_signal['tp']}")

    signal = Signal(
        symbol=test_signal["symbol"],
        side=test_signal["side"],
        entry=test_signal["entry"],
        stop=test_signal["stop"],
        tp=test_signal["tp"]
    )

    is_valid, msg = validate_signal(signal)
    if not is_valid:
        print(f"  [INVALID] {msg}")
        continue

    stop_pct = abs(signal.entry - signal.stop) / signal.entry * 100
    tp_pct = abs(signal.tp - signal.entry) / signal.entry * 100
    rr_ratio = tp_pct / stop_pct

    print(f"  Stop Distance: {stop_pct:.2f}%")
    print(f"  TP Distance: {tp_pct:.2f}%")
    print(f"  Risk/Reward: 1:{rr_ratio:.2f}")
    print(f"  Position Size: {signal.qty} contracts")
    print(f"  Risk Amount: ${RISK_USD}")

    signal_json = {
        "symbol": signal.symbol,
        "side": signal.side,
        "entry": signal.entry,
        "stop": signal.stop,
        "tp": signal.tp,
        "qty": signal.qty,
        "timestamp": signal.timestamp,
        "source": "test_generator"
    }

    with open(SIGNAL_INPUT_FILE, "w") as f:
        json.dump(signal_json, f)

    print("  [OK] Signal sent to executor")
    time.sleep(2)

print("\n" + "=" * 70)
print("All test signals completed!")
print("=" * 70)
print("\nCheck executor logs to verify:")
print("  [1] Order placement")
print("  [2] Risk calculation ($2.50)")
print("  [3] TP/SL execution")
print("  [4] Position sizing")
