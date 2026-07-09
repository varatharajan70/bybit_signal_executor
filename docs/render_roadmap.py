#!/usr/bin/env python3
"""One-off script: renders the project roadmap tree as a PNG image using Pillow.
Not part of the bot itself - run manually whenever the roadmap text needs updating."""

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_PATH_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
OUT_PATH = "/home/varatharajan70/bybit_signal_executor/docs/project_roadmap.png"

FONT_SIZE = 15
LINE_HEIGHT = 20
PADDING = 30
BG = (18, 20, 24)
FG = (223, 226, 230)
HEADER_FG = (110, 200, 255)
WARN_FG = (255, 120, 90)
DIM_FG = (140, 148, 158)

LINES = [
    ("header", "bybit_signal_executor/ -- project roadmap"),
    ("blank", ""),
    ("plain", "main.py                     -- Entry point. Parses --mode (executor|telegram|both)."),
    ("plain", "                               Starts SignalExecutor + TelegramSignalListener,"),
    ("plain", "                               wires the cross-device takeover watcher."),
    ("warn",  "                               WARNING: \"both\" mode never calls check_positions()"),
    ("blank", ""),
    ("header", "config/"),
    ("plain", "  settings.py               -- RISK_USD=2.5, LEVERAGE=10, MAX_SIGNALS_PER_DAY=5,"),
    ("plain", "                               MAX_CONCURRENT_POSITIONS=5, fee/validation constants,"),
    ("plain", "                               TRADING_ENV two-key demo/live gate (demo-pinned)"),
    ("blank", ""),
    ("header", "telegram_bot/"),
    ("plain", "  listener.py               -- Telethon USER client, watches your channel, parses"),
    ("plain", "                               each message -> Signal, posts \"Signal received\""),
    ("plain", "                               (arm/exit levels), writes to data/signal_input.txt"),
    ("blank", ""),
    ("header", "core/"),
    ("plain", "  signal_handler.py         -- Signal class + qty formula (fixed $ risk, capital-"),
    ("plain", "                               independent), parse_signal_text (3 formats),"),
    ("plain", "                               validate_signal (ordering/stop%/fee-clearing checks),"),
    ("plain", "                               calc_rr_and_profit (legs) / calc_runner_profit (new)"),
    ("blank", ""),
    ("plain", "  risk_manager.py           -- Daily signal cap + concurrent-position cap,"),
    ("plain", "                               duplicate-symbol guard, self-heals open_symbols"),
    ("plain", "                               against live exchange state every check_positions()"),
    ("blank", ""),
    ("plain", "  trade_tracker.py          -- Persists active_trades.json. Each trade has a"),
    ("plain", "                               \"strategy\" field:"),
    ("plain", "                                 \"legs\"   -> leg_qty, tp_order_ids[], legs_filled[]"),
    ("plain", "                                            (old trades only, e.g. MUBARAKUSDT)"),
    ("plain", "                                 \"runner\" -> arm_tp, exit_tp, exit_order_id, armed"),
    ("plain", "                                            (all new signals from now on)"),
    ("blank", ""),
    ("plain", "  bybit_client.py           -- All ByBit v5 REST calls: place_order,"),
    ("plain", "                               place_reduce_only_limit, set_stop_loss,"),
    ("plain", "                               get_order_status, cancel_order, get_ticker,"),
    ("plain", "                               get_positions, check_and_fix_protection"),
    ("plain", "                               (skips TP reconciliation for tracked symbols)"),
    ("blank", ""),
    ("plain", "  executor.py               -- SignalExecutor: the core trade lifecycle"),
    ("plain", "      _monitor_loop           watches signal_input.txt (1s poll, own thread)"),
    ("plain", "      execute_trade           validates -> places entry+SL -> _place_runner_exit"),
    ("plain", "      _place_runner_exit      places ONE reduce-only order at final TP, full qty"),
    ("plain", "      _place_tp_legs          (legacy, kept only for old in-flight trades)"),
    ("warn",  "      check_positions         only invoked in mode=\"executor\" -- reconciles"),
    ("plain", "                              positions, dispatches to _check_trades"),
    ("plain", "      _check_trades           routes each tracked trade by strategy"),
    ("plain", "      _check_runner_trade     arms breakeven @ TP1, exits full qty @ final TP"),
    ("plain", "      _check_legs_trade       old per-leg fill/SL-trail logic (unchanged)"),
    ("blank", ""),
    ("plain", "  device_lock.py            -- Telegram pinned-message claim: newest device"),
    ("plain", "                               starts -> older device sees it -> stops itself,"),
    ("plain", "                               no duplicate order placement across devices"),
    ("blank", ""),
    ("plain", "  notifier.py               -- send_telegram_message (+ reply_to threading)"),
    ("plain", "  colors.py                 -- log coloring helpers"),
    ("blank", ""),
    ("header", "data/ (gitignored)"),
    ("plain", "  signal_input.txt          -- hand-off file: listener writes, executor reads"),
    ("plain", "  active_trades.json        -- TradeTracker state"),
    ("plain", "  daily_stats.json          -- RiskManager state"),
    ("blank", ""),
    ("header", "start.sh / stop.sh / tail.sh   -- process lifecycle (bot.pid + background nohup)"),
    ("plain", "diagnose_bybit.sh              -- connectivity/credentials sanity check"),
    ("plain", "test_signal.sh                 -- manually inject a signal into signal_input.txt"),
    ("plain", "tests/                         -- test_signal_parsing.py, test_signals.py,"),
    ("plain", "                                  stress_test_demo.py"),
    ("blank", ""),
    ("blank", ""),
    ("header", "Signal -> Trade lifecycle"),
    ("blank", ""),
    ("dim",   "  Telegram channel message"),
    ("dim",   "          |"),
    ("dim",   "          v"),
    ("plain", "  telegram_bot/listener.py  --parse-->  Signal (entry/stop/tps/qty)"),
    ("dim",   "          |  writes"),
    ("plain", "  data/signal_input.txt"),
    ("dim",   "          |  picked up by _monitor_loop (1s poll)"),
    ("plain", "  core/executor.py: _check_for_signals"),
    ("plain", "          |- validate_signal()  --fail-->  dropped, logged"),
    ("plain", "          |- risk.can_trade()   --fail-->  [RISK BLOCK] logged"),
    ("dim",   "          v  pass"),
    ("plain", "  execute_trade()"),
    ("plain", "          |- place_order(entry, SL)                     -> ByBit"),
    ("plain", "          `- _place_runner_exit()"),
    ("plain", "                |- place_reduce_only_limit(full qty, final TP)  -> ByBit"),
    ("plain", "                `- trades.open_trade(strategy=\"runner\", armed=False)"),
    ("warn",  "          v  (needs check_positions() running -- currently broken in \"both\" mode)"),
    ("plain", "  _check_runner_trade()  every cycle"),
    ("plain", "          |- not armed + price crosses TP1 -> set_stop_loss(entry), mark_armed"),
    ("plain", "          |                                 -> \"Armed\" notification"),
    ("plain", "          |- exit order Filled -> \"Final target hit\" -> close_trade, record_exit"),
    ("plain", "          `- position gone, order not filled -> SL/breakeven hit ->"),
    ("plain", "                      \"SL hit\" ($2.50 loss) or \"Breakeven stop hit\" ($0)"),
]

font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
font_bold = ImageFont.truetype(FONT_PATH_BOLD, FONT_SIZE)

max_width = 0
tmp_img = Image.new("RGB", (10, 10))
tmp_draw = ImageDraw.Draw(tmp_img)
for kind, text in LINES:
    f = font_bold if kind == "header" else font
    bbox = tmp_draw.textbbox((0, 0), text, font=f)
    max_width = max(max_width, bbox[2] - bbox[0])

width = max_width + PADDING * 2
height = LINE_HEIGHT * len(LINES) + PADDING * 2

img = Image.new("RGB", (width, height), BG)
draw = ImageDraw.Draw(img)

y = PADDING
for kind, text in LINES:
    if kind == "header":
        draw.text((PADDING, y), text, font=font_bold, fill=HEADER_FG)
    elif kind == "warn":
        draw.text((PADDING, y), text, font=font, fill=WARN_FG)
    elif kind == "dim":
        draw.text((PADDING, y), text, font=font, fill=DIM_FG)
    else:
        draw.text((PADDING, y), text, font=font, fill=FG)
    y += LINE_HEIGHT

img.save(OUT_PATH)
print(f"Saved {OUT_PATH} ({width}x{height})")
