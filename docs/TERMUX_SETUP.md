# Running on Termux (Android)

This bot has no Termux-specific code — it runs the same `main.py` used on a
PC/WSL. All three dependencies (`requests`, `telethon`, `python-dotenv`) are
pure Python, so nothing needs to compile on-device. There's no systemd on
Termux, so the bot is run in the background with `nohup` + a PID file instead
of tmux/systemd (see `start.sh`/`stop.sh`/`tail.sh`).

## 1. One-time setup

Install **Termux** from F-Droid (not the Play Store version — it's outdated
and `pip install` often fails on it). Optionally also install **Termux:Boot**
(auto-start on reboot) and **Termux:API** (`termux-wake-lock`) from F-Droid.

```bash
pkg update -y
pkg install -y python git
termux-setup-storage   # grants access to shared storage, needed if you transfer files via /sdcard
```

## 2. Get the code onto the phone

Either `git clone` the repo, or zip it on your PC (excluding `.venv/`,
`__pycache__/`, `.git/`, `data/telegram_session*`) and copy it into
`/sdcard/Download/`, then in Termux:

```bash
cp -r /sdcard/Download/bybit_signal_executor ~/
cd ~/bybit_signal_executor
```

## 3. Install dependencies

Termux fully supports Python venvs:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 4. Configure

Copy your `.env` (or create one) with `BOT_TOKEN`, `CHAT_ID`, and any
`TELEGRAM_API_ID`/`TELEGRAM_API_HASH` credentials — same file format as on
PC/WSL. **Never set `TRADING_ENV=live`/`LIVE_TRADING_CONFIRM` here unless you
mean it** — the safety gate in `config/settings.py` applies identically on
Termux.

The first run of the Telegram listener needs an interactive login (phone
number + code) to create `data/telegram_session*` — do this once in the
foreground before backgrounding the bot:

```bash
.venv/bin/python main.py --mode telegram
```

Once it connects and starts listening, Ctrl+C out of it — the session file is
now saved, and future runs (including backgrounded ones) won't prompt again.

## 5. Run in the background

```bash
chmod +x start.sh stop.sh tail.sh
./start.sh          # mode defaults to "both"
./start.sh executor  # or "telegram" / "executor" only
./tail.sh            # live-tail the running log
./stop.sh             # stop it
```

`start.sh` writes a timestamped log to `logs/run_*.log` and tracks the
process in `bot.pid`. Colors in the log are ANSI codes (red = danger, green =
good, yellow = processing) — they render correctly with `./tail.sh` in a
Termux terminal.

## 6. Keep it alive in the background

Android aggressively kills background processes to save battery. To survive:

- Run `termux-wake-lock` before backgrounding (needs Termux:API installed).
- In Android settings, set Termux's battery usage to **Unrestricted**.
- Don't swipe Termux out of the recent-apps list — that kills its process
  tree, including the bot.

## 7. Optional: auto-start on phone reboot

Requires the **Termux:Boot** app installed and opened once.

```bash
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/start-bybit-bot.sh << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
cd ~/bybit_signal_executor
./start.sh
EOF
chmod +x ~/.termux/boot/start-bybit-bot.sh
```
