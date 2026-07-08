#!/usr/bin/env python3
"""Test signal parsing with various formats."""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.signal_handler import parse_signal_text, validate_signal

test_signals = [
    # Telegram format (emoji-based)
    """#BTCUSDT | SHORT SETUP
🪙 Entry: $63,500
🎯 Target1: $63,000
🎯 Target2: $62,500
🎯 Target3: $62,000
🎯 Target4: $61,500
🎯 Target5: $61,000
🛑 STOP: $64,500""",

    # Telegram format without emojis
    """#ETHUSDT | LONG SETUP
Entry: $3,500
Target1: $3,600
Target2: $3,650
Target3: $3,700
Target4: $3,750
Target5: $3,800
STOP: $3,400""",

    # JSON format
    """{"symbol": "BTCUSDT", "side": "Sell", "entry": 63500, "stop": 64500, "tp": 63000}""",

    # Key-value format
    """symbol: BNBUSDT
side: Buy
entry: 615.5
stop: 610.0
tp: 625.0""",
]

print("=" * 60)
print("Signal Parsing Test")
print("=" * 60)

for i, test_signal in enumerate(test_signals, 1):
    print(f"\n[Test {i}]")
    print(f"Input:\n{test_signal[:80]}...")

    signal = parse_signal_text(test_signal)

    if signal:
        print(f"[OK] Parsed: {signal}")
        is_valid, msg = validate_signal(signal)
        if is_valid:
            print(f"[OK] Valid: {msg}")
        else:
            print(f"[FAIL] Invalid: {msg}")
    else:
        print("[FAIL] Failed to parse")

print("\n" + "=" * 60)
print("All tests completed!")
