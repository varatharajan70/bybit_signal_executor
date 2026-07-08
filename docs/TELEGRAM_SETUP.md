# Telegram Signal Listener Setup Guide

## Overview
This integration connects your ByBit trading bot directly to your Telegram channel (50X SCALP) to automatically receive and execute trading signals in real-time.

## Prerequisites
- Telegram account with admin access to 50X SCALP channel
- Python 3.7+
- API credentials from https://my.telegram.org/apps

## Step 1: Get Telegram API Credentials

1. Go to https://my.telegram.org/apps
2. Log in with your Telegram account
3. Click "Create new application"
4. Fill in the form:
   - **App title**: "ByBit Signal Executor"
   - **Short name**: "bybit_executor"
   - **URL**: (leave blank)
   - **Platform**: Web
5. Click "Create"
6. You'll get:
   - **API_ID** (numeric)
   - **API_HASH** (alphanumeric string)

**Save these carefully — you'll need them in Step 3.**

## Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `telethon` — Telegram client library
- `requests` — HTTP library for ByBit API

## Step 3: Run the Setup

Run the Telegram listener to set it up:

```bash
python telegram_listener.py
```

On first run, you'll be prompted for:

1. **Telegram API_ID** — paste the numeric ID from Step 1
2. **Telegram API_HASH** — paste the alphanumeric string from Step 1
3. **Channel identifier** — either:
   - Username: `@50xscalp` (with @)
   - Or numeric ID: (channel ID if available)

### First-Time Authentication

On first run, Telethon will ask for your **phone number** (the account linked to your Telegram).

- The bot will send a **login code to your Telegram app**
- Enter the code when prompted
- If you have **2FA enabled**, you'll also need to enter your password

**The session is then saved** (`telegram_session.session` file) and won't require re-authentication on future runs.

## Step 4: Test the Connection

After authentication, the listener will:

1. Connect to your Telegram channel
2. Display: `Connected to channel: 50X SCALP` (or your channel name)
3. Start listening: `Listening to @50xscalp...`

**Test signal**: 

Send a test message to your 50X SCALP channel in the format:

```
#BTCUSDT | SHORT SETUP
🪙 Entry: $63,500
🎯 Target1: $63,000
🎯 Target2: $62,500
🎯 Target3: $62,000
🎯 Target4: $61,500
🎯 Target5: $61,000
🛑 STOP: $64,500
```

The listener should log:
```
Processing message: #BTCUSDT | SHORT SETUP...
✓ Signal saved: BTCUSDT SHORT @ 63500
```

## Step 5: Run Both Together

Start the combined executor + listener:

```bash
python run_with_telegram.py
```

This runs:
1. **ByBit Executor** — waits for signals and places orders
2. **Telegram Listener** — listens for new signals and writes them

Output should show:
```
============================================================
ByBit Executor + Telegram Signal Listener
============================================================
[MAIN] Starting ByBit Executor...
[Executor] Monitoring signal_input.txt for new signals...
[MAIN] Starting Telegram Listener...
[Telegram] Connected to Telegram
[Telegram] Connected to channel: 50X SCALP
[Telegram] Listening to @50xscalp...
```

## Signal Format

The listener parses signals in this format:

```
#SYMBOL | SETUP_TYPE
🪙 Entry: $PRICE
🎯 Target1: $PRICE
🎯 Target2: $PRICE
🎯 Target3: $PRICE
🎯 Target4: $PRICE
🎯 Target5: $PRICE
🛑 STOP: $PRICE
```

Where:
- **#SYMBOL** — trading pair (e.g., #BTCUSDT, #ETHUSDT)
- **SETUP_TYPE** — SHORT SETUP or LONG SETUP
- **Entry** — entry price in USDT
- **Target1-5** — take-profit levels
- **STOP** — stop-loss price

## Troubleshooting

### "Could not access channel"
- Make sure you're an admin/member of the channel
- Try using the numeric channel ID instead of username
- Verify channel exists and is accessible

### "Session password needed"
- You have 2FA enabled on your account
- When prompted, enter your Telegram password

### "No signals received"
- Check the signal format matches exactly
- Make sure messages contain both `#SYMBOL` and `STOP`
- Verify listener is connected (check log output)

### "Orders not placing"
- Check ByBit demo account has balance in USDT
- Verify API credentials are correct in `config.py`
- Check executor.py logs for ByBit API errors

## Live Trading Migration

When ready to go live:

1. Switch `DEMO_MODE` in `config.py` to `False` (it already is)
2. Update `BYBIT_URL` to `BYBIT_LIVE_URL`
3. Restart the bot

**⚠️ WARNING: LIVE TRADING WILL PLACE REAL ORDERS**

## Files Modified

- ✓ `requirements.txt` — added `telethon`
- ✓ `telegram_listener.py` — new file
- ✓ `run_with_telegram.py` — new combined launcher
- ✓ `config.py` — no changes needed (already configured)
