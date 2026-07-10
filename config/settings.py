# config/settings.py
# Central configuration for the ByBit Signal Executor

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Load .env (gitignored) if present, so live/demo credentials can be set there instead of
# hardcoded or exported by hand every session. Silently no-ops if python-dotenv isn't
# installed or no .env file exists - env vars set another way still work either case.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

# --- ByBit API ---
DEMO_MODE = False   # True = local simulation only (no network calls, no real API hit at all).
                    # Set False to make real network requests, to whichever base URL TRADING_ENV
                    # resolves to below (demo by default, live only via the explicit gate).

# TRADING_ENV selects which real API this bot talks to when DEMO_MODE = False.
#   "demo" (default) -> BYBIT_DEMO_URL, using demo-account credentials, fake funds only.
#   "live"           -> BYBIT_LIVE_URL, using REAL account credentials, REAL funds at risk.
# Switching to "live" additionally requires LIVE_TRADING_CONFIRM to exactly match the phrase
# below. This is a deliberate two-key gate: an env var flip alone is not enough to go live.
TRADING_ENV = os.getenv("TRADING_ENV", "demo").strip().lower()
_LIVE_CONFIRM_PHRASE = "I UNDERSTAND THIS RISKS REAL FUNDS"
LIVE_TRADING_CONFIRM = os.getenv("LIVE_TRADING_CONFIRM", "")

if TRADING_ENV == "live" and LIVE_TRADING_CONFIRM != _LIVE_CONFIRM_PHRASE:
    print("!" * 70)
    print("[SAFETY] TRADING_ENV=live requested but LIVE_TRADING_CONFIRM did not match.")
    print(f"         Set LIVE_TRADING_CONFIRM=\"{_LIVE_CONFIRM_PHRASE}\" to actually go live.")
    print("         Forcing TRADING_ENV back to 'demo' for this run.")
    print("!" * 70)
    TRADING_ENV = "demo"

# Demo credentials (fake demo-account funds). Env-only, no hardcoded fallback - set these in a
# gitignored .env file (see .env.example).
BYBIT_API_KEY = os.getenv("BYBIT_DEMO_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_DEMO_API_SECRET", "")
BYBIT_DEMO_URL = "https://api-demo.bybit.com"

# Live credentials: env-only, no fallback. Blank until you actually export them. Not committed
# anywhere - see .gitignore for the .env file pattern this is meant to be sourced from.
BYBIT_LIVE_API_KEY = os.getenv("BYBIT_LIVE_API_KEY", "")
BYBIT_LIVE_API_SECRET = os.getenv("BYBIT_LIVE_API_SECRET", "")
BYBIT_LIVE_URL = "https://api.bybit.com"  # Real mainnet trading API

if TRADING_ENV == "live":
    BYBIT_URL = BYBIT_LIVE_URL
    _active_key, _active_secret = BYBIT_LIVE_API_KEY, BYBIT_LIVE_API_SECRET
    if not DEMO_MODE and (not _active_key or not _active_secret):
        print("!" * 70)
        print("[SAFETY] TRADING_ENV=live but BYBIT_LIVE_API_KEY/SECRET are not set.")
        print("         Export them (e.g. via a gitignored .env) before going live.")
        print("!" * 70)
else:
    BYBIT_URL = BYBIT_DEMO_URL
    _active_key, _active_secret = BYBIT_API_KEY, BYBIT_API_SECRET

# --- Trading parameters ---
# Risk plan: selects both risk-per-trade AND the runner strategy's SL-trail shape.
# Switch via RISK_PLAN in .env - no code change needed.
#   "A" (default) -> $2.5 risk. SL -> breakeven at TP3, SL -> TP2's price at TP4.
#   "B"           -> $4 risk.   SL -> breakeven at TP2, SL -> TP2's price at TP3,
#                                SL -> TP3's price at TP4.
# Each stage tuple is (trigger TP index, SL target TP index or None=breakeven/entry),
# 0-based against signal.tps. Stored on each trade at open time (core/trade_tracker.py)
# so an in-flight trade keeps the shape it opened under even if RISK_PLAN changes later.
RISK_PLANS = {
    "A": {"risk_usd": 2.5, "sl_stages": [(2, None), (3, 1)]},          # TP3->BE, TP4->TP2
    "B": {"risk_usd": 4.0, "sl_stages": [(1, None), (2, 1), (3, 2)]},  # TP2->BE, TP3->TP2, TP4->TP3
}
RISK_PLAN = os.getenv("RISK_PLAN", "A").strip().upper()
if RISK_PLAN not in RISK_PLANS:
    print(f"[WARN] Unknown RISK_PLAN={RISK_PLAN!r}, falling back to 'A'")
    RISK_PLAN = "A"
RISK_USD = RISK_PLANS[RISK_PLAN]["risk_usd"]
SL_STAGES = RISK_PLANS[RISK_PLAN]["sl_stages"]
LEVERAGE = 10           # Fallback leverage, only used if per-trade calculation (below) can't
                        # get the data it needs (e.g. instrument/risk-limit lookup fails).

# Leverage is calculated per-trade (core/bybit_client.py: calc_safe_leverage), not fixed, so
# a tight-stop signal doesn't waste margin at a low leverage and a wide-stop signal doesn't
# get liquidated before its own stop-loss ever triggers. For a given signal's stop distance,
# the highest leverage is chosen such that ByBit's liquidation price still stays at least
# LEVERAGE_SAFETY_FACTOR times further away than the signal's own stop - so the strategy's
# stop-loss always fires first, with headroom for slippage/fees. Also capped at
# MAX_LEVERAGE_CEILING regardless of how tight the stop is, and at the symbol's own exchange
# max leverage.
LEVERAGE_SAFETY_FACTOR = 2.0
MAX_LEVERAGE_CEILING = 25
POSITION_MODE = "one_way"  # one_way or hedge_mode

# --- Risk limits (executor-side guard rails, enforced by core/risk_manager.py) ---
MAX_SIGNALS_PER_DAY = 5        # Hard cap on new entries per calendar day (UTC)
MAX_CONCURRENT_POSITIONS = 5   # Hard cap on simultaneously open positions
DAILY_STATS_FILE = os.path.join(DATA_DIR, "daily_stats.json")

# --- Fee-aware validation (scalping: TP must clear round-trip fees by a safety margin) ---
TAKER_FEE_RATE = 0.00055        # ByBit linear perp taker fee (confirmed via demo executions)
MIN_NET_PROFIT_MULTIPLE = 3.0   # TP distance must be >= this many times the round-trip fee cost

# --- Consecutive-failure alerting ---
MAX_CONSECUTIVE_FAILURES = 5   # Log CRITICAL after this many execute_trade failures in a row

# --- Telegram bot notifications (separate bot/account from the channel listener below) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

# --- Telegram client (user account) credentials ---
# Env-only, no hardcoded fallback - set these in a gitignored .env file (see .env.example).
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0")) or None
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
# Not a secret (just a channel ID), fine to keep hardcoded - override via TELEGRAM_CHANNEL if needed.
CHANNEL_USERNAME = -1002773084634
TELEGRAM_SESSION_PATH = os.path.join(DATA_DIR, "telegram_session")

# --- Signal input file ---
SIGNAL_INPUT_FILE = os.path.join(DATA_DIR, "signal_input.txt")
