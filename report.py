#!/usr/bin/env python3
# report.py
# Regenerate the P&L journal CSVs from Bybit's authoritative closed-pnl record.
# Safe to run any time / repeatedly - it rebuilds from source, never appends duplicates.
#
# Writes:
#   reports/signal_trades.csv  - one row per real signal trade (gross / fees / net)
#   reports/daily_pnl.csv      - one row per day (totals, win rate, running cumulative net)
#
# "Real signal trade" = any symbol the bot has tagged in data/signal_trades.jsonl
# (core/trade_ledger.py records these at order time), plus a fixed historical seed for the
# trades that pre-date tagging. Manual test trades (BTC/ETH/SOL probing, the Jul 8-9 dumps)
# are simply never tagged, so they're excluded automatically.

import csv
import os
import sys
import time

from core.bybit_client import ByBitClient
from core.trade_ledger import signal_symbols
from core.notifier import send_telegram_message
from config.settings import BASE_DIR

REPORTS_DIR = os.path.join(BASE_DIR, "reports")
TRADES_CSV = os.path.join(REPORTS_DIR, "signal_trades.csv")
DAILY_CSV = os.path.join(REPORTS_DIR, "daily_pnl.csv")

# Real signal trades placed before ledger-tagging existed (verified against Bybit closed-pnl
# on 2026-07-15). Kept so the journal is complete for the bot's first days.
HISTORICAL_SEED = {
    "CUSDT", "CELOUSDT", "DOODUSDT", "PHAUSDT", "ANKRUSDT", "BREVUSDT", "B2USDT",
    "PUNDIXUSDT", "MELANIAUSDT", "AZTECUSDT", "CVCUSDT", "MUUSDT", "PIPPINUSDT",
    "CRWVUSDT", "BLURUSDT", "COINUSDT", "ALPINEUSDT", "PENGUUSDT",
}

DAY_MS = 24 * 60 * 60 * 1000
LOOKBACK_DAYS = 30


def fetch_closed_trades(client):
    """Pull closed-pnl in <=1-day windows (each day has few trades, so one page per window -
    avoids the cursor-pagination signing quirk). Deduped on (symbol, updatedTime)."""
    now_ms = client._now_ms()
    seen = {}
    for d in range(LOOKBACK_DAYS, -1, -1):
        end = now_ms - (d - 1) * DAY_MS
        start = now_ms - d * DAY_MS
        if start > now_ms:
            continue
        r = None
        for attempt in range(5):
            r = client._request("GET", "/v5/position/closed-pnl", params={
                "category": "linear", "limit": 100,
                "startTime": start, "endTime": min(end, now_ms),
            })
            if r.get("retCode") == 0:
                break
            time.sleep(2 * (attempt + 1))
        time.sleep(0.8)
        if not r or r.get("retCode") != 0:
            print(f"  warn: window d-{d} failed: {r.get('retMsg') if r else 'no response'}")
            continue
        for t in r.get("result", {}).get("list", []):
            seen[(t.get("symbol"), t.get("updatedTime"))] = t
    return list(seen.values())


def main():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    client = ByBitClient()
    keep = signal_symbols() | HISTORICAL_SEED

    rows = fetch_closed_trades(client)
    rows = [t for t in rows if t.get("symbol") in keep]
    rows.sort(key=lambda x: int(x.get("updatedTime", 0)))

    trade_records = []
    for t in rows:
        net = float(t.get("closedPnl", 0))
        fees = float(t.get("openFee", 0) or 0) + float(t.get("closeFee", 0) or 0)
        ts = int(t.get("updatedTime", 0)) / 1000
        trade_records.append({
            "date": time.strftime("%Y-%m-%d", time.localtime(ts)),
            "time": time.strftime("%H:%M", time.localtime(ts)),
            "symbol": t.get("symbol", ""),
            "side": t.get("side", ""),
            "qty": t.get("qty", ""),
            "entry": t.get("avgEntryPrice", ""),
            "exit": t.get("avgExitPrice", ""),
            "gross": round(net + fees, 4),
            "fees": round(fees, 4),
            "net": round(net, 4),
        })

    # Per-trade CSV
    with open(TRADES_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "date", "time", "symbol", "side", "qty", "entry", "exit", "gross", "fees", "net"])
        w.writeheader()
        w.writerows(trade_records)

    # Daily summary CSV with running cumulative net
    by_day = {}
    for r in trade_records:
        d = by_day.setdefault(r["date"], {"trades": 0, "wins": 0, "losses": 0,
                                          "gross": 0.0, "fees": 0.0, "net": 0.0})
        d["trades"] += 1
        d["wins" if r["net"] >= 0 else "losses"] += 1
        d["gross"] += r["gross"]
        d["fees"] += r["fees"]
        d["net"] += r["net"]

    cumulative = 0.0
    with open(DAILY_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "trades", "wins", "losses", "win_rate_%",
                    "gross_pnl", "fees", "net_pnl", "cumulative_net"])
        for day in sorted(by_day):
            d = by_day[day]
            cumulative += d["net"]
            wr = round(d["wins"] / d["trades"] * 100) if d["trades"] else 0
            w.writerow([day, d["trades"], d["wins"], d["losses"], wr,
                        round(d["gross"], 4), round(d["fees"], 4),
                        round(d["net"], 4), round(cumulative, 4)])

    total_net = sum(r["net"] for r in trade_records)
    total_fees = sum(r["fees"] for r in trade_records)
    wins = sum(1 for r in trade_records if r["net"] >= 0)
    print(f"[{time.strftime('%Y-%m-%d %H:%M')}] report updated: {len(trade_records)} signal trades")
    print(f"  net {total_net:+.2f} USDT | fees {total_fees:.2f} | "
          f"{wins}W/{len(trade_records)-wins}L | cumulative {cumulative:+.2f}")
    print(f"  -> {TRADES_CSV}")
    print(f"  -> {DAILY_CSV}")

    # Telegram daily summary (skip with --quiet, e.g. for ad-hoc reruns)
    if "--quiet" not in sys.argv:
        send_daily_summary(trade_records, total_net, total_fees, wins)


def send_daily_summary(trade_records, total_net, total_fees, total_wins):
    """Post a clean, styled daily summary to Telegram alongside the CSV refresh."""
    today = time.strftime("%Y-%m-%d")
    todays = [r for r in trade_records if r["date"] == today]
    day_net = sum(r["net"] for r in todays)
    day_wins = sum(1 for r in todays if r["net"] >= 0)
    day_losses = len(todays) - day_wins
    total_losses = len(trade_records) - total_wins
    win_rate = round(total_wins / len(trade_records) * 100) if trade_records else 0

    if todays:
        trade_lines = "\n".join(
            f"{'🟢' if r['net'] >= 0 else '🔴'}  #{r['symbol']}  {r['side']}  →  {r['net']:+.2f} USDT"
            for r in todays
        )
        today_block = (
            f"🔶  Trades : {len(todays)}   ( {day_wins}W / {day_losses}L )\n"
            f"🔶  Net P&L : {day_net:+.2f} USDT\n"
            f"\n{trade_lines}"
        )
    else:
        today_block = "🔶  No trades closed today"

    msg = (
        f"💫  DAILY P&L REPORT 💫\n"
        f"\n"
        f"🗓 DATE:  {today}\n"
        f"\n"
        f"{today_block}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"❇️  All-time Net : {total_net:+.2f} USDT\n"
        f"❇️  Win Rate : {win_rate}%   ( {total_wins}W / {total_losses}L )\n"
        f"❇️  Total Fees : {total_fees:.2f} USDT\n"
        f"\n"
        f"✅  Journal CSV updated"
    )
    send_telegram_message(msg)


if __name__ == "__main__":
    main()
