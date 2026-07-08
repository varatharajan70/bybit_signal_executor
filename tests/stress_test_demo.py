#!/usr/bin/env python3
"""
Real ByBit demo API stress test.

Places 20+ orders across many altcoins (via realistic, live-price-based random
signals in JSON / key-value / Telegram-emoji formats), then exercises:
  - leverage setting
  - market order fills
  - open position accounting / limits
  - trailing stop
  - position close
  - fee/commission reporting (execution list)

Only ever talks to BYBIT_DEMO_URL (fake demo-account funds). Requires
config.settings.DEMO_MODE = False to actually hit the network instead of the
local simulator.
"""

import os
import sys
import json
import random
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bybit_client import ByBitClient
from core.signal_handler import parse_signal_text, validate_signal
from config.settings import DEMO_MODE, LEVERAGE, RISK_USD, BYBIT_URL

if DEMO_MODE:
    print("[ABORT] config.settings.DEMO_MODE is True (local simulation only).")
    print("        Set DEMO_MODE = False to run this against the real demo API.")
    sys.exit(1)

client = ByBitClient()

SYMBOLS = [
    "ADAUSDT", "DOGEUSDT", "XRPUSDT", "LINKUSDT", "ALGOUSDT",
    "LTCUSDT", "SOLUSDT", "DOTUSDT", "TRXUSDT", "BNBUSDT", "AVAXUSDT",
]

results = {"orders": [], "leverage": [], "trailing_stop": [], "close": [], "errors": {}}


def record_error(tag, ret):
    code = ret.get("retCode")
    msg = ret.get("retMsg")
    if code != 0:
        results["errors"].setdefault(tag, []).append(f"[{code}] {msg}")
    return code == 0


print("=" * 70)
print("BYBIT DEMO API STRESS TEST")
print(f"Base URL: {BYBIT_URL}  (demo funds only)")
print("=" * 70)

# --- Step 1: fetch live prices ---
prices = {}
for sym in SYMBOLS:
    p = client.get_ticker(sym)
    if p:
        prices[sym] = p
        print(f"  {sym}: live price {p}")
    else:
        print(f"  {sym}: [SKIP] could not fetch ticker")
time.sleep(0.2)

if len(prices) < 5:
    print("[ABORT] Could not fetch enough live prices to build realistic signals.")
    sys.exit(1)

# --- Step 2: build 20+ signals in mixed formats around live prices ---
FORMATS = ["json", "keyvalue", "telegram"]
signals_text = []

for i in range(24):
    sym = random.choice(list(prices.keys()))
    price = prices[sym]
    side = random.choice(["Buy", "Sell"])
    stop_pct = random.uniform(0.01, 0.04)
    tp_pct = stop_pct * random.uniform(1.3, 2.5)

    if side == "Buy":
        entry = price
        stop = price * (1 - stop_pct)
        tp = price * (1 + tp_pct)
    else:
        entry = price
        stop = price * (1 + stop_pct)
        tp = price * (1 - tp_pct)

    fmt = FORMATS[i % len(FORMATS)]
    if fmt == "json":
        text = json.dumps({"symbol": sym, "side": side, "entry": entry, "stop": stop, "tp": tp})
    elif fmt == "keyvalue":
        text = f"symbol: {sym}\nside: {side}\nentry: {entry}\nstop: {stop}\ntp: {tp}"
    else:  # telegram emoji format
        setup = "SHORT SETUP" if side == "Sell" else "LONG SETUP"
        text = (
            f"#{sym.replace('USDT', '')} | {setup}\n"
            f"🪙 Entry: ${entry:.6f}\n"
            f"🎯 Target1: ${tp:.6f}\n"
            f"🛑 STOP: ${stop:.6f}"
        )
    signals_text.append((fmt, text))

print(f"\nGenerated {len(signals_text)} random signals across {len(prices)} symbols\n")

# --- Step 3: parse, validate, set leverage, place market orders ---
placed = 0
for i, (fmt, text) in enumerate(signals_text, 1):
    signal = parse_signal_text(text)
    if not signal:
        print(f"[{i}] [PARSE FAIL] ({fmt}) {text[:60]}...")
        results["errors"].setdefault("parse", []).append(text[:80])
        continue

    is_valid, msg = validate_signal(signal)
    if not is_valid:
        print(f"[{i}] [INVALID] ({fmt}) {signal.symbol} {signal.side} - {msg}")
        continue

    # Set leverage before opening
    lev_result = client.set_leverage(signal.symbol, LEVERAGE)
    lev_ok = record_error("leverage", lev_result)
    results["leverage"].append((signal.symbol, lev_ok, lev_result.get("retMsg")))

    # Place market order (guarantees a fill so we can test trailing-stop/close)
    order_result = client.place_market_order(
        symbol=signal.symbol,
        side=signal.side,
        qty=signal.qty,
        stop_price=signal.stop,
        tp_price=signal.tp,
    )
    order_ok = record_error("order", order_result)
    results["orders"].append({
        "symbol": signal.symbol, "side": signal.side, "qty": signal.qty,
        "stop": signal.stop, "tp": signal.tp, "ok": order_ok,
        "retCode": order_result.get("retCode"), "retMsg": order_result.get("retMsg"),
    })

    status = "[OK]" if order_ok else "[FAIL]"
    print(f"[{i}] {status} ({fmt}) {signal.symbol} {signal.side} qty={signal.qty} "
          f"lev={'OK' if lev_ok else 'FAIL'} -> retCode={order_result.get('retCode')} {order_result.get('retMsg')}")

    if order_ok:
        placed += 1

    time.sleep(0.3)  # avoid rate limits

print(f"\nOrders attempted: {len(results['orders'])}, filled/accepted: {placed}")

# --- Step 4: check open positions (reveals real accounting / any limits) ---
time.sleep(1)
positions_result = client.get_positions()
open_positions = []
if positions_result.get("retCode") == 0:
    open_positions = [p for p in positions_result.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
    print(f"\nOpen positions after stress run: {len(open_positions)}")
    for p in open_positions:
        print(f"  {p.get('symbol')}: {p.get('side')} size={p.get('size')} "
              f"avgPrice={p.get('avgPrice')} leverage={p.get('leverage')} "
              f"liqPrice={p.get('liqPrice')} unrealisedPnl={p.get('unrealisedPnl')}")
else:
    print(f"[FAIL] get_positions error: {positions_result}")

# --- Step 5: trailing stop test on up to 3 open positions ---
for p in open_positions[:3]:
    sym = p.get("symbol")
    side = p.get("side")
    entry_price = float(p.get("avgPrice", 0))
    trailing_amount = round(entry_price * 0.01, 6)  # 1% trailing distance
    ts_result = client.set_trailing_stop(sym, side, trailing_amount)
    ok = record_error("trailing_stop", ts_result)
    results["trailing_stop"].append((sym, ok, ts_result.get("retMsg")))
    print(f"[TRAILING STOP] {sym} amount={trailing_amount} -> {ts_result.get('retCode')} {ts_result.get('retMsg')}")
    time.sleep(0.3)

# --- Step 6: close a few positions, then check executions/fees ---
for p in open_positions[:3]:
    sym = p.get("symbol")
    side = p.get("side")
    close_result = client.close_position(sym, side)
    ok = record_error("close", close_result)
    results["close"].append((sym, ok, close_result.get("retMsg")))
    print(f"[CLOSE] {sym} {side} -> {close_result.get('retCode')} {close_result.get('retMsg')}")
    time.sleep(0.3)

time.sleep(1)
exec_result = client.get_executions(limit=30)
fees = []
if exec_result.get("retCode") == 0:
    for e in exec_result.get("result", {}).get("list", []):
        fees.append((e.get("symbol"), e.get("side"), e.get("execFee"), e.get("feeRate"), e.get("execPrice")))
    print(f"\nRecent executions with fees ({len(fees)}):")
    for f in fees[:15]:
        print(f"  {f[0]} {f[1]} price={f[4]} execFee={f[2]} feeRate={f[3]}")
else:
    print(f"[FAIL] get_executions error: {exec_result}")

# --- Final summary ---
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Signals generated: {len(signals_text)}")
print(f"Orders attempted:  {len(results['orders'])}")
print(f"Orders filled OK:  {sum(1 for o in results['orders'] if o['ok'])}")
print(f"Leverage calls OK: {sum(1 for l in results['leverage'] if l[1])}/{len(results['leverage'])}")
print(f"Open positions:    {len(open_positions)}")
print(f"Trailing stop OK:  {sum(1 for t in results['trailing_stop'] if t[1])}/{len(results['trailing_stop'])}")
print(f"Close OK:          {sum(1 for c in results['close'] if c[1])}/{len(results['close'])}")
print(f"Executions w/fees: {len(fees)}")

if results["errors"]:
    print("\nDistinct errors encountered by category:")
    for tag, errs in results["errors"].items():
        uniq = sorted(set(errs))
        print(f"  [{tag}] ({len(errs)} total)")
        for u in uniq:
            print(f"      {u}")
else:
    print("\nNo errors encountered.")
